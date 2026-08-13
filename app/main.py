"""RONDA — servidor principal.

Superficies (contrato funcional del reto):
- Interfaz de llamada:  GET /            + WS /ws/llamada/{session_id}
- Consola de admin:     GET /consola     + REST /api/docs...

Flujo de un turno de voz por WebSocket:
  navegador envía audio (binario webm) → STT (Groq Whisper) → orquestador
  (decisión doble carril + RAG cita-o-silencio) → TTS → binario mp3 de vuelta
  + evento JSON con transcript, citas, semáforo y latencia del turno.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import audio, config, observability, stt, tts
from .conversation import summary as summary_mod
from .conversation.orchestrator import CallSession
from .decision import engine as decision_engine
from .rag import ingest

app = FastAPI(title="RONDA", version="0.1.0")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

SESSIONS: dict[str, CallSession] = {}


def _sello(nombre: str) -> str:
    """Huella corta del contenido de un asset estático."""
    try:
        return hashlib.sha256((STATIC_DIR / nombre).read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"


def _pagina(nombre: str) -> HTMLResponse:
    """Sirve una página con sus hojas y scripts versionados por contenido.

    POR QUÉ EXISTE ESTO. `StaticFiles` solo emite ETag y Last-Modified, sin
    Cache-Control. Ante esa ausencia el navegador aplica caché heurística
    (RFC 9111 §4.2.2) y puede reutilizar el CSS sin revalidarlo: se sirve un
    HTML nuevo con una hoja vieja y la maquetación se desmorona sin que el
    servidor se entere de nada. Sellar la URL con el hash del contenido hace
    que esa combinación sea imposible de construir.
    """
    html = (STATIC_DIR / nombre).read_text(encoding="utf-8")
    for asset in ("styles.css", "llamada.js", "consola.js"):
        html = html.replace(f"/static/{asset}", f"/static/{asset}?v={_sello(asset)}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store, must-revalidate"})


# ── Superficie 1: interfaz de llamada ───────────────────────────────────────
@app.get("/")
def llamada_page():
    return _pagina("llamada.html")


@app.get("/api/paciente-demo")
def paciente_demo():
    """Paciente que se atenderá al iniciar. Lo usa la portada para presentarlo
    ANTES de la llamada, en vez de mostrar una pantalla vacía. Es el mismo
    perfil que cargará `CallSession`; no crea sesión ni estado."""
    from .conversation.orchestrator import _load_demo_patient

    return _load_demo_patient()


@app.post("/api/llamada/iniciar")
async def iniciar_llamada():
    session = CallSession()
    # IDENTIDAD DE VOZ: se resuelve UNA vez, aquí, y queda atada a la sesión.
    # El saludo y todos los turnos usan este perfil; nadie vuelve a elegir
    # motor a mitad de llamada.
    session.voice_profile = await tts.elegir_perfil()
    observability.log_event({
        "tipo": "voice_profile",
        "session_id": session.session_id,
        **session.voice_profile.como_dict(),
    })
    saludo = session.saludo_inicial()
    try:
        audio = await tts.synthesize(saludo["texto"], session.voice_profile)
    except Exception:
        # La sesión aún NO está en SESSIONS: si la inicialización falla, no
        # queda ninguna sesión huérfana que nadie pueda finalizar.
        # El evento `tts_error` ya lo emitió tts.synthesize.
        return JSONResponse(
            {
                "error": "voz_no_disponible",
                "detalle": "No se pudo generar la voz del agente. Revise el motor de TTS.",
            },
            status_code=503,
        )
    # Registro transaccional: solo tras completar la inicialización con éxito.
    SESSIONS[session.session_id] = session
    observability.log_event(
        {"tipo": "llamada_iniciada", "session_id": session.session_id,
         "paciente": session.paciente.get("nombre")}
    )
    return {
        "session_id": session.session_id,
        "paciente": session.paciente,
        "saludo": saludo,
        "audio_b64": _b64(audio),
        # Identidad de voz de la sesión, para que la interfaz muestre con quién
        # está hablando el paciente. Es el mismo perfil que usarán todos los
        # turnos: no se vuelve a elegir.
        "voz": session.voice_profile.como_dict() if session.voice_profile else None,
    }


# Protocolo de error del WebSocket. El navegador recibe SIEMPRE un mensaje
# explicable; nunca un cierre mudo. El texto es fijo por código: jamás viaja
# el mensaje de la excepción (podría contener URLs con credenciales).
ERRORES_WS = {
    "stt": ("stt_no_disponible",
            "No pude procesar el audio. ¿Me lo repite, por favor?", True),
    "motor": ("motor_no_disponible",
              "Tuve un problema al procesar lo que me dijo. Intentémoslo otra vez.", True),
    "tts": ("tts_no_disponible",
            "Le respondí por escrito: mi voz no está disponible en este momento.", True),
    "interno": ("error_interno",
                "Ocurrió un problema técnico. Podemos intentarlo nuevamente.", True),
}


async def _error_ws(ws: WebSocket, session_id: str, turno: int, componente: str, exc: Exception):
    """Informa al navegador y deja rastro auditable. Devuelve si es recuperable."""
    codigo, mensaje, recuperable = ERRORES_WS.get(componente, ERRORES_WS["interno"])
    observability.log_error(
        "ws_error", exc,
        {"session_id": session_id, "turno": turno, "componente": componente,
         "codigo": codigo, "recuperable": recuperable},
    )
    await ws.send_text(json.dumps(
        {"tipo": "error", "codigo": codigo, "mensaje": mensaje, "recuperable": recuperable},
        ensure_ascii=False,
    ))
    return recuperable


@app.websocket("/ws/llamada/{session_id}")
async def ws_llamada(ws: WebSocket, session_id: str):
    await ws.accept()
    session = SESSIONS.get(session_id)
    if session is None:
        await ws.close(code=4404)
        return
    vad_meta: dict = {}
    try:
        while True:
            mensaje = await ws.receive()
            if mensaje.get("type") == "websocket.disconnect":
                break
            if mensaje.get("text") is not None:
                # Telemetría del VAD que precede al audio del turno.
                try:
                    vad_meta = json.loads(mensaje["text"])
                except Exception:
                    vad_meta = {}
                continue
            audio_bytes = mensaje.get("bytes")
            if not audio_bytes:
                continue

            timer = observability.TurnTimer(session_id, session.turnos + 1)
            timer.audio_in_s = audio.duracion_segundos(audio_bytes)
            meta_turno = {k: v for k, v in vad_meta.items() if k != "tipo"}
            vad_meta = {}

            # ── STT ───────────────────────────────────────────────────────
            try:
                texto_paciente, meta_stt = stt.transcribe_detallado(audio_bytes)
            except Exception as e:
                # Recuperable: el paciente puede repetir. La llamada sigue viva.
                await _error_ws(ws, session_id, timer.turno, "stt", e)
                continue
            timer.mark("t_stt_fin")
            meta_turno.update(meta_stt)

            # Segunda barrera: audio sin habla no puede convertirse en turno.
            # Whisper alucina sobre silencio (".", "Gracias por ver el video."),
            # así que una transcripción sin un solo carácter alfanumérico se
            # trata como "no le escuché", no como intervención del paciente.
            if texto_paciente and not stt.tiene_contenido(texto_paciente):
                observability.log_event(
                    {"tipo": "captura_descartada", "session_id": session_id,
                     "turno": timer.turno, "motivo": "sin_contenido_pronunciable",
                     "transcripcion": texto_paciente[:40], **meta_turno})
                texto_paciente = ""

            if not texto_paciente:
                respuesta_texto = "No le escuché bien, ¿me lo repite por favor?"
                await ws.send_text(json.dumps(
                    {"tipo": "sin_audio", "texto": respuesta_texto},
                    ensure_ascii=False,
                ))
                try:
                    audio_bytes_out = await tts.synthesize(respuesta_texto,
                                                           session.voice_profile)
                    timer.mark("t_tts_primer_byte")
                    await ws.send_bytes(audio_bytes_out)
                except Exception as e:
                    await _error_ws(ws, session_id, timer.turno, "tts", e)
                timer.close({"nota": "sin_transcripcion"})
                continue

            # ── Motor de decisión + generación ────────────────────────────
            try:
                resultado = session.turno(texto_paciente)
            except Exception as e:
                await _error_ws(ws, session_id, timer.turno, "motor", e)
                continue
            if getattr(session, "t_decision_fin", None):
                timer.marks["t_decision_fin"] = session.t_decision_fin
            timer.mark("t_llm_fin")
            timer.add_usage(resultado.get("usage", {}))
            if getattr(session, "_last_usage", None):
                timer.add_usage(session._last_usage)
                session._last_usage = None
            timer.rag_queries = resultado.get("consultas_rag", 0)

            # ── Entrega del texto ANTES que la voz ────────────────────────
            # El transcript aparece en cuanto el modelo termina, sin esperar a
            # la síntesis: recorta varios segundos de espera percibida.
            await ws.send_text(json.dumps(
                {
                    "tipo": "turno",
                    "paciente_texto": texto_paciente,
                    "agente_texto": resultado["texto"],
                    "citas": resultado["citas"],
                    "semaforo": resultado["semaforo"],
                    "nivel_turno": resultado["nivel_turno"],
                    # Estado de fundamentación del turno, para que la interfaz
                    # pueda distinguir "respondió citando" de "se abstuvo".
                    "response_mode": resultado.get("response_mode"),
                    "kb_version": resultado.get("kb_version"),
                    "alerta": resultado["alerta"],
                    # Los tres ejes, separados: riesgo clínico ≠ estado de la
                    # evaluación ≠ acción operativa. El panel los muestra como
                    # tres tarjetas distintas porque son tres cosas distintas.
                    "riesgo_clinico": resultado.get("riesgo_clinico"),
                    "estado_evaluacion": resultado.get("estado_evaluacion"),
                    "accion_operativa": resultado.get("accion_operativa"),
                    "razon_de_incertidumbre": resultado.get("razon_de_incertidumbre"),
                    "evidencias_recuperadas": resultado.get("evidencias_recuperadas"),
                    "estado_fsm": resultado.get("estado"),
                    "cobertura": resultado.get("cobertura"),
                    # Cierre conversacional: se ANUNCIA aquí, junto al texto,
                    # pero el navegador no cuelga hasta haber reproducido el
                    # audio entero. Nunca se corta el TTS a mitad de frase.
                    "estado_cierre": resultado.get("estado_cierre"),
                    "motivo_cierre": resultado.get("motivo_cierre"),
                },
                ensure_ascii=False,
            ))

            # ── TTS en streaming ──────────────────────────────────────────
            # Cada trozo sale hacia el navegador en cuanto el motor lo produce.
            trozos = 0
            bytes_audio = 0
            fallo_tts = None
            meta_tts: dict = {}
            try:
                # El router resuelve el motor (y el fallback) antes de emitir:
                # `audio_inicio` ya declara el formato que va a llegar.
                meta_tts, generador = await tts.iniciar(resultado["texto"],
                                                        session.voice_profile)
                await ws.send_text(json.dumps(
                    {"tipo": "audio_inicio", "turno": timer.turno,
                     "formato": meta_tts["formato"],
                     "sample_rate": meta_tts["sample_rate"]}))
                async for trozo in generador:
                    if trozos == 0:
                        timer.mark("t_tts_primer_byte")
                    trozos += 1
                    bytes_audio += len(trozo)
                    await ws.send_bytes(trozo)
                timer.mark("t_tts_fin")
                await ws.send_text(json.dumps(
                    {"tipo": "audio_fin", "turno": timer.turno, "trozos": trozos,
                     "bytes": bytes_audio}))
            except Exception as e:
                fallo_tts = e

            evento = timer.close(
                {"semaforo": resultado["semaforo"], "alerta": resultado["alerta"],
                 "sin_voz": trozos == 0, "tts_trozos": trozos, "tts_bytes": bytes_audio,
                 "tts_motor": meta_tts.get("motor"),
                 # Identidad de voz REAL de este turno frente a la de la
                 # sesión. Si divergen sin una transición autorizada, es un
                 # cambio de persona a mitad de llamada y hay que verlo.
                 "voice_id_turno": meta_tts.get("voz") or meta_tts.get("voice_id_sesion"),
                 "voice_id_sesion": (session.voice_profile.voice_id
                                     if session.voice_profile else None),
                 "persona_id": (session.voice_profile.persona_id
                                if session.voice_profile else None),
                 "voz_consistente": (
                     session.voice_profile is None
                     or meta_tts.get("motor") == session.voice_profile.provider),
                 "tts_reintentos": meta_tts.get("reintentos", 0),
                 "tts_primary": meta_tts.get("primario") or config.TTS_PRIMARY,
                 "tts_fallback_usado": meta_tts.get("fallback_usado"),
                 "tts_error_primary": meta_tts.get("error_primario"),
                 "tts_error_fallback": (type(fallo_tts).__name__ if fallo_tts else None),
                 "tts_formato": meta_tts.get("formato"),
                 **meta_turno}
            )
            # El error va ANTES del cierre: cada turno termina siempre con el
            # frame `latencia`, así el cliente sabe sin ambigüedad dónde acaba.
            if fallo_tts is not None:
                await _error_ws(ws, session_id, timer.turno, "tts", fallo_tts)
            await ws.send_text(json.dumps(
                {"tipo": "latencia", "turno": timer.turno,
                 "servidor_a_primer_audio_ms": evento.get("servidor_a_primer_audio_ms"),
                 "etapas": timer.etapas()}, ensure_ascii=False))

            # §A7 · CIERRE NATURAL. Va DESPUÉS del último frame del turno, con
            # todo el audio ya en la cola del navegador. El servidor no cuelga
            # aquí: solo anuncia. Quien cierra es el cliente, cuando termina de
            # reproducir —cortar el TTS a mitad de la despedida sería peor que
            # no cerrar— y lo hace con código 1000 tras persistir el acta.
            if resultado.get("cerrar_llamada"):
                await ws.send_text(json.dumps(
                    {"tipo": "cierre_llamada", "turno": timer.turno,
                     "motivo": resultado.get("motivo_cierre") or "seguimiento_completado"},
                    ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        # Red de seguridad: nada puede cerrar el WS sin explicación.
        try:
            await _error_ws(ws, session_id, session.turnos, "interno", e)
            await ws.close(code=1011)
        except Exception:
            pass


@app.post("/api/llamada/{session_id}/finalizar")
def finalizar_llamada(session_id: str):
    session = SESSIONS.get(session_id)
    if session is None:
        return JSONResponse({"error": "sesión no encontrada"}, status_code=404)
    resumen = session.finalizar()
    observability.log_event(
        {"tipo": "llamada_finalizada", "session_id": session_id,
         "criticidad_final": resumen["criticidad_final"],
         "escalado": resumen["decision"]["escalado"]}
    )
    SESSIONS.pop(session_id, None)
    return resumen


# ── Superficie 2: consola de administración ────────────────────────────────
@app.get("/consola")
def consola_page():
    return _pagina("consola.html")


@app.post("/api/docs")
async def subir_documento(file: UploadFile = File(...)):
    dest = config.UPLOADS_DIR / file.filename
    dest.write_bytes(await file.read())
    resultado = ingest.ingest_file(dest, file.filename)
    duplicado = bool(resultado.get("duplicado"))
    observability.log_event(
        {"tipo": "doc_ingresado", "doc_id": resultado.get("doc_id"),
         "titulo": file.filename, "estado": resultado.get("estado"),
         "duplicado": duplicado,
         # `chunks` es el total que ya tenía el documento; `chunks_indexados`
         # es lo que ESTA petición añadió realmente. En una deduplicación es 0:
         # el evento no puede afirmar una segunda indexación que no ocurrió.
         "chunks": resultado.get("chunks"),
         "chunks_indexados": 0 if duplicado else resultado.get("chunks")}
    )
    return resultado


@app.get("/api/docs")
def listar_documentos():
    return ingest.list_documents()


@app.delete("/api/docs/{doc_id}")
def eliminar_documento(doc_id: str):
    try:
        tombstone = ingest.delete_document(doc_id)
    except KeyError:
        return JSONResponse({"error": "documento no encontrado"}, status_code=404)
    observability.log_event({"tipo": "doc_eliminado", **tombstone})
    return {"eliminado": True, "tombstone": tombstone}


@app.post("/api/docs/{doc_id}/verificar-olvido")
def verificar_olvido(doc_id: str):
    resultado = ingest.verify_forgotten(doc_id)
    observability.log_event({"tipo": "verificacion_olvido", **resultado})
    return resultado


@app.get("/api/alertas")
def listar_alertas():
    return decision_engine.listar_alertas()


@app.get("/api/actas")
def listar_actas():
    return summary_mod.listar_actas()


@app.get("/api/actas/{session_id}")
def leer_acta(session_id: str):
    acta = summary_mod.leer_acta(session_id)
    if acta is None:
        return JSONResponse({"error": "acta no encontrada"}, status_code=404)
    return acta


@app.get("/api/salud")
def salud():
    from .rag import retrieve, store

    return {
        "estado": "ok",
        "modelo": config.GROQ_MODEL if config.LLM_PROVIDER == "groq" else config.GEMINI_MODEL,
        "proveedor": config.LLM_PROVIDER,
        "tts": tts.salud(),
        "documentos_indexados": len(ingest.list_documents()),
        "vectores": store.collection_count(),
        "kb_version": retrieve.kb_version(),
        "codigo": _estado_del_codigo(),
    }


# Marca temporal del código que este proceso cargó al arrancar. `run.py` no
# usa recarga automática —a propósito: un reinicio a mitad de llamada sería
# peor— así que un servidor levantado antes de un cambio sigue sirviendo el
# código viejo sin dar ninguna señal.
#
# Esto costó un ciclo completo de validación humana: la corrección estaba en
# disco, las pruebas pasaban, y el navegador seguía mostrando el fallo porque
# el proceso llevaba tres cuartos de hora en marcha. Comparar ambas marcas lo
# convierte en algo visible en vez de en una tarde perdida.
def _mtime_del_codigo() -> float:
    raiz = Path(__file__).parent
    return max((p.stat().st_mtime for p in raiz.rglob("*.py")), default=0.0)


_CODIGO_AL_ARRANCAR = _mtime_del_codigo()


def _estado_del_codigo() -> dict:
    actual = _mtime_del_codigo()
    obsoleto = actual > _CODIGO_AL_ARRANCAR + 1  # 1 s de tolerancia
    return {
        "cargado": datetime.fromtimestamp(_CODIGO_AL_ARRANCAR).isoformat(timespec="seconds"),
        "en_disco": datetime.fromtimestamp(actual).isoformat(timespec="seconds"),
        "obsoleto": obsoleto,
        **({"aviso": "El servidor está ejecutando código anterior al del disco. "
                     "Reinícielo para que los cambios tengan efecto."} if obsoleto else {}),
    }


@app.on_event("startup")
async def _precalentar_voz():
    """Arranque en frío del motor local pagado UNA vez, al levantar el
    servidor, y no en el primer turno del paciente. No bloquea el arranque."""
    import asyncio

    asyncio.create_task(tts.precalentar())


@app.on_event("startup")
async def _precalentar_embeddings():
    """Carga el modelo de embeddings al arrancar, no en la primera pregunta.

    Medido: la primera consulta al RAG tardaba ~8,5 s de los 11,3 s de la
    demostración G5 completa, y todo ese tiempo era cargar el modelo. Pagarlo
    al levantar el servidor —en segundo plano, sin bloquear la interfaz ni el
    arranque— deja la primera pregunta del jurado en la latencia real.

    No descarga nada: el modelo ya está en la caché local desde la instalación,
    así que esto no afecta al arranque en 15 minutos (G2). Tampoco toca el
    índice, así que el hot-swap es indiferente a este precalentamiento.
    """
    import asyncio
    import time

    from .rag import store

    async def _cargar():
        try:
            t0 = time.perf_counter()
            # Una consulta trivial fuerza la carga del modelo y del índice.
            await asyncio.to_thread(store.embed, ["precalentamiento"])
            observability.log_event({
                "tipo": "embeddings_precalentados",
                "ms": int((time.perf_counter() - t0) * 1000),
                "modelo": config.EMBEDDING_MODEL,
            })
        except Exception as e:  # noqa: BLE001
            # Un fallo aquí no puede impedir que el servidor levante: el
            # modelo se cargará en la primera consulta, como antes.
            observability.log_error("embeddings_precalentado_fallo", e, {})

    asyncio.create_task(_cargar())


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode()


