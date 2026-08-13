"""Suite de seguridad clínica del carril determinista.

Protege la propiedad más cara del proyecto: una señal roja expresable con
reglas NO puede depender de que el modelo de lenguaje esté vivo ni acertado.

Origen de los casos: evidencia humana real. En una llamada de prueba el
paciente dijo "También me falta el aire" y el carril determinista de texto lo
clasificó VERDE; el rojo lo salvó el extractor LLM. Si el proveedor hubiera
estado caído, se habría perdido un rojo.

Ejecutable sin dependencias extra:
    python tests/test_seguridad_clinica.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.decision import rules  # noqa: E402

PROC = "apendicectomia"

# ── A-G: dificultad respiratoria y negación ────────────────────────────────
CASOS_TEXTO = [
    # (frase, nivel esperado, etiqueta)
    ("Me falta el aire.", "rojo", "A · canónica"),
    ("También me falta el aire.", "rojo", "B · evidencia humana real"),
    ("Me falta aire.", "rojo", "C · sin artículo"),
    ("No puedo respirar.", "rojo", "D · canónica alternativa"),
    ("Estoy respirando bien.", "verde", "E · control positivo"),
    ("No me falta el aire.", "verde", "F · negación explícita"),
    ("Antes me faltaba el aire pero ya no.", "verde", "G · temporalidad pasada"),

    # Negaciones que no deben alarmar
    ("No tengo dificultad para respirar.", "verde", "negación · dificultad"),
    ("Puedo respirar bien.", "verde", "control · puedo respirar"),
    ("No he tenido sangrado.", "verde", "negación · sangrado"),
    ("No me duele el pecho.", "verde", "negación · dolor torácico"),
    ("Ya no me falta el aire.", "verde", "negación · ya no"),
    ("Nunca he tenido fiebre.", "verde", "negación · nunca"),
    ("Sin sangrado ni fiebre.", "verde", "negación · sin"),

    # Rojas que deben seguir disparando
    ("Me está costando mucho respirar.", "rojo", "dificultad · me cuesta"),
    ("Siento que me ahogo.", "rojo", "dificultad · ahogo"),
    ("Me quedo sin aire al caminar.", "rojo", "dificultad · sin aire"),
    ("Me duele el pecho.", "rojo", "dolor torácico"),
    ("Siento una opresión en el pecho.", "rojo", "dolor torácico · opresión"),
    ("La herida está sangrando mucho.", "rojo", "sangrado activo"),
    ("Me desmayé esta mañana.", "rojo", "pérdida de conciencia"),
    ("Se me abrió la herida.", "rojo", "dehiscencia"),
    ("Tengo fiebre con escalofríos.", "rojo", "signos sépticos"),
    ("Tengo la barriga dura como una tabla.", "rojo", "abdomen rígido"),
]

# ── Regionalismos colombianos de las MISMAS condiciones ────────────────────
CASOS_REGIONALES = [
    ("No me entra el aire.", "rojo", "dificultad respiratoria"),
    ("Me estoy asfixiando.", "rojo", "dificultad respiratoria"),
    ("Me falta la respiración.", "rojo", "dificultad respiratoria"),
    ("Estoy botando mucha sangre.", "rojo", "sangrado activo"),
    ("La herida está botando sangre.", "rojo", "sangrado activo"),
    ("Se me reventaron los puntos.", "rojo", "dehiscencia"),
    ("Me dio un desmayo.", "rojo", "pérdida de conciencia"),
    ("Me siento muy apretado del pecho.", "rojo", "dolor torácico"),
    ("Tengo la barriga como una piedra.", "rojo", "abdomen rígido"),
    ("Estoy temblando de la fiebre.", "rojo", "signos sépticos"),
]

# ── H-I: extracción numérica determinista ──────────────────────────────────
CASOS_NUMERICOS = [
    ("Dolor 3 de 10.", {"dolor_0_10": 3}, "H1 · dolor bajo"),
    ("Ahora es 9 de 10.", {"dolor_0_10": 9}, "H2 · dolor alto"),
    ("Nueve de diez.", {"dolor_0_10": 9}, "dolor en letras"),
    ("El dolor está en nueve.", {"dolor_0_10": 9}, "dolor en letras, otra forma"),
    ("Un dolor de ocho.", {"dolor_0_10": 8}, "dolor ocho"),
    ("Tengo 37.5.", {"temperatura_c": 37.5}, "I1 · temperatura sin fiebre"),
    ("Ahora tengo 39.5.", {"temperatura_c": 39.5}, "I2 · temperatura roja"),
    ("Tengo 39,5 de fiebre.", {"temperatura_c": 39.5}, "coma decimal"),
    ("Treinta y nueve punto cinco.", {"temperatura_c": 39.5}, "temperatura en letras"),
    ("Tengo treinta y ocho de fiebre.", {"temperatura_c": 38.0}, "38 en letras"),
    # Números que NO deben interpretarse como valores clínicos
    ("Tengo 3 hijos.", {}, "número sin contexto clínico"),
    ("Me operaron hace 5 días.", {}, "días, no dolor"),
    ("Tomo la pastilla cada 8 horas.", {}, "horas, no dolor"),
    ("Vivo en el piso 9.", {}, "piso, no dolor"),
]


def nivel(frase, procedimiento=PROC):
    return rules.evaluate_text(frase, procedimiento)["nivel"]


def reglas(frase, procedimiento=PROC):
    return [d["regla"] for d in rules.evaluate_text(frase, procedimiento)["disparos"]]


# ── Pruebas ────────────────────────────────────────────────────────────────
def test_texto_determinista():
    fallos = [(f, esp, nivel(f), etq) for f, esp, etq in CASOS_TEXTO if nivel(f) != esp]
    assert not fallos, "\n".join(f"  {etq}: {f!r} esperado={esp} real={real}"
                                 for f, esp, real, etq in fallos)


def test_regionalismos():
    fallos = [(f, esp, nivel(f), etq) for f, esp, etq in CASOS_REGIONALES if nivel(f) != esp]
    assert not fallos, "\n".join(f"  {etq}: {f!r} esperado={esp} real={real}"
                                 for f, esp, real, etq in fallos)


def test_extraccion_numerica():
    fallos = []
    for frase, esperado, etq in CASOS_NUMERICOS:
        obtenido = rules.extraer_valores(frase)
        for clave, valor in esperado.items():
            if obtenido.get(clave) != valor:
                fallos.append((etq, frase, clave, valor, obtenido.get(clave)))
        for clave in obtenido:
            if clave not in esperado:
                fallos.append((etq, frase, clave, None, obtenido[clave]))
    assert not fallos, "\n".join(
        f"  {e}: {f!r} {c}: esperado={v} real={r}" for e, f, c, v, r in fallos)


def test_numeros_disparan_umbrales():
    """El valor extraído por reglas debe cruzar el umbral SIN pasar por el LLM."""
    assert rules.evaluate_slots(rules.extraer_valores("Ahora es 9 de 10."))["nivel"] == "rojo"
    assert rules.evaluate_slots(rules.extraer_valores("Ahora tengo 39.5."))["nivel"] == "rojo"
    assert rules.evaluate_slots(rules.extraer_valores("Dolor 3 de 10."))["nivel"] == "verde"
    assert rules.evaluate_slots(rules.extraer_valores("Tengo 37.5."))["nivel"] == "verde"


def _resumen():
    total = fallos = 0
    for titulo, casos in (("TEXTO DETERMINISTA", CASOS_TEXTO),
                          ("REGIONALISMOS", CASOS_REGIONALES)):
        print(f"\n{titulo}")
        print("-" * 92)
        for frase, esp, etq in casos:
            real = nivel(frase)
            ok = real == esp
            total += 1
            fallos += not ok
            print(f"  [{'PASS' if ok else 'FAIL'}] {etq:<34} {frase[:38]!r:<40} "
                  f"esp={esp:<8} real={real:<8} {reglas(frase) if real != 'verde' else ''}")
    print("\nEXTRACCIÓN NUMÉRICA DETERMINISTA")
    print("-" * 92)
    for frase, esperado, etq in CASOS_NUMERICOS:
        obtenido = rules.extraer_valores(frase)
        ok = all(obtenido.get(k) == v for k, v in esperado.items()) and \
            all(k in esperado for k in obtenido)
        total += 1
        fallos += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {etq:<34} {frase[:38]!r:<40} "
              f"esp={esperado} real={obtenido}")
    print(f"\nRESULTADO: {total - fallos}/{total}")
    return fallos


if __name__ == "__main__":
    sys.exit(1 if _resumen() else 0)
