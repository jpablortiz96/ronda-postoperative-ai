"""Harness de evaluación de RONDA contra el ground truth del reto.

Reproduce las conversaciones del dataset oficial a través del MOTOR DE
DECISIÓN (doble carril) y compara contra `label_ground_truth`
(verde/amarillo/rojo, constante por caso_id). Reporta:

- Matriz de confusión 3x3
- RECALL EN ROJO (la métrica reina: falso negativo = falla catastrófica)
- Exactitud global y desglose por capa (limpia vs ruidosa)

Uso:
  python eval/run.py --dataset ruta/a/dataset_final.xlsx            # solo reglas (rápido, $0)
  python eval/run.py --dataset ruta/a/dataset_final.xlsx --llm      # doble carril completo
  python eval/run.py --dataset ... --capa capa2_ruidosa --limite 40

Notas de robustez: los nombres exactos de columnas del xlsx pueden variar;
este script detecta columnas por heurística y acepta overrides:
  --col-caso caso_id --col-capa capa --col-rol rol --col-texto texto --col-label label_ground_truth
La hoja se llama `result` según el kit oficial.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import pandas as pd  # noqa: E402

from app.decision import rules  # noqa: E402

NIVELES = ["verde", "amarillo", "rojo"]


def detectar_columna(df: pd.DataFrame, override: str | None, candidatos: list[str]) -> str:
    if override:
        if override not in df.columns:
            raise SystemExit(f"Columna '{override}' no existe. Columnas: {list(df.columns)}")
        return override
    cols_norm = {c.lower().strip(): c for c in df.columns}
    for cand in candidatos:
        for key, original in cols_norm.items():
            if cand in key:
                return original
    raise SystemExit(
        f"No pude detectar columna entre {candidatos}. Columnas disponibles: "
        f"{list(df.columns)}. Usa los flags --col-*."
    )


def clasificar_caso(textos_paciente: list[str], procedimiento: str | None, usar_llm: bool) -> str:
    """Clasifica un caso completo: máximo nivel disparado en toda la conversación."""
    texto_completo = " \n".join(textos_paciente)
    nivel = rules.evaluate_text(texto_completo, procedimiento)["nivel"]
    if usar_llm:
        from app.decision import engine

        decision = engine.decide(
            texto_completo[:4000], procedimiento,
            f"Procedimiento: {procedimiento or 'desconocido'}", {},
        )
        if rules.LEVELS[decision["nivel_final"]] > rules.LEVELS[nivel]:
            nivel = decision["nivel_final"]
    return nivel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True, help="ruta a dataset_final.xlsx")
    ap.add_argument("--perfiles", help="ruta a perfiles_clinicos_pacientes_silver_contest.xlsx (para procedimiento)")
    ap.add_argument("--llm", action="store_true", help="activar carril LLM (consume API)")
    ap.add_argument("--capa", default=None, help="filtrar: capa1_limpia | capa2_ruidosa")
    ap.add_argument("--limite", type=int, default=None, help="máximo de casos a evaluar")
    ap.add_argument("--col-caso", dest="col_caso")
    ap.add_argument("--col-capa", dest="col_capa")
    ap.add_argument("--col-rol", dest="col_rol")
    ap.add_argument("--col-texto", dest="col_texto")
    ap.add_argument("--col-label", dest="col_label")
    ap.add_argument("--col-paciente", dest="col_paciente")
    args = ap.parse_args()

    df = pd.read_excel(args.dataset, sheet_name="result")
    col_caso = detectar_columna(df, args.col_caso, ["caso_id", "caso"])
    col_capa = detectar_columna(df, args.col_capa, ["capa", "layer"])
    col_rol = detectar_columna(df, args.col_rol, ["rol", "role", "speaker", "hablante"])
    col_texto = detectar_columna(df, args.col_texto, ["texto", "text", "utterance", "mensaje", "contenido"])
    col_label = detectar_columna(df, args.col_label, ["label_ground_truth", "ground_truth", "label"])

    procedimientos: dict[str, str] = {}
    if args.perfiles:
        col_pac = None
        perfiles = pd.read_excel(args.perfiles, sheet_name="result")
        col_pac = detectar_columna(perfiles, args.col_paciente, ["paciente_id", "paciente"])
        col_proc = detectar_columna(perfiles, None, ["procedimiento", "procedure", "cirugia"])
        procedimientos = dict(zip(perfiles[col_pac].astype(str), perfiles[col_proc].astype(str)))

    if args.capa:
        df = df[df[col_capa].astype(str).str.contains(args.capa, case=False, na=False)]

    casos = defaultdict(lambda: {"textos": [], "label": None, "capa": None, "paciente": None})
    col_paciente_conv = None
    for cand in ["paciente_id", "paciente"]:
        matches = [c for c in df.columns if cand in c.lower()]
        if matches:
            col_paciente_conv = matches[0]
            break

    for _, row in df.iterrows():
        caso = str(row[col_caso])
        c = casos[caso]
        c["label"] = str(row[col_label]).strip().lower()
        c["capa"] = str(row[col_capa])
        if col_paciente_conv is not None:
            c["paciente"] = str(row[col_paciente_conv])
        if "paciente" in str(row[col_rol]).lower():
            c["textos"].append(str(row[col_texto]))

    items = list(casos.items())
    if args.limite:
        items = items[: args.limite]

    matriz = Counter()
    por_capa: dict[str, Counter] = defaultdict(Counter)
    errores_rojo: list[str] = []

    for caso_id, c in items:
        if c["label"] not in NIVELES or not c["textos"]:
            continue
        proc = procedimientos.get(c["paciente"] or "", None)
        pred = clasificar_caso(c["textos"], proc, args.llm)
        matriz[(c["label"], pred)] += 1
        por_capa[c["capa"]][(c["label"], pred)] += 1
        if c["label"] == "rojo" and pred != "rojo":
            errores_rojo.append(caso_id)

    imprimir_reporte(matriz, por_capa, errores_rojo, usar_llm=args.llm)


def imprimir_reporte(matriz, por_capa, errores_rojo, usar_llm):
    total = sum(matriz.values())
    aciertos = sum(v for (gt, pred), v in matriz.items() if gt == pred)

    print("\n══════ RONDA · eval contra ground truth ══════")
    print(f"Modo: {'doble carril (reglas + LLM)' if usar_llm else 'solo carril determinista'}")
    print(f"Casos evaluados: {total}\n")
    print("Matriz de confusión (fila = real, columna = predicho):")
    print(f"{'':>10} {'verde':>8} {'amarillo':>9} {'rojo':>6}")
    for gt in NIVELES:
        fila = " ".join(f"{matriz[(gt, p)]:>8}" for p in NIVELES)
        print(f"{gt:>10} {fila}")

    for nivel in NIVELES:
        soporte = sum(matriz[(nivel, p)] for p in NIVELES)
        tp = matriz[(nivel, nivel)]
        recall = tp / soporte if soporte else 0
        marcador = "  ← MÉTRICA CRÍTICA" if nivel == "rojo" else ""
        print(f"\nRecall {nivel}: {recall:.1%} ({tp}/{soporte}){marcador}")

    print(f"\nExactitud global: {aciertos}/{total} = {aciertos/total:.1%}" if total else "")
    if errores_rojo:
        print(f"\n⚠ FALSOS NEGATIVOS EN ROJO ({len(errores_rojo)}): {errores_rojo}")
        print("  → Revisar config/red_flags.yaml y añadir patrones para estos casos.")
    else:
        print("\n✔ CERO falsos negativos en rojo.")

    if por_capa:
        print("\nDesglose por capa:")
        for capa, m in por_capa.items():
            t = sum(m.values())
            a = sum(v for (gt, p), v in m.items() if gt == p)
            rojos = sum(m[("rojo", p)] for p in NIVELES)
            rojos_ok = m[("rojo", "rojo")]
            print(f"  {capa}: exactitud {a}/{t}, recall rojo {rojos_ok}/{rojos}")

    resultado = {
        "matriz": {f"{gt}->{p}": matriz[(gt, p)] for gt in NIVELES for p in NIVELES},
        "falsos_negativos_rojo": errores_rojo,
    }
    out = BASE / "eval" / "ultimo_resultado.json"
    out.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResultado guardado en {out}")


if __name__ == "__main__":
    main()
