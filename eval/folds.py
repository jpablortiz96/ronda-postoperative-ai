# -*- coding: utf-8 -*-
"""Validación cruzada de estabilidad en 6 bloques.

CÓMO SE LLAMA ESTO Y CÓMO NO
----------------------------
Es `stability cross-validation`, NO una estimación de generalización externa.
Parte de este dataset ya se usó para diseñar el motor, así que estos números
dicen si el comportamiento es ESTABLE entre subconjuntos, no si generalizaría
a pacientes nuevos. Presentarlo como lo segundo sería deshonesto.

LA UNIDAD DE PARTICIÓN ES EL `caso_id`
--------------------------------------
Nunca el turno, nunca la capa. La capa limpia y la ruidosa del mismo caso
comparten paciente, historia y etiqueta: separarlas metería el mismo caso en
dos bloques y volvería el resultado optimista por construcción.

POR QUÉ 6 BLOQUES
-----------------
Hay 12 case_id rojos en todo el dataset. Con 6 bloques tocan ~2 rojos por
bloque, que es lo máximo que permite este material. No se derivan intervalos
de confianza: con 2 casos por bloque, cualquier intervalo sería inventado. Se
reporta media, mínimo y máximo, que es lo que los datos aguantan.
"""
from __future__ import annotations

import random
from collections import defaultdict

SEMILLA = 20260811
N_BLOQUES = 6


def repartir(df, n_bloques: int = N_BLOQUES) -> dict[str, int]:
    """Devuelve {caso_id: nº de bloque}, estratificado por etiqueta.

    El reparto es por rotación dentro de cada clase: así cada bloque recibe
    una proporción casi idéntica de rojos, amarillos y verdes aunque las
    clases estén muy desbalanceadas.
    """
    etiqueta = df.groupby("caso_id")["label_ground_truth"].first().to_dict()
    por_clase = defaultdict(list)
    for caso, lab in etiqueta.items():
        por_clase[str(lab).strip().lower()].append(caso)

    rng = random.Random(SEMILLA)
    asignacion: dict[str, int] = {}
    for clase in sorted(por_clase):
        casos = sorted(por_clase[clase])
        rng.shuffle(casos)
        for i, caso in enumerate(casos):
            asignacion[caso] = i % n_bloques
    return asignacion


def resumen(df, asignacion: dict[str, int]) -> str:
    etiqueta = df.groupby("caso_id")["label_ground_truth"].first().to_dict()
    cuenta = defaultdict(lambda: defaultdict(int))
    for caso, bloque in asignacion.items():
        cuenta[bloque][str(etiqueta[caso]).strip().lower()] += 1
    lineas = [f"semilla={SEMILLA}  bloques={N_BLOQUES}  unidad=caso_id "
              f"(capas limpia+ruidosa siempre juntas)"]
    for b in sorted(cuenta):
        c = cuenta[b]
        total = sum(c.values())
        lineas.append(f"  bloque {b}: casos={total:>3}  "
                      f"verde={c['verde']:>3}  amarillo={c['amarillo']:>2}  rojo={c['rojo']:>2}")
    return "\n".join(lineas)


# ── Métricas pareadas por caso ──────────────────────────────────────────────
def metricas_pareadas(resultados: list[dict]) -> dict:
    """Recall rojo mirando el CASO, no la conversación.

    Un `caso_id` aparece dos veces (capa limpia y ruidosa). Contarlas como dos
    observaciones independientes infla la n y esconde el dato que de verdad
    importa en producción: si el sistema aguanta el mismo caso cuando la
    transcripción se degrada.

        recall limpio  — casos rojos detectados en la capa limpia
        recall ruidoso — casos rojos detectados en la capa ruidosa
        recall pareado — casos rojos detectados en AMBAS capas
    """
    por_caso: dict[str, dict] = defaultdict(dict)
    etiquetas: dict[str, str] = {}
    for r in resultados:
        capa = "limpia" if "limpia" in str(r["capa"]) else "ruidosa"
        por_caso[r["caso_id"]][capa] = r["prediccion"]
        etiquetas[r["caso_id"]] = r["label"]

    rojos = [c for c, lab in etiquetas.items() if lab == "rojo"]
    limpia = [c for c in rojos if por_caso[c].get("limpia") == "rojo"]
    ruidosa = [c for c in rojos if por_caso[c].get("ruidosa") == "rojo"]
    ambas = [c for c in rojos if c in limpia and c in ruidosa]

    catastroficos, parciales = [], []
    for c in rojos:
        for capa, pred in por_caso[c].items():
            if pred == "verde":
                catastroficos.append(f"{c}/{capa}")
            elif pred == "amarillo":
                parciales.append(f"{c}/{capa}")

    verdes = [c for c, lab in etiquetas.items() if lab == "verde"]
    verde_a_rojo = [f"{c}/{capa}" for c in verdes
                    for capa, pred in por_caso[c].items() if pred == "rojo"]
    verde_a_amarillo = [f"{c}/{capa}" for c in verdes
                        for capa, pred in por_caso[c].items() if pred == "amarillo"]

    n = len(rojos) or 1
    return {
        "casos_rojos": len(rojos),
        "recall_rojo_limpia": len(limpia) / n,
        "recall_rojo_ruidosa": len(ruidosa) / n,
        "recall_rojo_pareado": len(ambas) / n,
        "detectados_limpia": len(limpia),
        "detectados_ruidosa": len(ruidosa),
        "detectados_ambas": len(ambas),
        "fallo_catastrofico": catastroficos,     # ROJO → VERDE
        "fallo_parcial": parciales,              # ROJO → AMARILLO
        "verde_a_rojo": verde_a_rojo,
        "verde_a_amarillo": verde_a_amarillo,
        "casos_verdes": len(verdes),
    }
