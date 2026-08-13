# -*- coding: utf-8 -*-
"""Cobertura de la evaluación: qué se logró preguntar y qué quedó sin saber.

EL PROBLEMA QUE RESUELVE
------------------------
Hasta ahora el motor solo distinguía dos cosas: hay señal / no hay señal. Eso
mete en el mismo saco dos situaciones clínicamente opuestas:

    "No he tenido fiebre"           → el paciente evaluó y descartó fiebre
    (nunca se preguntó por fiebre)  → no sabemos nada de su temperatura

Ambas producían "sin señal de temperatura", y por tanto verde. La segunda no
es tranquilidad clínica: es ausencia de información presentada como si fuera
tranquilidad. En un seguimiento telefónico, donde la mitad de las llamadas se
cortan, el paciente se distrae o el reconocimiento de voz falla, ese error es
sistemático, no anecdótico.

TRES ESTADOS, NO DOS
--------------------
    PRESENTE  — se observó un hallazgo en el dominio
    AUSENTE   — se preguntó y el paciente lo descartó ("la herida se ve bien")
    DESCONOCIDO — no se preguntó, o la respuesta no fue interpretable
    FALLO     — se preguntó y la respuesta se perdió (silencio, inaudible,
                el paciente esquiva la pregunta)

FALLO es un subtipo de DESCONOCIDO que sí distingue culpa: en FALLO la llamada
intentó cubrir el dominio y no pudo, y por eso es lo que dispara la repregunta.

SEPARACIÓN DE RESPONSABILIDADES
-------------------------------
Este módulo NO decide criticidad. Lo desconocido no suma severidad: un dominio
sin evaluar no es un síntoma. El motor de composición trabaja solo con
evidencia observada; la cobertura trabaja solo con lo que falta. Se juntan en
un único punto —la compuerta de verde de `engine.decide`— y con una única
consecuencia: si no hay alarma pero faltan dominios críticos, la llamada no
puede cerrarse en verde.

DE DÓNDE SALEN LOS DOMINIOS CRÍTICOS
------------------------------------
De la intersección entre el checklist que la FSM ya recorre
(`orchestrator.CHECKLIST`) y las listas de signos de alarma de los planes de
cuidado oficiales. No se inventa ninguno:

    dolor       — checklist; "Dolor abdominal que no mejora o que va
                  aumentando" (plan de apendicectomía), "Aumento del dolor"
                  (plan de reemplazo articular)
    temperatura — checklist ("fiebre o calentura"); "Fiebre mayor de 38 °C"
                  aparece en TODOS los planes del corpus
    herida      — checklist; "Enrojecimiento, calor, sangrado o salida de pus
                  en la herida quirúrgica" y "Apertura de la herida"

Movilidad, alimentación y medicación siguen en el checklist y se registran en
la cobertura, pero no bloquean el verde: son calidad de cuidado, no criterios
de urgencia en las guías. `dificultad respiratoria` sí está en todas las listas
oficiales, pero el agente no la pregunta de forma rutinaria, así que exigirla
convertiría cada llamada en incompleta; se detecta si el paciente la menciona.
"""
from __future__ import annotations

import re

from . import rules

PRESENTE = "presente"
AUSENTE = "ausente"
DESCONOCIDO = "desconocido"
FALLO = "fallo_de_evaluacion"

# Precedencia al fusionar turnos: una vez observado, no se degrada.
_ORDEN = {DESCONOCIDO: 0, FALLO: 1, AUSENTE: 2, PRESENTE: 3}

# ── Dos preguntas distintas, dos respuestas distintas ───────────────────────
#   ¿SE EVALUÓ el dominio?    → assessed
#   ¿SE ENCONTRÓ un hallazgo? → positive
#
# Confundirlas producía actas que contradecían la traza. Con 37,5 °C el
# dominio quedaba marcado "presente" —porque hay un dato— mientras el motor
# clínico no generaba ninguna señal, porque su umbral de febrícula es 37,8.
# El acta decía "evaluado positivo: temperatura" y la traza no mostraba nada.
#
# Ahora se registran por separado. Una medición dentro de lo normal es
# `assessed=true, positive=false, valor=37.5`: información obtenida, sin
# hallazgo. Los umbrales clínicos NO se tocan — el problema era de
# representación, no de criterio.
def _ficha(estado: str, motivo: str, hablante: str, valor=None) -> dict:
    return {
        "estado": estado,
        "assessed": estado in (PRESENTE, AUSENTE),
        "positive": estado == PRESENTE,
        "motivo": motivo,
        "fuente_hablante": hablante,
        **({"valor": valor} if valor is not None else {}),
    }

DOMINIOS_CRITICOS = ("dolor", "temperatura", "herida")
DOMINIOS_SEGUIDOS = DOMINIOS_CRITICOS + ("alimentacion", "movilidad", "medicacion")

# Vocabulario con el que se reconoce que el paciente HABLÓ de un dominio,
# aunque no haya hallazgo. Es lo que permite leer una negación como evaluación
# negativa en vez de como silencio.
VOCABULARIO: dict[str, str] = {
    "dolor": r"(dolor|duele|duelen|dolia|molest|adolorid)",
    # Incluye los términos con los que el propio agente formula la pregunta
    # ("¿ha sentido escalofríos, sudoración o calor en el cuerpo?"): el
    # paciente contesta con esas palabras, no con "temperatura".
    "temperatura": r"(fiebre|calentura|febril|temperatura|termometro|grados|destemplad|"
                   r"acalorad|escalofri|tibi|sudoracion|sudor|calor en el cuerpo)",
    "herida": r"(herida|corte|puntos|incision|cicatriz|venda|aposito|curacion|"
              r"pus|secrecion|supura)",
    "alimentacion": r"(comer|comida|comido|apetito|hambre|alimenta|liquidos|vomit|"
                    r"nausea|deposicion|obrar|bano|gases)",
    "movilidad": r"(camin|mover|moverme|levantar|pararme|andador|baston|de pie)",
    "medicacion": r"(pastilla|medicament|droga|acetaminofen|analgesic|antibiotic|"
                  r"tratamiento|formulad)",
}

# Respuestas que NO responden. Ninguna se copió del dataset: son las formas en
# que una llamada telefónica pierde información.
NO_RESPUESTA = (
    r"^\s*[.\-…\s]*$",                      # silencio transcrito
    r"\[inaudible\]",
    r"^\s*(eh+|mmm+|este\.{0,3})\s*[.,]?\s*$",
    r"\bno (le )?(entend|escuch|oig|oi)\w*",
    r"\bque dijo\b|\bcomo dice\b|\bmande\b|\brepitame\b|\bno le copio\b",
    r"\bno (se|sabria)\b[^.]{0,20}$",
    r"\bsiga con (la|el) (otra|siguiente|otro)\b",
    r"\bno,? nada,? siga\b",
    r"\bse (corto|cayo) la llamada\b",
    r"\bahorita no puedo\b|\bestoy ocupad\w*\b|\bllame (mas tarde|despues)\b",
)

# El paciente contesta que ese dominio está bien. Es evaluación NEGATIVA, no
# desconocimiento: cuenta como cobertura lograda.
NORMALIDAD = (
    r"\b(bien|normal|normalit\w*|tranquil\w*|sin (problema|novedad|nada)|"
    r"todo bien|nada raro|nada rar\w*|para nada|nada del otro mundo|"
    r"igual que siempre|como siempre|estable|mejorando|va mejor)\b",
)

# Una respuesta que abre con un negador seco ES la respuesta a la pregunta:
# "¿ha sentido escalofríos?" → "No, para nada, a lo mucho tibio". Eso es una
# evaluación negativa, no una pérdida de información. Si además hubiera un
# hallazgo ("No, doctor, pero se me abrió la herida"), el paso de señales ya
# lo marcó como PRESENTE y la precedencia lo conserva.
RESPUESTA_NEGATIVA = re.compile(r"^\s*(ay\s+|pues\s+|uy\s+|eh\s+)*"
                                r"(no|nada|nunca|ninguno|ninguna|negativo)\b")


# El paciente dice, explícitamente, que NO SABE. Es la diferencia entre
# "no tengo fiebre" y "no me he puesto a pensar en eso": la primera es una
# evaluación, la segunda es un hueco. Sin esta lista, cualquier respuesta que
# no encajara en un patrón conocido se daba por perdida, y respuestas
# perfectamente claras ("un tresito y con la pastilla se me quita") contaban
# como información no obtenida.
EVASION = (
    r"\bno (lo )?se\b|\bno sabria\b|\bni idea\b|\bquien sabe\b|\bya ni (se|sabe)\b",
    r"\bni sabe\b|\bno sabe uno\b",
    r"\bno me he (puesto a )?(fijad|fijar|pensar|mirar|revisar|dado cuenta)",
    r"\bno le he puesto (mucha )?(atencion|cuidado)",
    r"\bno me he tomado la temperatura\b|\bno tengo termometro\b",
    r"\bno (me gusta|quiero) (mirar|ver|destapar)",
    r"\bno (la |lo )?he (mirado|visto|revisado|destapado)\b",
    r"\bno (he )?puesto atencion\b|\bno me fije\b",
)

# Marcas de ruido de transcripción. Se retiran antes de juzgar si el turno
# tiene contenido: "Ninguno [inaudible] nada" SÍ responde a la pregunta.
RUIDO_STT = re.compile(r"\[inaudible\]|\[ruido\]|\[silencio\]|-{2,}|\.{3,}")

# El turno es ÍNTEGRAMENTE una pregunta de vuelta: el paciente devuelve la
# pelota en vez de contestar. Se exige que sea todo el turno; una respuesta
# que contesta y luego pregunta ("...bien, ¿usted cree que es normal?") sí
# cubre el dominio.
DEVUELVE_PREGUNTA = re.compile(
    r"^\s*[¿]?\s*(que|como|cuando|cuanto|donde|por que|usted|y usted|me repite|"
    r"cual|quien)\b[^.!]*\?\s*$")


def es_no_respuesta(texto: str) -> bool:
    """¿Este turno perdió la información que se le pidió?"""
    if not texto or not any(c.isalnum() for c in texto):
        return True
    norm = rules._normalize(texto)
    if any(re.search(p, norm) for p in EVASION):
        return True
    # Se juzga sobre el texto SIN las marcas de ruido: un turno con una
    # palabra perdida sigue siendo una respuesta.
    limpio = RUIDO_STT.sub(" ", norm).strip()
    if not any(c.isalnum() for c in limpio):
        return True
    return any(re.search(p, limpio) for p in NO_RESPUESTA)


def _afirma_normalidad(norm: str) -> bool:
    return any(re.search(p, norm) for p in NORMALIDAD)


def _domina_negado(norm: str, dominio: str) -> bool:
    """¿El paciente nombró el dominio para descartarlo?"""
    patron = VOCABULARIO.get(dominio)
    if not patron:
        return False
    for m in re.finditer(patron, norm):
        if rules.esta_negado(norm, m.start(), m.end()):
            return True
    return False


def _menciona(norm: str, dominio: str) -> bool:
    patron = VOCABULARIO.get(dominio)
    return bool(patron and re.search(patron, norm))


def observar_turno(texto: str, pregunta_previa: str = "", hablante: str = "paciente",
                   señales: list[dict] | None = None,
                   valores: dict | None = None) -> dict[str, dict]:
    """Estado de cobertura que aporta ESTE turno. No decide criticidad."""
    from . import composicion  # import local: evita ciclo entre módulos

    norm = rules._normalize(texto or "")
    valores = valores or {}
    observado: dict[str, dict] = {}

    def anotar(dominio, estado, motivo, valor=None):
        previo = observado.get(dominio)
        if previo is None or _ORDEN[estado] > _ORDEN[previo["estado"]]:
            observado[dominio] = _ficha(estado, motivo, hablante, valor)
        elif valor is not None and "valor" not in previo:
            # El dato numérico se conserva aunque no cambie el estado: es
            # información útil para el acta ("se midió 37.5") aunque el
            # dominio ya estuviera marcado por otra vía.
            previo["valor"] = valor

    # 1) HALLAZGO observado por el motor clínico → assessed + positive.
    #    La fuente de verdad de qué es un hallazgo es la composición, no este
    #    módulo: así el acta no puede contradecir a la traza.
    for s in (señales or []):
        if s.get("dominio") in DOMINIOS_SEGUIDOS:
            anotar(s["dominio"], PRESENTE, "hallazgo observado por el motor clínico")

    # 2) CIFRA MEDIDA → el dominio se evaluó, con dato. Que la cifra sea o no
    #    un hallazgo YA lo decidió el motor clínico en el paso 1: si generó
    #    señal, el dominio quedó `positive`; si no, aquí queda `assessed` sin
    #    hallazgo, conservando el valor.
    #
    #    Antes este bloque repetía los umbrales (37.5, 3) por su cuenta. Al
    #    subir el umbral de febrícula a 37.8 en el motor, esta copia se quedó
    #    atrás y produjo actas que decían "temperatura positiva" donde la
    #    traza no mostraba ninguna señal. Duplicar un umbral clínico en dos
    #    módulos es la forma segura de que se desincronicen.
    if valores.get("temperatura_c") is not None:
        anotar("temperatura", AUSENTE, "temperatura medida dentro de lo normal",
               valor=valores["temperatura_c"])
    if valores.get("dolor_0_10") is not None:
        anotar("dolor", AUSENTE, "dolor referido sin alcanzar umbral de hallazgo",
               valor=valores["dolor_0_10"])

    # 3) Negación explícita de un dominio → evaluado y descartado.
    for dominio in DOMINIOS_SEGUIDOS:
        if _domina_negado(norm, dominio):
            anotar(dominio, AUSENTE, "el paciente lo descartó explícitamente")

    # 4) El tema que el agente acaba de preguntar: aquí se decide si la
    #    llamada logró cubrirlo o lo perdió.
    tema = composicion.tema_de_pregunta(pregunta_previa)
    if tema in DOMINIOS_SEGUIDOS:
        if es_no_respuesta(texto):
            anotar(tema, FALLO, "se preguntó y no hubo respuesta interpretable")
        elif DEVUELVE_PREGUNTA.match(norm) and not _menciona(norm, tema):
            # PREGUNTA LATERAL: el paciente interrumpe para preguntar otra
            # cosa. NO es un intento fallido de contestar — no hubo intento.
            #
            # Marcarlo como fallo tuvo una consecuencia concreta y observada:
            # el dominio quedaba «fallo_de_evaluacion», eso disparaba una
            # repregunta en el turno siguiente, y la repregunta secuestraba
            # ese turno antes de que se generara nada. Una sola pregunta
            # documental bastaba para descarrilar la entrevista.
            #
            # El dominio se deja INTACTO: sigue pendiente, y se retomará. Solo
            # cuenta como fallo un intento real de responder que no llegó a
            # buen puerto (silencio, evasión explícita, STT roto), que son las
            # ramas de arriba.
            pass
        elif tema not in observado:
            # Respondió con contenido y no evadió: el dominio quedó abordado
            # aunque no haya hallazgo. La carga de la prueba está en detectar
            # la EVASIÓN (arriba, en `es_no_respuesta`), no en reconocer todas
            # las formas de decir que uno está bien — que son infinitas.
            anotar(tema, AUSENTE, "el paciente respondió sin reportar hallazgo")

    return observado


class CoberturaEvaluacion:
    """Acumula la cobertura de una llamada. Serializable para el acta."""

    def __init__(self) -> None:
        self.estado: dict[str, dict] = {}

    def actualizar(self, observado: dict[str, dict]) -> None:
        for dominio, d in observado.items():
            previo = self.estado.get(dominio)
            if previo is None or _ORDEN[d["estado"]] > _ORDEN[previo["estado"]]:
                self.estado[dominio] = dict(d)

    # ── lectura ─────────────────────────────────────────────────────────────
    def _por_estado(self, *estados) -> list[str]:
        return sorted(d for d, v in self.estado.items() if v["estado"] in estados)

    def criticos_sin_cubrir(self) -> list[str]:
        return [d for d in DOMINIOS_CRITICOS
                if self.estado.get(d, {}).get("estado", DESCONOCIDO)
                in (DESCONOCIDO, FALLO)]

    def permite_verde(self) -> bool:
        """Solo se puede declarar verde si hay con qué justificarlo."""
        return not self.criticos_sin_cubrir()

    def pendientes_de_repregunta(self) -> list[str]:
        """Dominios críticos que se intentaron cubrir y se perdieron."""
        return [d for d in DOMINIOS_CRITICOS
                if self.estado.get(d, {}).get("estado") == FALLO]

    def resumen(self) -> dict:
        evaluados = self._por_estado(PRESENTE, AUSENTE)
        cubiertos_criticos = [d for d in DOMINIOS_CRITICOS if d not in self.criticos_sin_cubrir()]
        return {
            "evaluado_positivo": self._por_estado(PRESENTE),
            "evaluado_negativo": self._por_estado(AUSENTE),
            "desconocido": [d for d in DOMINIOS_SEGUIDOS
                            if self.estado.get(d, {}).get("estado", DESCONOCIDO) == DESCONOCIDO],
            "fallo_de_evaluacion": self._por_estado(FALLO),
            "dominios_criticos": list(DOMINIOS_CRITICOS),
            "criticos_sin_cubrir": self.criticos_sin_cubrir(),
            # Proporción de dominios CRÍTICOS cubiertos. No es una medida
            # clínica: es cuánto de la entrevista se logró completar.
            "razon_de_cobertura": (round(len(cubiertos_criticos) / len(DOMINIOS_CRITICOS), 2)
                                   if DOMINIOS_CRITICOS else 1.0),
            "evaluacion_completa": self.permite_verde(),
            "detalle": {d: dict(v) for d, v in sorted(self.estado.items())},
            "total_evaluados": len(evaluados),
            # Vista explícita de los dos ejes por dominio. `evaluado_positivo`
            # y `evaluado_negativo` se conservan por compatibilidad, pero esta
            # es la que no admite lectura ambigua: dice si se evaluó y, por
            # separado, si se encontró algo — con el valor medido cuando lo hay.
            "por_dominio": {
                d: {"assessed": v.get("assessed", False),
                    "positive": v.get("positive", False),
                    **({"valor": v["valor"]} if "valor" in v else {})}
                for d, v in sorted(self.estado.items())
            },
        }

    def motivo_de_incertidumbre(self) -> str:
        faltan = self.criticos_sin_cubrir()
        if not faltan:
            return ""
        perdidos = set(self.pendientes_de_repregunta())
        partes = []
        for d in faltan:
            causa = "se preguntó y no se obtuvo respuesta" if d in perdidos \
                else "no se alcanzó a preguntar"
            partes.append(f"{d} ({causa})")
        return "evaluación incompleta: " + "; ".join(partes)
