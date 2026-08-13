# -*- coding: utf-8 -*-
"""UNKNOWN no es NEGATIVO, y VERDE exige evidencia para serlo.

DOS PROPIEDADES QUE SE COMPRUEBAN AQUÍ
--------------------------------------
1. Distinguir los tres estados. "No he tenido fiebre" es una evaluación
   negativa; que nunca se preguntara por la fiebre, o que la respuesta se
   perdiera, es desconocimiento. Confundirlos convierte la falta de
   información en tranquilidad clínica.

2. La compuerta de verde. Sin los dominios críticos cubiertos, la llamada no
   se cierra en verde: pasa a amarillo POR EVALUACIÓN INCOMPLETA, que se
   reporta como una categoría distinta de "amarillo por riesgo".

   La compuerta solo sube verde→amarillo. Si la evidencia dice rojo, manda la
   evidencia; y una llamada bien cubierta y sin hallazgos sigue siendo verde
   —si no, el sistema alarmaría siempre y no serviría para nada.

Ningún caso se copió del dataset: son las formas en que una llamada
telefónica real pierde información.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.decision import cobertura, composicion, engine, rules  # noqa: E402

fallos = 0


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    global fallos
    marca = "PASS" if condicion else "FAIL"
    fallos += not condicion
    print(f"  [{marca}] {nombre}" + (f"   {detalle}" if detalle and not condicion else ""))


def _observar(texto: str, pregunta: str = "", hablante: str = "paciente") -> dict:
    valores = rules.extraer_valores(texto)
    señales = composicion.señales_de_turno(
        texto, turno=0, hablante=hablante, slots_numericos=valores, pregunta_previa=pregunta)
    return cobertura.observar_turno(texto, pregunta_previa=pregunta, hablante=hablante,
                                    señales=señales, valores=valores)


def _estado(texto, pregunta, dominio, hablante="paciente"):
    return _observar(texto, pregunta, hablante).get(dominio, {}).get(
        "estado", cobertura.DESCONOCIDO)


# ── 1 · los tres estados ────────────────────────────────────────────────────
print("\n1 · PRESENTE / AUSENTE / DESCONOCIDO")
check("negación explícita → AUSENTE",
      _estado("No he tenido fiebre, doctora", "¿ha tenido fiebre?", "temperatura")
      == cobertura.AUSENTE)
check("hallazgo → PRESENTE",
      _estado("le sale pus a la herida", "¿cómo se ve la herida?", "herida")
      == cobertura.PRESENTE)
check("cifra normal → AUSENTE (se midió, salió bien)",
      _estado("me la tomé y estaba en 36.5", "¿se tomó la temperatura?", "temperatura")
      == cobertura.AUSENTE)
check("cifra alta → PRESENTE",
      _estado("me la tomé y estaba en 38.5", "¿se tomó la temperatura?", "temperatura")
      == cobertura.PRESENTE)
check("nunca se preguntó → DESCONOCIDO",
      _observar("buenos días, todo bien").get("herida") is None)
check("negativa abierta con vocabulario del agente → AUSENTE",
      _estado("No, para nada, a lo mucho me he sentido tibio, nada de escalofríos",
              "¿ha sentido escalofríos o se ha tomado la temperatura?", "temperatura")
      == cobertura.AUSENTE)
check("un hallazgo gana a la apertura negativa",
      _estado("No, doctor, pero se me abrió la herida",
              "¿cómo se ve la herida?", "herida") == cobertura.PRESENTE)
check("«no le he puesto atención a eso» NO es una evaluación negativa",
      _estado("Ay, pues no le he puesto mucha atención a eso, uno se distrae",
              "¿se ha tomado la temperatura?", "temperatura") == cobertura.FALLO)

print("\n2 · pérdida de información → FALLO, nunca AUSENTE")
for texto, etiqueta in (("...", "silencio"),
                        ("[inaudible]", "audio perdido"),
                        ("¿mande? no le escuché", "no oyó la pregunta"),
                        ("Este... no, nada, siga con la otra pregunta.", "esquiva")):
    check(f"{etiqueta} → FALLO",
          _estado(texto, "¿ha tenido fiebre o calentura?", "temperatura")
          == cobertura.FALLO, f"obtuvo {_estado(texto, '¿ha tenido fiebre?', 'temperatura')}")

# Una PREGUNTA LATERAL no es un intento fallido: el paciente no intentó
# responder, interrumpió. El dominio queda intacto y se retomará. Marcarlo
# como fallo disparaba una repregunta que secuestraba el turno siguiente.
check("pregunta lateral → el dominio queda DESCONOCIDO, no fallido",
      _estado("¿usted qué comió hoy?", "¿ha tenido fiebre o calentura?", "temperatura")
      == cobertura.DESCONOCIDO,
      _estado("¿usted qué comió hoy?", "¿ha tenido fiebre?", "temperatura"))

# ── 3 · el tercero sí evalúa (§M) ───────────────────────────────────────────
print("\n3 · terceros")
o = _observar("Soy la hija. La herida se ve bien, sin enrojecimiento.",
              "¿cómo se ve la herida?", hablante="tercero")
check("un familiar que responde clínicamente cubre el dominio",
      o.get("herida", {}).get("estado") == cobertura.AUSENTE)
check("y queda registrado como tercero",
      o.get("herida", {}).get("fuente_hablante") == "tercero")

# ── 4 · la compuerta de verde ───────────────────────────────────────────────
print("\n4 · compuerta de verde")


def _cobertura_de(pares) -> dict:
    cob = cobertura.CoberturaEvaluacion()
    for texto, pregunta in pares:
        cob.actualizar(_observar(texto, pregunta))
    return cob.estado


completa = _cobertura_de([
    ("No he tenido fiebre", "¿ha tenido fiebre?"),
    ("No, ningún dolor", "¿tiene dolor?"),
    ("La herida se ve bien, sin enrojecimiento", "¿cómo se ve la herida?"),
])
incompleta = _cobertura_de([
    ("No he tenido fiebre", "¿ha tenido fiebre?"),
    ("...", "¿cómo se ve la herida?"),
])

c1 = engine.cerrar_llamada("verde", completa)
check("cobertura completa + sin hallazgos → VERDE",
      c1["riesgo_clinico"] == "verde", str(c1["riesgo_clinico"]))
check("evaluación completa", c1["estado_evaluacion"] == "completa")
check("acción: continuar", c1["accion_operativa"] == "continuar")

c2 = engine.cerrar_llamada("verde", incompleta)
check("cobertura incompleta NO falsea el riesgo: sigue VERDE",
      c2["riesgo_clinico"] == "verde", str(c2["riesgo_clinico"]))
check("pero el estado de evaluación lo refleja",
      c2["estado_evaluacion"] in ("incompleta", "fallida"), c2["estado_evaluacion"])
check("y la acción NO es seguimiento normal",
      c2["accion_operativa"] != "continuar", c2["accion_operativa"])
check("explica qué quedó sin saber",
      "herida" in c2["razon_de_incertidumbre"], c2["razon_de_incertidumbre"])

c3 = engine.cerrar_llamada("rojo", incompleta)
check("un ROJO con evaluación incompleta sigue siendo ROJO",
      c3["riesgo_clinico"] == "rojo")
check("y su acción es escalar, con cobertura o sin ella",
      c3["accion_operativa"] == "escalar")
c4 = engine.cerrar_llamada("amarillo", incompleta)
check("un amarillo por riesgo va a revisión humana",
      c4["riesgo_clinico"] == "amarillo" and c4["accion_operativa"] == "revision_humana")

# ── 5 · lo desconocido no suma severidad (§J) ───────────────────────────────
print("\n5 · lo desconocido no inventa gravedad")
d = engine.decide("no le entendí, ¿qué dijo?", None, "ctx", {}, turno=1,
                  pregunta_previa="¿ha tenido fiebre?")
check("un turno perdido no genera señal clínica",
      d["evidencia_clinica"] == {}, str(d["evidencia_clinica"]))
check("ni eleva el nivel del turno", d["nivel_final"] == "verde", d["nivel_final"])
check("pero sí queda como dominio a repreguntar",
      "temperatura" in d["repreguntar"], str(d["repreguntar"]))
check("y la evidencia y la cobertura viajan separadas",
      "evidencia_clinica" in d and "cobertura_evaluacion" in d)

print(f"\nRESULTADO: {27 - fallos}/27")
raise SystemExit(1 if fallos else 0)

