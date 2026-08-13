"""Barrera de salida: lo que el agente NO puede afirmar sin evidencia.

Evidencia humana que motiva este módulo. En llamadas reales RONDA dijo:

    "[FUENTE: Protocolo de apendicectomía laparoscópica]"   con citas = []
    "según su historial, es común tener una recuperación estable"
    "el procedimiento fue exitoso"
    "de acuerdo con su historial, se le recetó paracetamol"

Ninguna de esas afirmaciones tenía una sola consulta al corpus detrás. La
última es la más grave: **medicación inventada**.

Un prompt no es una garantía: es una petición. Esto sí lo es, porque actúa
sobre el texto ya generado, después del modelo y antes del TTS y del acta.

Alcance deliberadamente acotado: aquí NO se implementa CITE OR ABSTAIN
completo —eso exige rediseñar el flujo RAG—, sino que se impide que una cita
falsa o una prescripción inventada lleguen al paciente.
"""
from __future__ import annotations

import re
import unicodedata

from .. import observability

# Marcadores de cita que el modelo puede fabricar imitando el formato real.
# Las citas legítimas viajan como objetos `citas`, nunca dentro del texto.
_MARCADOR_CITA = re.compile(
    r"\[\s*(fuente|source|ref|referencia|doc(?:umento)?)\b[^\]]*\]",
    re.IGNORECASE,
)

# Afirmaciones que exigen evidencia estructurada. Se evalúan por ORACIÓN: una
# oración que se apoya en una fuente inexistente es insostenible entera, así
# que se retira completa. Recortar fragmentos producía español roto
# ("El no tengo el detalle...") y dejaba a medias la afirmación.
_SIN_EVIDENCIA = [
    ("apela_a_historia_clinica",
     re.compile(r"(seg[uú]n|de acuerdo con|conforme a|revisando)\s+(su|sus|el|la|los|las)?\s*"
                r"(historial|historia|expediente|registros?|ficha)", re.IGNORECASE)),
    ("apela_a_protocolos",
     re.compile(r"(seg[uú]n|de acuerdo con|conforme a)\s+(nuestros?|los?|las?|el|mis)\s+"
                r"(protocolos?|gu[ií]as?|est[aá]ndares?|registros?)", re.IGNORECASE)),
    ("afirma_exito_quirurgico",
     re.compile(r"\b(cirug[ií]a|procedimiento|operaci[oó]n|intervenci[oó]n)\b[^.]{0,40}?"
                r"\b(fue|result[oó]|sali[oó])\b[^.]{0,20}?"
                r"\b(exitosa?|exitoso|bien|satisfactori\w+|sin complicaciones)\b",
                re.IGNORECASE)),
    ("afirma_prescripcion",
     re.compile(r"\b(le|se le|te)\s+(recet\w+|formul\w+|prescrib\w+|mandaron|indicaron)",
                re.IGNORECASE)),
    ("indica_dosis",
     re.compile(r"\b(debe|puede|tiene que|tómese|tome)\b[^.]{0,30}?"
                r"\b(cada\s+\d+\s*(horas?|h)|\d+\s*(mg|gramos?|ml))", re.IGNORECASE)),
]

_FRASE_SIN_SUSTENTO = ("Eso no lo tengo confirmado en mis protocolos y prefiero no "
                       "adivinar; lo dejo anotado para la enfermera.")

# Fármacos frecuentes en postoperatorio. Nombrarlos sin evidencia recuperada
# es prescribir de memoria del modelo.
_MEDICAMENTOS = (
    "acetaminofen", "paracetamol", "ibuprofeno", "diclofenaco", "naproxeno",
    "dipirona", "metamizol", "tramadol", "codeina", "morfina", "ketorolaco",
    "amoxicilina", "cefalexina", "ciprofloxacina", "metronidazol", "omeprazol",
    "dexametasona", "prednisona", "aspirina", "acido acetilsalicilico",
)
_FRASE_LIMITE = ("Prefiero no darle datos de medicación que no tenga confirmados; "
                 "la enfermera se los precisa hoy mismo.")


def _sin_tildes(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _limpiar_espacios(texto: str) -> str:
    texto = re.sub(r"\s{2,}", " ", texto).strip()
    texto = re.sub(r"\s+([,.;:!?])", r"\1", texto)
    texto = re.sub(r"^[,;:.\s]+", "", texto)
    # Recapitaliza si el recorte dejó la frase empezando en minúscula.
    return texto[:1].upper() + texto[1:] if texto else texto


def _oraciones(texto: str) -> list[str]:
    partes = re.split(r"(?<=[.!?])\s+", texto.strip())
    return [p for p in partes if p.strip()]


def _menciona_medicamento(oracion: str) -> list[str]:
    plano = _sin_tildes(oracion)
    return [m for m in _MEDICAMENTOS if re.search(rf"\b{m}\w*\b", plano)]


def sanear_respuesta(texto: str, citas: list[dict] | None,
                     session_id: str = "", turno: int = 0) -> str:
    """Elimina del texto lo que no puede sostenerse con la evidencia del turno.

    `citas` es la evidencia REAL devuelta por el retriever. Si está vacía, el
    agente no puede invocar fuentes, protocolos, historia clínica ni fármacos.

    Se trabaja por oración: una afirmación sin sustento se retira entera, de
    modo que lo que queda siempre es español correcto y sostenible.
    """
    original = texto
    infracciones: list[str] = []
    hay_evidencia = bool(citas)

    # 1. Marcadores de cita fabricados por el modelo. Se eliminan SIEMPRE:
    #    las citas verdaderas viajan como objetos y se muestran aparte, nunca
    #    salen del texto libre del modelo.
    if _MARCADOR_CITA.search(texto):
        infracciones.append("cita_fabricada")
        texto = _limpiar_espacios(_MARCADOR_CITA.sub("", texto))

    conservadas: list[str] = []
    medicacion_retirada = False
    for oracion in _oraciones(texto):
        motivo = None
        if not hay_evidencia:
            for etiqueta, patron in _SIN_EVIDENCIA:
                if patron.search(oracion):
                    motivo = etiqueta
                    break
            if motivo is None:
                farmacos = _menciona_medicamento(oracion)
                if farmacos:
                    motivo = "medicacion_sin_evidencia:" + ",".join(farmacos[:3])
                    medicacion_retirada = True
        if motivo:
            infracciones.append(motivo)
            continue
        conservadas.append(oracion)

    texto = _limpiar_espacios(" ".join(conservadas))
    if medicacion_retirada:
        texto = _limpiar_espacios(f"{texto} {_FRASE_LIMITE}")
    elif not texto and infracciones:
        # Todo el turno se apoyaba en algo insostenible: se declara el límite
        # en lugar de quedarse mudo.
        texto = _FRASE_SIN_SUSTENTO

    if infracciones:
        observability.log_event({
            "tipo": "respuesta_saneada",
            "session_id": session_id,
            "turno": turno,
            "infracciones": infracciones,
            "tenia_evidencia": hay_evidencia,
            "caracteres_antes": len(original),
            "caracteres_despues": len(texto),
        })
    return texto or original
