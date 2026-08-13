# -*- coding: utf-8 -*-
"""Lectura de cifras clínicas sin LLM (carril numérico determinista).

POR QUÉ EXISTE
--------------
La fiebre >38 °C y el dolor son criterios de urgencia explícitos en los planes
de cuidado del corpus oficial. Si la cifra no se lee, el criterio no existe:
es un falso negativo silencioso, el peor error de este sistema.

El extractor fallaba en dos frentes que este archivo fija:

  · Vocabulario de contexto. Exigía "fiebre|temperatura|grados|tengo|marca"
    con límites de palabra, así que "me la TOMÉ y estaba en 38.2" y "MARCABA
    como 38" devolvían nada. Son las formas más comunes del habla real.
  · Descalificación por unidad. La temperatura no la aplicaba, de modo que
    "tengo 38 años" podía leerse como una fiebre de 38 grados.

Los casos están redactados como español general, no copiados de la partición
de validación: comprueban el comportamiento del extractor, no unas etiquetas.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.decision import rules  # noqa: E402

# Casos donde el contexto lo aporta la PREGUNTA del agente, no la respuesta.
# El paciente contesta con una cifra suelta y hay que saber de qué habla.
CASOS_CON_PREGUNTA: list[tuple[str, str, dict]] = [
    ("un poquito nada mas, 38.4, pero es del clima", "¿ha tenido fiebre?",
     {"temperatura_c": 38.4}),
    ("como un 7", "¿de 0 a 10 qué tan fuerte es el dolor?", {"dolor_0_10": 7.0}),
    ("pues 4", "¿del 0 al 10?", {"dolor_0_10": 4.0}),
    # El decimal de una temperatura NO es una escala de dolor. Este defecto
    # convertía "marcó como 36.8" en un dolor de 8/10 —severidad crítica— y
    # mandaba a rojo a pacientes con temperatura normal.
    ("me la tome hace un rato y marco como 36.8 C", "y el dolor, ¿de 0 a 10?",
     {"temperatura_c": 36.8}),
    ("me la he tomado y hoy marco 36.9, o sea normalita", "¿cómo va el dolor?",
     {"temperatura_c": 36.9}),
    ("tengo 3 hijos", "¿cómo va el dolor?", {}),
    ("me la tomo cada 8 horas", "¿cómo va el dolor?", {}),
]

CASOS: list[tuple[str, dict]] = [
    # ── Temperatura que SÍ debe leerse ─────────────────────────────────────
    ("me la tome y estaba en 38.2", {"temperatura_c": 38.2}),
    ("la tome y marcaba como 38", {"temperatura_c": 38.0}),
    ("me subio a 39 anoche", {"temperatura_c": 39.0}),
    ("el termometro dio 37.8", {"temperatura_c": 37.8}),
    ("tengo fiebre de 39 grados", {"temperatura_c": 39.0}),
    ("me la tome a las 10 y marcaba 38.4", {"temperatura_c": 38.4}),
    # ── Cifras que NO son temperatura ──────────────────────────────────────
    ("tengo 38 anos", {}),
    ("vivo en el piso 38", {}),
    ("me tomo 2 pastillas cada 8 horas", {}),
    ("no he tenido fiebre", {}),
    # ── Dolor ──────────────────────────────────────────────────────────────
    ("algo de dolor ahi en la cadera, sera un 5 mas o menos", {"dolor_0_10": 5.0}),
    ("el dolor como un cinco", {"dolor_0_10": 5.0}),
    ("un dolor de ocho", {"dolor_0_10": 8.0}),
    ("me duele, seria un 9 de 10", {"dolor_0_10": 9.0}),
    # ── Cifras que NO son escala de dolor ──────────────────────────────────
    ("el dolor me dura como 3 horas", {}),
    ("tengo dolor hace 2 dias", {}),
]


def main() -> int:
    fallos = 0
    for texto, esperado in CASOS:
        obtenido = rules.extraer_valores(texto)
        marca = "PASS" if obtenido == esperado else "FAIL"
        fallos += marca == "FAIL"
        print(f"  [{marca}] {texto[:54]:<56} -> {str(obtenido):<26} esperado={esperado}")
    print("\n  — el contexto lo aporta la pregunta del agente —")
    for texto, pregunta, esperado in CASOS_CON_PREGUNTA:
        obtenido = rules.extraer_valores(texto, pregunta)
        marca = "PASS" if obtenido == esperado else "FAIL"
        fallos += marca == "FAIL"
        print(f"  [{marca}] {texto[:44]:<46} ← {pregunta[:28]:<30} -> {obtenido}")

    total = len(CASOS) + len(CASOS_CON_PREGUNTA)
    print(f"\nRESULTADO: {total - fallos}/{total}")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
