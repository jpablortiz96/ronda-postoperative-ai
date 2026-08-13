# -*- coding: utf-8 -*-
"""Alcance de la negación en el motor determinista.

POR QUÉ EXISTE
--------------
La evaluación contra el dataset oficial mostró que el motor registraba como
signos clínicos frases donde el paciente decía exactamente lo contrario:

    "No, la herida se ve bien, sin enrojecimiento ni hinchazón"  → herida
    "No, nada de escalofríos, doctora"                           → escalofríos
    "Sangrado no ha habido"                                      → sangrado

Tres causas distintas: "ni" y "nada" no figuraban entre los negadores, la
negación pospuesta al síntoma no se miraba, y una coma inicial ("No, ...")
cortaba el alcance del negador.

EL RIESGO DE LA CORRECCIÓN ES EL INVERSO
----------------------------------------
Ampliar la negación puede silenciar una alarma real, que es el peor error
posible de este sistema. Por eso la mitad de los casos de abajo son alarmas
que DEBEN seguir disparando, incluidas las que contienen un "no" como parte
del síntoma ("no puedo respirar") y las que mezclan una negación con una
alarma en la cláusula siguiente ("la herida no tiene pus, pero me duele el
pecho"). Si una corrección futura de la negación rompe una de esas, este
archivo falla.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.decision import composicion, rules  # noqa: E402

# (texto, debe_disparar)
CASOS: list[tuple[str, bool]] = [
    # ── Negaciones que deben silenciar ─────────────────────────────────────
    ("No, la herida se ve bien, sin enrojecimiento ni hinchazon, "
     "y no ha salido ninguna secrecion.", False),
    ("Se ve normal, sin enrojecimiento ni secrecion, ni mal olor.", False),
    ("No, escalofrios no he sentido.", False),
    ("No, nada de escalofrios, doctora.", False),
    ("Nada de fiebre ni escalofrios.", False),
    ("No he tenido fiebre.", False),
    ("No tengo dificultad para respirar.", False),
    ("Sangrado no ha habido.", False),
    ("No, todo bien por aca.", False),
    # Negación EN MEDIO de una regla de combinación: el patrón de fiebre +
    # escalofríos abarca las dos palabras y la denegación queda dentro.
    ("Mmm, fiebre? Creo que no, no me he sentido con escalofrios.", False),
    ("Que yo sepa no, ni fiebre ni escalofrios.", False),
    ("No he tenido fiebre ni me he sentido con escalofrios.", False),
    # ── Alarmas que deben SEGUIR disparando ────────────────────────────────
    ("Tengo fiebre de 39 grados.", True),
    ("Se me abrio la herida.", True),
    ("Me cuesta respirar.", True),
    ("Me duele el pecho.", True),
    ("La herida esta botando pus y huele feo.", True),
    ("Estoy sangrando mucho, no para.", True),
    ("Me desmaye esta manana.", True),
    # El "no" forma parte del síntoma, no lo niega
    ("No puedo respirar bien.", True),
    ("No he podido orinar desde ayer.", True),
    ("No me para el sangrado.", True),
    # Negación en una cláusula, alarma en la siguiente
    ("No he podido dormir, tengo fiebre de 39.", True),
    ("La herida no tiene pus, pero me duele el pecho.", True),
    ("Sin escalofrios, aunque se me abrio la herida.", True),
    ("No tengo nauseas, y estoy sangrando mucho.", True),
    ("No, doctora, pero se me abrio la herida.", True),
    ("Me duele el pecho, no he tenido fiebre.", True),
]


def main() -> int:
    fallos = 0
    for texto, esperado in CASOS:
        reglas = rules.evaluate_text(texto, None)
        señales = composicion.señales_de_turno(
            texto, turno=0, slots_numericos=rules.extraer_valores(texto))
        disparo = bool(reglas["disparos"]) or bool(señales)
        marca = "PASS" if disparo == esperado else "FAIL"
        fallos += marca == "FAIL"
        detalle = ([d["regla"] for d in reglas["disparos"]]
                   + [f"{s['dominio']}:{s['severidad']}" for s in señales])
        print(f"  [{marca}] esperado={'alarma' if esperado else 'silencio':<8} "
              f"{texto[:58]:<60} {detalle}")
    print(f"\nRESULTADO: {len(CASOS) - fallos}/{len(CASOS)}")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
