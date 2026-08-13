# -*- coding: utf-8 -*-
"""G5 por la ruta humana completa: servidor real, sesión real, FSM real.

POR QUÉ ESTE ARCHIVO EXISTE APARTE
----------------------------------
La regresión anterior (R1) daba PASS mientras la llamada humana fallaba. El
motivo: R1 llamaba a `retrieve.recuperar(PREGUNTA)` por su cuenta para obtener
la evidencia y se la pasaba a un modelo doblado. Así se saltaba justo la parte
rota —la construcción de la consulta dentro del orquestador— y probaba el
gate, no el camino del paciente.

Aquí no se llama a `retrieve`, ni a `gate`, ni a `orchestrator.turno` a mano.
Se arranca el servidor con `TestClient`, se hace `POST /api/llamada/iniciar` y
se conversa por la misma sesión que usaría el navegador. Lo único doblado es
el proveedor del modelo, para que la prueba no dependa de la cuota.
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import app.main as main_mod  # noqa: E402
from app.conversation import generacion  # noqa: E402
from app.decision import assess  # noqa: E402
from app.rag import ingest, retrieve, store  # noqa: E402

fallos = 0
CLAVE = "NEBULA-6249"
PREGUNTA = "¿Cuál es la clave de verificación temporal de este documento?"
DOC = ("DOCUMENTO TEMPORAL DE VERIFICACION DE CONOCIMIENTO VIVO\n\n"
       f"La clave de verificacion temporal de este documento es {CLAVE}.\n")


def check(nombre: str, cond: bool, detalle: str = "") -> None:
    global fallos
    fallos += not cond
    print(f"  [{'PASS' if cond else 'FAIL'}] {nombre}" + (f"   → {detalle}" if not cond else ""))


def limpiar_g5() -> None:
    for doc_id, d in list(ingest.load_manifest()["documentos"].items()):
        if d.get("estado") == "disponible" and "G5-DEMO" in (d.get("titulo") or ""):
            ingest.delete_document(doc_id)


# El extractor de slots dice FUERA DE MISIÓN, como hizo con el evaluador real.
assess.extract_slots = lambda t, c, h=None: (
    assess._merge_slots(h or {}, {**assess._empty_slots(), "fuera_de_mision": True}),
    {"provider": "prueba", "input_tokens": 0, "output_tokens": 0})


def modelo(messages, **kw):
    """Modelo cooperativo: cita lo que se le entregue, si se le entrega algo."""
    bloque = messages[-1]["content"]
    ids = re.findall(r'id="(ev_[0-9a-f]+)"', bloque)
    clave = re.search(r"(NEBULA-\d+)", bloque)
    if ids and clave:
        return ({"sentences": [{"text": f"La clave de verificación temporal es "
                                        f"{clave.group(1)}.",
                                "clinical": False, "evidence_ids": [ids[0]]}],
                 "followup_question": ""}, {"provider": "prueba"})
    return ({"sentences": [{"text": "No tengo evidencia suficiente en el conocimiento "
                                    "activo para responder esa pregunta con seguridad.",
                            "clinical": False, "evidence_ids": []}],
             "followup_question": ""}, {"provider": "prueba"})


generacion.llm.chat_json = modelo

# La voz no interviene: se neutraliza para que la prueba no dependa de red.
async def _synthesize(texto, perfil=None):
    return b"\x00"


main_mod.tts.synthesize = _synthesize


async def _perfil():
    return main_mod.tts._perfil_edge()


main_mod.tts.elegir_perfil = _perfil

limpiar_g5()

with TestClient(main_mod.app) as c:
    # ── 1 · el jurado sube el documento por la consola ─────────────────────
    print("\n1 · subida por la consola")
    r = c.post("/api/docs", files={"file": ("G5-DEMO-UNICO.txt",
                                            io.BytesIO(DOC.encode()), "text/plain")})
    check("HTTP 200 al subir", r.status_code == 200, str(r.status_code))
    doc_id = (r.json() or {}).get("doc_id")
    check("el documento queda con 1 fragmento", (r.json() or {}).get("chunks") == 1,
          str(r.json()))
    kb_con_doc = retrieve.kb_version()

    # ── 2 · llamada real ──────────────────────────────────────────────────
    print("\n2 · POST /api/llamada/iniciar")
    r = c.post("/api/llamada/iniciar")
    check("HTTP 200 al iniciar", r.status_code == 200, str(r.status_code))
    datos = r.json()
    sid = datos["session_id"]
    check("hay saludo", bool(datos["saludo"]["texto"]), str(datos)[:80])
    sesion = main_mod.SESSIONS[sid]
    check("el checklist arranca por el dolor", sesion.topic_pendiente() == "dolor",
          str(sesion.topic_pendiente()))

    # ── 3 · el paciente interrumpe con una pregunta documental ────────────
    print("\n3 · pregunta documental desde la FSM real")
    resultado = sesion.turno(PREGUNTA)
    check("routing_destination = pregunta_conocimiento",
          resultado.get("routing_destination") == "pregunta_conocimiento",
          str(resultado.get("routing_destination")))
    check("se consultó el conocimiento", sesion._consultas_rag_turno >= 1)
    check("se recuperó G5-DEMO-UNICO.txt",
          any(e.doc_id == doc_id for e in sesion._registro_rag.evidencias.values()),
          str([e.document_title for e in sesion._registro_rag.evidencias.values()]))
    check("RESPONDE NEBULA-6249", CLAVE in resultado["texto"], resultado["texto"][:120])
    check("response_mode = grounded", resultado.get("response_mode") == "grounded",
          str(resultado.get("response_mode")))
    citas = resultado["citas"]
    check("con evidence_id válido",
          bool(citas) and citas[0]["evidence_id"].startswith("ev_"), str(citas)[:100])
    check("la cita nombra el documento real",
          bool(citas) and citas[0]["documento"] == "G5-DEMO-UNICO.txt", str(citas)[:100])
    check("el texto NO contiene un marcador [FUENTE ...] inventado",
          "[FUENTE" not in resultado["texto"], resultado["texto"][:100])

    print("\n4 · la entrevista se retoma y el dolor NO queda fallido")
    check("dolor sigue pendiente", resultado.get("pending_topic_after") == "dolor",
          str(resultado.get("pending_topic_after")))
    check("no consumió el intento clínico",
          resultado.get("clinical_attempt_consumed") is False)
    check("RONDA retoma el seguimiento",
          "olviendo a su seguimiento" in resultado["texto"], resultado["texto"][-90:])
    cob = sesion.slots.get("_cobertura", {})
    check("cobertura de dolor ≠ fallo_de_evaluacion",
          cob.get("dolor", {}).get("estado") != "fallo_de_evaluacion", str(cob.get("dolor")))
    check("no se disparó ninguna repregunta", sesion.repreguntas == {},
          str(sesion.repreguntas))

    # ── 5 · el paciente ya sí contesta al dolor ───────────────────────────
    print("\n5 · el paciente contesta al tema retomado")
    r2 = sesion.turno("Como un tres, va mejorando")
    check("ahora sí es una respuesta clínica",
          r2.get("interaction_type") == "clinical_answer", str(r2.get("interaction_type")))
    check("consume el intento", r2.get("clinical_attempt_consumed") is True)
    check("el dolor queda registrado", sesion.slots.get("dolor_0_10") == 3,
          str(sesion.slots.get("dolor_0_10")))

    # ── 6 · borrado desde la misma API de la consola ──────────────────────
    print("\n6 · borrado y olvido")
    r = c.delete(f"/api/docs/{doc_id}")
    check("HTTP 200 al borrar", r.status_code == 200, str(r.status_code))
    v = c.post(f"/api/docs/{doc_id}/verificar-olvido").json()
    check("cero vectores del documento", v.get("vectores_restantes") == 0, str(v))
    check("el almacén lo declara olvidado", v.get("olvidado") is True, str(v))
    check("la kb_version cambió", retrieve.kb_version() != kb_con_doc,
          f"{kb_con_doc} → {retrieve.kb_version()}")

    # ── 7 · misma pregunta, misma sesión ──────────────────────────────────
    print("\n7 · misma pregunta tras el borrado")
    r3 = sesion.turno(PREGUNTA)
    check("YA NO responde la clave", CLAVE not in r3["texto"], r3["texto"][:110])
    check("sin citas", r3["citas"] == [], str(r3["citas"])[:80])
    check("queda registrada como pregunta sin respuesta",
          any(PREGUNTA in q for q in sesion.preguntas_sin_respuesta),
          str(sesion.preguntas_sin_respuesta))
    check("y sigue retomando el seguimiento",
          "olviendo a su seguimiento" in r3["texto"] or sesion.topic_pendiente() is not None,
          r3["texto"][-90:])

    # ── 8 · el acta refleja lo ocurrido ───────────────────────────────────
    print("\n8 · acta")
    acta = c.post(f"/api/llamada/{sid}/finalizar").json()
    check("el acta registra la pregunta sin respuesta",
          bool(acta.get("preguntas_sin_respuesta_en_corpus")),
          str(acta.get("preguntas_sin_respuesta_en_corpus")))
    check("y conserva la referencia usada mientras el documento existía",
          bool(acta.get("referencias_usadas")),
          str(acta.get("referencias_usadas"))[:90])

print(f"\n  vectores finales en el índice: {store.collection_count()}")
total = 27
print(f"\nRESULTADO: {total - fallos}/{total}")
raise SystemExit(1 if fallos else 0)

