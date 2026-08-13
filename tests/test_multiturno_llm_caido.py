"""Seguridad clínica a lo largo de la llamada, y con el modelo caído.

Dos propiedades que ninguna prueba de un solo turno puede cubrir:

  1. NEVER DOWNGRADE TEMPORAL — un rojo alcanzado en cualquier momento fija
     la criticidad de la llamada, aunque el paciente mejore después.
  2. INDEPENDENCIA DEL PROVEEDOR — una señal roja expresable con reglas se
     detecta aunque el modelo devuelva basura, tarde o no responda.

Se sustituye el extractor por dobles: estas pruebas no consumen API.

    python tests/test_multiturno_llm_caido.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.conversation.orchestrator import CallSession  # noqa: E402
from app.decision import assess, engine, rules  # noqa: E402

_EXTRACTOR_REAL = assess.extract_slots


def _extractor(nivel="verde", **slots):
    """Doble del extractor LLM que devuelve lo que se le indique."""
    def f(patient_text, contexto, historial=None):
        base = assess._empty_slots()
        base["nivel_sugerido"] = nivel
        base.update(slots)
        return assess._merge_slots(historial or {}, base), {"provider": "doble"}
    return f


def _extractor_caido(excepcion=RuntimeError("proveedor caido")):
    """Reproduce la degradación real: assess captura el fallo y devuelve
    slots vacíos, que es lo que ocurre con timeout, 429 o key inválida."""
    def f(patient_text, contexto, historial=None):
        try:
            raise excepcion
        except Exception:
            return assess._merge_slots(historial or {}, assess._empty_slots()), \
                {"provider": "none", "input_tokens": 0, "output_tokens": 0}
    return f


def _sesion():
    s = CallSession()
    s.saludo_inicial()
    return s


# ── H · escalada de dolor entre turnos ─────────────────────────────────────
def test_escalada_de_dolor():
    assess.extract_slots = _extractor()
    try:
        s = _sesion()
        r1 = s.turno("Dolor 3 de 10.")
        assert s.slots.get("dolor_0_10") == 3, s.slots.get("dolor_0_10")
        assert r1["nivel_turno"] == "verde"
        r2 = s.turno("Ahora es 9 de 10.")
        assert s.slots.get("dolor_0_10") == 9, f"el valor nuevo no reemplazó: {s.slots}"
        assert r2["nivel_turno"] == "rojo", r2["nivel_turno"]
    finally:
        assess.extract_slots = _EXTRACTOR_REAL


# ── I · escalada de temperatura entre turnos ───────────────────────────────
def test_escalada_de_temperatura():
    assess.extract_slots = _extractor()
    try:
        s = _sesion()
        s.turno("Tengo 37.5.")
        assert s.slots.get("temperatura_c") == 37.5, s.slots.get("temperatura_c")
        r2 = s.turno("Ahora tengo 39.5.")
        assert s.slots.get("temperatura_c") == 39.5, f"no se actualizó: {s.slots}"
        assert r2["nivel_turno"] == "rojo", r2["nivel_turno"]
    finally:
        assess.extract_slots = _EXTRACTOR_REAL


# ── NEVER DOWNGRADE temporal ───────────────────────────────────────────────
def test_riesgo_historico_no_baja():
    assess.extract_slots = _extractor()
    try:
        s = _sesion()
        s.turno("Ahora es 9 de 10.")
        assert s.nivel_max == "rojo"
        s.turno("Ya casi no me duele, como 2 de 10.")
        assert s.slots.get("dolor_0_10") == 2, "el estado ACTUAL debe reflejar la mejora"
        assert s.slots.get("dolor_0_10_max") == 9, "el máximo histórico debe conservarse"
        assert s.nivel_max == "rojo", "la criticidad de la llamada no puede bajar"
        r = s.turno("Todo bien, gracias.")
        assert r["semaforo"] == "rojo"
        acta = s.finalizar()
        assert acta["criticidad_final"] == "rojo"
        assert acta["decision"]["escalado"] is True
    finally:
        assess.extract_slots = _EXTRACTOR_REAL


def test_banderas_de_alarma_no_se_apagan():
    assess.extract_slots = _extractor(sangrado_activo=True)
    try:
        s = _sesion()
        s.turno("Estoy sangrando mucho.")
        assert s.slots["sangrado_activo"] is True
        assess.extract_slots = _extractor(sangrado_activo=False)
        s.turno("Ya me siento mejor.")
        assert s.slots["sangrado_activo"] is True, "una alarma no se apaga sola"
    finally:
        assess.extract_slots = _EXTRACTOR_REAL


# ── J · el proveedor cae y la señal roja sobrevive ─────────────────────────
FRASES_ROJAS = [
    "Me falta el aire.", "También me falta el aire.", "Me duele el pecho.",
    "La herida está sangrando mucho.", "Se me abrió la herida.",
    "Me desmayé esta mañana.", "Tengo fiebre con escalofríos.",
    "No me entra el aire.", "Estoy botando mucha sangre.",
    "Tengo la barriga como una piedra.",
]
FRASES_ROJAS_NUMERICAS = ["Ahora es 9 de 10.", "Tengo 39.5 de fiebre.",
                          "El dolor está en nueve.", "Treinta y nueve punto cinco."]


def test_llm_caido_conserva_rojo():
    assess.extract_slots = _extractor_caido()
    try:
        fallos = []
        for frase in FRASES_ROJAS + FRASES_ROJAS_NUMERICAS:
            d = engine.decide(frase, "apendicectomia", "Paciente de prueba", {})
            if d["nivel_final"] != "rojo":
                fallos.append((frase, d["nivel_final"], d["niveles"]))
        assert not fallos, "\n".join(f"  {f!r} -> {n} {niv}" for f, n, niv in fallos)
    finally:
        assess.extract_slots = _EXTRACTOR_REAL


def test_llm_caido_varios_modos():
    """Timeout, 429, key inválida y excepción genérica: misma garantía."""
    modos = [TimeoutError("timeout"), RuntimeError("429 rate limit"),
             PermissionError("invalid api key"), ValueError("respuesta corrupta")]
    fallos = []
    for exc in modos:
        assess.extract_slots = _extractor_caido(exc)
        try:
            d = engine.decide("Me falta el aire.", "apendicectomia", "ctx", {})
            if d["nivel_final"] != "rojo":
                fallos.append((type(exc).__name__, d["nivel_final"]))
        finally:
            assess.extract_slots = _EXTRACTOR_REAL
    assert not fallos, fallos


# ── K · el modelo dice VERDE contra una regla roja ─────────────────────────
def test_llm_verde_no_rebaja():
    assess.extract_slots = _extractor(nivel="verde")
    try:
        for frase in FRASES_ROJAS:
            d = engine.decide(frase, "apendicectomia", "ctx", {})
            assert d["nivel_final"] == "rojo", (frase, d["niveles"])
            assert d["niveles"]["carril_llm"] == "verde"
    finally:
        assess.extract_slots = _EXTRACTOR_REAL


if __name__ == "__main__":
    pruebas = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fallos = 0
    for p in pruebas:
        try:
            p()
            print(f"  [PASS] {p.__name__}")
        except AssertionError as e:
            fallos += 1
            print(f"  [FAIL] {p.__name__}\n         {e}")
        except Exception as e:
            fallos += 1
            print(f"  [ERROR] {p.__name__}: {type(e).__name__}: {e}")
    print(f"\nRESULTADO: {len(pruebas) - fallos}/{len(pruebas)}")
    sys.exit(1 if fallos else 0)
