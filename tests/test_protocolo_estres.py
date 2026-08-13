# -*- coding: utf-8 -*-
"""Estrés clínico derivado de los PROTOCOLOS, no de las etiquetas del dataset.

POR QUÉ ESTA SUITE ES DISTINTA
------------------------------
Todas las métricas anteriores se miden contra un dataset que también se usó
para diseñar el motor. Por bien que se separen las particiones, siguen midiendo
lo mismo: si el sistema reproduce unas etiquetas.

Aquí las expectativas salen de otra parte. Cada caso cita el fragmento del
plan de cuidado oficial que lo justifica, y las frases están redactadas de cero
—en español colombiano, con las vueltas del habla real— sin copiar ninguna
oración del dataset. Si el motor solo hubiera aprendido cadenas de texto, esta
suite lo delata; si aprendió los conceptos clínicos, la pasa.

FUENTES (corpus oficial del reto, carpeta `dataset/textos`):
  [APX]  PLAN DE CUIDADO EN CASA DE PACIENTE EN POSTOPERATORIO DE
         APENDICECTOMÍA — lista "signos de alarma / acudir a urgencias"
  [COL]  PLAN DE CUIDADO COLECISTECTOMIA — "Fiebre > 38 ºC"
  [ART]  Recomendaciones Programa Reemplazo Articular de Rodilla y PLAN CASERO
         REEMPLAZO TOTAL DE RODILLA — signos de alarma
  [CCR]  Colon Cancer Surgery and Recovery — "Call your surgeon for these
         danger signs"
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.decision import assess, engine  # noqa: E402

# Estas pruebas comprueban el MOTOR DETERMINISTA. Se desactiva el extractor
# LLM para que corran sin red, sin cuota y con el mismo resultado siempre: una
# suite de seguridad clínica que depende de un servicio externo no es una
# suite, es una encuesta. El carril LLM tiene su propia cobertura en
# `test_dos_ejes.py` (contrato de evidencia) y `test_multiturno_llm_caido.py`.
assess.extract_slots = lambda t, c, h=None: (
    assess._merge_slots(h or {}, assess._empty_slots()),
    {"provider": "none", "input_tokens": 0, "output_tokens": 0,
     "llm_disponible": False, "modo_degradado": True, "motivo": "desactivado_en_pruebas"})

# (nombre, turnos [(pregunta_agente, respuesta)], procedimiento, nivel mínimo
#  esperado, nivel máximo aceptable, fuente que lo justifica)
CASOS = [
    # ── 1 · señales individuales de la lista oficial ────────────────────────
    ("fiebre por encima de 38", [
        ("¿ha tenido fiebre?", "Sí, anoche me la tomé y me marcó 38 y medio."),
    ], "Apendicectomía", "amarillo", "rojo",
        "[APX][COL] «Fiebre mayor de 38 °C» figura como signo para acudir a urgencias"),

    ("pus en la herida", [
        ("¿cómo se ve la herida?", "Le está saliendo una cosa amarilla espesa por el corte."),
    ], "Apendicectomía", "amarillo", "rojo",
        "[APX] «salida de pus en la herida quirúrgica»"),

    ("apertura de la herida", [
        ("¿cómo se ve la herida?", "Se me soltaron los puntos y quedó abierto un pedazo."),
    ], "Apendicectomía", "rojo", "rojo",
        "[APX] «Apertura de la herida o separación de los puntos»"),

    ("dificultad respiratoria", [
        ("¿cómo se ha sentido?", "Me está costando coger aire, doctora."),
    ], "Colectomía", "rojo", "rojo",
        "[APX][ART] «Dificultad para respirar o dolor en el pecho»"),

    ("dolor en el pecho", [
        ("¿cómo sigue?", "Siento como si me apretaran el pecho desde anoche."),
    ], "Reemplazo de cadera/rodilla", "rojo", "rojo",
        "[ART] «Dolor en pecho o dificultad para respirar»"),

    ("dolor en la pantorrilla", [
        ("¿cómo va la pierna?", "La pantorrilla la tengo hinchada y caliente."),
    ], "Reemplazo de cadera/rodilla", "rojo", "rojo",
        "[APX] «Dolor o hinchazón en las piernas o pantorrillas»"),

    ("vómito que no cede", [
        ("¿cómo ha comido?", "Devuelvo todo, no me pasa ni el agua."),
    ], "Colectomía", "amarillo", "rojo",
        "[APX][CCR] «Náuseas o vómitos que no se detienen»"),

    ("dolor que va aumentando", [
        ("¿cómo va el dolor?", "Cada día está peor, no mejora ni con lo que me formularon."),
    ], "Colectomía", "amarillo", "rojo",
        "[APX][CCR] «Dolor que no mejora o que va aumentando»"),

    # ── 2 · combinaciones moderadas: el cuadro compuesto ────────────────────
    ("deterioro en varios frentes, cada uno minimizado", [
        ("¿cómo va el dolor?", "Ahí molestando un poquito, nada del otro mundo."),
        ("¿se ha tomado la temperatura?", "Me marcó 37 y pico, será del calor."),
        ("¿cómo se ve la herida?", "La veo un poco rosada por el borde, pero normal."),
        ("¿cómo ha comido?", "Casi no me provoca nada, como por obligación."),
        ("¿cómo ha dormido?", "Fatal, me despierto a cada rato."),
        ("¿y para caminar?", "Camino despacito, me canso enseguida."),
    ], "Apendicectomía", "rojo", "rojo",
        "[APX] cuadro compuesto: varios signos de la lista a la vez, ninguno aislado"),

    # ── 3 · negaciones: nada de esto debe alarmar ───────────────────────────
    ("todo negado explícitamente", [
        ("¿cómo va el dolor?", "Ninguno, doctora, ni molestia."),
        ("¿ha tenido fiebre?", "No, nada de fiebre ni escalofríos."),
        ("¿cómo se ve la herida?", "Limpia, sin enrojecimiento ni secreción ni mal olor."),
    ], "Apendicectomía", "verde", "verde",
        "ausencia de todo signo de la lista, con los tres dominios críticos evaluados"),

    ("negación con vocabulario del propio protocolo", [
        ("¿cómo va el dolor?", "No me duele nada."),
        ("¿ha tenido fiebre?", "Para nada, me la tomé y estaba en 36.4."),
        ("¿cómo se ve la herida?", "No le veo pus, ni está abierta, ni huele feo."),
    ], "Colecistectomía", "verde", "verde",
        "nombrar un signo para descartarlo no puede disparar ese signo"),

    # ── 4 · recuperación normal esperable ───────────────────────────────────
    ("postoperatorio que evoluciona bien", [
        ("¿cómo va el dolor?", "Un tresito, y con la pastilla se me quita."),
        ("¿ha tenido fiebre?", "No señor, me la tomo todos los días y anda en 36.6."),
        ("¿cómo se ve la herida?", "Se ve bien, sequita, cada día mejor."),
    ], "Colecistectomía", "verde", "verde",
        "[COL] evolución esperada; ningún criterio de alarma presente"),

    ("dolor de hombro tras laparoscopia", [
        ("¿cómo va el dolor?", "Me molesta el hombro derecho, como un dos."),
        ("¿ha tenido fiebre?", "No, ninguna."),
        ("¿cómo se ve la herida?", "Bien, sin nada raro."),
    ], "Colecistectomía", "verde", "amarillo",
        "[COL] el dolor referido al hombro es esperable tras laparoscopia; "
        "no está en la lista de alarma"),

    # ── 5 · información incompleta ─────────────────────────────────────────
    # OJO: estos dos casos esperaban AMARILLO hasta FASE 4.7, cuando la
    # incompletitud contaminaba la etiqueta clínica. Ya no: el riesgo clínico
    # es VERDE porque no hay ningún hallazgo, y lo que cambia es el eje de
    # evaluación y la acción. Se comprueban por separado más abajo.
    ("el paciente esquiva los dominios críticos", [
        ("¿cómo va el dolor?", "Ay, no sé, uno con tantas pastillas ya ni sabe."),
        ("¿ha tenido fiebre?", "No me he puesto a pensar en eso."),
        ("¿cómo se ve la herida?", "No me gusta mirármela, me da cosita."),
    ], "Apendicectomía", "verde", "verde",
        "no hay hallazgo alguno: el riesgo clínico es verde. Lo que falta es "
        "evaluación, y eso vive en el otro eje"),

    ("la llamada se pierde a mitad", [
        ("¿cómo va el dolor?", "Ninguno, estoy bien."),
        ("¿ha tenido fiebre?", "[inaudible]"),
        ("¿cómo se ve la herida?", "..."),
    ], "Apendicectomía", "verde", "verde",
        "dos dominios críticos perdidos: la etiqueta clínica no se falsea"),

    # ── 6 · terceros ───────────────────────────────────────────────────────
    ("un familiar reporta el signo de alarma", [
        ("¿cómo se ve la herida?",
         "Soy la hija. Le está saliendo pus por la herida y anoche tenía 38.7."),
    ], "Apendicectomía", "rojo", "rojo",
        "[APX] el signo no deja de existir porque lo cuente el cuidador"),

    ("un familiar descarta correctamente", [
        ("¿cómo va el dolor?", "Habla la hija: dice que no le duele nada."),
        ("¿ha tenido fiebre?", "No, le tomo la temperatura y anda en 36.5."),
        ("¿cómo se ve la herida?", "Yo se la curo y está limpia, sin nada raro."),
    ], "Apendicectomía", "verde", "verde",
        "un tercero que responde clínicamente SÍ cubre el dominio"),

    # ── 7 · minimización ───────────────────────────────────────────────────
    ("el paciente resta importancia a un signo de la lista", [
        ("¿cómo se ve la herida?",
         "Bueno, le sale un poquitico de pus, pero es normal de la sanada, no se preocupe."),
    ], "Apendicectomía", "amarillo", "rojo",
        "[APX] el pus está en la lista; que el paciente lo minimice no lo borra"),

    ("minimizar una fiebre medida", [
        ("¿ha tenido fiebre?", "Un poquito nada más, 38.4, pero es del clima de aquí."),
    ], "Colecistectomía", "amarillo", "rojo",
        "[COL] «Fiebre > 38 ºC»; la explicación del paciente no cambia la cifra"),

    # ── 8 · error de transcripción ─────────────────────────────────────────
    ("la frase llega troceada por el STT", [
        ("¿cómo se ve la herida?", "le sale pus- [inaudible] por el cor- corte"),
    ], "Apendicectomía", "amarillo", "rojo",
        "[APX] el signo sobrevive a una transcripción degradada"),

    ("ruido que no debe inventar un signo", [
        ("¿cómo va el dolor?", "Ninguno [inaudible] nada."),
        ("¿ha tenido fiebre?", "No, ninguna, me la tomé: 36.2."),
        ("¿cómo se ve la herida?", "Bien, sin nada raro por ahí."),
    ], "Apendicectomía", "verde", "verde",
        "el ruido no puede fabricar un hallazgo donde el paciente niega"),
]

NIVEL = {"verde": 0, "amarillo": 1, "rojo": 2}


def evaluar(turnos, procedimiento) -> tuple[str, dict]:
    slots: dict = {}
    nivel_max = "verde"
    for i, (pregunta, respuesta) in enumerate(turnos):
        d = engine.decide(respuesta, procedimiento, f"Procedimiento: {procedimiento}.",
                          slots, turno=i, pregunta_previa=pregunta)
        slots = d["slots"]
        if NIVEL[d["nivel_final"]] > NIVEL[nivel_max]:
            nivel_max = d["nivel_final"]
    cierre = engine.cerrar_llamada(nivel_max, slots.get("_cobertura"))
    return cierre["nivel_final"], cierre


def main() -> int:
    fallos = 0
    for nombre, turnos, proc, minimo, maximo, fuente in CASOS:
        obtenido, cierre = evaluar(turnos, proc)
        ok = NIVEL[minimo] <= NIVEL[obtenido] <= NIVEL[maximo]
        fallos += not ok
        rango = minimo if minimo == maximo else f"{minimo}..{maximo}"
        print(f"  [{'PASS' if ok else 'FAIL'}] {nombre:<48} esperado={rango:<16} "
              f"obtenido={obtenido}")
        if not ok:
            print(f"          justificación: {fuente}")
            print(f"          cobertura: "
                  f"{cierre['cobertura_evaluacion']['criticos_sin_cubrir']}  "
                  f"elevado_por_cobertura={cierre['elevado_por_cobertura']}")
    # Los dos casos de información incompleta: el riesgo clínico no se falsea,
    # pero la llamada tampoco puede cerrarse como si estuviera comprobada.
    print("\n  — eje de evaluación en los casos incompletos —")
    for nombre in ("el paciente esquiva los dominios críticos", "la llamada se pierde a mitad"):
        turnos, proc = next((c[1], c[2]) for c in CASOS if c[0] == nombre)
        _, cierre = evaluar(turnos, proc)
        ok = (cierre["estado_evaluacion"] in ("incompleta", "fallida")
              and cierre["accion_operativa"] != "continuar")
        fallos += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {nombre:<48} "
              f"estado={cierre['estado_evaluacion']} acción={cierre['accion_operativa']}")

    total = len(CASOS) + 2
    print(f"\nRESULTADO: {total - fallos}/{total}")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
