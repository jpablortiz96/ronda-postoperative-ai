# -*- coding: utf-8 -*-
"""Política de cierre conversacional: cuándo se termina una llamada y qué
se ha dicho ya.

POR QUÉ EXISTE ESTE MÓDULO
--------------------------
De una sesión humana real salieron tres defectos que no son clínicos sino
conversacionales, y que ninguna capa anterior podía ver:

1. El paciente dijo «Sí, mi mamá» cuando se le preguntó si estaba acompañado.
   Turnos después RONDA volvió a preguntar «¿Hay alguien que lo esté
   acompañando?». Nadie llevaba la cuenta de lo que el paciente YA había
   contestado.

2. RONDA repitió en varios turnos seguidos que el caso estaba escalado y que
   enfermería llamaría. Cierto las tres veces, e insoportable las tres veces.
   Nadie llevaba la cuenta de lo que RONDA ya había dicho.

3. El paciente dijo «No, nada más. Eso sería todo» y la llamada siguió. No
   existía el concepto de que el paciente quiere terminar.

Este módulo es determinista a propósito. Decidir el fin de una llamada clínica
por inferencia de un modelo sería exactamente el tipo de dependencia que el
resto del sistema evita. Aquí no se consulta a ningún LLM: se leen patrones y
se lleva un registro.

LO QUE ESTE MÓDULO NO HACE
--------------------------
No decide riesgo, no toca umbrales, no rebaja alarmas y no inventa requisitos
clínicos nuevos. La condición de suficiencia usa el checklist que ya existe.
Que el paciente quiera colgar no cierra nada por sí solo: si acaba de aparecer
una alarma sin resolver, la llamada sigue.
"""
from __future__ import annotations

import re

from ..decision import rules

# ── Estados terminales ──────────────────────────────────────────────────────
# Compatibles con la FSM actual: viven en paralelo a `state`, no la sustituyen.
ACTIVO = "activo"
ESCALADO = "escalado"
LISTO = "cierre_listo"
CERRANDO = "cerrando"
CERRADO = "cerrado"

# ── Mensajes operativos con memoria (A5) ────────────────────────────────────
ESCALAMIENTO = "escalamiento"
ENFERMERIA = "enfermeria_contacta"
ACOMPANAMIENTO = "acompanamiento_preguntado"
CIERRE_EXPLICADO = "cierre_explicado"

# ── Hechos que el paciente ya aportó (A4) ───────────────────────────────────
ACOMPANANTE = "acompanante"

# ── A3 · el paciente manifiesta que no tiene nada más ───────────────────────
# Se busca la INTENCIÓN, no una frase. Todos los patrones corren sobre texto
# ya normalizado por `rules._normalize` (minúsculas, sin tildes).
_FIN = (
    r"\beso (es|seria|era) todo\b",
    r"\beso es (to|todo)\b",
    r"\b(seria|era) todo\b",
    r"\bnada mas\b",
    r"\bnada nada\b",
    r"\bno tengo (nada|ninguna|ningun|mas|otra)\b",
    r"\bno,? ninguna\b",
    r"\bningun[ao] (otra |mas )?(duda|pregunta|cosa|inquietud)\b",
    r"\bsin (mas|otra) (duda|pregunta|cosa)\b",
    r"\bpodemos (terminar|cerrar|finalizar|colgar|dejarlo)\b",
    r"\bya (podemos|esta|estaria|seria)\b",
    r"\b(esta|estamos|estoy) listo\b",
    r"\bgracias,? eso (era|es)\b",
    r"\bpor ahora no\b",
    r"\basi esta bien\b",
    r"\bhasta luego\b", r"\bhasta pronto\b", r"\badios\b", r"\bchao\b",
    r"\bmuchas gracias\b",
)

# Un «no» pelado NO es intención de terminar: casi siempre está respondiendo a
# una pregunta clínica («¿ha tenido fiebre?» → «no»). Solo cuenta acompañado.
_FIN_SOLO_CON_CONTEXTO = (r"^no[.,!\s]*$", r"^nop[.,!\s]*$")


def quiere_terminar(texto: str, hubo_pregunta_de_cierre: bool = False) -> bool:
    """¿El paciente ha manifestado que no tiene nada más?

    `hubo_pregunta_de_cierre` indica que la última frase del agente preguntaba
    si quedaba alguna duda. Solo en ese caso un «no» aislado significa fin.
    """
    if not texto:
        return False
    norm = rules._normalize(texto)
    if any(re.search(p, norm) for p in _FIN):
        return True
    if hubo_pregunta_de_cierre and any(re.search(p, norm) for p in _FIN_SOLO_CON_CONTEXTO):
        return True
    return False


# La pregunta del agente que convierte un «no» en despedida.
_PREGUNTA_DE_CIERRE = (
    r"\balguna (otra )?(duda|pregunta|inquietud)\b",
    r"\balgo mas\b",
    r"\bqueda alguna\b",
    r"\bnecesita (algo|alguna)\b",
)


def es_pregunta_de_cierre(texto_agente: str) -> bool:
    if not texto_agente:
        return False
    norm = rules._normalize(texto_agente)
    return any(re.search(p, norm) for p in _PREGUNTA_DE_CIERRE)


# ── A4 · quién acompaña al paciente ─────────────────────────────────────────
_ACOMPANADO = (
    r"\b(mi|con mi|esta mi|aqui esta mi|con la|con el) "
    r"(mama|madre|papa|padre|esposa|esposo|marido|mujer|hija|hijo|hermana|"
    r"hermano|novia|novio|nuera|yerno|nieta|nieto|tia|tio|prima|primo|"
    r"vecina|vecino|cuidadora|cuidador|senora|sobrina|sobrino)\b",
    r"\bestoy acompanad[oa]\b",
    r"\bno estoy sol[oa]\b",
    r"\bsi,? (esta|estan|me acompana|estoy acompanad)",
    r"\besta conmigo\b",
    r"\bme acompana\b",
)
_SOLO = (
    r"\bestoy sol[oa]\b",
    r"\bno hay nadie\b",
    r"\bnadie (me acompana|esta conmigo)\b",
    r"\bvivo sol[oa]\b",
)


def leer_acompanante(texto: str) -> str | None:
    """Devuelve una etiqueta si el turno responde a «¿está acompañado?».

    No extrae el parentesco para usarlo clínicamente —eso no le corresponde a
    este módulo—; solo constata que la pregunta YA tiene respuesta.
    """
    if not texto:
        return None
    norm = rules._normalize(texto)
    if any(re.search(p, norm) for p in _SOLO):
        return "sin_acompanante"
    if any(re.search(p, norm) for p in _ACOMPANADO):
        return "acompanado"
    return None


class MemoriaConversacional:
    """Lo que el paciente ya contestó y lo que RONDA ya anunció.

    Dos diccionarios y ninguna magia. Su valor no está en la complejidad sino
    en que alguien, por fin, lleve la cuenta.
    """

    def __init__(self) -> None:
        self.hechos: dict[str, str] = {}
        self.anunciados: set[str] = set()
        self.quiere_terminar = False

    # ── hechos del paciente ────────────────────────────────────────────────
    def observar(self, texto_paciente: str, texto_agente_previo: str = "") -> None:
        """Lee un turno del paciente y guarda lo que aporte."""
        acomp = leer_acompanante(texto_paciente)
        if acomp and ACOMPANANTE not in self.hechos:
            self.hechos[ACOMPANANTE] = acomp
        if quiere_terminar(texto_paciente, es_pregunta_de_cierre(texto_agente_previo)):
            self.quiere_terminar = True

    def sabe(self, clave: str) -> bool:
        return clave in self.hechos

    # ── anuncios de RONDA ──────────────────────────────────────────────────
    def anunciar(self, *claves: str) -> None:
        self.anunciados.update(claves)

    def ya_anuncio(self, clave: str) -> bool:
        return clave in self.anunciados

    def anotar_texto_del_agente(self, texto: str) -> None:
        """Deduce del texto emitido qué anuncios operativos ya se hicieron.

        Se hace por texto y no por bandera para que también cuente lo que dijo
        el modelo por su cuenta, no solo los guiones deterministas.
        """
        if not texto:
            return
        norm = rules._normalize(texto)
        if re.search(r"\b(escalado|escalar|paso su caso|pasar su caso|reportado)\b", norm):
            self.anunciar(ESCALAMIENTO)
        if re.search(r"\benfermer(ia|a|o)\b", norm):
            self.anunciar(ENFERMERIA)
        if re.search(r"\b(alguien con usted|acompanad|alguien que lo|esta solo)\b", norm):
            self.anunciar(ACOMPANAMIENTO)

    # ── instrucciones para el generador (A5) ───────────────────────────────
    def restricciones(self) -> list[str]:
        """Frases que el modelo NO debe volver a producir."""
        fuera = []
        if self.ya_anuncio(ESCALAMIENTO) or self.ya_anuncio(ENFERMERIA):
            fuera.append(
                "YA informaste al paciente de que su caso quedó escalado y de que "
                "enfermería lo contactará. NO vuelvas a explicarlo. Como mucho "
                "aludelo en tres palabras («como ya quedó escalado…») y solo si "
                "aporta algo. Prioriza información NUEVA.")
        if self.sabe(ACOMPANANTE):
            fuera.append(
                "El paciente YA dijo si está acompañado. NO vuelvas a preguntar "
                "si hay alguien con él.")
        return fuera


def puede_cerrar(*, quiere_terminar_paciente: bool, escalado: bool,
                 alerta_persistida: bool, nueva_alarma: bool,
                 temas_sin_intentar: list[str]) -> tuple[bool, str]:
    """¿Se dan las condiciones para cerrar? Devuelve (decisión, motivo).

    Las condiciones son las del §A2 y ninguna más. En particular NO se exige
    haber cubierto todo el checklist: se exige haberlo INTENTADO. Un dominio
    que el paciente no supo contestar tras la repregunta no puede mantener la
    llamada abierta para siempre.
    """
    if not quiere_terminar_paciente:
        return False, "el paciente no ha manifestado que quiera terminar"
    if nueva_alarma:
        return False, "ha aparecido una alarma en este mismo turno"
    if escalado and not alerta_persistida:
        return False, "el escalamiento aún no está registrado"
    if temas_sin_intentar and not escalado:
        return False, f"quedan temas sin intentar: {', '.join(temas_sin_intentar)}"
    return True, "seguimiento_completado"


def texto_de_cierre(nombre: str, escalado: bool) -> str:
    """Despedida única y breve (§A6). Sin consejo médico nuevo."""
    pila = nombre.split()[0] if nombre else ""
    saludo = f"Perfecto, {pila}. " if pila else "Perfecto. "
    if escalado:
        return (saludo + "Ya dejamos registrado el seguimiento y el equipo de "
                "enfermería tiene su caso. Manténgase atento a su teléfono. "
                "Gracias por responder la llamada.")
    return (saludo + "Ya dejamos registrado su seguimiento. Si algo cambia, "
            "comuníquese con la clínica. Gracias por responder la llamada.")
