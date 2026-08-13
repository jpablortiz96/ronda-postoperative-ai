"""Pruebas de la barrera de salida (app/conversation/saneado.py).

Casos tomados literalmente de llamadas reales en las que el agente afirmó
cosas que no podía sostener, con `referencias_usadas = []` y
`consultas_rag = 0`.

    python tests/test_barrera_evidencia.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.conversation.saneado import sanear_respuesta  # noqa: E402

CITA_REAL = [{"doc_id": "abc123", "documento": "protocolo.pdf", "chunk": 0, "distancia": 0.31}]

# (descripción, texto del modelo, citas, fragmentos que NO deben sobrevivir)
SIN_EVIDENCIA = [
    ("cita fabricada — evidencia humana real",
     "Es normal algo de dolor. [FUENTE: Protocolo de apendicectomía laparoscópica]",
     None, ["[FUENTE", "Protocolo de apendicectomía"]),
    ("marcador en otro formato",
     "Puede caminar. [Fuente: guía clínica interna]", None, ["[Fuente"]),
    ("historia clínica inexistente",
     "Según su historial, es común tener una recuperación estable.",
     None, ["Según su historial"]),
    ("historia clínica, otra forma",
     "De acuerdo con su historia clínica, todo va bien.",
     None, ["De acuerdo con su historia"]),
    ("protocolos inexistentes",
     "Según nuestros protocolos, debe reposar.", None, ["Según nuestros protocolos"]),
    ("cirugía exitosa",
     "Su cirugía fue exitosa, así que no se preocupe.", None, ["fue exitosa"]),
    ("procedimiento exitoso",
     "El procedimiento resultó exitoso.", None, ["resultó exitoso"]),
    ("MEDICACIÓN INVENTADA",
     "De acuerdo con su historial, se le recetó paracetamol cada ocho horas.",
     None, ["paracetamol", "se le recetó"]),
    ("medicación mencionada de pasada",
     "Puede tomar acetaminofén si le duele.", None, ["acetaminofén"]),
    ("dosis inventada",
     "Debe tomar ibuprofeno cada 8 horas.", None, ["ibuprofeno"]),
]

# Texto legítimo que NO debe alterarse
DEBE_CONSERVARSE = [
    ("escalamiento determinista",
     "Le agradezco que me lo cuente. Por lo que me describe, esa molestia en el pecho, "
     "voy a pasar su caso ahora mismo al equipo de enfermería."),
    ("pregunta de checklist",
     "Cuénteme ahora sobre el dolor: dónde, qué tan fuerte de 0 a 10."),
    ("límite honesto",
     "Esa pregunta puntual no la tengo en mis protocolos, y prefiero no adivinar."),
    ("respuesta empática neutra",
     "Entiendo que eso asusta, y qué bueno que me lo cuenta. ¿Desde cuándo le pasa?"),
]


def test_sin_evidencia_se_sanea():
    fallos = []
    for desc, texto, citas, prohibidos in SIN_EVIDENCIA:
        salida = sanear_respuesta(texto, citas)
        for p in prohibidos:
            if p.lower() in salida.lower():
                fallos.append((desc, p, salida))
    assert not fallos, "\n".join(f"  {d}: sobrevivió {p!r} en {s!r}" for d, p, s in fallos)


def test_marcador_de_cita_se_elimina_aunque_haya_evidencia():
    """Las citas reales se muestran desde objetos, nunca desde texto libre."""
    salida = sanear_respuesta("Puede bañarse. [FUENTE: doc abc123]", CITA_REAL)
    assert "[FUENTE" not in salida


def test_texto_legitimo_intacto():
    fallos = []
    for desc, texto in DEBE_CONSERVARSE:
        salida = sanear_respuesta(texto, None)
        if salida.strip() != texto.strip():
            fallos.append((desc, texto, salida))
    assert not fallos, "\n".join(f"  {d}\n    antes: {a!r}\n    después: {b!r}"
                                 for d, a, b in fallos)


def test_nunca_devuelve_vacio():
    for _, texto, citas, _ in SIN_EVIDENCIA:
        assert sanear_respuesta(texto, citas).strip()


if __name__ == "__main__":
    fallos = 0
    print("AFIRMACIONES SIN EVIDENCIA — deben sanearse")
    print("-" * 100)
    for desc, texto, citas, prohibidos in SIN_EVIDENCIA:
        salida = sanear_respuesta(texto, citas)
        ok = not any(p.lower() in salida.lower() for p in prohibidos)
        fallos += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {desc}")
        print(f"        antes  : {texto}")
        print(f"        después: {salida}")
    print("\nTEXTO LEGÍTIMO — debe quedar intacto")
    print("-" * 100)
    for desc, texto in DEBE_CONSERVARSE:
        salida = sanear_respuesta(texto, None)
        ok = salida.strip() == texto.strip()
        fallos += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {desc}")
        if not ok:
            print(f"        antes  : {texto}")
            print(f"        después: {salida}")
    total = len(SIN_EVIDENCIA) + len(DEBE_CONSERVARSE)
    print(f"\nRESULTADO: {total - fallos}/{total}")
    sys.exit(1 if fallos else 0)
