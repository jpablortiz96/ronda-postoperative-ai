"""Síntesis de voz del agente — router con dos motores.

                      ┌→ edge   (Microsoft, voz es-CO, red obligatoria)
    RONDA → tts.iniciar│
                      └→ piper  (local, sin red, ~90 ms al primer audio)

`TTS_ENGINE` elige el modo: `edge`, `piper` o `auto`. En `auto` se intenta
`TTS_PRIMARY` y, si ese motor no logra emitir el primer trozo, se cae al otro
y la llamada continúa. Un corte del servicio externo deja de ser un fallo de
la llamada y pasa a ser una nota en el log.

CONTRATO ÚNICO hacia el WebSocket: `iniciar()` devuelve (metadatos, trozos).
Los metadatos declaran el formato del audio, de modo que el resto de RONDA no
necesita saber qué motor respondió. Ningún `if motor ==` fuera de este módulo.

FORMATOS: edge entrega MP3; Piper entrega PCM de 16 bits. No se transcodifica
—sería CPU y latencia gratuitas—: el formato viaja declarado en el contrato y
el reproductor elige la ruta adecuada.
"""
from __future__ import annotations

from dataclasses import dataclass

import asyncio

from . import config, observability

MOTORES = ("edge", "piper")


class TtsNoDisponible(RuntimeError):
    """Ningún motor pudo emitir audio. Lleva el error de cada intento."""

    def __init__(self, errores: dict):
        self.errores = errores
        detalle = "; ".join(f"{m}: {type(e).__name__}" for m, e in errores.items())
        super().__init__(f"ningún motor de voz disponible ({detalle})")


# ── Orden de intentos ───────────────────────────────────────────────────────
def orden_motores() -> list[str]:
    modo = (config.TTS_ENGINE or "edge").lower()
    if modo == "auto":
        primario = (config.TTS_PRIMARY or "edge").lower()
        if primario not in MOTORES:
            primario = "edge"
        return [primario, "piper" if primario == "edge" else "edge"]
    return [modo if modo in MOTORES else "edge"]


# ── Identidad de voz de la sesión ───────────────────────────────────────────
# POR QUÉ EXISTE
# --------------
# `iniciar()` resolvía el motor en CADA turno. Con `TTS_ENGINE=auto`, un fallo
# transitorio de Edge en mitad de la llamada caía a Piper solo para ese turno
# y volvía a Edge en el siguiente. Reportado por un evaluador humano y
# reproducible: la voz pasaba de mujer colombiana a hombre mexicano y volvía.
# Además el saludo viajaba por REST (`synthesize`) y los turnos por WebSocket
# (`iniciar`), así que podían resolverse a motores distintos.
#
# La identidad conversacional vale más que mantener audio a cualquier precio:
# un paciente que oye tres personas distintas deja de creer que habla con una.
@dataclass(frozen=True)
class PerfilDeVoz:
    persona_id: str
    provider: str
    voice_id: str
    locale: str
    gender: str

    def como_dict(self) -> dict:
        return {"persona_id": self.persona_id, "provider": self.provider,
                "voice_id": self.voice_id, "locale": self.locale,
                "gender": self.gender}


def _perfil_edge() -> PerfilDeVoz:
    voz = config.TTS_VOICE or "es-CO-SalomeNeural"
    return PerfilDeVoz(persona_id="ronda_salome", provider="edge", voice_id=voz,
                       locale="-".join(voz.split("-")[:2]) or "es-CO",
                       gender="female")


def _perfil_piper() -> PerfilDeVoz:
    voz = config.PIPER_VOZ or "es_MX-ald-medium"
    return PerfilDeVoz(persona_id=f"ronda_{voz.split('-')[1] if '-' in voz else voz}",
                       provider="piper", voice_id=voz,
                       locale=voz.split("-")[0].replace("_", "-"),
                       # El modelo local disponible es masculino: por eso NO
                       # sirve como sustituto silencioso de Salomé a mitad de
                       # llamada. Ver la política en `iniciar`.
                       gender="male")


_PERFILES = {"edge": _perfil_edge, "piper": _perfil_piper}


async def elegir_perfil() -> PerfilDeVoz:
    """Resuelve la identidad de voz UNA vez, al iniciar la llamada.

    Prueba los motores en orden y devuelve el perfil del primero que responde
    de verdad —no el que está configurado—, de modo que la sesión arranca ya
    con una identidad comprobada en vez de descubrirla en el primer turno.
    """
    for motor in orden_motores():
        generar, _ = _IMPL[motor]
        gen = generar("Hola.")
        try:
            await gen.__anext__()
        except StopAsyncIteration:
            pass
        except Exception as e:
            observability.log_error("tts_perfil_descartado", e, {"motor": motor})
            await gen.aclose()
            continue
        else:
            await gen.aclose()
        return _PERFILES[motor]()
    # Ningún motor responde: se devuelve el perfil nominal para que la sesión
    # exista y el sistema degrade a respuesta escrita, no para forzar audio.
    return _PERFILES[orden_motores()[0]]()


def perfiles_compatibles(perfil: PerfilDeVoz) -> list[str]:
    """Motores que pueden sustituir a este perfil SIN cambiar de persona.

    Hoy devuelve solo el propio motor: no hay una voz Piper femenina en
    es-CO instalada, así que ningún motor alternativo suena como Salomé.
    Cuando exista, se declara aquí y el cambio pasa a ser legítimo.
    """
    return [perfil.provider]


# ── Motor 1: edge (remoto) ──────────────────────────────────────────────────
async def _trozos_edge(texto: str):
    import edge_tts

    communicate = edge_tts.Communicate(texto, voice=config.TTS_VOICE, rate="+4%")
    async for mensaje in communicate.stream():
        if mensaje["type"] == "audio" and mensaje["data"]:
            yield mensaje["data"]


def _meta_edge() -> dict:
    return {"motor": "edge", "formato": "mp3", "sample_rate": None,
            "voz": config.TTS_VOICE}


# ── Motor 2: piper (local) ──────────────────────────────────────────────────
_voz_piper = None


def _cargar_piper():
    """Carga perezosa y única del modelo. Reutilizarlo evita pagar ~2,3 s de
    arranque en frío en cada turno."""
    global _voz_piper
    if _voz_piper is None:
        from piper import PiperVoice

        _voz_piper = PiperVoice.load(config.PIPER_MODELO)
    return _voz_piper


def _pcm_de(trozo) -> bytes:
    datos = getattr(trozo, "audio_int16_bytes", None)
    if datos is not None:
        return datos
    arr = getattr(trozo, "audio_int16_array", b"")
    return arr.tobytes() if hasattr(arr, "tobytes") else bytes(arr)


_FIN_PIPER = object()


async def _trozos_piper(texto: str):
    """Emite cada trozo EN CUANTO el motor lo produce.

    La síntesis es CPU pura y bloqueante, así que corre en un hilo aparte para
    no congelar el WebSocket; los trozos viajan por una cola. Acumularlos y
    devolverlos al final anularía el streaming: el primer audio pasaría de
    ~90 ms a esperar la frase entera.
    """
    voz = await asyncio.to_thread(_cargar_piper)
    cola: asyncio.Queue = asyncio.Queue()
    bucle = asyncio.get_running_loop()

    def producir():
        try:
            for t in voz.synthesize(texto):
                datos = _pcm_de(t)
                if datos:
                    bucle.call_soon_threadsafe(cola.put_nowait, datos)
        except Exception as e:  # se propaga al consumidor
            bucle.call_soon_threadsafe(cola.put_nowait, e)
        finally:
            bucle.call_soon_threadsafe(cola.put_nowait, _FIN_PIPER)

    tarea = asyncio.create_task(asyncio.to_thread(producir))
    try:
        while True:
            item = await cola.get()
            if item is _FIN_PIPER:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        await tarea


def _meta_piper() -> dict:
    sr = 22050
    try:
        cfg = getattr(_cargar_piper(), "config", None)
        sr = getattr(cfg, "sample_rate", sr) or sr
    except Exception:
        pass
    return {"motor": "piper", "formato": "pcm16", "sample_rate": sr,
            "voz": config.PIPER_VOZ}


_IMPL = {
    "edge": (_trozos_edge, _meta_edge),
    "piper": (_trozos_piper, _meta_piper),
}


# ── API pública ─────────────────────────────────────────────────────────────
def salud() -> dict:
    """Disponibilidad de cada motor, sin sintetizar nada."""
    estado = {"modo": config.TTS_ENGINE, "primario": config.TTS_PRIMARY,
              "orden": orden_motores(), "motores": {}}
    try:
        import edge_tts  # noqa: F401

        estado["motores"]["edge"] = {"disponible": True, "voz": config.TTS_VOICE}
    except Exception as e:
        estado["motores"]["edge"] = {"disponible": False, "motivo": type(e).__name__}
    try:
        import pathlib

        import piper  # noqa: F401

        hay_modelo = pathlib.Path(config.PIPER_MODELO).exists()
        estado["motores"]["piper"] = {
            "disponible": hay_modelo, "voz": config.PIPER_VOZ,
            "modelo": config.PIPER_MODELO if hay_modelo else None,
            "motivo": None if hay_modelo else "modelo no encontrado",
            "cargado": _voz_piper is not None,
        }
    except Exception as e:
        estado["motores"]["piper"] = {"disponible": False, "motivo": type(e).__name__}
    return estado


async def precalentar() -> dict:
    """Carga el modelo local y hace una síntesis mínima para que el primer
    turno del paciente no pague el arranque en frío. No escribe archivos."""
    import time

    if "piper" not in orden_motores():
        return {"precalentado": False, "motivo": "piper no está en el orden de motores"}
    t0 = time.perf_counter()
    try:
        await asyncio.to_thread(_cargar_piper)
        async for _ in _trozos_piper("Listo."):
            break
        ms = round((time.perf_counter() - t0) * 1000)
        observability.log_event({"tipo": "tts_precalentado", "motor": "piper", "ms": ms})
        return {"precalentado": True, "motor": "piper", "ms": ms}
    except Exception as e:
        observability.log_error("tts_error", e, {"fase": "precalentado", "motor": "piper"})
        return {"precalentado": False, "motivo": type(e).__name__}


REINTENTOS_MISMO_MOTOR = 1


async def iniciar(texto: str, perfil: PerfilDeVoz | None = None) -> tuple[dict, "object"]:
    """Devuelve (metadatos, generador de trozos), ya con el motor resuelto.

    El fallback se decide al obtener el PRIMER trozo: es la única forma de
    saber que un motor realmente responde. Si un motor falla después de haber
    emitido audio no se cambia de motor —ya suena algo— y el error se propaga.

    CON PERFIL DE SESIÓN (llamada en curso)
    ---------------------------------------
    El motor NO se vuelve a elegir. Se reintenta el mismo una vez y, si sigue
    fallando, solo se admite un motor declarado compatible con esa persona.
    Como hoy no existe una voz local femenina en es-CO, eso significa en la
    práctica que la llamada continúa por escrito en vez de cambiar de
    personaje a mitad de conversación. Es deliberado.
    """
    if perfil is not None:
        return await _iniciar_con_perfil(texto, perfil)
    intentos = orden_motores()
    errores: dict[str, Exception] = {}
    for motor in intentos:
        generar, describir = _IMPL[motor]
        gen = generar(texto)
        try:
            primero = await gen.__anext__()
        except StopAsyncIteration:
            primero = b""
        except Exception as e:
            errores[motor] = e
            observability.log_error(
                "tts_error", e,
                {"motor": motor, "caracteres": len(texto),
                 "quedan_alternativas": motor != intentos[-1]},
            )
            await gen.aclose()
            continue

        meta = describir()
        meta.update({
            "primario": intentos[0],
            "fallback_usado": motor != intentos[0],
            "error_primario": (type(errores[intentos[0]]).__name__
                               if intentos[0] in errores else None),
        })
        return meta, _con_primer_trozo(primero, gen)

    raise TtsNoDisponible(errores)


async def _iniciar_con_perfil(texto: str, perfil: PerfilDeVoz):
    """Síntesis atada a la identidad de la sesión."""
    compatibles = perfiles_compatibles(perfil)
    errores: dict[str, Exception] = {}
    for motor in compatibles:
        generar, describir = _IMPL[motor]
        for intento in range(REINTENTOS_MISMO_MOTOR + 1):
            gen = generar(texto)
            try:
                primero = await gen.__anext__()
            except StopAsyncIteration:
                primero = b""
            except Exception as e:
                errores[motor] = e
                observability.log_error(
                    "tts_error", e,
                    {"motor": motor, "caracteres": len(texto), "intento": intento + 1,
                     "persona_id": perfil.persona_id,
                     "quedan_alternativas": intento < REINTENTOS_MISMO_MOTOR})
                await gen.aclose()
                continue
            meta = describir()
            meta.update({
                "primario": perfil.provider,
                "fallback_usado": motor != perfil.provider,
                "error_primario": (type(errores[perfil.provider]).__name__
                                   if perfil.provider in errores else None),
                "persona_id": perfil.persona_id,
                "voice_id_sesion": perfil.voice_id,
                "reintentos": intento,
            })
            return meta, _con_primer_trozo(primero, gen)
    # Sin motor compatible: NO se cambia de persona. El llamador degrada a
    # texto, que es lo que la política de esta fase considera preferible.
    observability.log_event({
        "tipo": "tts_sin_perfil_compatible",
        "persona_id": perfil.persona_id, "provider": perfil.provider,
        "motivo": "el motor de la sesión no responde y no hay voz equivalente",
    })
    raise TtsNoDisponible(errores)


async def _con_primer_trozo(primero: bytes, resto):
    if primero:
        yield primero
    async for t in resto:
        yield t


async def stream(texto: str):
    """Trozos sin metadatos. Se conserva para pruebas y usos simples."""
    _, trozos = await iniciar(texto)
    async for t in trozos:
        yield t


async def synthesize(text: str, perfil: PerfilDeVoz | None = None) -> bytes:
    """Audio completo en un bloque. Lo usa el saludo inicial, que viaja por
    REST. Devuelve MP3 o PCM segun el motor que haya respondido.

    Acepta el perfil de sesion para que el SALUDO use exactamente la misma
    identidad que los turnos posteriores: antes el saludo iba por esta ruta y
    los turnos por `iniciar`, y podian resolverse a motores distintos."""
    meta, trozos = await iniciar(text, perfil)
    datos = b"".join([t async for t in trozos])
    if meta["formato"] == "pcm16":
        datos = envolver_wav(datos, meta["sample_rate"])
        meta["formato"] = "wav"
    return datos


def envolver_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Cabecera WAV sobre PCM de 16 bits mono. Necesario cuando el audio viaja
    como un bloque único (saludo por REST), donde no hay contrato que declare
    el formato: un WAV se reproduce solo."""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate or 22050)
        w.writeframes(pcm)
    return buf.getvalue()

