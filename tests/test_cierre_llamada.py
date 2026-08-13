# -*- coding: utf-8 -*-
"""Cierre conversacional (§A9, C1–C8).

Estas pruebas nacen de una llamada humana real en la que RONDA:
  · volvió a preguntar por el acompañante después de que el paciente
    contestara «Sí, mi mamá»;
  · repitió en cada turno que el caso estaba escalado;
  · siguió la conversación después de «No, nada más. Eso sería todo».

Se recorre la sesión REAL —`CallSession.turno`—, no funciones sueltas. El
único doble es el proveedor del modelo, para no depender de la cuota.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.conversation import cierre, generacion  # noqa: E402
from app.conversation.orchestrator import CallSession  # noqa: E402
from app.decision import assess  # noqa: E402

fallos = 0


def check(nombre: str, cond: bool, detalle: str = "") -> None:
    global fallos
    fallos += not cond
    print(f"  [{'PASS' if cond else 'FAIL'}] {nombre}" + (f"   → {detalle}" if not cond else ""))


# El carril LLM no aporta nada aquí: la política de cierre es determinista.
assess.extract_slots = lambda t, c, h=None: (
    assess._merge_slots(h or {}, assess._empty_slots()),
    {"provider": "prueba", "input_tokens": 0, "output_tokens": 0})


def modelo(messages, **kw):
    """Modelo dócil que devuelve una frase no clínica y sin evidencia."""
    return ({"sentences": [{"text": "Entendido, gracias por contarme.",
                            "clinical": False, "evidence_ids": []}],
             "followup_question": ""}, {"provider": "prueba"})


generacion.llm.chat_json = modelo

PACIENTE = {"paciente_id": "P9", "nombre": "Carlos Ramírez", "edad": 52,
            "procedimiento": "apendicectomia", "procedimiento_nombre": "apendicectomía",
            "dia_postoperatorio": 3, "comorbilidades": []}


def sesion() -> CallSession:
    s = CallSession(dict(PACIENTE))
    s.saludo_inicial()
    return s


# ── A3 · detección general de intención de terminar ─────────────────────────
print("\nA3 · el paciente manifiesta que no tiene nada más")
FRASES_FIN = [
    "Eso es todo", "eso sería todo", "No, nada más", "no tengo nada más",
    "Ninguna otra duda", "podemos terminar", "gracias, eso era",
    "Así está bien", "por ahora no", "Ya está, muchas gracias",
]
for f in FRASES_FIN:
    check(f"«{f}» → fin", cierre.quiere_terminar(f), f)

FRASES_NO_FIN = [
    "Me duele bastante", "No he tenido fiebre", "La herida está roja",
    "¿Puedo comer normal?", "Tengo una duda sobre los puntos",
]
for f in FRASES_NO_FIN:
    check(f"«{f}» NO es fin", not cierre.quiere_terminar(f), f)

check("«no» aislado NO cierra sin pregunta de cierre",
      not cierre.quiere_terminar("no"))
check("«no» aislado SÍ cierra tras «¿alguna otra duda?»",
      cierre.quiere_terminar("no", hubo_pregunta_de_cierre=True))
check("se reconoce la pregunta de cierre del agente",
      cierre.es_pregunta_de_cierre("¿Tiene alguna otra duda antes de despedirnos?"))

# ── A4 · memoria del acompañante ────────────────────────────────────────────
print("\nA4 · quién acompaña al paciente")
check("«Sí, mi mamá» se lee como acompañado",
      cierre.leer_acompanante("Si mi mamá") == "acompanado",
      str(cierre.leer_acompanante("Si mi mamá")))
check("«estoy con mi esposa» se lee como acompañado",
      cierre.leer_acompanante("estoy con mi esposa") == "acompanado")
check("«estoy solo» se lee como sin acompañante",
      cierre.leer_acompanante("Estoy solo en la casa") == "sin_acompanante")
check("una frase clínica no inventa acompañante",
      cierre.leer_acompanante("me duele el abdomen") is None)

# ── C1 · rojo con datos faltantes y SIN intención de terminar → no cierra ───
print("\nC1 · rojo, faltan datos, el paciente no pide terminar")
s = sesion()
r = s.turno("Tengo mucha fiebre, 39 grados, y la herida bota pus")
check("el turno escala a rojo", r["nivel_turno"] == "rojo", str(r["nivel_turno"]))
check("queda alerta persistida", s.alerta is not None)
check("NO se cierra la llamada", r["cerrar_llamada"] is False, str(r["cerrar_llamada"]))
check("el estado de cierre es 'escalado'", s.estado_cierre == cierre.ESCALADO,
      s.estado_cierre)
r = s.turno("¿Y eso es grave?")
check("sigue sin cerrarse mientras no lo pida", r["cerrar_llamada"] is False)

# ── C1b · quiere terminar PERO acaba de aparecer una alarma → no cierra ─────
print("\nC1b · una alarma nueva en el mismo turno bloquea el cierre")
s2 = sesion()
s2.turno("Todo bien, sin novedad")
r = s2.turno("Eso es todo, aunque ahora me está saliendo pus por la herida")
check("hubo intención de terminar", s2.memoria.quiere_terminar is True)
check("NO cierra con alarma nueva", r["cerrar_llamada"] is False,
      f"nivel={r['nivel_turno']} cerrar={r['cerrar_llamada']}")

# ── C2 · rojo escalado + «eso es todo» → cierra ─────────────────────────────
print("\nC2 · rojo ya escalado y el paciente dice que no tiene nada más")
s3 = sesion()
s3.turno("Tengo fiebre de 39 y me sale pus de la herida")
check("hay acta de alerta", s3.alerta is not None)
r = s3.turno("No, nada más. Eso sería todo.")
check("CIERRA la llamada", r["cerrar_llamada"] is True, str(r.get("motivo_cierre")))
check("el motivo es seguimiento_completado",
      r["motivo_cierre"] == "seguimiento_completado", str(r["motivo_cierre"]))
check("el estado pasa a CERRANDO", s3.estado_cierre == cierre.CERRANDO,
      s3.estado_cierre)
check("la despedida es única y breve",
      r["texto"].count("enfermería") <= 1, r["texto"])
check("se despide por su nombre", "Carlos" in r["texto"], r["texto"][:80])
check("no añade consejo médico nuevo",
      "tome" not in r["texto"].lower() and "dosis" not in r["texto"].lower(),
      r["texto"])

# ── C3 · no volver a preguntar por el acompañante ──────────────────────────
print("\nC3 · el paciente ya dijo que está con su mamá")
s4 = sesion()
s4.turno("Si mi mamá")
check("la memoria lo registra", s4.memoria.sabe(cierre.ACOMPANANTE))
r = s4.turno("Tengo fiebre de 39 y me sale pus de la herida")
check("el guion de escalamiento NO vuelve a preguntar",
      "¿Hay alguien con usted" not in r["texto"], r["texto"][-90:])
check("en su lugar da la indicación",
      "Quédese acompañado" in r["texto"], r["texto"][-90:])
check("la restricción llega al generador",
      any("NO vuelvas a preguntar" in x for x in s4.memoria.restricciones()),
      str(s4.memoria.restricciones()))

# ── C4 · no repetir el párrafo de escalamiento ─────────────────────────────
print("\nC4 · el escalamiento no se repite en cada turno")
s5 = sesion()
r1 = s5.turno("Tengo fiebre de 39 y me sale pus de la herida")
check("el primer turno SÍ lo anuncia", "enfermería" in r1["texto"], r1["texto"][:70])
check("queda anotado como anunciado", s5.memoria.ya_anuncio(cierre.ESCALAMIENTO))
check("y también el aviso de enfermería", s5.memoria.ya_anuncio(cierre.ENFERMERIA))
restr = s5.memoria.restricciones()
check("el generador recibe la prohibición de repetirlo",
      any("NO vuelvas a explicarlo" in x for x in restr), str(restr))
r2 = s5.turno("Bueno, está bien")
check("el segundo turno no repite el párrafo completo",
      "voy a pasar su caso ahora mismo" not in r2["texto"], r2["texto"][:90])

# ── C8 · pregunta lateral justo antes del cierre ───────────────────────────
print("\nC8 · el paciente pregunta algo al despedirse")
s6 = sesion()
for t in ("Un dolor de dos", "No he tenido fiebre", "La herida está bien",
          "Camino sin problema", "Tengo buen apetito y he podido comer",
          "Tomo el acetaminofén"):
    s6.turno(t)
check("el checklist quedó cubierto", s6.checklist_pendiente == [],
      str(s6.checklist_pendiente))
r = s6.turno("¿El protocolo dice algo sobre la ducha? Ya con eso sería todo.")
check("responde antes de cerrar", len(r["texto"]) > 60, r["texto"][:80])
check("y cierra en el mismo mensaje", r["cerrar_llamada"] is True,
      str(r.get("motivo_cierre")))
check("la despedida va al final",
      r["texto"].rstrip().endswith("Gracias por responder la llamada."),
      r["texto"][-60:])
check("no retoma un tema del checklist al cerrar",
      "Volviendo a su seguimiento" not in r["texto"], r["texto"][-90:])

# ── C6 · el acta persiste tras el cierre ───────────────────────────────────
print("\nC6 · el acta se guarda")
resumen = s3.finalizar()
check("finalizar devuelve acta", isinstance(resumen, dict) and bool(resumen))
check("el acta trae criticidad clínica", "criticidad_clinica" in resumen,
      str(sorted(resumen))[:100])
check("la sesión queda marcada como finalizada", s3.finalizada is True)

# ── Cierre manual sigue existiendo (§A8) ───────────────────────────────────
print("\nA8 · el cierre manual no desaparece")
s7 = sesion()
s7.turno("Todo bien")
check("sin intención de terminar no hay autocierre",
      s7.estado_cierre == cierre.ACTIVO, s7.estado_cierre)
rs = s7.finalizar()
check("y aun así se puede finalizar a mano", bool(rs) and s7.finalizada is True)

# ── La política pura, sin sesión ───────────────────────────────────────────
print("\npolítica de cierre aislada")
ok, motivo = cierre.puede_cerrar(quiere_terminar_paciente=False, escalado=False,
                                 alerta_persistida=False, nueva_alarma=False,
                                 temas_sin_intentar=[])
check("sin intención → no cierra", ok is False, motivo)
ok, motivo = cierre.puede_cerrar(quiere_terminar_paciente=True, escalado=True,
                                 alerta_persistida=False, nueva_alarma=False,
                                 temas_sin_intentar=[])
check("escalado sin acta → no cierra", ok is False, motivo)
ok, motivo = cierre.puede_cerrar(quiere_terminar_paciente=True, escalado=False,
                                 alerta_persistida=False, nueva_alarma=False,
                                 temas_sin_intentar=["dolor"])
check("temas sin intentar → no cierra", ok is False, motivo)
ok, motivo = cierre.puede_cerrar(quiere_terminar_paciente=True, escalado=True,
                                 alerta_persistida=True, nueva_alarma=False,
                                 temas_sin_intentar=["dolor"])
check("escalado con acta cierra aunque falten temas", ok is True, motivo)

# ── C5/C7 · contrato del frame de cierre (comprobación ESTRUCTURAL) ────────
# Honestidad sobre el alcance: esto verifica el CONTRATO entre orquestador y
# WebSocket —qué clave se lee y en qué orden se emite—, no una reproducción de
# audio real. El auto-colgado del navegador (esperar a que la voz termine)
# solo puede validarlo una persona con altavoces.
print("\nC5/C7 · contrato del frame de cierre (estructural)")
fuente = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
check("main.py lee la clave 'cerrar_llamada'", 'resultado.get("cerrar_llamada")' in fuente)
check("main.py emite el frame 'cierre_llamada'", '"tipo": "cierre_llamada"' in fuente)
check("C5 · el frame de cierre va DESPUÉS del de latencia",
      fuente.index('"tipo": "latencia"') < fuente.index('"tipo": "cierre_llamada"'))
check("C7 · el servidor NO cierra el WS al anunciar el cierre",
      "ws.close" not in fuente[fuente.index('"tipo": "cierre_llamada"'):
                               fuente.index('"tipo": "cierre_llamada"') + 400])
cliente = (Path(__file__).resolve().parents[1] / "app" / "static" / "llamada.js").read_text(encoding="utf-8")
check("el cliente espera a que el audio termine",
      "esperarAudioYCerrar" in cliente and "agenteAudible()" in cliente)
check("A7 · el cliente cierra el WS con código 1000", "ws.close(1000" in cliente)
check("A8 · el botón de cierre manual sigue conectado",
      "btnFinalizar" in cliente and "finalizarLlamada()" in cliente)

print(f"\nRESULTADO: {'PASS' if fallos == 0 else f'{fallos} FALLOS'}")
sys.exit(1 if fallos else 0)
