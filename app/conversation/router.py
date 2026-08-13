# -*- coding: utf-8 -*-
"""A dónde va cada intervención del paciente.

EL FALLO QUE CORRIGE
--------------------
Con un documento recién subido y activo, el paciente preguntó por su
contenido y RONDA contestó «Estoy aquí únicamente para acompañar su
recuperación». El retriever ni siquiera se llamó.

La causa no fue el RAG ni la indexación: el extractor de slots marcó la
pregunta como `fuera_de_mision`, y esa comprobación corría ANTES de la
recuperación. Y el modelo no se equivocaba —preguntar por «la clave de
verificación de un documento» no es una pregunta sobre la recuperación del
paciente—; el error era de ORDEN. Se decidía que algo estaba fuera de misión
sin haber mirado si el conocimiento activo podía responderlo.

LA REGLA
--------
`fuera_de_mision` deja de ser una puerta anterior al conocimiento y pasa a ser
la ÚLTIMA salida: solo se declara fuera de misión lo que, además de parecerlo,
no tiene evidencia que lo sustente.

    respuesta al checklist  → flujo clínico (nunca se enruta a conocimiento)
    pregunta con evidencia  → respuesta con cita
    pregunta clínica sin evidencia → abstención clínica
    lo demás                → fuera de misión

LO QUE ESTO NO ES
-----------------
No convierte a RONDA en un asistente general. El router decide a qué flujo va
la frase; la compuerta de evidencia sigue decidiendo qué puede decirse. Sin
evidencia activa, «¿cuál es la capital de Japón?» no obtiene respuesta: cae al
último tramo igual que antes. Enrutar no es autorizar.
"""
from __future__ import annotations

import re
import unicodedata

# ── Destinos ────────────────────────────────────────────────────────────────
CLINICO = "respuesta_clinica"          # el paciente contesta al checklist
CONOCIMIENTO = "pregunta_conocimiento"  # pregunta que el corpus puede responder
ABSTENCION = "abstencion_clinica"       # pregunta clínica sin respaldo
FUERA = "fuera_de_mision"

# ── Tipo de intervención ────────────────────────────────────────────────────
# Se clasifica ANTES de recuperar, porque de esto depende cómo se construye la
# consulta. Es deliberadamente barato y determinista: solo mira la forma del
# turno, no su contenido semántico.
CLINICAL_ANSWER = "clinical_answer"      # contesta a lo que se le preguntó
SIDE_QUESTION = "side_question"          # pregunta lateral: interrumpe, no responde
NON_RESPONSE = "non_response"            # silencio, ruido, no dijo nada
AMBIGUOUS = "ambiguous"                  # dijo algo, pero no se sabe qué es


def clasificar_intervencion(texto: str, hubo_pregunta_del_agente: bool) -> str:
    """Qué acaba de hacer el paciente, antes de saber si hay evidencia.

    La distinción que importa es CLINICAL_ANSWER frente a SIDE_QUESTION: la
    primera consume el intento de evaluación del dominio pendiente, la segunda
    no. Confundirlas fue lo que hizo que una pregunta documental legítima se
    registrara como «el paciente no respondió al dolor» y disparara una
    repregunta que secuestró el turno siguiente.
    """
    from ..decision import cobertura  # local: evita ciclo de importación

    n = _norm(texto or "").strip()
    # La detección de «esto no responde nada» ya vive en cobertura —silencio,
    # ruido de transcripción, evasión explícita— y es la misma que decide si
    # un dominio queda como fallo. Reimplementarla aquí las desincronizaría.
    if cobertura.es_no_respuesta(texto or ""):
        return NON_RESPONSE
    if es_respuesta_al_checklist(texto, hubo_pregunta_del_agente):
        return CLINICAL_ANSWER
    if _INTERROGATIVO.search(n) or _PIDE_CONOCIMIENTO.search(n):
        # Turno que pregunta en vez de responder. Puede tener respuesta en el
        # corpus o no —eso lo decide la recuperación—, pero en ningún caso es
        # un intento fallido de contestar a la pregunta clínica pendiente.
        return SIDE_QUESTION
    return AMBIGUOUS


def consulta_para(texto: str, tipo: str, contexto_clinico: str = "") -> str:
    """Consulta que se manda al recuperador, según lo que el paciente hizo.

    POR QUÉ NO SE CONCATENA SIEMPRE EL CONTEXTO
    -------------------------------------------
    La ruta anterior añadía «apendicectomía laparoscópica día 3
    postoperatorio» a TODA consulta. En una pregunta clínica eso ayuda: acota
    el protocolo relevante. En una pregunta documental corta, esos cinco
    términos clínicos dominan el embedding y arrastran la recuperación hacia
    los documentos de protocolo.

    Medido sobre la sesión humana que falló, con una pregunta corta sobre un
    documento recién subido:

        pregunta sola              → el documento sale en primera posición
        pregunta + contexto clínico → el documento desaparece del top-k

    Un documento nuevo no tiene por qué hablar del procedimiento ni del día
    postoperatorio del paciente, así que ese contexto es ruido puro para él.
    Por eso la pregunta lateral viaja sola.
    """
    if tipo == SIDE_QUESTION:
        return texto
    return f"{texto} {contexto_clinico}".strip() if contexto_clinico else texto


def _norm(t: str) -> str:
    t = (t or "").lower()
    t = unicodedata.normalize("NFD", t)
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


# Marcas de que el paciente está CONTESTANDO, no preguntando. Van primero
# porque una respuesta al checklist nunca debe enrutarse a conocimiento: si el
# agente pregunta «¿tiene fiebre?» y el paciente dice «no», eso es un dato
# clínico, no una consulta documental.
_RESPUESTA_CORTA = re.compile(
    r"^\s*(si|no|nada|ninguno|ninguna|bien|mal|regular|normal|mas o menos|"
    r"igual|creo que no|creo que si|un poco|poquito|"
    r"cero|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|\d{1,2})\b"
)

_INTERROGATIVO = re.compile(
    r"\?|^\s*[¿]|"
    r"\b(que dice|que indica|cual es|cuales son|donde dice|me puede decir|"
    r"puede decirme|sabe si|sabe que|que sabe|segun el documento|"
    r"en el documento|el documento dice|que menciona)\b"
)

# Preguntas ABIERTAS sobre el contenido del conocimiento. No nombran ningún
# hecho concreto a propósito: el router no debe conocer qué documentos hay.
_PIDE_CONOCIMIENTO = re.compile(
    r"\b(documento|protocolo|guia|plan de cuidado|instruccion|indicacion|"
    r"recomendacion|dice|indica|menciona|figura|aparece)\b"
)


def es_respuesta_al_checklist(texto: str, hubo_pregunta_del_agente: bool) -> bool:
    """¿Está contestando a lo que se le acaba de preguntar?

    Dos señales: el agente venía de preguntar, y la intervención tiene forma
    de respuesta (corta, afirmativa/negativa o una cifra) y no de pregunta.
    """
    if not hubo_pregunta_del_agente:
        return False
    n = _norm(texto).strip()
    if _INTERROGATIVO.search(n):
        return False
    # Respuesta breve o que empieza contestando.
    return bool(_RESPUESTA_CORTA.match(n)) or len(n.split()) <= 12


def parece_pregunta(texto: str) -> bool:
    return bool(_INTERROGATIVO.search(_norm(texto)))


def pide_conocimiento(texto: str) -> bool:
    """Pregunta que alude a lo que el sistema tiene documentado.

    Deliberadamente amplio: el coste de enrutar de más es una consulta al
    índice, y la compuerta de evidencia impide que eso se convierta en una
    respuesta sin respaldo. El coste de enrutar de menos es lo que acaba de
    pasar: una pregunta con respuesta activa contestada con una negativa.
    """
    n = _norm(texto)
    return bool(_INTERROGATIVO.search(n) or _PIDE_CONOCIMIENTO.search(n))


def enrutar(texto: str, hubo_pregunta_del_agente: bool, fuera_de_mision_llm: bool,
            hay_evidencia: bool, es_pregunta_clinica: bool) -> str:
    """Decide el destino. Se llama DESPUÉS de recuperar, no antes.

    `hay_evidencia` es el dato que faltaba en el flujo anterior: sin él, la
    decisión de «fuera de misión» se tomaba a ciegas respecto al conocimiento
    activo.
    """
    if es_respuesta_al_checklist(texto, hubo_pregunta_del_agente):
        return CLINICO
    if hay_evidencia and pide_conocimiento(texto):
        # Hay conocimiento activo que responde: no puede ser «fuera de misión»
        # por mucho que lo parezca. La cita la valida la compuerta después.
        return CONOCIMIENTO
    if fuera_de_mision_llm:
        return FUERA
    if es_pregunta_clinica or parece_pregunta(texto):
        return ABSTENCION
    return CLINICO
