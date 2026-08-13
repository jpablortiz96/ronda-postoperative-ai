# -*- coding: utf-8 -*-
"""Riesgo clínico y estado de la evaluación son ejes independientes.

EL ERROR QUE ESTA SUITE IMPIDE
------------------------------
Hasta FASE 4.7 el motor mezclaba dos cosas: lo que el paciente reportó y lo
que la llamada logró preguntar. La consecuencia medida fue que 57
conversaciones clínicamente verdes salían etiquetadas de AMARILLO solo porque
la entrevista había quedado a medias. Eso es un diagnóstico falso: le dice a
enfermería que hay un riesgo donde lo que hay es una llamada incompleta.

La separación correcta:

    riesgo clínico      ← SOLO evidencia observada
    estado evaluación   ← SOLO lo que se logró (o no) preguntar
    acción operativa    ← única capa donde se combinan

Y una asimetría que no puede romperse: el ROJO domina la acción siempre, con
cobertura completa o sin ella. Nunca al revés.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.decision import assess, cobertura, composicion, engine, rules  # noqa: E402

# Motor determinista: sin red. Los bloques que SÍ prueban el carril LLM
# (sección 6) sustituyen el extractor por uno controlado, que es la única
# forma de comprobar el contrato de evidencia sin depender de lo que el
# proveedor decida contestar hoy.
_SIN_LLM = lambda t, c, h=None: (  # noqa: E731
    assess._merge_slots(h or {}, assess._empty_slots()),
    {"provider": "none", "input_tokens": 0, "output_tokens": 0,
     "llm_disponible": False, "modo_degradado": True, "motivo": "desactivado_en_pruebas"})
assess.extract_slots = _SIN_LLM

fallos = 0


def check(nombre: str, cond: bool, detalle: str = "") -> None:
    global fallos
    fallos += not cond
    print(f"  [{'PASS' if cond else 'FAIL'}] {nombre}" + (f"   → {detalle}" if not cond else ""))


def _cobertura(pares) -> dict:
    cob = cobertura.CoberturaEvaluacion()
    for texto, pregunta in pares:
        valores = rules.extraer_valores(texto)
        señales = composicion.señales_de_turno(
            texto, turno=0, slots_numericos=valores, pregunta_previa=pregunta)
        cob.actualizar(cobertura.observar_turno(
            texto, pregunta_previa=pregunta, señales=señales, valores=valores))
    return cob.estado


COMPLETA = _cobertura([
    ("No he tenido fiebre", "¿ha tenido fiebre?"),
    ("No, ningún dolor", "¿tiene dolor?"),
    ("La herida se ve bien, sin enrojecimiento", "¿cómo se ve la herida?"),
])
NO_PREGUNTADA = _cobertura([("No he tenido fiebre", "¿ha tenido fiebre?")])
PERDIDA = _cobertura([
    ("No he tenido fiebre", "¿ha tenido fiebre?"),
    ("No, ningún dolor", "¿tiene dolor?"),
    ("[inaudible]", "¿cómo se ve la herida?"),
])

print("\n1 · lo desconocido no toca el riesgo clínico")
for nombre, cob in (("no se alcanzó a preguntar", NO_PREGUNTADA),
                    ("se preguntó y se perdió", PERDIDA)):
    c = engine.cerrar_llamada("verde", cob)
    check(f"{nombre} → riesgo sigue VERDE", c["riesgo_clinico"] == "verde",
          c["riesgo_clinico"])
    check(f"{nombre} → la acción NO es seguimiento normal",
          c["accion_operativa"] != "continuar", c["accion_operativa"])

print("\n2 · desconocido y fallo se distinguen")
check("no preguntado → incompleta",
      engine.cerrar_llamada("verde", NO_PREGUNTADA)["estado_evaluacion"] == "incompleta",
      engine.cerrar_llamada("verde", NO_PREGUNTADA)["estado_evaluacion"])
check("preguntado y perdido → fallida",
      engine.cerrar_llamada("verde", PERDIDA)["estado_evaluacion"] == "fallida",
      engine.cerrar_llamada("verde", PERDIDA)["estado_evaluacion"])
check("no preguntado → repreguntar",
      engine.cerrar_llamada("verde", NO_PREGUNTADA)["accion_operativa"] == "repreguntar")
check("perdido tras preguntar → revisión humana",
      engine.cerrar_llamada("verde", PERDIDA)["accion_operativa"] == "revision_humana")

print("\n3 · el rojo domina la acción, con cobertura o sin ella")
for nombre, cob in (("completa", COMPLETA), ("incompleta", NO_PREGUNTADA),
                    ("fallida", PERDIDA)):
    c = engine.cerrar_llamada("rojo", cob)
    check(f"rojo + evaluación {nombre} → riesgo rojo y acción escalar",
          c["riesgo_clinico"] == "rojo" and c["accion_operativa"] == "escalar",
          f"{c['riesgo_clinico']}/{c['accion_operativa']}")

print("\n4 · el caso completo y tranquilo sigue siendo verde")
c = engine.cerrar_llamada("verde", COMPLETA)
check("riesgo verde", c["riesgo_clinico"] == "verde")
check("evaluación completa", c["estado_evaluacion"] == "completa")
check("acción continuar", c["accion_operativa"] == "continuar")

print("\n5 · la repregunta puede convertir incompleta → completa")
cob = cobertura.CoberturaEvaluacion()
cob.estado = dict(PERDIDA)
antes = engine.cerrar_llamada("verde", cob.estado)["estado_evaluacion"]
# El paciente responde a la repregunta cerrada sobre la herida.
respuesta = "No, ninguna, la herida está limpia y sin secreción"
pregunta = "¿Le ha visto enrojecimiento, líquido o mal olor: sí o no?"
valores = rules.extraer_valores(respuesta)
señales = composicion.señales_de_turno(respuesta, turno=9, slots_numericos=valores,
                                       pregunta_previa=pregunta)
cob.actualizar(cobertura.observar_turno(respuesta, pregunta_previa=pregunta,
                                        señales=señales, valores=valores))
despues = engine.cerrar_llamada("verde", cob.estado)
check(f"antes {antes} → después completa",
      despues["estado_evaluacion"] == "completa", despues["estado_evaluacion"])
check("y la acción vuelve a seguimiento normal",
      despues["accion_operativa"] == "continuar", despues["accion_operativa"])

print("\n6 · el LLM no puede escalar sin evidencia estructurada")


def _riesgo(slots_llm: dict, texto: str = "buenos días") -> dict:
    assess.extract_slots = lambda t, c, h=None: (
        assess._merge_slots(h or {}, {**assess._empty_slots(), **slots_llm}),
        {"provider": "prueba", "input_tokens": 0, "output_tokens": 0})
    try:
        return engine.decide(texto, None, "ctx", {}, turno=1)
    finally:
        assess.extract_slots = _SIN_LLM


d = _riesgo({"nivel_sugerido": "rojo"})
check("«rojo» sin evidencia se recorta a verde",
      d["niveles"]["carril_llm"] == "verde", d["niveles"]["carril_llm"])
check("y queda anotado por qué se recortó",
      "recortado" in d["contrato_llm"], d["contrato_llm"])

d = _riesgo({"nivel_sugerido": "rojo", "dificultad_respiratoria": True})
check("«rojo» CON bandera estructurada sí se admite",
      d["niveles"]["carril_llm"] == "rojo", d["niveles"]["carril_llm"])
check("y declara la evidencia que lo sostiene",
      "dificultad_respiratoria" in d["contrato_llm"], d["contrato_llm"])

d = _riesgo({"nivel_sugerido": "rojo"}, "me falta el aire")
check("«rojo» respaldado por el carril determinista se admite",
      d["niveles"]["carril_llm"] == "rojo", d["niveles"]["carril_llm"])

print("\n7 · toda decisión tiene un porqué")
d = engine.decide("se me abrió la herida", "Apendicectomía", "ctx", {}, turno=1)
check("hay contribuyentes", bool(d["contribuyentes"]), str(d["contribuyentes"])[:80])
check("el primero explica el nivel final",
      d["contribuyentes"][0]["nivel"] == d["nivel_final"])

total = 24
print(f"\nRESULTADO: {total - fallos}/{total}")
raise SystemExit(1 if fallos else 0)
