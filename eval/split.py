"""Partición DEV / HOLDOUT del dataset oficial.

El diseño del motor se hace mirando SOLO el DEV. El HOLDOUT se ejecuta una
única vez, al final, y no se vuelve a ajustar nada después de verlo: es lo
que separa "mejoramos el sistema" de "memorizamos el examen".

La partición es por `caso_id`, nunca por conversación: las dos capas de un
mismo caso van siempre juntas, porque comparten el ground truth y el
contenido. Separarlas filtraría el examen al material de diseño.

Estratificada por ground truth y con semilla fija, para que sea reproducible.
"""
from __future__ import annotations

import random
from collections import defaultdict

SEMILLA = 20260811
PROPORCION_HOLDOUT = 0.20


def dividir(df) -> tuple[set[str], set[str]]:
    """Devuelve (casos_dev, casos_holdout) por caso_id, estratificado."""
    etiqueta = df.groupby("caso_id")["label_ground_truth"].first().to_dict()
    por_clase = defaultdict(list)
    for caso, lab in etiqueta.items():
        por_clase[str(lab).strip().lower()].append(caso)

    rng = random.Random(SEMILLA)
    dev, holdout = set(), set()
    for clase in sorted(por_clase):
        casos = sorted(por_clase[clase])
        rng.shuffle(casos)
        n = max(1, round(len(casos) * PROPORCION_HOLDOUT))
        holdout.update(casos[:n])
        dev.update(casos[n:])
    return dev, holdout


def resumen(df, dev, holdout) -> str:
    etiqueta = df.groupby("caso_id")["label_ground_truth"].first().to_dict()
    lineas = [f"semilla={SEMILLA}  holdout={PROPORCION_HOLDOUT:.0%}"]
    for nombre, casos in (("DEV", dev), ("HOLDOUT", holdout)):
        cuenta = defaultdict(int)
        for c in casos:
            cuenta[str(etiqueta[c]).strip().lower()] += 1
        det = "  ".join(f"{k}={v}" for k, v in sorted(cuenta.items()))
        lineas.append(f"  {nombre:<8} casos={len(casos):>3}  conversaciones={len(casos)*2:>3}  {det}")
    return "\n".join(lineas)
