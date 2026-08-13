# -*- coding: utf-8 -*-
"""«Evaluado» y «positivo» son dos preguntas distintas.

EL PROBLEMA QUE RESUELVE
------------------------
La cobertura repetía por su cuenta los umbrales clínicos (37,5 °C para la
febrícula, 3/10 para el dolor). Cuando el motor subió su umbral de febrícula a
37,8 esa copia se quedó atrás, y una temperatura de 37,5 producía un acta que
decía «evaluado positivo: temperatura» mientras la traza del motor clínico no
mostraba ninguna señal. El acta contradecía al motor.

La corrección no toca ningún umbral —eso está congelado— sino la
representación: la cobertura ya no juzga, solo registra si el dominio se
evaluó, y deja que el motor clínico sea la única fuente de verdad sobre qué es
un hallazgo.

    assessed  ¿obtuvimos información de este dominio?
    positive  ¿el motor clínico encontró algo en él?
    valor     la cifra medida, cuando la hay

La invariante que se comprueba abajo es la importante: **positive es cierto si
y solo si el motor de composición generó una señal**. Mientras eso se cumpla,
acta y traza no pueden divergir.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.decision import cobertura, composicion, rules  # noqa: E402

fallos = 0


def check(nombre: str, cond: bool, detalle: str = "") -> None:
    global fallos
    fallos += not cond
    print(f"  [{'PASS' if cond else 'FAIL'}] {nombre}" + (f"   → {detalle}" if not cond else ""))


def observar(texto: str, pregunta: str = "") -> tuple[dict, list]:
    valores = rules.extraer_valores(texto, pregunta)
    señales = composicion.señales_de_turno(
        texto, turno=0, slots_numericos=valores, pregunta_previa=pregunta)
    obs = cobertura.observar_turno(texto, pregunta_previa=pregunta,
                                   señales=señales, valores=valores)
    return obs, señales


def ficha(texto, pregunta, dominio):
    obs, _ = observar(texto, pregunta)
    return obs.get(dominio, {})


# ── Temperatura ─────────────────────────────────────────────────────────────
print("\n1 · temperatura")
f = ficha("Me la tomé y marcó 37.5", "¿se ha tomado la temperatura?", "temperatura")
check("37.5 → assessed", f.get("assessed") is True, str(f))
check("37.5 → NO positive", f.get("positive") is False, str(f))
check("37.5 → conserva el valor medido", f.get("valor") == 37.5, str(f))

f = ficha("Me la tomé y marcó 39.5", "¿se ha tomado la temperatura?", "temperatura")
check("39.5 → assessed", f.get("assessed") is True, str(f))
check("39.5 → positive", f.get("positive") is True, str(f))

f = ficha("No he tenido fiebre", "¿ha tenido fiebre?", "temperatura")
check("«no he tenido fiebre» → assessed", f.get("assessed") is True, str(f))
check("«no he tenido fiebre» → NO positive", f.get("positive") is False, str(f))

obs, _ = observar("Buenos días, todo bien por acá")
check("nunca preguntada → no aparece (unknown)", "temperatura" not in obs, str(obs))

f = ficha("[inaudible]", "¿ha tenido fiebre?", "temperatura")
check("STT falla → failed", f.get("estado") == cobertura.FALLO, str(f))
check("failed → NO assessed", f.get("assessed") is False, str(f))
check("failed → NO positive", f.get("positive") is False, str(f))

# ── Herida ──────────────────────────────────────────────────────────────────
print("\n2 · herida")
f = ficha("Se ve normal, sin secreción", "¿cómo se ve la herida?", "herida")
check("«normal, sin secreción» → assessed", f.get("assessed") is True, str(f))
check("«normal, sin secreción» → NO positive", f.get("positive") is False, str(f))

f = ficha("Está saliendo líquido amarillo por la herida", "¿cómo se ve la herida?",
          "herida")
check("«líquido amarillo» → assessed", f.get("assessed") is True, str(f))
check("«líquido amarillo» → positive", f.get("positive") is True, str(f))

# ── Dolor ───────────────────────────────────────────────────────────────────
print("\n3 · dolor")
f = ficha("Como un 2, casi nada", "¿de 0 a 10 cómo va el dolor?", "dolor")
check("dolor 2/10 → assessed", f.get("assessed") is True, str(f))
check("dolor 2/10 → NO positive", f.get("positive") is False, str(f))
check("dolor 2/10 → conserva el valor", f.get("valor") == 2.0, str(f))

f = ficha("Como un 9, insoportable", "¿de 0 a 10 cómo va el dolor?", "dolor")
check("dolor 9/10 → positive", f.get("positive") is True, str(f))

# ── LA INVARIANTE ───────────────────────────────────────────────────────────
print("\n4 · invariante: positive ⟺ el motor clínico generó señal")
CASOS = [
    ("Me la tomé y marcó 37.5", "¿se ha tomado la temperatura?", "temperatura"),
    ("Me la tomé y marcó 37.9", "¿se ha tomado la temperatura?", "temperatura"),
    ("Me la tomé y marcó 38.4", "¿se ha tomado la temperatura?", "temperatura"),
    ("Me la tomé y marcó 36.2", "¿se ha tomado la temperatura?", "temperatura"),
    ("Como un 2", "¿de 0 a 10 el dolor?", "dolor"),
    ("Como un 5", "¿de 0 a 10 el dolor?", "dolor"),
    ("Como un 9", "¿de 0 a 10 el dolor?", "dolor"),
    ("Se ve limpia, sin nada raro", "¿cómo se ve la herida?", "herida"),
    ("Le está saliendo pus", "¿cómo se ve la herida?", "herida"),
    ("No he comido casi nada", "¿cómo ha comido?", "alimentacion"),
]
for texto, pregunta, dominio in CASOS:
    obs, señales = observar(texto, pregunta)
    tiene_señal = any(s["dominio"] == dominio for s in señales)
    positive = obs.get(dominio, {}).get("positive", False)
    check(f"«{texto[:34]:<34}» señal={tiene_señal} positive={positive}",
          tiene_señal == positive, f"divergen para {dominio}")

# ── El resumen expone los dos ejes ─────────────────────────────────────────
print("\n5 · el acta expone ambos ejes")
cob = cobertura.CoberturaEvaluacion()
for texto, pregunta in (("Me la tomé y marcó 37.5", "¿se ha tomado la temperatura?"),
                        ("Como un 2", "¿de 0 a 10 el dolor?"),
                        ("Le está saliendo pus", "¿cómo se ve la herida?")):
    obs, _ = observar(texto, pregunta)
    cob.actualizar(obs)
r = cob.resumen()
pd = r["por_dominio"]
check("temperatura: assessed sin positive",
      pd["temperatura"]["assessed"] and not pd["temperatura"]["positive"], str(pd))
check("temperatura conserva 37.5 en el acta", pd["temperatura"].get("valor") == 37.5)
check("herida: assessed y positive",
      pd["herida"]["assessed"] and pd["herida"]["positive"], str(pd))
check("«evaluado_positivo» solo lista hallazgos reales",
      r["evaluado_positivo"] == ["herida"], str(r["evaluado_positivo"]))
check("la evaluación se considera completa", r["evaluacion_completa"] is True)

total = 30
print(f"\nRESULTADO: {total - fallos}/{total}")
raise SystemExit(1 if fallos else 0)
