"""Pruebas del normalizador de transcripciones (app/stt.py).

Protegen una propiedad clínicamente crítica: una transcripción compuesta solo
por una cifra ("9", "38", "39.5") no puede perderse. El SDK del proveedor la
entrega como int/float, y esos valores son los que gobiernan los umbrales de
dolor y temperatura del motor de decisión.

Ejecutable sin dependencias extra:
    python tests/test_stt_normalizacion.py
También compatible con pytest si está disponible.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.stt import _texto_de  # noqa: E402


class _RespuestaConAtributo:
    """Imita el objeto que devuelve el SDK con response_format='verbose_json'."""

    def __init__(self, text):
        self.text = text
        self.duration = 5.1
        self.language = "spanish"


CASOS = [
    # (descripción, entrada, salida esperada)
    ("cadena numérica decimal", "39.5", "39.5"),
    ("float del SDK (cifra desnuda)", 39.5, "39.5"),
    ("int del SDK (cifra desnuda)", 9, "9"),
    ("cadena numérica entera", "9", "9"),
    ("cadena con espacios", " 9 de 10 ", "9 de 10"),
    ("frase normal", "Tengo dolor.", "Tengo dolor."),
    ("objeto con .text", _RespuestaConAtributo("39.5"), "39.5"),
    ("objeto con .text numérico", _RespuestaConAtributo(39.5), "39.5"),
    ("diccionario", {"text": "9"}, "9"),
    ("diccionario con texto normal", {"text": " Sangrado abundante. "}, "Sangrado abundante."),
    ("None", None, ""),
    ("cero es una transcripción válida", 0, "0"),
    ("cadena vacía", "", ""),
    ("tipo inesperado no se convierte a repr", object(), ""),
    ("booleano no es una transcripción", True, ""),
    ("diccionario sin clave text", {"otro": "x"}, ""),
]


def test_normalizacion():
    fallos = []
    for descripcion, entrada, esperado in CASOS:
        obtenido = _texto_de(entrada)
        if obtenido != esperado:
            fallos.append((descripcion, entrada, esperado, obtenido))
    assert not fallos, "casos fallidos: " + repr(fallos)


def test_nunca_devuelve_repr_de_objeto():
    """Ningún resultado puede contener la repr de un objeto interno."""
    for _, entrada, _ in CASOS:
        assert "object at 0x" not in _texto_de(entrada)


def test_siempre_devuelve_cadena():
    for _, entrada, _ in CASOS:
        assert isinstance(_texto_de(entrada), str)


if __name__ == "__main__":
    ancho = max(len(d) for d, _, _ in CASOS)
    fallos = 0
    print(f"{'caso':<{ancho}} | {'entrada':<34} | {'esperado':<22} | {'obtenido':<22} | ")
    print("-" * (ancho + 96))
    for descripcion, entrada, esperado in CASOS:
        obtenido = _texto_de(entrada)
        ok = obtenido == esperado
        fallos += not ok
        entrada_txt = repr(entrada)
        if len(entrada_txt) > 32:
            entrada_txt = type(entrada).__name__ + "(...)"
        print(f"{descripcion:<{ancho}} | {entrada_txt:<34} | {esperado!r:<22} | "
              f"{obtenido!r:<22} | {'PASS' if ok else 'FAIL'}")
    for prueba in (test_nunca_devuelve_repr_de_objeto, test_siempre_devuelve_cadena):
        try:
            prueba()
            print(f"\n{prueba.__name__}: PASS")
        except AssertionError as e:
            fallos += 1
            print(f"\n{prueba.__name__}: FAIL {e}")
    print(f"\nresultado: {len(CASOS) - fallos}/{len(CASOS)} casos + invariantes")
    sys.exit(1 if fallos else 0)
