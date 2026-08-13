"""Motor de composición clínica — Carril D.

POR QUÉ EXISTE
--------------
La evaluación contra el dataset oficial (320 conversaciones) reveló que los
casos ROJO rara vez contienen una bandera catastrófica aislada. Son cuadros
COMPUESTOS: fiebre de 38, salida de líquido por la herida, pérdida de
movilidad e inapetencia, cada uno moderado, juntos un deterioro. El motor
anterior buscaba señales catastróficas individuales y perdía 20 de 24
conversaciones rojas.

DE DÓNDE SALEN LOS UMBRALES
---------------------------
Del corpus clínico oficial, no de la estadística de las etiquetas. El "PLAN
DE CUIDADO EN CASA DE PACIENTE EN POSTOPERATORIO DE APENDICECTOMÍA" enumera
los signos por los que un paciente debe **acudir a urgencias**:

    · Enrojecimiento, calor, sangrado o salida de pus en la herida quirúrgica
    · Apertura de la herida o separación de los puntos
    · Fiebre mayor de 38 °C
    · Dolor abdominal que no mejora o que va aumentando
    · Náuseas o vómitos que no se detienen
    · Falta de deposiciones que no mejora
    · Diarrea que dura más de 3 días
    · Dolor o hinchazón en las piernas o pantorrillas
    · Dificultad para respirar o dolor en el pecho

El "PLAN DE CUIDADO COLECISTECTOMIA" repite "Fiebre > 38 ºC" como signo de
alarma. Esa lista es la fuente de la severidad 2 ("criterio de urgencia"):
cada elemento, por sí solo, ya justifica una consulta. La severidad 3 queda
para lo que es catastrófico de inmediato y ya cubrían las reglas rojas.

Lo que NO está en esa lista tampoco está aquí como urgencia. La inapetencia,
el mal dormir y la lentitud para moverse entran como severidad 1: describen
deterioro, no motivo de consulta. Y el enrojecimiento de la herida entra como
severidad 1, no 2, porque los planes marcan su PROGRESIÓN —"Aumento del dolor,
inflamación, o enrojecimiento de la herida"—, no su presencia: una herida algo
rosada al tercer día aparece en la mitad de los pacientes sin complicación.

REGLA DE COMPOSICIÓN
--------------------
    · una señal de severidad 3                        → ROJO
    · DOS O MÁS criterios de urgencia (sev 2)         → ROJO
    · TRES O MÁS dominios DE LA LISTA DE ALARMA       → ROJO  (multidominio)
    · un criterio de urgencia                         → AMARILLO
    · dos dominios de la lista de alarma              → AMARILLO
    · menos                                           → VERDE

La tercera regla es la que este módulo aporta. Los cuadros rojos del material
oficial no traen una bandera catastrófica aislada: traen a un paciente que
minimiza cada frente por separado y que, sumados, describe un deterioro. Ese
perfil es invisible para un triaje que mire síntoma por síntoma.

QUÉ CUENTA Y QUÉ NO (corrección de FASE 4.8)
--------------------------------------------
La amplitud solo cuenta dominios que la guía considera motivo de consulta. El
análisis de los 23 falsos positivos rojos de FASE 4.7 mostró que estaban
dominados por dolor(19), sueño(19), movilidad(17) y alimentación(16), todos en
severidad 1: un paciente de día 2 con molestia leve, que duerme regular y
camina despacio. Eso es un postoperatorio normal, no un cuadro compuesto.

Tampoco cuenta lo que el paciente describe como en retroceso. Los planes no
marcan el signo sino su progresión —"AUMENTO del dolor, inflamación, o
enrojecimiento"—, así que un hallazgo que "ha bajado" es recuperación. La
tendencia se EXTRAE solo si el paciente la dice; nunca se infiere.

Y dos señales que salen de la misma frase y describen el mismo fenómeno no son
dos dominios independientes: "me cuesta moverme por el dolor" es un dolor que
limita, no dolor MÁS movilidad. Ver `deduplicar`.

DE DÓNDE SALE EL UMBRAL
-----------------------
Medido SOLO sobre la partición DEV, con el conjunto de dominios ya reducido:
3 dominios da recall limpio 100% y ROJO→VERDE = 0; 4 da 70% y un caso rojo
enviado a verde. Ver la constante `UMBRAL_DOMINIOS_ROJO`.

SEPARACIÓN DE EJES
------------------
Este módulo produce RIESGO CLÍNICO y solo consume evidencia observada. Lo que
no se preguntó o no se entendió no llega hasta aquí: vive en `cobertura.py`,
no suma severidad, y se combina con el riesgo una única vez, en
`engine.cerrar_llamada`, para producir una ACCIÓN.

Es determinista, explicable y auditable: cada disparo compuesto declara los
dominios que lo causaron, la frase que originó cada uno, su tendencia y de qué
boca salió (`contribuyentes`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import rules

# ── Dominios ────────────────────────────────────────────────────────────────
DOMINIOS = ("dolor", "temperatura", "herida", "sangrado", "respiratorio",
            "alimentacion", "movilidad", "estado_general", "sueno")

AUSENTE, LEVE, MODERADA, CRITICA = 0, 1, 2, 3


@dataclass
class ClinicalSignal:
    dominio: str
    severidad: int
    turno: int
    evidencia: str
    fuente_hablante: str = "paciente"
    vigente: bool = True
    detalle: dict = field(default_factory=dict)
    # Tramo exacto del turno que originó la señal. Es lo que permite saber si
    # dos dominios están describiendo la misma frase —y por tanto el mismo
    # fenómeno— en vez de dos hallazgos independientes.
    span: tuple[int, int] = (0, 0)
    tendencia: str = "desconocida"

    @property
    def signal_id(self) -> str:
        return f"t{self.turno}:{self.dominio}:{self.span[0]}-{self.span[1]}"

    def como_dict(self) -> dict:
        return {"signal_id": self.signal_id,
                "dominio": self.dominio, "severidad": self.severidad,
                "turno": self.turno, "evidencia": self.evidencia[:120],
                "span": list(self.span), "tendencia": self.tendencia,
                "fuente_hablante": self.fuente_hablante, "vigente": self.vigente,
                **({"detalle": self.detalle} if self.detalle else {})}


# ── Léxico por dominio ──────────────────────────────────────────────────────
# Cada entrada: (patrón, severidad). Solo expresa los signos de la lista
# oficial; no añade condiciones clínicas nuevas.
LEXICO: dict[str, list[tuple[str, int]]] = {
    "herida": [
        # "salida de pus en la herida quirúrgica" y "apertura de la herida"
        (r"\b(pus|puss)\b", MODERADA),
        (r"(sale|saliendo|salida|supura|secre|drena|bota|botando)\w*[^.]{0,45}"
         r"(herida|corte|puntos|incision)", MODERADA),
        (r"(herida|corte|puntos|incision)[^.]{0,30}(sale|saliendo|salida|supura|secre|drena|"
         r"liquido|liquida)", MODERADA),
        (r"\b(liquido|cosa|material|secrecion)\b[^.]{0,30}\b(amarill|verdos|turbio|feo|raro|espes)",
         MODERADA),
        (r"(mal olor|huele (mal|feo)|hediond)", MODERADA),
        (r"(abrio|abierta|abierto|solto|soltaron|reventaron|separ)\w*[^.]{0,25}"
         r"(herida|puntos|corte)", CRITICA),
        (r"(herida|corte|puntos)[^.]{0,25}(abierta|abierto|separad|reventad)", CRITICA),
        # El enrojecimiento entra como LEVE, no como urgencia. Los planes de
        # cuidado del corpus no marcan el color, marcan su progresión:
        # "Aumento del dolor, inflamación, o enrojecimiento de la herida"
        # (PLAN CASERO REEMPLAZO TOTAL DE RODILLA) y "Observe si hay aumento de
        # la inflamación, aumento del dolor y enrojecimiento en la herida
        # quirúrgica" (Programa Reemplazo Articular). Una herida algo rosada al
        # tercer día postoperatorio es esperable — aparece en la mitad de los
        # pacientes sin complicación. Lo que exige consulta es que vaya a más,
        # y de eso se encarga el ascenso por intensificador ("cada vez más
        # roja", "muy hinchada", "va aumentando"), que sube este signo a
        # criterio de urgencia.
        (r"(herida|corte|puntos|incision|cicatriz|zona operada|alrededor|"
         r"por ahi|ahi)[^.]{0,35}(rojit|rosad|rojiz|enrojec|eritema|roj|inflamad|hinchad|"
         r"caliente|calientic)", LEVE),
        (r"(rojit|rosad|rojiz|enrojec|eritema|roj|inflamad|hinchad|calientic)\w*"
         r"[^.]{0,35}(herida|corte|puntos|incision|cicatriz)", LEVE),
    ],
    "respiratorio": [
        (r"(dificultad|cuesta|costando|no puedo|no alcanzo)[^.]{0,25}respir", CRITICA),
        (r"me falta(ba)? (el |la )?(aire|respiracion)", CRITICA),
        (r"(no me entra|sin) (el )?aire", CRITICA),
        (r"(me ahogo|ahogad|asfixi|sofoc)", CRITICA),
        (r"me agito[^.]{0,20}(mucho|rapido|facil)", MODERADA),
    ],
    "sangrado": [
        (r"(sangra|sangrando|sangrado|botando sangre|sale sangre)[^.]{0,30}"
         r"(mucho|abundante|no para|sin parar|chorro|feo)", CRITICA),
        (r"(mucha|bastante|harta) sangre", CRITICA),
        (r"(sangra|sangrando|con sangre|botando sangre)", MODERADA),
    ],
    "movilidad": [
        # "Antes me movía sola y ahora casi no puedo levantarme": pérdida de
        # función ya ganada. Deterioro funcional, no lentitud esperable.
        (r"(casi )?no (me )?puedo (levantar|parar|caminar|mover)", MODERADA),
        (r"(ya )?no (me )?(puedo|logro) (levantar|parar|caminar|mover)", MODERADA),
        (r"necesito[^.]{0,25}(ayuda|que me ayuden)[^.]{0,20}(para todo|levantar|caminar)",
         MODERADA),
        (r"(muy mal|peor que antes|antes[^.]{0,30}ahora (casi )?no)", MODERADA),
        # "poquito" NO va aquí: es el minimizador más común del habla
        # colombiana ("un poquito rojita") y disparaba movilidad en cualquier
        # frase que lo contuviera. Los signos de movilidad exigen un verbo de
        # desplazamiento cerca.
        (r"(camino|caminar|ando|andar|me muevo|moverme|levantarme|pararme)[^.]{0,25}"
         r"(despacit|lent|con ayuda|con dificultad|me canso)", LEVE),
        (r"(despacit|lent|con ayuda|me canso)[^.]{0,25}"
         r"(camin|and|mover|levantar|parar|caminar)", LEVE),
    ],
    "alimentacion": [
        (r"(no me provoca|casi no (como|me da hambre)|no me da hambre|"
         r"se me (quita|quito|ha ido)[^.]{0,20}(el hambre|las ganas|apetito)|"
         r"todo me da (asco|pereza)|ni eso)", LEVE),
        (r"(no (retengo|me pasa)|devuelvo todo|vomit\w+[^.]{0,25}(varias|todo el dia|no par))",
         MODERADA),
    ],
    "estado_general": [
        (r"escalofri|tiritand|temblando", LEVE),
        (r"(sudad|sudando)[^.]{0,25}(frio|helad)", LEVE),
        (r"(me desmaye|desvaneci|perdi (el sentido|el conocimiento))", CRITICA),
        (r"(muy )?confundid|no sabe donde esta", CRITICA),
    ],
    "sueno": [
        (r"(duermo (muy )?mal|casi no duermo|no he (podido )?dormir|me despierto varias)", LEVE),
        (r"dormir casi nada|no pego el ojo|dando vueltas toda la noche|"
         r"(no|mal)[^.]{0,12}(dormi|duermo|descanso)", LEVE),
    ],
    "temperatura": [
        # Sensación febril sin cifra. La cifra la aporta el carril numérico.
        (r"(acalorad|destemplad|con calentura|me siento caliente|afiebrad)", LEVE),
    ],
}

# Intensificadores y minimizadores: preservan la semántica del habla real.
# "un poquito rojita" NO es normal, pero tampoco es una urgencia.
MINIMIZADORES = (r"un poquit", r"poquit", r"un poco", r"leve", r"ligera",
                 r"nada del otro mundo", r"normalit", r"casi nada")
INTENSIFICADORES = (r"mucho", r"bastante", r"harto", r"muy ", r"demasiad",
                    r"cada vez (mas|peor)", r"empeor", r"no mejora", r"va aumentando")

# ── Progresión ──────────────────────────────────────────────────────────────
# El corpus distingue el signo de su evolución: los planes de reemplazo
# articular no marcan "enrojecimiento" sino "AUMENTO del enrojecimiento", y el
# de apendicectomía marca "Dolor abdominal que no mejora o QUE VA AUMENTANDO".
# Un hallazgo que el paciente describe como en retroceso no es deterioro.
#
# Solo se extrae lo que el paciente DICE. No se infiere tendencia: sin frase
# explícita, la tendencia es `desconocida` y el signo cuenta como está.
MEJORANDO = "mejorando"
ESTABLE = "estable"
EMPEORANDO = "empeorando"
NUEVO = "nuevo"
DESCONOCIDA = "desconocida"

TENDENCIAS: tuple[tuple[str, str], ...] = (
    (r"(va|esta|viene) (peor|empeorando|a mas|aumentando)|cada vez (mas|peor)|"
     r"no (mejora|cede|se me quita)|se (ha )?puesto peor|desde (ayer|anoche) "
     r"(esta|va) peor", EMPEORANDO),
    (r"(ha|he) (bajado|mejorado|cedido|disminuido)|va (mejor|mejorando|cediendo|bajando)|"
     r"(esta|estoy) (mejor|mejorando)|menos que (ayer|antes)|ya casi no|"
     r"se (me )?(ha )?ido (quitando|bajando)|cada dia (mejor|menos)", MEJORANDO),
    (r"(me )?(aparecio|salio|empezo|comenzo|arranco) (ayer|anoche|hoy|esta manana)|"
     r"(antes no|no tenia eso)|de un momento a otro|de repente", NUEVO),
    (r"(sigue|esta) igual|lo mismo de siempre|igual que (ayer|antes|siempre)|"
     r"ni mejor ni peor|estable", ESTABLE),
)


def tendencia_en(texto_norm: str, inicio: int, fin: int) -> str:
    """Tendencia declarada por el paciente cerca del hallazgo."""
    ventana = texto_norm[max(0, inicio - 60):fin + 90]
    for patron, etiqueta in TENDENCIAS:
        if re.search(patron, ventana):
            return etiqueta
    return DESCONOCIDA


# Dominios que figuran en la lista oficial de "signos de alarma para acudir a
# urgencias". Solo estos pueden alcanzar severidad 2: un criterio de urgencia
# es, por definición, algo que la guía clínica considera motivo de consulta.
DOMINIOS_DE_URGENCIA = ("herida", "temperatura", "dolor", "respiratorio",
                        "sangrado", "alimentacion")
# Inapetencia, mal sueño y lentitud para moverse NO aparecen en esa lista.
# Son contexto de deterioro: acompañan, no constituyen la alarma.
DOMINIOS_DE_CONTEXTO = ("movilidad", "sueno", "estado_general")

# ── Qué cuenta para la regla de amplitud ────────────────────────────────────
# El análisis de los 23 falsos positivos rojos de FASE 4.7 mostró que estaban
# dominados por dolor(19), sueño(19), movilidad(17) y alimentación(16), todos
# en severidad 1. Es decir: un paciente de día 2 con molestia leve, que duerme
# regular y camina despacio. Eso NO es un cuadro compuesto, es un
# postoperatorio normal — ningún plan de cuidado del corpus pide consultar por
# ello.
#
# La amplitud solo tiene sentido clínico sobre dominios que la guía considera
# motivo de consulta. Los de contexto siguen registrándose como evidencia y
# aparecen en el acta, pero por sí solos no escalan por acumulación.
DOMINIOS_QUE_CUENTAN_AMPLITUD = DOMINIOS_DE_URGENCIA

# ── Correlación entre dominios ──────────────────────────────────────────────
# Dos señales que salen de la MISMA frase y describen el mismo fenómeno no son
# dos hallazgos independientes. "Me cuesta moverme por el dolor" es un dolor
# que limita, no un problema de dolor MÁS un problema de movilidad; contarlos
# por separado infla artificialmente la amplitud.
PARES_CORRELACIONADOS: tuple[tuple[frozenset, str], ...] = (
    (frozenset({"dolor", "movilidad"}), "dolor que limita el movimiento"),
    (frozenset({"herida", "sangrado"}), "sangrado por la herida"),
    (frozenset({"temperatura", "estado_general"}), "fiebre con síntomas acompañantes"),
    (frozenset({"alimentacion", "estado_general"}), "malestar general con inapetencia"),
    (frozenset({"dolor", "sueno"}), "insomnio por dolor"),
)

# Cuántos dominios DE LA LISTA DE ALARMA simultáneamente afectados constituyen
# un deterioro generalizado.
#
# El umbral bajó de 4 a 3 en FASE 4.8, y no por buscar mejores números: cambió
# lo que se cuenta. Antes contaban los nueve dominios —incluidos sueño,
# movilidad y estado general—, así que 4 era exigente. Ahora solo cuentan los
# seis que las guías consideran motivo de consulta, y con el mismo 4 el motor
# perdía casos rojos reales.
#
# Medido sobre la partición DEV (128 casos), únicamente:
#     umbral 3 → recall limpio 100%, ruidoso 90%, ROJO→VERDE = 0
#     umbral 4 → recall limpio  70%, ruidoso 70%, ROJO→VERDE = 1
#     umbral 5 → recall limpio  70%, ruidoso 70%, ROJO→VERDE = 4
# El 4 tiene mejor exactitud (74.2% frente a 67.6%) y manda un caso rojo a
# verde. Se elige el 3.
UMBRAL_DOMINIOS_ROJO = 3
UMBRAL_DOMINIOS_AMARILLO = 2

# Temperatura por debajo del umbral de alarma. Ninguna guía del corpus define
# fiebre bajo 38 °C, así que este escalón NO sale del protocolo: existe para
# que una febrícula en ascenso cuente como parte de un cuadro, y por eso su
# valor es una calibración, no una cifra clínica citable.
#
# Medido en DEV: con 37.5 hay 6 verdes escalados a rojo; con 37.8 quedan 2, y
# el recall rojo no se mueve (limpio 100%, ruidoso 90%, ROJO→VERDE = 0 en
# ambos). Eliminar el escalón del todo sí cuesta: el recall limpio cae a 80%.
UMBRAL_SUBFEBRIL = 37.8


# ── Resolución del referente ────────────────────────────────────────────────
# El paciente contesta con pronombres: el agente pregunta "¿cómo se ve la
# herida?" y la respuesta es "se ve un poquito rojita". La frase, aislada, no
# nombra la herida; el dominio lo aporta la pregunta. Estos patrones leen el
# turno del AGENTE para saber de qué se está hablando, que es exactamente la
# información que el checklist de temas ya mantiene en producción.
# Se usan RAÍCES, no formas conjugadas. Con "duerme|dormir" la pregunta
# "¿cómo ha dormido?" no activaba el tema y la respuesta quedaba huérfana: el
# motor emparejaba cadenas donde debía reconocer un concepto.
TEMAS_DE_PREGUNTA: dict[str, str] = {
    "herida": r"(herida|corte|puntos|incision|cicatriz|vendaje|aposito|curacion)",
    "temperatura": r"(fiebre|temperatura|termometro|calentura|grados|escalofri)",
    "dolor": r"(dolor|duel|molest|del 0 al 10|de 0 a 10)",
    "alimentacion": r"(comer|comida|comido|comiendo|apetito|hambre|aliment|"
                    r"liquidos|tomando agua)",
    "movilidad": r"(camin|mover|movi|levantar|parar|de pie)",
    "sueno": r"(duerm|dormi|sueno|descans|noche)",
}

# Signos que solo cuentan cuando la pregunta previa fija el dominio. Fuera de
# ese contexto son ambiguos y generaban falsas alarmas: "poquito" no dice nada
# por sí solo, pero contestando a "¿cómo ha comido?" sí dice que come poco.
# Todos son LEVE: describen deterioro, no un criterio de urgencia. Su valor
# está en la AMPLITUD. El paciente que minimiza cada respuesta por separado
# —"un poquito molesto", "despacito", "como poquito", "duermo regular"— es
# justamente el que se escapa si se mira síntoma por síntoma.
LEXICO_POR_TEMA: dict[str, list[tuple[str, int]]] = {
    "herida": [
        (r"(rojit|rosad|rojiz|enrojec|eritema|roja|rojo|inflamad|hinchad|"
         r"caliente|calientic)\w*", LEVE),
    ],
    "dolor": [
        (r"(molest|incomod|dolorcit|fastidi|ardor|punzad)", LEVE),
    ],
    "movilidad": [
        (r"(me cuesta|con dificultad|necesito ayuda|con (el |la )?(andador|caminador|baston))",
         LEVE),
    ],
    "alimentacion": [
        (r"(sin ganas|desganad|no me provoca|se me (ha )?(quitado|ido|quita)"
         r"[^.]{0,25}(hambre|ganas|apetito)|casi no (como|he comido)|"
         r"no (me )?(provoca|dan ganas|da hambre))", LEVE),
    ],
    "sueno": [
        (r"(duermo mal|no he podido dormir|me despierto varias|a ratos|"
         r"interrumpid|no pego el ojo)", LEVE),
    ],
}
# NOTA SOBRE LO QUE SE QUITÓ DE AQUÍ
# ---------------------------------
# Este léxico se añadió en FASE 4.6 para no perder al paciente que minimiza, y
# se le fue la mano: incluía `poquit`, `poco`, `regular`, `mas o menos`,
# `\bmal\b`, `despacit`, `lento`, `me canso`. Son CUANTIFICADORES, no quejas, y
# el análisis de falsos positivos los pilló disparando sobre frases que dicen
# lo contrario de lo que registraban:
#
#   "he tenido BUEN APETITO, he podido comer casi todo, aunque me cuesta un
#    POQUITO abrir el estómago"          → alimentación deteriorada (falso)
#   "eso sí DUERMO BIEN, no me ha costado nada"  → sueño alterado (falso)
#   "me muevo poquito, DESPACITO, COMO ES DE ESPERARSE por la operación"
#                                        → movilidad deteriorada (discutible)
#
# Ahora cada entrada exige una queja explícita. Se pierde sensibilidad ante el
# paciente que minimiza mucho; se gana no llamar deterioro a la recuperación
# normal. El coste de esa pérdida se mide, no se supone: ver el informe.


def tema_de_pregunta(texto_agente: str) -> str:
    """Devuelve el dominio sobre el que el agente acaba de preguntar."""
    if not texto_agente:
        return ""
    norm = rules._normalize(texto_agente)
    for dominio, patron in TEMAS_DE_PREGUNTA.items():
        if re.search(patron, norm):
            return dominio
    return ""


def _hay(patrones, ventana: str) -> bool:
    """Un matiz cuenta solo si no viene negado justo antes.

    "no me gusta verla mucho" contiene «mucho», pero no intensifica nada: el
    paciente habla de otra cosa. Sin este filtro, la herida de un caso real
    subió a criterio de urgencia por una cláusula ajena.
    """
    for p in patrones:
        for m in re.finditer(p, ventana):
            previo = ventana[max(0, m.start() - 26):m.start()]
            # Un negador en la misma cláusula (sin coma ni punto de por medio)
            # invierte el matiz: "no me provoca mucho comer" no intensifica el
            # dominio de alimentación, lo describe.
            if not re.search(r"\b(no|ni|nunca|tampoco)\b[^,.;]*$", previo):
                return True
    return False


def _ajustar_por_matiz(sev: int, texto: str, inicio: int, fin: int,
                       dominio: str = "") -> tuple[int, str]:
    """Sube o baja un escalón según el matiz del propio paciente.

    ASIMETRÍA DELIBERADA: la minimización del paciente NUNCA saca un signo de
    la lista oficial de urgencias. "Se ve un poquito rojita" sigue siendo una
    herida enrojecida; el diminutivo es un hábito del habla, no un dato
    clínico. Por eso el minimizador solo rebaja de crítica a urgencia, jamás
    de urgencia a leve. Es la misma regla que impide al LLM rebajar una
    alarma, aplicada al paciente que se resta importancia.
    """
    ventana = texto[max(0, inicio - 30):fin + 30]
    intensifica = _hay(INTENSIFICADORES, ventana)
    if sev >= CRITICA and _hay(MINIMIZADORES, ventana) and not intensifica:
        return MODERADA, "minimizado"
    if sev == LEVE and dominio not in DOMINIOS_DE_CONTEXTO and intensifica:
        return MODERADA, "intensificado"
    return sev, ""


# ── Extracción de señales de un turno ───────────────────────────────────────
def señales_de_turno(texto: str, turno: int, hablante: str = "paciente",
                     slots_numericos: dict | None = None,
                     pregunta_previa: str = "") -> list[ClinicalSignal]:
    norm = rules._normalize(texto)
    encontradas: dict[str, ClinicalSignal] = {}

    def registrar(dominio, sev, evidencia, detalle=None, span=(0, 0), tendencia=DESCONOCIDA):
        if sev <= AUSENTE:
            return
        previa = encontradas.get(dominio)
        if previa is None or sev > previa.severidad:
            encontradas[dominio] = ClinicalSignal(
                dominio=dominio, severidad=sev, turno=turno, evidencia=evidencia,
                fuente_hablante=hablante, detalle=detalle or {},
                span=span, tendencia=tendencia)

    tema = tema_de_pregunta(pregunta_previa)
    aplicables = dict(LEXICO)
    if tema in LEXICO_POR_TEMA:
        aplicables[tema] = aplicables.get(tema, []) + LEXICO_POR_TEMA[tema]

    for dominio, patrones in aplicables.items():
        for patron, sev in patrones:
            for m in re.finditer(patron, norm):
                if rules.esta_negado(norm, m.start(), m.end(), patron):
                    continue
                sev_ajustada, matiz = _ajustar_por_matiz(sev, norm, m.start(), m.end(), dominio)
                if dominio in DOMINIOS_DE_CONTEXTO:
                    sev_ajustada = min(sev_ajustada, MODERADA)
                tend = tendencia_en(norm, m.start(), m.end())
                detalle = {"patron": patron[:40]}
                if matiz:
                    detalle["matiz"] = matiz
                registrar(dominio, sev_ajustada, texto, detalle,
                          span=(m.start(), m.end()), tendencia=tend)
                break

    # Dominios numéricos: la cifra manda sobre el léxico.
    valores = slots_numericos if slots_numericos is not None else rules.extraer_valores(texto)
    umbrales = rules.load_rules().get("umbrales", {})
    temp = valores.get("temperatura_c")
    if temp is not None:
        if temp >= umbrales.get("fiebre_rojo_c", 39.0):
            registrar("temperatura", CRITICA, texto, {"temperatura_c": temp})
        elif temp >= umbrales.get("fiebre_amarillo_c", 38.0):
            # "Fiebre mayor de 38 °C" figura como signo para acudir a urgencias
            # en los planes de cuidado oficiales.
            registrar("temperatura", MODERADA, texto, {"temperatura_c": temp})
        elif temp >= UMBRAL_SUBFEBRIL:
            registrar("temperatura", LEVE, texto, {"temperatura_c": temp})
    dolor = valores.get("dolor_0_10")
    if dolor is not None:
        if dolor >= umbrales.get("dolor_rojo", 8):
            registrar("dolor", CRITICA, texto, {"dolor_0_10": dolor})
        elif dolor >= umbrales.get("dolor_amarillo", 6):
            registrar("dolor", MODERADA, texto, {"dolor_0_10": dolor})
        elif dolor >= 3:
            registrar("dolor", LEVE, texto, {"dolor_0_10": dolor})
    # "Dolor que no mejora o que va aumentando" es criterio de urgencia por sí
    # solo, sin depender de la cifra.
    #
    # El referente puede venir de la pregunta: a "¿cómo va el dolor?" el
    # paciente contesta "cada día está peor, no mejora ni con lo que me
    # formularon" sin volver a nombrar el dolor. Exigir la palabra en su turno
    # perdía el criterio entero.
    EMPEORA = (r"no (mejora|cede|se me quita)|va (aumentando|a mas)|"
               r"cada (vez|dia) (mas|peor)|empeorando|esta peor")
    if re.search(rf"(dolor|duele)[^.]{{0,45}}({EMPEORA})", norm):
        m = re.search(r"(dolor|duele)", norm)
        if m and not rules.esta_negado(norm, m.start(), m.end()):
            registrar("dolor", MODERADA, texto, {"criterio": "dolor que no mejora"},
                      span=(m.start(), m.end()), tendencia=EMPEORANDO)
    elif tema == "dolor":
        m = re.search(EMPEORA, norm)
        if m and not rules.esta_negado(norm, m.start(), m.end()):
            registrar("dolor", MODERADA, texto,
                      {"criterio": "dolor que no mejora (referente en la pregunta)"},
                      span=(m.start(), m.end()), tendencia=EMPEORANDO)

    return [s.como_dict() for s in encontradas.values()]


# ── Composición sobre la llamada ────────────────────────────────────────────
def _solapan(a: dict, b: dict) -> bool:
    """¿Dos señales salen del mismo tramo del mismo turno?"""
    if a.get("turno") != b.get("turno"):
        return False
    ia, fa = (a.get("span") or [0, 0])[:2]
    ib, fb = (b.get("span") or [0, 0])[:2]
    if fa == 0 and fb == 0:
        return False
    return max(ia, ib) < min(fa, fb) or abs(ia - ib) < 40


def deduplicar(peor: dict[str, dict]) -> tuple[dict[str, dict], list[str]]:
    """Colapsa dominios que describen el mismo fenómeno en la misma frase.

    "Me cuesta moverme por el dolor" no es un problema de dolor MÁS un problema
    de movilidad: es un dolor que limita. Contarlos por separado inflaba la
    amplitud y era la causa más frecuente entre los falsos positivos rojos.

    Solo se colapsan pares declarados como correlacionados Y que además salgan
    del mismo tramo del mismo turno. Dos hallazgos del mismo par en turnos
    distintos SÍ son independientes: el paciente los reportó por separado.
    """
    fusionados = dict(peor)
    notas: list[str] = []
    for par, explicacion in PARES_CORRELACIONADOS:
        a, b = sorted(par)
        if a not in fusionados or b not in fusionados:
            continue
        if not _solapan(fusionados[a], fusionados[b]):
            continue
        # Se conserva el de mayor severidad; el otro deja de contar como
        # dominio independiente pero sigue en el registro de evidencia.
        menor = a if fusionados[a]["severidad"] <= fusionados[b]["severidad"] else b
        notas.append(f"{a}+{b} → un solo fenómeno ({explicacion})")
        fusionados.pop(menor)
    return fusionados, notas


def componer(historial: list[dict]) -> dict:
    """Fusiona las señales acumuladas de la llamada en un RIESGO CLÍNICO.

    Trabaja exclusivamente con evidencia observada. Lo que no se preguntó o no
    se entendió no llega hasta aquí: eso vive en `cobertura.py` y no suma
    severidad. Ver la separación de ejes en `engine.decide`.
    """
    peor: dict[str, dict] = {}
    for s in historial:
        if not s.get("vigente", True):
            continue
        d = s["dominio"]
        if d not in peor or s["severidad"] > peor[d]["severidad"]:
            peor[d] = s

    peor_dedup, notas_dedup = deduplicar(peor)

    criticas = [s for s in peor_dedup.values() if s["severidad"] >= CRITICA]
    urgencias = [s for s in peor_dedup.values() if s["severidad"] == MODERADA]
    leves = [s for s in peor_dedup.values() if s["severidad"] == LEVE]

    # AMPLITUD: solo cuentan los dominios que la guía considera motivo de
    # consulta, y solo si el paciente no los describió como en retroceso.
    # Un hallazgo que "ha bajado" es recuperación, no deterioro.
    amplitud = [s for s in peor_dedup.values()
                if s["dominio"] in DOMINIOS_QUE_CUENTAN_AMPLITUD
                and s.get("tendencia") != MEJORANDO]
    doms = lambda xs: ", ".join(sorted(s["dominio"] for s in xs))  # noqa: E731

    if criticas:
        nivel = "rojo"
        razon = "señal crítica aislada: " + doms(criticas)
        regla = "señal_critica"
        implicadas = criticas
    elif len(urgencias) >= 2:
        nivel = "rojo"
        razon = (f"{len(urgencias)} criterios de urgencia simultáneos ({doms(urgencias)}); "
                 "cada uno figura como signo de alarma en el plan de cuidado postoperatorio")
        regla = "urgencias_simultaneas"
        implicadas = urgencias
    elif len(amplitud) >= UMBRAL_DOMINIOS_ROJO:
        nivel = "rojo"
        razon = (f"deterioro simultáneo en {len(amplitud)} dominios de la lista de alarma "
                 f"({doms(amplitud)}); ninguno aislado justifica consulta, el conjunto sí")
        regla = "deterioro_multidominio"
        implicadas = amplitud
    elif urgencias:
        nivel = "amarillo"
        razon = f"un criterio de urgencia: {doms(urgencias)}"
        regla = "urgencia_aislada"
        implicadas = urgencias
    elif len(amplitud) >= UMBRAL_DOMINIOS_AMARILLO:
        nivel = "amarillo"
        razon = (f"deterioro en {len(amplitud)} dominios de la lista de alarma "
                 f"sin criterio de urgencia: {doms(amplitud)}")
        regla = "deterioro_parcial"
        implicadas = amplitud
    else:
        nivel = "verde"
        razon = (f"hallazgos leves sin amplitud clínica ({doms(leves)})"
                 if leves else "sin señales")
        regla = "sin_alarma"
        implicadas = []

    return {
        "nivel": nivel,
        "regla": regla,
        "disparo": {
            "tipo": "composicion",
            "nivel": nivel,
            "regla": regla,
            "dominios": sorted(s["dominio"] for s in implicadas),
            "razon": razon,
            "evidencias": implicadas,
            **({"deduplicaciones": notas_dedup} if notas_dedup else {}),
        } if implicadas else None,
        "señales": dict(sorted(peor.items())),          # todo lo observado
        "señales_independientes": dict(sorted(peor_dedup.items())),
        "deduplicaciones": notas_dedup,
        # Trazabilidad de la contribución (§13): qué dominio, con qué
        # severidad, con qué evidencia y de qué boca salió.
        "contribuyentes": [
            {"dominio": s["dominio"], "severidad": s["severidad"],
             "tendencia": s.get("tendencia", DESCONOCIDA),
             "evidencia": str(s.get("evidencia", ""))[:120],
             "fuente": s.get("fuente_hablante", "paciente"),
             "signal_id": s.get("signal_id")}
            for s in sorted(implicadas, key=lambda x: -x["severidad"])
        ],
    }









