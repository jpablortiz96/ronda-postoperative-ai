# -*- coding: utf-8 -*-
"""Explicación estructurada de cada falso positivo rojo, y de los amarillos por riesgo.

Se ejecuta ANTES de tocar el motor. El objetivo no es corregir caso por caso
—eso sería ajustar al examen— sino encontrar PATRONES DE CAUSA que se puedan
arreglar con reglas generalizables.

Taxonomía de causa (la del enunciado de la fase):
    A  señal mal extraída
    B  severidad sobredimensionada
    C  dos dominios contando el mismo fenómeno
    D  composición demasiado agresiva
    E  procedimiento incorrecto
    F  negación o temporalidad
    G  desacuerdo entre la etiqueta y el protocolo
    H  otra
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from app.decision import composicion  # noqa: E402

# Dominios que describen el mismo fenómeno clínico cuando coinciden en la misma
# frase. No es una lista de sinónimos: es qué señales NO son independientes.
SOLAPAMIENTOS = {
    frozenset({"herida", "sangrado"}): "sangrado por la herida",
    frozenset({"temperatura", "estado_general"}): "fiebre y sus síntomas acompañantes",
    frozenset({"alimentacion", "estado_general"}): "malestar general con inapetencia",
    frozenset({"movilidad", "dolor"}): "dolor que limita el movimiento",
}


def clasificar(señales: dict, disparo: dict | None, turnos: list[dict]) -> tuple[str, str]:
    """Devuelve (código de causa, explicación)."""
    doms = {d: s["severidad"] for d, s in señales.items()}
    n = len(doms)
    urgencias = [d for d, sev in doms.items() if sev >= 2]

    # C · dominios que describen el mismo fenómeno
    for par, nombre in SOLAPAMIENTOS.items():
        if par <= set(doms):
            return "C", f"posible doble conteo: {'+'.join(sorted(par))} ({nombre})"

    # D · el disparo es de amplitud y todo es leve
    if disparo and "deterioro simultáneo" in (disparo.get("razon") or ""):
        if not urgencias:
            return "D", (f"amplitud sin ninguna urgencia: {n} dominios, todos leves "
                         f"({', '.join(sorted(doms))})")
        return "D", f"amplitud con {len(urgencias)} urgencia(s): {', '.join(sorted(doms))}"

    # B · una urgencia sostiene el rojo pero la evidencia es débil
    if urgencias:
        return "B", f"criterio(s) de urgencia: {', '.join(sorted(urgencias))}"

    return "H", f"{n} dominios: {', '.join(sorted(doms))}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resultados", type=Path, required=True)
    ap.add_argument("--kit-root", type=Path, required=True)
    ap.add_argument("--detalle", type=int, default=6)
    a = ap.parse_args()

    res = json.loads(a.resultados.read_text(encoding="utf-8"))["resultados"]
    df = pd.read_excel(a.kit_root / "dataset" / "dataset_final.xlsx", sheet_name="result")
    textos = defaultdict(list)
    for r in df.itertuples():
        if str(r.hablante) in ("paciente", "tercero"):
            textos[(r.caso_id, r.capa)].append((str(r.hablante), str(r.texto)))

    def analizar(filtro, titulo):
        casos = [r for r in res if filtro(r)]
        print("\n" + "=" * 88)
        print(f"{titulo}   (n={len(casos)})")
        print("=" * 88)
        causas = Counter()
        dominios = Counter()
        combos = Counter()
        fuentes = Counter()
        for i, r in enumerate(casos):
            señales = (r.get("slots") or {}).get("_composicion") or {}
            disparo = None
            for d in reversed((r.get("traza") or [])):
                pass
            comp = composicion.componer((r.get("slots") or {}).get("_señales") or [])
            disparo = comp.get("disparo")
            codigo, expl = clasificar(señales or comp["señales"], disparo,
                                      r.get("traza") or [])
            causas[codigo] += 1
            usadas = señales or comp["señales"]
            for d in usadas:
                dominios[d] += 1
            combos[tuple(sorted(usadas))] += 1
            for s in usadas.values():
                fuentes[s.get("fuente_hablante", "paciente")] += 1
            if i < a.detalle:
                print(f"\n  · {r['caso_id']} | {r['capa']} | {r.get('procedimiento')}")
                print(f"    real={r['label']}  predicho={r['prediccion']}  causa={codigo}: {expl}")
                cob = (r.get("cierre") or {}).get("cobertura_evaluacion") or {}
                print(f"    cobertura={cob.get('razon_de_cobertura')} "
                      f"sin_cubrir={cob.get('criticos_sin_cubrir')}")
                for dom, s in sorted(usadas.items()):
                    print(f"      {dom:<15} sev{s['severidad']}  turno {s.get('turno')}  "
                          f"[{s.get('fuente_hablante')}]  «{str(s.get('evidencia'))[:80]}»")
                if disparo:
                    print(f"      regla: {disparo.get('razon', '')[:140]}")

        print(f"\n  causas: {dict(causas.most_common())}")
        print(f"  dominios implicados: {dict(dominios.most_common())}")
        print("  combinaciones más frecuentes:")
        for c, k in combos.most_common(8):
            print(f"    {k:>3}  {c}")
        print(f"  fuente de las señales: {dict(fuentes)}")

    analizar(lambda r: r["label"] == "verde" and r["prediccion"] == "rojo",
             "VERDE → ROJO   (falsos positivos rojos)")
    analizar(lambda r: (r["label"] == "verde" and r["prediccion"] == "amarillo"
                        and not (r.get("cierre") or {}).get("elevado_por_cobertura")),
             "VERDE → AMARILLO POR RIESGO   (excluye los de evaluación incompleta)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
