"""Resumen estructurado de cada llamada (Acta de Llamada).

Cubre exactamente lo que la rúbrica pide al terminar la llamada: identidad del
paciente y su procedimiento, síntomas reportados, decisión tomada, referencias
usadas y próximos pasos. Se persiste como JSON en data/actas/.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .. import config


def crear_resumen(session) -> dict:
    p = session.paciente
    citas_unicas = _dedup_citas(session.citas_llamada)
    cierre = getattr(session, "cierre", None) or session.cierre_clinico()
    cob = cierre.get("cobertura_evaluacion", {}) or {}
    resumen = {
        "tipo": "acta_llamada",
        "session_id": session.session_id,
        "inicio": session.inicio,
        "fin": datetime.now(timezone.utc).isoformat(),
        "paciente": {
            "paciente_id": p.get("paciente_id"),
            "nombre": p.get("nombre"),
            "procedimiento": p.get("procedimiento_nombre", p.get("procedimiento")),
            "dia_postoperatorio": p.get("dia_postoperatorio"),
        },
        "criticidad_final": session.nivel_max,
        "sintomas_reportados": session.slots.get("sintomas_mencionados", []),
        "hallazgos": {
            "dolor_0_10": session.slots.get("dolor_0_10"),
            "dolor_tendencia": session.slots.get("dolor_tendencia"),
            "temperatura_c": session.slots.get("temperatura_c"),
            "fiebre_reportada": session.slots.get("fiebre_reportada"),
            "estado_herida": (
                "signos de alarma"
                if session.slots.get("herida_pus_o_abierta")
                else "sin alarmas reportadas"
            ),
        },
        "decision": {
            "escalado": session.alerta is not None,
            "alerta_id": (session.alerta or {}).get("alerta_id"),
            "trazabilidad_por_turno": session.decisiones,
            # Distingue "amarillo porque hay riesgo" de "amarillo porque no
            # conseguimos completar una evaluación segura". Quien recibe la
            # alerta necesita saber cuál de las dos es.
            "razon_de_incertidumbre": cierre.get("razon_de_incertidumbre", ""),
        },
        # ── Los tres ejes, separados ────────────────────────────────────────
        # `criticidad_final` (arriba) y `criticidad_clinica` son lo mismo y
        # significan RIESGO CLÍNICO. La calidad de la evaluación nunca los
        # modifica: va en `evaluacion`, y la consecuencia práctica en
        # `accion_operativa`. Así el acta distingue "amarillo porque hay
        # riesgo" de "verde que no pudimos terminar de comprobar".
        "criticidad_clinica": cierre.get("riesgo_clinico", session.nivel_max),
        "evaluacion": {
            "estado": cierre.get("estado_evaluacion", "completa"),
            "dominios_evaluados": (cob.get("evaluado_positivo", [])
                                   + cob.get("evaluado_negativo", [])),
            "desconocidos": cob.get("desconocido", []),
            "fallidos": cob.get("fallo_de_evaluacion", []),
            "motivo": cierre.get("razon_de_incertidumbre", ""),
        },
        "accion_operativa": cierre.get("accion_operativa", "continuar"),
        # ── Qué se logró evaluar y qué quedó sin saber ──────────────────────
        # `razon_de_cobertura` NO es una medida clínica: es cuánto de la
        # entrevista se completó sobre los dominios críticos.
        "cobertura": {
            "evaluado_positivo": cob.get("evaluado_positivo", []),
            "evaluado_negativo": cob.get("evaluado_negativo", []),
            "desconocido": cob.get("desconocido", []),
            "fallo_de_evaluacion": cob.get("fallo_de_evaluacion", []),
            "dominios_criticos": cob.get("dominios_criticos", []),
            "criticos_sin_cubrir": cob.get("criticos_sin_cubrir", []),
            "razon_de_cobertura": cob.get("razon_de_cobertura"),
            "detalle": cob.get("detalle", {}),
        },
        "evaluacion_completa": cierre.get("evaluacion_completa", True),
        "motivo_evaluacion_incompleta": cierre.get("razon_de_incertidumbre", ""),
        "repreguntas_realizadas": dict(getattr(session, "repreguntas", {}) or {}),
        # Cada afirmación clínica del transcript es rastreable hasta aquí:
        # evidence_id → doc_id + chunk_id + sha256 + versión del conocimiento.
        # Se guarda la REFERENCIA y un extracto acotado, no el documento.
        "referencias_usadas": citas_unicas,
        "kb_version": getattr(session, "_registro_rag", None)
        and session._registro_rag.kb_version,
        "preguntas_sin_respuesta_en_corpus": session.preguntas_sin_respuesta,
        "checklist_sin_cubrir": session.checklist_pendiente,
        "proximos_pasos": _proximos_pasos(session),
        "transcript": session.transcript,
    }
    path = config.ACTAS_DIR / f"acta_{session.session_id}.json"
    path.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
    return resumen


def _proximos_pasos(session) -> list[str]:
    pasos = []
    if session.nivel_max == "rojo":
        pasos.append("Enfermería contacta al paciente en los próximos 30 minutos (alerta activa).")
    elif session.nivel_max == "amarillo":
        pasos.append("Revisión del caso por enfermería en el siguiente corte de turno.")
    else:
        pasos.append("Continuar seguimiento programado según protocolo del procedimiento.")
    if session.preguntas_sin_respuesta:
        pasos.append(
            "Responder al paciente las preguntas que quedaron fuera del corpus "
            f"({len(session.preguntas_sin_respuesta)})."
        )
    if session.checklist_pendiente:
        pasos.append(
            "Completar en la próxima llamada los temas no cubiertos: "
            + ", ".join(session.checklist_pendiente) + "."
        )
    cierre = getattr(session, "cierre", None) or {}
    if cierre.get("estado_evaluacion") in ("incompleta", "fallida"):
        faltan = (cierre.get("cobertura_evaluacion") or {}).get("criticos_sin_cubrir") or []
        riesgo = cierre.get("riesgo_clinico", "verde")
        # El matiz cambia con el riesgo: en verde lo relevante es que la calma
        # no está comprobada; en amarillo o rojo, que además falta información
        # para dimensionar lo que sí se encontró.
        contexto = ("No se detectaron señales de alarma, pero la entrevista quedó a "
                    "medias y esa parte no se comprobó."
                    if riesgo == "verde" else
                    f"El riesgo clínico registrado es {riesgo.upper()} por lo que sí se "
                    "alcanzó a valorar; lo que falta puede cambiar su dimensión.")
        pasos.insert(0, "Completar la evaluación: no se logró valorar "
                        + ", ".join(faltan) + ". " + contexto)
    return pasos


def _dedup_citas(citas: list[dict]) -> list[dict]:
    # La clave es el evidence_id, que ya incluye documento, fragmento y
    # versión del conocimiento. Con (doc_id, chunk) dos citas del mismo
    # fragmento bajo versiones distintas se confundían en una sola.
    seen, out = set(), []
    for c in citas:
        key = c.get("evidence_id") or (c.get("doc_id"), c.get("chunk"))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def listar_actas() -> list[dict]:
    actas = []
    for p in sorted(config.ACTAS_DIR.glob("acta_*.json"), reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            actas.append(
                {
                    "session_id": data["session_id"],
                    "fin": data.get("fin"),
                    "paciente": data["paciente"]["nombre"],
                    "criticidad_final": data["criticidad_final"],
                    "escalado": data["decision"]["escalado"],
                }
            )
        except Exception:
            continue
    return actas


def leer_acta(session_id: str) -> dict | None:
    path = config.ACTAS_DIR / f"acta_{session_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None
