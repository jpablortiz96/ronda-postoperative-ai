# -*- coding: utf-8 -*-
"""Generación estructurada: el modelo propone oraciones, no párrafos.

POR QUÉ NO UN PÁRRAFO LIBRE
---------------------------
Si el modelo devuelve prosa, la única forma de saber qué parte está
respaldada es adivinarlo a posteriori. Con un párrafo mixto —una frase
operativa, una afirmación clínica correcta y una inventada— hay que elegir
entre publicar la invención o borrar el turno entero.

Pidiendo la respuesta descompuesta en oraciones, cada una con su marca de
`clinical` y sus `evidence_ids`, la compuerta puede aceptar unas y recortar
otras. El paciente recibe lo respaldado y pierde solo lo que no lo estaba.

QUÉ PASA SI EL MODELO NO COOPERA
--------------------------------
Nada especial, y ese es el punto. Si devuelve JSON inválido, si marca todo
como no clínico, si inventa identificadores o si el proveedor se cae, el
resultado es el mismo: la compuerta recorta y, si no queda nada respaldado,
el agente se abstiene. El modelo no tiene ninguna vía para autorizarse.
"""
from __future__ import annotations

import json

from .. import llm, observability
from ..rag.evidencia import RegistroDeTurno
from . import gate

CONTRATO = """Devuelve SOLO un JSON con esta forma exacta:

{
  "sentences": [
    {"text": "<una sola oración>", "clinical": true|false,
     "evidence_ids": ["ev_..."]}
  ],
  "followup_question": "<una sola pregunta, o cadena vacía>"
}

REGLAS DEL CONTRATO
- Una oración por elemento. Máximo 3 oraciones más la pregunta.
- `clinical: true` para TODA oración que interprete un síntoma, diga si algo
  es normal o esperable, recomiende un cuidado, hable de evolución, de
  protocolo o de medicación.
- `clinical: true` OBLIGA a poner al menos un `evidence_id` de la evidencia
  recuperada en ESTE turno. No inventes identificadores: si el hecho no está
  en la evidencia, no escribas la oración.
- `clinical: false` solo para lo puramente conversacional u operativo
  (saludar, acusar recibo, avisar de que se pasa el caso, pedir que repita).
  Una oración no clínica NO puede llevar evidence_ids.
- NUNCA escribas «[FUENTE ...]» ni cites en el texto: las referencias las
  añade el sistema.
- Si no tienes evidencia para lo que el paciente pregunta, devuelve una
  oración no clínica diciendo que no lo tienes respaldado, y sigue con la
  pregunta del checklist."""


def _respuesta_vacia(motivo: str) -> dict:
    return {"sentences": [], "followup_question": "", "motivo_fallo": motivo}


def generar(messages: list[dict], registro: RegistroDeTurno,
            kb_version: str, doc_activos: set[str],
            session_id: str = "", turno: int = 0) -> dict:
    """Pide la respuesta estructurada y la pasa por la compuerta.

    Devuelve siempre un dict con `texto`, `evidencias`, `rechazos`,
    `abstenida` y `response_mode`, incluso si el proveedor falla.
    """
    modo = "grounded"
    try:
        cruda, usage = llm.chat_json(messages, temperature=0.3, max_tokens=900)
    except Exception as e:
        # DEGRADACIÓN SEGURA: sin modelo no hay afirmación clínica. Un
        # proveedor caído convierte "no sé" en silencio, nunca en invención.
        motivo = getattr(e, "motivo", None) or type(e).__name__
        observability.log_event({
            "tipo": "generacion_degradada", "session_id": session_id,
            "turno": turno, "motivo": motivo,
        })
        return {
            "texto": "", "evidencias": [], "rechazos": [], "abstenida": True,
            "response_mode": "abstained", "abstention_reason": f"llm_no_disponible:{motivo}",
            "usage": {}, "cruda": _respuesta_vacia(motivo),
        }

    if not isinstance(cruda, dict) or "sentences" not in cruda:
        observability.log_event({
            "tipo": "contrato_invalido", "session_id": session_id, "turno": turno,
            "recibido": str(cruda)[:200],
        })
        cruda = _respuesta_vacia("contrato_invalido")

    # El marcador de cita jamás viene del modelo: se borra antes de validar,
    # para que ni siquiera pueda usarse como decoración.
    for s in cruda.get("sentences") or []:
        if isinstance(s, dict):
            s["text"] = gate.limpiar_marcadores(str(s.get("text") or ""))
    cruda["followup_question"] = gate.limpiar_marcadores(
        str(cruda.get("followup_question") or ""))

    resultado = gate.aplicar(cruda, registro, kb_version, doc_activos,
                             session_id=session_id, turno=turno)
    if not resultado["texto"]:
        modo = "abstained"
    elif resultado["abstenida"]:
        modo = "abstained"
    elif not resultado["evidencias"]:
        modo = "operational"

    resultado["response_mode"] = modo
    resultado["abstention_reason"] = (
        ", ".join(sorted({r["motivo"] for r in resultado["rechazos"]}))
        if modo == "abstained" and resultado["rechazos"] else
        ("sin_evidencia_recuperada" if modo == "abstained" else "")
    )
    resultado["usage"] = usage
    resultado["cruda"] = cruda
    return resultado


def json_de_prueba(sentences, followup="") -> str:
    """Ayuda para las suites: serializa un contrato como lo haría el modelo."""
    return json.dumps({"sentences": sentences, "followup_question": followup},
                      ensure_ascii=False)
