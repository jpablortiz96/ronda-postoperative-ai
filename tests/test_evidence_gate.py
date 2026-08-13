# -*- coding: utf-8 -*-
"""La compuerta de evidencia: ninguna afirmación clínica sin respaldo activo.

QUÉ SE COMPRUEBA AQUÍ
---------------------
Que «cita o silencio» es una propiedad del código y no una política del
prompt. Las pruebas no le piden nada al modelo: le entregan al gate salidas
que un modelo podría producir —incluidas las malintencionadas— y comprueban
qué sale por el otro lado.

El caso central es el 3: un identificador de evidencia INVENTADO. Con la
arquitectura anterior, «[FUENTE: protocolo de apendicectomía]» escrito por el
modelo era indistinguible de una cita real, porque la cita era texto. Aquí la
cita es una referencia a un objeto que creó el código, y un id inventado no
existe en el registro del turno.

Ninguna prueba usa la red.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.conversation import gate  # noqa: E402
from app.rag.evidencia import (Evidence, RegistroDeTurno,  # noqa: E402
                               calcular_kb_version, nuevo_evidence_id)

fallos = 0


def check(nombre: str, cond: bool, detalle: str = "") -> None:
    global fallos
    fallos += not cond
    print(f"  [{'PASS' if cond else 'FAIL'}] {nombre}" + (f"   → {detalle}" if not cond else ""))


# ── Montaje: un corpus de dos documentos ────────────────────────────────────
DOCS = [
    {"doc_id": "doc_apx", "sha256": "a" * 64, "estado": "disponible"},
    {"doc_id": "doc_col", "sha256": "b" * 64, "estado": "disponible"},
]
KB = calcular_kb_version(DOCS)
ACTIVOS = {"doc_apx", "doc_col"}

TEXTO_EV = ("Mantenga la herida limpia y seca. Puede ducharse a partir de las 48 horas, "
            "secando la zona con toques suaves.")
EV = Evidence(
    evidence_id=nuevo_evidence_id(KB, "doc_apx", "doc_apx::3", TEXTO_EV),
    doc_id="doc_apx", chunk_id="doc_apx::3",
    document_title="Plan de cuidado en casa · apendicectomía",
    sha256="a" * 64, text=TEXTO_EV, retrieval_score=0.21, kb_version=KB, chunk_index=3,
)
REGISTRO = RegistroDeTurno(kb_version=KB)
REGISTRO.registrar([EV])


def aplicar(sentences, followup="", registro=REGISTRO, kb=KB, activos=ACTIVOS):
    return gate.aplicar({"sentences": sentences, "followup_question": followup},
                        registro, kb, activos, session_id="test", turno=1)


# ── 1 · respuesta soportada ─────────────────────────────────────────────────
print("\n1 · afirmación clínica CON evidencia válida")
r = aplicar([{"text": "Puede ducharse a partir de las 48 horas.", "clinical": True,
              "evidence_ids": [EV.evidence_id]}])
check("la afirmación pasa", "48 horas" in r["texto"], r["texto"])
check("no se marca abstención", r["abstenida"] is False)
check("la evidencia queda registrada", len(r["evidencias"]) == 1)
check("modo grounded", not r["rechazos"])

# ── 2 · respuesta NO soportada ──────────────────────────────────────────────
print("\n2 · afirmación clínica SIN evidencia")
r = aplicar([{"text": "Es normal que le duela hasta el día diez.", "clinical": True,
              "evidence_ids": []}])
check("la afirmación NO llega al paciente", "día diez" not in r["texto"], r["texto"])
check("se sustituye por abstención", r["abstenida"] is True)
check("motivo registrado", r["rechazos"][0]["motivo"] == gate.RECHAZADA_SIN_EVIDENCIA)

print("\n2b · afirmación clínica DISFRAZADA de no clínica")
r = aplicar([{"text": "Eso es completamente normal y va a mejorar en unos días.",
              "clinical": False, "evidence_ids": []}])
check("el detector la reclasifica y la bloquea", "normal" not in r["texto"], r["texto"])

# ── 3 · cita inventada por el LLM ───────────────────────────────────────────
print("\n3 · identificador de evidencia INVENTADO")
r = aplicar([{"text": "Según el protocolo puede retirar los puntos al día 7.",
              "clinical": True, "evidence_ids": ["ev_000000000000"]}])
check("no pasa", "día 7" not in r["texto"], r["texto"])
check("motivo = evidencia inexistente",
      r["rechazos"][0]["motivo"] == gate.RECHAZADA_ID_INVENTADO, str(r["rechazos"]))

print("\n3b · marcador [FUENTE ...] escrito por el modelo")
sucio = "Mantenga la herida seca [FUENTE: doc_apx, chunk 3] y no la moje."
check("el marcador se elimina del texto",
      "[FUENTE" not in gate.limpiar_marcadores(sucio), gate.limpiar_marcadores(sucio))

print("\n3c · evidencia de OTRO turno")
otro = RegistroDeTurno(kb_version=KB)   # registro vacío: otro turno
r = aplicar([{"text": "Puede ducharse a las 48 horas.", "clinical": True,
              "evidence_ids": [EV.evidence_id]}], registro=otro)
check("una cita válida de otro turno no sostiene esta frase",
      "48 horas" not in r["texto"], r["texto"])

# ── 4 · medicación ──────────────────────────────────────────────────────────
print("\n4 · medicación (barrera estructural, no lista de fármacos)")
# Cada frase va con una palabra TESTIGO que solo aparece si la frase pasó.
# (No vale mirar palabras comunes: el texto de abstención contiene "el", "de"…)
for frase, testigo in (("Tome 500 mg de acetaminofén cada 8 horas.", "acetaminofén"),
                       ("Puede tomar dos pastillas de zolpiflaxina cada 12 horas.",
                        "zolpiflaxina"),
                       ("Suspenda el antibiótico que le formularon.", "antibiótico"),
                       ("Su médico le autorizó ese medicamento.", "autorizó"),
                       ("Aumente la dosis a 3 tabletas.", "tabletas")):
    r = aplicar([{"text": frase, "clinical": False, "evidence_ids": []}])
    check(f"bloqueada: «{frase[:44]}»", testigo not in r["texto"], r["texto"])
check("un fármaco INVENTADO también cae (no hay lista finita)",
      gate.menciona_medicacion("Puede tomar dos pastillas de zolpiflaxina cada 12 horas."))

# ── 5 · versión de conocimiento ─────────────────────────────────────────────
print("\n5 · versión del conocimiento")
kb_menos = calcular_kb_version([DOCS[0]])
check("quitar un documento cambia la kb_version", kb_menos != KB)
check("añadir uno nuevo también",
      calcular_kb_version(DOCS + [{"doc_id": "doc_x", "sha256": "c" * 64,
                                   "estado": "disponible"}]) != KB)
check("el orden de los documentos no altera la versión",
      calcular_kb_version(list(reversed(DOCS))) == KB)
check("corpus vacío tiene versión propia", calcular_kb_version([]) == "kb_vacia")

print("\n5b · evidencia emitida bajo una versión anterior")
r = aplicar([{"text": "Puede ducharse a las 48 horas.", "clinical": True,
              "evidence_ids": [EV.evidence_id]}], kb=kb_menos)
check("no pasa tras un cambio de conocimiento",
      "48 horas" not in r["texto"], r["texto"])
check("motivo = versión obsoleta",
      r["rechazos"][0]["motivo"] == gate.RECHAZADA_KB_OBSOLETA, str(r["rechazos"]))

print("\n5c · documento eliminado DURANTE la sesión")
r = aplicar([{"text": "Puede ducharse a las 48 horas.", "clinical": True,
              "evidence_ids": [EV.evidence_id]}], activos={"doc_col"})
check("una evidencia de un documento borrado deja de valer",
      "48 horas" not in r["texto"], r["texto"])

# ── 6 · lo operativo sí pasa ────────────────────────────────────────────────
print("\n6 · mensajes operativos (excepción acotada del §D)")
r = aplicar([{"text": "Voy a pasar su caso al equipo de enfermería.",
              "clinical": False, "evidence_ids": []}],
            followup="¿Hay alguien con usted en este momento?")
check("el mensaje operativo pasa", "enfermería" in r["texto"], r["texto"])
check("la pregunta operativa pasa", "alguien con usted" in r["texto"])
check("y no arrastra evidencia", r["evidencias"] == [])

print("\n6b · la pregunta de seguimiento también se valida")
r = aplicar([{"text": "Entendido.", "clinical": False, "evidence_ids": []}],
            followup="¿Le sigue doliendo, aunque es normal al tercer día?")
check("una pregunta con afirmación clínica encubierta se recorta",
      "normal al tercer día" not in r["texto"], r["texto"])

# ── 7 · mezcla: se conserva lo bueno, se recorta lo malo ────────────────────
print("\n7 · respuesta mixta")
r = aplicar([
    {"text": "Gracias por contarme.", "clinical": False, "evidence_ids": []},
    {"text": "Puede ducharse a partir de las 48 horas.", "clinical": True,
     "evidence_ids": [EV.evidence_id]},
    {"text": "El dolor desaparece siempre al quinto día.", "clinical": True,
     "evidence_ids": []},
])
check("sobrevive la frase respaldada", "48 horas" in r["texto"], r["texto"])
check("cae la inventada", "quinto día" not in r["texto"], r["texto"])
check("y la operativa se conserva", "Gracias" in r["texto"])
check("no se declara abstención total", r["abstenida"] is False)

# ── 8 · las citas las renderiza el código ───────────────────────────────────
print("\n8 · renderizado de citas (§G)")
citas = gate.render_citas([EV])
check("la cita sale del objeto, no del texto", citas[0]["evidence_id"] == EV.evidence_id)
for campo in ("doc_id", "chunk_id", "sha256", "documento", "kb_version"):
    check(f"la cita expone {campo}", campo in citas[0])
check("el extracto es acotado, no el documento entero",
      len(citas[0]["extracto"]) <= 180)

# ── 9 · el id de evidencia no es adivinable ─────────────────────────────────
print("\n9 · el identificador lo genera el código")
otro_id = nuevo_evidence_id(kb_menos, "doc_apx", "doc_apx::3", TEXTO_EV)
check("mismo fragmento + otra kb_version = otro id", otro_id != EV.evidence_id)
check("mismo fragmento + misma versión = id estable",
      nuevo_evidence_id(KB, "doc_apx", "doc_apx::3", TEXTO_EV) == EV.evidence_id)

total = 40
print(f"\nRESULTADO: {total - fallos}/{total}")
raise SystemExit(1 if fallos else 0)
