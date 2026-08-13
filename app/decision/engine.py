"""Motor de decisión de RONDA: fusión de doble carril.

Regla de oro (asimetría clínica de la rúbrica): la criticidad final es
    max(carril_determinista_texto, carril_determinista_slots, carril_LLM)
El LLM jamás puede REBAJAR lo que las reglas dispararon. Ambos carriles quedan
registrados por separado en el acta para auditoría.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from .. import config, observability
from . import assess, cobertura, composicion, rules


def decide(
    patient_text: str,
    procedimiento: str | None,
    contexto_caso: str,
    historial_slots: dict | None = None,
    turno: int = 0,
    hablante: str = "paciente",
    pregunta_previa: str = "",
) -> dict:
    carril_texto = rules.evaluate_text(patient_text, procedimiento)

    # CARRIL NUMÉRICO DETERMINISTA: extrae dolor y temperatura solo con reglas.
    # Va antes que el LLM y se fusiona con prioridad, de modo que un "nueve de
    # diez" cruza el umbral aunque el proveedor esté caído o se equivoque.
    numericos = rules.extraer_valores(patient_text, pregunta_previa)
    carril_numerico = rules.evaluate_slots(numericos)

    slots, usage = assess.extract_slots(patient_text, contexto_caso, historial_slots)
    if numericos:
        # La cifra leída por reglas manda sobre la del LLM para el mismo turno.
        slots = assess._merge_slots(slots, numericos)
    carril_slots = rules.evaluate_slots(slots)

    # Los máximos históricos también se evalúan: si el dolor llegó a 9 en algún
    # turno, el riesgo de la llamada ya no baja aunque ahora diga 4.
    historicos = {}
    for clave in ("dolor_0_10", "temperatura_c"):
        maximo = slots.get(f"{clave}_max")
        if maximo is not None:
            historicos[clave] = maximo
    carril_historico = rules.evaluate_slots(historicos) if historicos else {"nivel": "verde", "disparos": []}

    # CARRIL DE COMPOSICIÓN: acumula señales por dominio a lo largo de la
    # llamada. Los rojos del material oficial son cuadros compuestos, no
    # banderas aisladas; sin este carril se perdían 20 de 24.
    historial_señales = list(historial_slots.get("_señales", []) if historial_slots else [])
    nuevas = composicion.señales_de_turno(
        patient_text, turno=turno, hablante=hablante, slots_numericos=numericos,
        pregunta_previa=pregunta_previa)
    historial_señales.extend(nuevas)
    comp = composicion.componer(historial_señales)
    slots["_señales"] = historial_señales
    slots["_composicion"] = comp["señales"]

    # COBERTURA DE LA EVALUACIÓN. Va aparte de la evidencia a propósito: lo
    # desconocido no suma severidad —un dominio sin preguntar no es un
    # síntoma—, pero tampoco puede hacerse pasar por normalidad.
    cob = cobertura.CoberturaEvaluacion()
    cob.estado = dict((historial_slots or {}).get("_cobertura", {}))
    cob.actualizar(cobertura.observar_turno(
        patient_text, pregunta_previa=pregunta_previa, hablante=hablante,
        señales=nuevas, valores=numericos))
    slots["_cobertura"] = cob.estado

    # CONTRATO DE EVIDENCIA DEL LLM. El modelo puede extraer y puede sugerir,
    # pero no puede escalar por su cuenta: una elevación sin evidencia
    # estructurada convertiría al LLM en un clasificador opaco, que es
    # exactamente lo que este motor existe para no ser.
    nivel_llm, motivo_llm = _nivel_llm_admisible(slots, comp, carril_texto, carril_numerico)

    niveles = {
        "carril_determinista_texto": carril_texto["nivel"],
        "carril_determinista_numerico": carril_numerico["nivel"],
        "carril_determinista_slots": carril_slots["nivel"],
        "carril_composicion": comp["nivel"],
        "carril_historico": carril_historico["nivel"],
        "carril_llm": nivel_llm,
    }
    por_evidencia = rules.LEVEL_NAMES[max(rules.LEVELS[n] for n in niveles.values())]

    # El nivel DEL TURNO es el de la evidencia observada, sin más. Lo que no se
    # preguntó o no se entendió NO entra aquí: vive en el eje de cobertura y no
    # suma severidad. Los dos ejes se combinan una sola vez, al cerrar la
    # llamada (`cerrar_llamada`), y ni siquiera allí se mezclan: producen un
    # riesgo clínico, un estado de evaluación y una acción, por separado.
    final = por_evidencia
    verde_elegible = cob.permite_verde()
    motivo_incertidumbre = "" if verde_elegible else cob.motivo_de_incertidumbre()

    vistos = set()
    disparos = []
    for d in (carril_texto["disparos"] + carril_numerico["disparos"]
              + carril_slots["disparos"] + carril_historico["disparos"]):
        clave = (d.get("regla"), d.get("patron"))
        if clave not in vistos:
            vistos.add(clave)
            disparos.append(d)

    todos_los_disparos = disparos + ([comp["disparo"]] if comp["disparo"] else [])
    return {
        "nivel_final": final,
        "niveles": niveles,
        "disparos": todos_los_disparos,
        "slots": slots,
        "confianza_llm": slots.get("confianza", 0),
        "informacion_faltante": slots.get("informacion_faltante", []),
        "fuera_de_mision": bool(slots.get("fuera_de_mision")),
        "usage": usage,
        # ── Por qué este nivel, en cuatro campos que no se mezclan ──────────
        # Permite distinguir "amarillo porque hay riesgo" de "amarillo porque
        # no conseguimos completar una evaluación segura". Son cosas distintas
        # para quien recibe la alerta.
        "evidencia_clinica": comp["señales"],
        "cobertura_evaluacion": cob.resumen(),
        "nivel_por_evidencia": por_evidencia,
        "verde_elegible": verde_elegible,
        "razon_de_decision": _razon_de_decision(final, por_evidencia, todos_los_disparos,
                                                motivo_incertidumbre),
        "razon_de_incertidumbre": motivo_incertidumbre,
        "repreguntar": cob.pendientes_de_repregunta(),
        # Traza de contribución: ninguna decisión puede quedar sin un "¿por qué?"
        "contribuyentes": _contribuyentes(niveles, todos_los_disparos, comp, motivo_llm),
        "contrato_llm": motivo_llm,
    }


def _contribuyentes(niveles: dict, disparos: list[dict], comp: dict, motivo_llm: str) -> list[dict]:
    """Quién empujó el nivel hacia arriba, con su evidencia.

    Se ordena por severidad del carril para que el primero sea siempre el que
    determinó el resultado. Sin esto una decisión compuesta es inauditable:
    "rojo" sin poder decir qué dominio, con qué frase y de qué boca.
    """
    salida = []
    for carril, nivel in niveles.items():
        if nivel == "verde":
            continue
        entrada = {"carril": carril, "nivel": nivel}
        if carril == "carril_composicion":
            entrada["regla"] = comp.get("regla")
            entrada["evidencias"] = comp.get("contribuyentes", [])
        elif carril == "carril_llm":
            entrada["contrato"] = motivo_llm
        else:
            entrada["evidencias"] = [
                {"regla": d.get("regla") or d.get("tipo"),
                 "descripcion": d.get("descripcion", ""),
                 "patron": d.get("patron", "")}
                for d in disparos if d.get("nivel") == nivel
            ][:4]
        salida.append(entrada)
    return sorted(salida, key=lambda e: rules.LEVELS[e["nivel"]], reverse=True)


# Slots que constituyen evidencia estructurada admisible para que el LLM
# eleve. Son los mismos que el extractor ya declara en su esquema: si el
# modelo dice "rojo" debe poder señalar cuál de estos encendió.
EVIDENCIA_ADMISIBLE = ("sangrado_activo", "dificultad_respiratoria", "dolor_toracico",
                       "herida_pus_o_abierta", "vomito_persistente", "no_orina",
                       "fiebre_reportada", "dolor_0_10", "temperatura_c")


def _nivel_llm_admisible(slots: dict, comp: dict, carril_texto: dict,
                         carril_numerico: dict) -> tuple[str, str]:
    """Nivel que se le acepta al LLM, y por qué se le acepta o se le recorta.

    Regla: para elevar por encima de verde, el LLM debe aportar evidencia
    estructurada —una bandera encendida o una cifra— o coincidir con algo que
    los carriles deterministas ya vieron. Un `nivel_sugerido="rojo"` a secas,
    sin nada que lo sostenga, se recorta a verde y queda anotado.

    Esto NO debilita la seguridad: los carriles deterministas siguen mandando
    con `max`, y el LLM nunca pudo rebajar. Lo que se elimina es su capacidad
    de inventar una alarma sin decir de dónde sale.
    """
    sugerido = slots.get("nivel_sugerido", "verde")
    if sugerido not in rules.LEVELS:
        return "verde", "nivel_sugerido inválido"
    if sugerido == "verde":
        return "verde", ""

    evidencias = [k for k in EVIDENCIA_ADMISIBLE if slots.get(k) not in (None, False, "")]
    sintomas = slots.get("sintomas_mencionados") or []
    respaldo_determinista = (carril_texto["nivel"] != "verde"
                             or carril_numerico["nivel"] != "verde"
                             or comp["nivel"] != "verde")
    if evidencias or sintomas or respaldo_determinista:
        detalle = ", ".join(evidencias) or ("síntomas: " + ", ".join(map(str, sintomas[:3]))
                                            if sintomas else "coincide con carril determinista")
        return sugerido, f"admitido con evidencia ({detalle})"
    return "verde", (f"recortado de «{sugerido}» a verde: el modelo no aportó "
                     "evidencia estructurada que lo sostenga")


def cerrar_llamada(nivel_por_evidencia: str, cobertura_estado: dict | None) -> dict:
    """Criticidad definitiva de la llamada, ya con la compuerta de verde.

    LA PROPIEDAD DE SEGURIDAD
    -------------------------
    RONDA solo puede declarar VERDE si logró la evidencia para justificarlo.
    No es "si falta algo → rojo": eso convertiría cada llamada interrumpida en
    una urgencia y ahogaría a la enfermería en falsas alarmas. Es más
    estrecho: sin los dominios críticos cubiertos, la llamada no puede
    cerrarse como tranquilizadora y queda en amarillo **por evaluación
    incompleta**, que es una categoría distinta de "amarillo por riesgo" y así
    se reporta.

    La compuerta solo sube verde→amarillo. Nunca rebaja: si hay evidencia
    amarilla o roja, manda la evidencia.
    """
    cob = cobertura.CoberturaEvaluacion()
    cob.estado = dict(cobertura_estado or {})
    resumen = cob.resumen()
    riesgo = nivel_por_evidencia if nivel_por_evidencia in rules.LEVELS else "verde"

    # EJE 2 — estado de la evaluación. Nada de esto toca el riesgo clínico.
    sin_cubrir = cob.criticos_sin_cubrir()
    perdidos = cob.pendientes_de_repregunta()
    if not sin_cubrir:
        estado = "completa"
    elif perdidos:
        estado = "fallida"     # se preguntó y se perdió la respuesta
    else:
        estado = "incompleta"  # no se alcanzó a preguntar

    # EJE 3 — qué hay que HACER. Aquí se combinan los dos ejes, y solo aquí.
    # El rojo domina siempre, con cobertura o sin ella.
    if riesgo == "rojo":
        accion = "escalar"
    elif riesgo == "amarillo":
        accion = "revision_humana"
    elif estado == "completa":
        accion = "continuar"
    else:
        # Verde clínico pero sin haber podido comprobarlo: no es una alarma,
        # es una entrevista a medio terminar. La acción lo dice; la etiqueta
        # clínica NO se falsea.
        accion = "revision_humana" if estado == "fallida" else "repreguntar"

    return {
        # ── Eje 1: riesgo clínico. Solo evidencia observada. ────────────────
        "riesgo_clinico": riesgo,
        # ── Eje 2: estado de la evaluación ─────────────────────────────────
        "estado_evaluacion": estado,
        "evaluacion_completa": estado == "completa",
        "razon_de_incertidumbre": cob.motivo_de_incertidumbre(),
        "cobertura_evaluacion": resumen,
        # ── Eje 3: acción operativa ────────────────────────────────────────
        "accion_operativa": accion,
        # Compatibilidad: `nivel_final` sigue existiendo y sigue significando
        # RIESGO CLÍNICO, nunca cobertura. Antes de FASE 4.8 mezclaba ambas
        # cosas y por eso 57 llamadas verdes salían etiquetadas de amarillo.
        "nivel_final": riesgo,
        "nivel_por_evidencia": riesgo,
        "elevado_por_cobertura": False,
    }


def _razon_de_decision(final, por_evidencia, disparos, motivo_incertidumbre) -> str:
    if final != por_evidencia:
        return "compuerta de cobertura: " + motivo_incertidumbre
    if not disparos:
        return "sin señales de alarma y evaluación suficiente"
    d = disparos[0]
    return d.get("razon") or d.get("descripcion") or d.get("regla") or d.get("tipo", "")


def crear_acta_alerta(
    session_id: str,
    paciente: dict,
    decision: dict,
    transcript_reciente: list[dict],
    citas_usadas: list[dict],
) -> dict:
    """Persiste el acta de alerta (lo que la rúbrica llama 'qué queda
    registrado, con qué estructura y con qué persistencia')."""
    acta = {
        "tipo": "alerta",
        "alerta_id": uuid.uuid4().hex[:10],
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "paciente": {
            "paciente_id": paciente.get("paciente_id"),
            "nombre": paciente.get("nombre"),
            "procedimiento": paciente.get("procedimiento"),
            "dia_postoperatorio": paciente.get("dia_postoperatorio"),
        },
        "nivel": decision["nivel_final"],
        "niveles_por_carril": decision["niveles"],
        "reglas_disparadas": decision["disparos"],
        "sintomas": decision["slots"].get("sintomas_mencionados", []),
        "slots": {
            k: v
            for k, v in decision["slots"].items()
            if k not in ("informacion_faltante",)
        },
        "citas_clinicas_usadas": citas_usadas,
        "extracto_transcript": transcript_reciente[-6:],
        "siguiente_paso": (
            "Contactar al paciente en los próximos 30 minutos"
            if decision["nivel_final"] == "rojo"
            else "Revisión por enfermería en el siguiente corte"
        ),
    }
    path = config.ALERTAS_DIR / f"alerta_{acta['alerta_id']}.json"
    path.write_text(json.dumps(acta, ensure_ascii=False, indent=2), encoding="utf-8")
    # Evento correlacionable con el acta por session_id + alerta_id. No lleva
    # texto libre del paciente: eso ya vive, completo, en data/alertas/.
    observability.log_event(
        {
            "tipo": "alerta_creada",
            "alerta_id": acta["alerta_id"],
            "session_id": session_id,
            "paciente_id": paciente.get("paciente_id"),
            "nivel": acta["nivel"],
            "niveles_por_carril": acta["niveles_por_carril"],
            "reglas_disparadas": [d.get("regla") or d.get("tipo") for d in decision["disparos"]],
            "archivo": path.name,
        }
    )
    return acta


def listar_alertas() -> list[dict]:
    actas = []
    for p in sorted(config.ALERTAS_DIR.glob("alerta_*.json"), reverse=True):
        try:
            actas.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return actas


