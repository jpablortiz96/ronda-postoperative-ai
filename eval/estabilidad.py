# -*- coding: utf-8 -*-
"""Benchmark completo + validación cruzada de estabilidad en 6 bloques.

Ejecuta las 320 conversaciones una sola vez y luego reparte los resultados en
los 6 bloques. No se reevalúa nada por bloque: el motor es determinista, así
que dividir los resultados ya obtenidos es idéntico a reevaluar y no gasta
tiempo ni tokens.

Los umbrales NO se tocan después de ver un bloque. Esto es medición.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pandas as pd

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import folds, huellas, run_v2  # noqa: E402

NIVELES = ("verde", "amarillo", "rojo")


def matriz(pares) -> str:
    m = Counter(pares)
    filas = [f"{'':<14}{'Pred V':>9}{'Pred A':>9}{'Pred R':>9}"]
    for real in NIVELES:
        fila = f"  Real {real.upper():<9}"
        for pred in NIVELES:
            fila += f"{m[(real, pred)]:>9}"
        filas.append(fila)
    aciertos = sum(m[(x, x)] for x in NIVELES)
    total = len(pares) or 1
    filas.append(f"  exactitud {aciertos / total:.1%}   (n={total})")
    return "\n".join(filas)


def transiciones(pares) -> str:
    m = Counter(pares)
    return (f"  ROJO→VERDE={m[('rojo', 'verde')]}   ROJO→AMARILLO={m[('rojo', 'amarillo')]}   "
            f"VERDE→ROJO={m[('verde', 'rojo')]}   VERDE→AMARILLO={m[('verde', 'amarillo')]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit-root", type=Path, required=True)
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--salida", type=Path)
    ap.add_argument("--desde-json", type=Path,
                    help="reutiliza resultados ya calculados en vez de reevaluar")
    a = ap.parse_args()

    print("=" * 78)
    print("RONDA · benchmark completo + estabilidad en 6 bloques")
    print("=" * 78)
    h = huellas.imprimir()
    print()

    df, proc, _ = run_v2.cargar(a.kit_root, None, None, None)

    if a.desde_json:
        resultados = json.loads(a.desde_json.read_text(encoding="utf-8"))["resultados"]
    else:
        convs = run_v2.conversaciones(df, incluir_tercero=True)
        resultados, usage, dur = run_v2.correr(convs, proc, usar_llm=a.llm)
        print(f"  {len(resultados)} conversaciones en {dur:.1f} s")
        if a.llm:
            print(f"  llamadas={usage['llamadas']}  tokens={usage['in']}/{usage['out']}")

    pares = [(r["label"], r["prediccion"]) for r in resultados]
    print("\n" + "-" * 78)
    print("GLOBAL — 320 conversaciones")
    print("-" * 78)
    print(matriz(pares))
    print(transiciones(pares))

    for capa, etiqueta in (("limpia", "CAPA LIMPIA"), ("ruidosa", "CAPA RUIDOSA")):
        sub = [(r["label"], r["prediccion"]) for r in resultados if capa in str(r["capa"])]
        print(f"\n{etiqueta}")
        print(matriz(sub))
        print(transiciones(sub))

    print("\n" + "-" * 78)
    print("MÉTRICAS PAREADAS POR CASO (12 case_id rojos, no 24 observaciones)")
    print("-" * 78)
    p = folds.metricas_pareadas(resultados)
    print(f"  casos rojos                : {p['casos_rojos']}")
    print(f"  recall rojo · capa limpia  : {p['recall_rojo_limpia']:.1%}  "
          f"({p['detectados_limpia']}/{p['casos_rojos']})")
    print(f"  recall rojo · capa ruidosa : {p['recall_rojo_ruidosa']:.1%}  "
          f"({p['detectados_ruidosa']}/{p['casos_rojos']})")
    print(f"  recall rojo · PAREADO      : {p['recall_rojo_pareado']:.1%}  "
          f"({p['detectados_ambas']}/{p['casos_rojos']})  ← el que importa")
    print(f"  fallo catastrófico (R→V)   : {len(p['fallo_catastrofico'])}  "
          f"{p['fallo_catastrofico']}")
    print(f"  fallo parcial (R→A)        : {len(p['fallo_parcial'])}  {p['fallo_parcial']}")
    print(f"  sobretriaje V→R            : {len(p['verde_a_rojo'])} de "
          f"{p['casos_verdes']} casos verdes")
    print(f"  sobretriaje V→A            : {len(p['verde_a_amarillo'])}")

    # ── Eje 2, reportado APARTE del riesgo clínico ─────────────────────────
    estados = Counter((r.get("cierre") or {}).get("estado_evaluacion", "?")
                      for r in resultados)
    acciones = Counter((r.get("cierre") or {}).get("accion_operativa", "?")
                       for r in resultados)
    if estados:
        print("\n" + "-" * 78)
        print("ESTADO DE LA EVALUACIÓN   (eje 2 — NO contamina la matriz de riesgo)")
        print("-" * 78)
        total = sum(estados.values())
        for k in ("completa", "incompleta", "fallida"):
            print(f"  {k:<14} {estados.get(k, 0):>4}   {estados.get(k, 0) / total:.1%}")
        print(f"  acciones: {dict(acciones)}")

    # ── Estabilidad ────────────────────────────────────────────────────────
    print("\n" + "-" * 78)
    print("STABILITY CROSS-VALIDATION · 6 bloques")
    print("  NO es generalización externa: parte del dataset se usó en el diseño.")
    print("-" * 78)
    asignacion = folds.repartir(df)
    print(folds.resumen(df, asignacion))
    print()
    print(f"  {'bloque':<8}{'V/A/R':>12}{'R limpia':>10}{'R ruidosa':>11}"
          f"{'R pareado':>11}{'R→V':>6}{'V→R':>6}")
    filas = []
    for b in range(folds.N_BLOQUES):
        sub = [r for r in resultados if asignacion.get(r["caso_id"]) == b]
        if not sub:
            continue
        pb = folds.metricas_pareadas(sub)
        cuenta = Counter(r["label"] for r in sub)
        filas.append(pb)
        # Las cuentas van entre 2 porque cada caso aparece en dos capas.
        composicion_bloque = (f"{cuenta['verde'] // 2}/{cuenta['amarillo'] // 2}/"
                              f"{pb['casos_rojos']}")
        print(f"  {b:<8}{composicion_bloque:>12}"
              f"{pb['recall_rojo_limpia']:>9.0%}{pb['recall_rojo_ruidosa']:>10.0%}"
              f"{pb['recall_rojo_pareado']:>10.0%}"
              f"{len(pb['fallo_catastrofico']):>6}{len(pb['verde_a_rojo']):>6}")

    for clave, nombre in (("recall_rojo_limpia", "recall rojo limpia"),
                          ("recall_rojo_ruidosa", "recall rojo ruidosa"),
                          ("recall_rojo_pareado", "recall rojo pareado")):
        vals = [f[clave] for f in filas]
        print(f"  {nombre:<22} media={sum(vals)/len(vals):.0%}  "
              f"min={min(vals):.0%}  max={max(vals):.0%}")
    print("  (sin intervalos de confianza: con ~2 casos rojos por bloque serían inventados)")

    if a.salida:
        a.salida.write_text(json.dumps({
            "huella_motor": h["huella_motor"],
            "pareadas": p,
            "resultados": resultados,
        }, ensure_ascii=False, default=str), encoding="utf-8")
        print(f"\n  guardado en {a.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
