"""Carril A del motor de decisión: reglas deterministas.

Este carril NO usa el LLM. Evalúa el texto crudo del paciente y los slots
estructurados contra banderas definidas en config/red_flags.yaml. Por diseño:

- Es inmune a inyección de prompt: una regla regex no obedece instrucciones.
- Nunca puede ser rebajado por el carril LLM (fusión = max de ambos).
- Cada disparo queda registrado con la regla exacta que lo causó (auditable).
"""
from __future__ import annotations

import re
import unicodedata

import yaml

from .. import config

LEVELS = {"verde": 0, "amarillo": 1, "rojo": 2}
LEVEL_NAMES = {v: k for k, v in LEVELS.items()}

_rules_cache: dict | None = None


def _normalize(text: str) -> str:
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


# ── Negación y temporalidad ─────────────────────────────────────────────────
# Ampliar los patrones sin esto convertiría "no tengo dificultad para respirar"
# en una alarma roja. La negación se evalúa sobre el texto que PRECEDE a la
# coincidencia, de modo que un patrón que ya incorpora la negación como parte
# del síntoma ("no puedo respirar", "no orino desde") sigue disparando.
NEGADORES = ("no", "nunca", "jamas", "tampoco", "sin", "ningun", "ninguna",
             "ningunos", "ningunas", "niego", "ni", "nada de", "nada")
SEPARADORES_CLAUSULA = (".", ";", ",", " pero ", " aunque ", " y ")
VENTANA_NEGACION = 45

# La enumeración negativa encadena con "ni" y sobrevive a la coma:
#   "se ve normal, sin enrojecimiento ni secreción, ni mal olor"
# Cortar en la coma dejaba «ni mal olor» fuera del alcance de "sin" y el mal
# olor entraba como signo de infección en un paciente que decía lo contrario.
# La coma solo prolonga el alcance cuando la reanuda un "ni": "no he dormido,
# tengo fiebre" NO debe negar la fiebre.
CONTINUA_NEGACION = re.compile(r",\s*(ni|tampoco)\b")

# Negación POSPUESTA al síntoma: en habla natural el paciente antepone el tema.
#   "escalofríos no he sentido"  ·  "secreción no ha salido"  ·  "fiebre no"
# Deliberadamente exigen un verbo de percepción o tenencia inmediato, para no
# tragarse un "no" que pertenezca a la frase siguiente.
# El `^\w*` absorbe la cola de la palabra que el patrón cortó ("escalofri|os");
# el alcance NO cruza una coma, para que "me duele el pecho, no he tenido
# fiebre" no anule el dolor torácico.
NEGACION_POSPUESTA = (
    r"^\w*\s*(no|nunca|tampoco)\s+(lo |la |los |las |le |me )?"
    r"(he |ha |han |hemos )?(sentido|tenido|notado|visto|presentado|salido|"
    r"aparecido|dado|habido|hay|tengo|siento|noto)",
    r"^\w*\s*(no|nunca|tampoco)\s*[.;!]",
    # El paciente REPITE el término como pregunta y lo niega a continuación:
    #   «Mmm, ¿fiebre? Creo que no.»   «¿Sangrado? Qué va.»
    # Se exige el signo de interrogación pegado al término, para no capturar
    # un "creo que no es nada" que minimiza un síntoma ya afirmado.
    r"^\s*[?¿]+\s*(creo que no|no creo|que va|para nada|no,|no\.|nada|"
    r"no me he sentido|no he tenido)",
)

# Negador DENTRO del tramo coincidente. Los patrones anclados abarcan desde el
# sustantivo hasta el signo ("herida … sin enrojecimiento"), así que el "sin"
# queda dentro de la coincidencia y la comprobación previa no lo ve.
#   Solo se admiten aquí los negadores que nunca forman parte de un síntoma.
#   "no" queda fuera a propósito: "no puedo respirar" y "no orino desde ayer"
#   SON el síntoma, y tratar ese "no" como negación silenciaría dos rojos.
#   "sin" sí entra, pero solo cuando cae en un COMODÍN del patrón. Si el propio
#   patrón escribe "sin" —como en `(no me entra|sin) (el )?aire`—, ese "sin" es
#   parte del síntoma que se busca, no una negación: "me quedo sin aire" es una
#   alarma roja. Por eso `esta_negado` recibe el patrón y descarta los
#   negadores que el patrón declara literalmente.
NEGADORES_INTERNOS = {
    "ni": r"\bni\b",
    "nada de": r"\bnada de\b",
    # "sangra sin parar" intensifica, no niega.
    "sin": r"\bsin\s+(?!parar|control|poder|descanso|mejorar|dejar|fin|remedio|freno)",
}

# Frases de negación EXPLÍCITA. A diferencia del "no" suelto, ninguna de estas
# puede formar parte de un síntoma —no existe un cuadro clínico llamado "no me
# he sentido"—, así que se comprueban siempre, incluso dentro del tramo
# coincidente y aunque el patrón contenga un "no".
#
# Sin esto, una regla de combinación como fiebre+escalofríos disparaba en ROJO
# sobre «¿fiebre? Creo que no, no me he sentido con escalofríos»: el patrón
# abarca las dos palabras y la negación queda en medio.
NEGACIONES_EXPLICITAS = (
    r"\bcreo que no\b", r"\bno creo\b", r"\bque yo sepa no\b",
    r"\bno me he (sentido|notado|dado cuenta)\b",
    r"\bno he (tenido|sentido|notado|visto|presentado)\b",
    r"\bno (he|me he) puesto (asi|mal)\b",
    r"\bno,? (para )?nada\b", r"\bnegativo\b",
)

# Una respuesta que ARRANCA con un negador seco ("No, la herida se ve bien…")
# es una negación de todo lo que sigue hasta el primer adversativo. Sin esto,
# la coma cortaba el "No," y la frase entera se leía en positivo.
NEGADOR_INICIAL = re.compile(r"^\s*(no|nada|nunca|tampoco|ninguno|ninguna)\s*,")

# Cancelaciones explícitas POSTERIORES al síntoma. Deliberadamente estrictas:
# ante ambigüedad clínica se prefiere alarmar. "Me falta el aire, ya no
# aguanto" NO se cancela; "antes me faltaba el aire pero ya no" sí.
CANCELACIONES = (
    r"pero ya no\b", r"\bya no\s*[.!]?\s*$", r"se me (quito|paso|fue|calmo)",
    r"ya se me (quito|paso)", r"ya estoy bien", r"ya no (lo|la|me) (siento|tengo)",
)


def _clausula_previa(norm: str, inicio: int) -> str:
    """Texto anterior a la coincidencia, recortado en el límite de cláusula.

    El recorte se salta las comas que reanudan una enumeración negativa con
    "ni", para que el alcance de un "sin" o un "no" cubra toda la lista.
    """
    ventana = norm[max(0, inicio - VENTANA_NEGACION):inicio]
    corte = -1
    for sep in SEPARADORES_CLAUSULA:
        p = ventana.rfind(sep)
        if sep == "," and p >= 0 and CONTINUA_NEGACION.match(ventana[p:]):
            # La coma continúa la enumeración negativa: busca la anterior.
            p = ventana.rfind(sep, 0, p)
        if p > corte:
            corte = p + len(sep)
    return ventana[corte:] if corte > 0 else ventana


def esta_negado(norm: str, inicio: int, fin: int, patron: str = "") -> bool:
    # Negador seco al inicio del turno: alcanza hasta el primer adversativo.
    m = NEGADOR_INICIAL.match(norm)
    if m and not re.search(r"\b(pero|aunque|sin embargo)\b", norm[m.end():inicio]):
        return True
    tramo = norm[inicio:fin]
    if any(re.search(p, tramo) for p in NEGACIONES_EXPLICITAS):
        return True
    for palabra, expr in NEGADORES_INTERNOS.items():
        if palabra in patron:
            continue  # el patrón lo declara: es parte del síntoma
        if re.search(expr, tramo):
            return True
    previo = _clausula_previa(norm, inicio)
    if any(re.search(rf"\b{n}\b", previo) for n in NEGADORES):
        return True
    cola = norm[fin:fin + 60]
    if any(re.search(p, cola) for p in NEGACION_POSPUESTA):
        return True
    return any(re.search(p, cola) for p in CANCELACIONES)


def load_rules() -> dict:
    global _rules_cache
    if _rules_cache is None:
        with open(config.RED_FLAGS_PATH, encoding="utf-8") as f:
            _rules_cache = yaml.safe_load(f)
    return _rules_cache


def evaluate_text(patient_text: str, procedimiento: str | None = None) -> dict:
    """Evalúa un turno del paciente. Devuelve {"nivel", "disparos": [...]}."""
    rules = load_rules()
    norm = _normalize(patient_text)
    disparos: list[dict] = []

    rule_sets = [("global", rules.get("global", []))]
    if procedimiento:
        proc_key = _match_procedure(procedimiento, rules)
        if proc_key:
            rule_sets.append((proc_key, rules["procedimientos"][proc_key]))

    for scope, rule_list in rule_sets:
        for rule in rule_list or []:
            for pattern in rule.get("patrones", []):
                patron_norm = _normalize(pattern)
                m = re.search(patron_norm, norm)
                if m and not esta_negado(norm, m.start(), m.end(), patron_norm):
                    disparos.append(
                        {
                            "regla": rule["id"],
                            "ambito": scope,
                            "nivel": rule["nivel"],
                            "patron": pattern,
                            # `descripcion` es INTERNA (auditoría, acta, clínicos):
                            # puede contener lenguaje diagnóstico. Nunca se pronuncia.
                            "descripcion": rule.get("descripcion", ""),
                            # `mensaje_paciente` es lo ÚNICO que puede llegar a voz.
                            "mensaje_paciente": rule.get("mensaje_paciente", ""),
                        }
                    )
                    break  # una coincidencia por regla basta

    nivel = max((LEVELS[d["nivel"]] for d in disparos), default=LEVELS["verde"])
    return {"nivel": LEVEL_NAMES[nivel], "disparos": disparos}


def evaluate_slots(slots: dict) -> dict:
    """Evalúa los slots estructurados (extraídos por el carril B) contra
    umbrales numéricos deterministas. El dato lo extrae el LLM, pero el
    umbral lo aplica esta función: el criterio de decisión nunca es del LLM."""
    rules = load_rules()
    thresholds = rules.get("umbrales", {})
    disparos: list[dict] = []

    temp = _as_float(slots.get("temperatura_c"))
    if temp is not None:
        if temp >= thresholds.get("fiebre_rojo_c", 39.0):
            disparos.append(_th("fiebre_alta", "rojo", f"temperatura {temp}°C"))
        elif temp >= thresholds.get("fiebre_amarillo_c", 38.0):
            disparos.append(_th("fiebre", "amarillo", f"temperatura {temp}°C"))

    dolor = _as_float(slots.get("dolor_0_10"))
    if dolor is not None:
        if dolor >= thresholds.get("dolor_rojo", 8):
            disparos.append(_th("dolor_severo", "rojo", f"dolor {dolor}/10"))
        elif dolor >= thresholds.get("dolor_amarillo", 6):
            disparos.append(_th("dolor_moderado", "amarillo", f"dolor {dolor}/10"))

    if slots.get("dolor_tendencia") == "empeorando" and dolor is not None and dolor >= 5:
        disparos.append(_th("dolor_creciente", "amarillo", "dolor en aumento"))

    for flag, nivel in [
        ("sangrado_activo", "rojo"),
        ("dificultad_respiratoria", "rojo"),
        ("dolor_toracico", "rojo"),
        ("herida_pus_o_abierta", "amarillo"),
        ("vomito_persistente", "amarillo"),
        ("no_orina", "amarillo"),
        ("fiebre_reportada", "amarillo"),
    ]:
        if slots.get(flag) is True:
            disparos.append(_th(flag, nivel, f"slot {flag}=true"))

    nivel = max((LEVELS[d["nivel"]] for d in disparos), default=LEVELS["verde"])
    return {"nivel": LEVEL_NAMES[nivel], "disparos": disparos}


# Frase NO diagnóstica que puede pronunciarse ante un umbral disparado.
# Describe lo que el paciente contó, nunca la hipótesis clínica.
MENSAJES_PACIENTE_UMBRAL = {
    "fiebre_alta": "esa fiebre",
    "fiebre": "esa fiebre",
    "dolor_severo": "lo fuerte que es ese dolor",
    "dolor_moderado": "ese dolor",
    "dolor_creciente": "que el dolor le viene aumentando",
    "sangrado_activo": "ese sangrado",
    "dificultad_respiratoria": "que le cueste respirar",
    "dolor_toracico": "esa molestia en el pecho",
    "herida_pus_o_abierta": "cómo está la herida",
    "vomito_persistente": "que no logre retener lo que toma",
    "no_orina": "que no haya podido orinar",
    "fiebre_reportada": "esa fiebre",
}


# ── Extracción numérica determinista ────────────────────────────────────────
# Tercer carril independiente. Hasta ahora, que un "nueve de diez" cruzara el
# umbral de dolor dependía por completo de que el extractor LLM estuviera vivo
# y acertara. Estas expresiones no dependen de ningún proveedor.
_UNIDADES = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
}
_DECENAS = {"treinta": 30, "cuarenta": 40}
_PALABRA_NUM = "|".join(sorted(list(_UNIDADES) + list(_DECENAS), key=len, reverse=True))

# Rango fisiológico plausible de temperatura corporal. Es lo que impide que
# "tengo 3 hijos" o "vivo en el piso 9" se lean como datos clínicos.
TEMP_MIN, TEMP_MAX = 35.0, 42.5


def _valor(txt: str) -> float | None:
    txt = txt.strip()
    if not txt:
        return None
    try:
        return float(txt.replace(",", "."))
    except ValueError:
        pass
    if txt in _UNIDADES:
        return float(_UNIDADES[txt])
    if txt in _DECENAS:
        return float(_DECENAS[txt])
    return None


def _temperatura_en_letras(norm: str) -> float | None:
    """"treinta y nueve punto cinco" -> 39.5 ; "treinta y ocho" -> 38.0"""
    m = re.search(
        rf"\b({'|'.join(_DECENAS)})(?:\s+y\s+({'|'.join(_UNIDADES)}))?"
        rf"(?:\s+(?:punto|coma)\s+({'|'.join(_UNIDADES)}))?\b", norm)
    if not m:
        return None
    total = float(_DECENAS[m.group(1)])
    if m.group(2):
        total += _UNIDADES[m.group(2)]
    if m.group(3):
        total += _UNIDADES[m.group(3)] / 10
    return total if TEMP_MIN <= total <= TEMP_MAX else None


def extraer_valores(texto: str, pregunta_previa: str = "") -> dict:
    """Slots numéricos deducidos SOLO con reglas, sin LLM.

    Exige contexto: un número suelto no es un dato clínico. "Vivo en el piso
    9" o "cada 8 horas" no deben convertirse en un dolor de 9/10.

    El contexto puede venir de la PREGUNTA, no solo de la respuesta. A
    "¿se tomó la temperatura?" el paciente contesta "un poquito nada más,
    38.4" — sin decir "fiebre" ni "grados". Exigir la palabra en su turno hacía
    que una fiebre de 38.4 —criterio de urgencia explícito en los planes de
    cuidado— se perdiera entera. Es el mismo problema de referencia que ya
    resuelve el carril de composición, aplicado aquí.
    """
    norm = _normalize(texto)
    norm_contexto = norm + " " + _normalize(pregunta_previa or "")
    slots: dict = {}

    # Temperatura: cifra dentro del rango fisiológico y con contexto corporal.
    # Se recorren TODAS las cifras, no solo la primera: "me la tomé a las 10 y
    # marcaba 38.4" tiene dos números y el clínico es el segundo. Cada
    # candidata debe además sobrevivir a la descalificación por unidad, para
    # que "tengo 38 años" no se convierta en una fiebre de 38 grados.
    if re.search(CONTEXTO_TEMPERATURA, norm_contexto):
        for m in re.finditer(r"\b(\d{2}(?:[.,]\d)?)\b", norm):
            v = _valor(m.group(1))
            if v is None or not (TEMP_MIN <= v <= TEMP_MAX):
                continue
            if _descalificado(norm, m.end()) or esta_negado(norm, m.start(), m.end()):
                continue
            slots["temperatura_c"] = v
            break
    if "temperatura_c" not in slots:
        v = _temperatura_en_letras(norm)
        if v is not None and re.search(
                r"\b(fiebre|calentura|temperatura|grados?|tengo|punto|coma)\b", norm):
            slots["temperatura_c"] = v

    dolor = _extraer_dolor(norm)
    if dolor is None and re.search(r"(dolor|duele|del 0 al 10|de 0 a 10|escala)",
                                   _normalize(pregunta_previa or "")):
        # Contestando a "¿qué tan fuerte, de 0 a 10?", una cifra suelta ES la
        # escala. Se exige que sea 0-10, que no lleve una unidad detrás y que
        # NO sea parte de un decimal: "marcó como 36.8" leía el 8 como un dolor
        # de 8/10 y convertía una temperatura normal en una alarma roja.
        for m in re.finditer(r"(?<![\d.,])\b(\d{1,2})\b(?![.,]\d)", norm):
            v = _valor(m.group(1))
            if v is not None and 0 <= v <= 10 and not _descalificado(
                    norm, m.end(), _UNIDADES_NO_CLINICAS_DOLOR):
                dolor = v
                break
    if dolor is not None:
        slots["dolor_0_10"] = dolor

    return slots


# Palabras que ponen una cifra en terreno de temperatura corporal. El listado
# anterior exigía "tengo|marca|grados|fiebre" con límites de palabra, y dejaba
# fuera las formas que la gente usa de verdad: "me la TOMÉ y estaba en 38.2",
# "MARCABA como 38", "me SUBIÓ a 39". Ninguna de esas se extraía, y la fiebre
# —criterio de urgencia explícito en los planes de cuidado— se perdía.
CONTEXTO_TEMPERATURA = (
    r"(fiebre|calentura|febril|temperatura|termometro|grado|"
    r"\btom[eoáa]\w*|\bmarca\w*|\bmarco\b|\bsubi\w*|\bllego a\b|\bdio\b|"
    r"\bsalio\b|\bestaba en\b|\bestoy en\b|\bteng\w*|\bteni\w*|\btuv\w*)"
)

# Unidades que descalifican un número: no es una escala de dolor.
_UNIDADES_NO_CLINICAS = (
    "hora", "horas", "dia", "dias", "semana", "semanas", "mes", "meses",
    "ano", "anos", "minuto", "minutos", "vez", "veces", "hijo", "hijos",
    "pastilla", "pastillas", "punto", "puntos",
    "piso", "cuadra", "cuadras", "kilo", "kilos", "litro", "litros",
)

# "grados" NO descalifica una temperatura —es justo lo contrario—, así que se
# excluye de la lista de arriba y se mantiene solo para el dolor.
_UNIDADES_NO_CLINICAS_DOLOR = _UNIDADES_NO_CLINICAS + ("grado", "grados")


def _descalificado(norm: str, fin: int, unidades=None) -> bool:
    """¿Al número le sigue una unidad que lo saca del terreno clínico?"""
    cola = norm[fin:fin + 22]
    return any(re.match(rf"\s*{u}\b", cola) for u in (unidades or _UNIDADES_NO_CLINICAS))


def _extraer_dolor(norm: str) -> float | None:
    """Escala 0-10 de dolor, en cifra o en palabra.

    Exige contexto y descarta unidades. El artículo indefinido se excluye de
    las escalas: "como un cinco" son 5, no 1 — el patrón anterior enganchaba
    "un" y devolvía 1.0, error detectado con el dataset oficial en cuatro
    conversaciones rojas.
    """
    escala = "|".join(sorted(_UNIDADES, key=len, reverse=True))
    # Palabras-número válidas como VALOR de escala: sin artículos.
    valor_num = "|".join(sorted(
        [p for p in _UNIDADES if p not in ("un", "uno", "una")], key=len, reverse=True))

    candidatos = [
        # 1. "nueve de diez", "6 sobre 10", "5/10"
        rf"\b(\d{{1,2}}|{escala})\s*(?:de|sobre|/|entre)\s*(?:10|diez)\b",
        # 2. "como un cinco", "como cinco", "como de seis"
        rf"\bcomo\s+(?:un[oa]?\s+|de\s+)?(\d{{1,2}}|{valor_num})\b",
        # 3. "un dolor de ocho", "dolor de unos siete", "dolor en nueve"
        rf"\bdolor\b[^.]{{0,20}}?\b(?:de|en|como)\s+(?:un[oa]?s?\s+)?(\d{{1,2}}|{valor_num})\b",
        # 4. "dolor ... 6" a corta distancia. La ventana llega a 40 porque el
        # paciente intercala la localización antes de dar la cifra: "algo de
        # dolor ahí en la cadera, será un 5". Con 18 caracteres esa forma —muy
        # frecuente— se perdía por completo.
        rf"\bdolor\b[^.]{{0,40}}?\b(?:un[oa]?s?\s+)?(\d{{1,2}}|{valor_num})\b",
        # 5. "ocho de dolor"
        rf"\b(\d{{1,2}}|{valor_num})\b[^.]{{0,12}}?\bde dolor\b",
    ]
    for patron in candidatos:
        for m in re.finditer(patron, norm):
            if _descalificado(norm, m.end(1), _UNIDADES_NO_CLINICAS_DOLOR):
                continue
            v = _valor(m.group(1))
            if v is not None and 0 <= v <= 10:
                return v
    return None


def _th(regla: str, nivel: str, detalle: str) -> dict:
    return {
        "regla": f"umbral:{regla}",
        "ambito": "slots",
        "nivel": nivel,
        "patron": detalle,
        "descripcion": "Umbral determinista sobre dato estructurado",
        "mensaje_paciente": MENSAJES_PACIENTE_UMBRAL.get(regla, ""),
    }


def _match_procedure(procedimiento: str, rules: dict) -> str | None:
    """Empareja el procedimiento del perfil con una clave de `procedimientos`.

    Antes comparaba la clave literal contra el texto, y "Reemplazo de
    cadera/rodilla" no contiene la cadena "reemplazo_rodilla": el paciente se
    quedaba solo con las reglas globales. Ahora manda la tabla de alias del
    YAML, editable por personal clínico sin tocar código.
    """
    norm = _normalize(procedimiento)
    if not norm:
        return None
    bloques = rules.get("procedimientos") or {}
    alias = rules.get("alias_procedimientos") or {}
    for key, nombres in alias.items():
        if key not in bloques:
            continue
        if any(_normalize(a) in norm for a in (nombres or [])):
            return key
    # Respaldo: la clave con guiones bajos convertidos en espacios.
    for key in bloques:
        suelto = _normalize(key).replace("_", " ")
        if suelto in norm or norm in suelto:
            return key
    return None


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

