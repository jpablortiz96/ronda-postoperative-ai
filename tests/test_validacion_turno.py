"""Pruebas de la barrera contra capturas sin habla (app/stt.tiene_contenido).

Contexto: ante audio sin voz, el modelo de transcripción no devuelve vacío,
alucina texto. Se han observado ".", "Gracias por ver el video." y
"Subtitulado por ...". Esta barrera descarta el caso inequívoco sin dañar las
respuestas cortas, que en una llamada clínica son datos de primera línea:
"Sí", "No", "9" y "39.5" alimentan directamente los umbrales de escalamiento.

Ejecutable sin dependencias extra:
    python tests/test_validacion_turno.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.stt import tiene_contenido  # noqa: E402

# Respuestas cortas REALES que deben seguir llegando al motor de decisión.
DEBEN_PASAR = [
    "Sí.", "Sí", "No.", "No", "9", "9.", "39.5", "Bien.", "Bien",
    "Nueve.", "Ocho de diez", "Un poco.", "Ya no.", "Mucho.", "0",
    "Me duele.", "Sangrado abundante.", "Hola, me siento bastante bien hoy.",
]

# Salidas observadas o esperables ante audio sin habla.
DEBEN_DESCARTARSE = [
    "", " ", ".", "..", "...", " . ", "!", "¿?", "¡!", "…", "-", "—", ",", "·",
]


def test_respuestas_cortas_sobreviven():
    fallos = [t for t in DEBEN_PASAR if not tiene_contenido(t)]
    assert not fallos, f"se habrían descartado respuestas válidas: {fallos}"


def test_puntuacion_sola_se_descarta():
    fallos = [t for t in DEBEN_DESCARTARSE if tiene_contenido(t)]
    assert not fallos, f"no se descartaron capturas sin habla: {fallos}"


def test_alucinaciones_con_texto_no_se_filtran_aqui():
    """Documenta el límite: una alucinación con palabras reales no puede
    distinguirse por texto. Esa defensa vive en el VAD del cliente."""
    assert tiene_contenido("Gracias por ver el video.")
    assert tiene_contenido("Subtitulado por la comunidad de Amara.org")


if __name__ == "__main__":
    fallos = 0
    print("respuestas cortas que DEBEN pasar")
    for t in DEBEN_PASAR:
        ok = tiene_contenido(t)
        fallos += not ok
        print(f"  {t!r:<40} -> {'PASA' if ok else 'DESCARTADA  << FALLO'}")
    print("\ncapturas sin habla que DEBEN descartarse")
    for t in DEBEN_DESCARTARSE:
        ok = not tiene_contenido(t)
        fallos += not ok
        print(f"  {t!r:<40} -> {'descartada' if ok else 'PASA  << FALLO'}")
    try:
        test_alucinaciones_con_texto_no_se_filtran_aqui()
        print("\nlímite documentado: una alucinación con palabras reales no se")
        print("filtra por texto; la defensa acústica vive en el VAD del cliente.")
    except AssertionError:
        fallos += 1
    total = len(DEBEN_PASAR) + len(DEBEN_DESCARTARSE)
    print(f"\nresultado: {total - fallos}/{total}")
    sys.exit(1 if fallos else 0)
