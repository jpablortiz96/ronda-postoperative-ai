"""Harness de evaluación V2 — arquitectura actual de RONDA.

Qué corrige respecto de `eval/run.py`, que se conserva como línea base:

1. UNIDAD DE EVALUACIÓN. El kit trae cada `caso_id` en DOS capas
   (`capa1_limpia` y `capa2_ruidosa`). Agrupar solo por `caso_id` fusiona dos
   conversaciones distintas en una: 320 conversaciones reales se convertían en
   160 mezcladas. Aquí la unidad es `(caso_id, capa)`.
2. CONVERSACIÓN, NO BOLSA DE TEXTO. Los turnos se reproducen en orden por
   `turno_idx`, manteniendo el estado entre ellos. Concatenarlos anulaba la
   fusión de slots, los máximos históricos y el carril histórico.
3. CARRIL NUMÉRICO. El legacy solo llamaba a `evaluate_text`, así que un
   "nueve de diez" no cruzaba ningún umbral sin LLM.
4. TERCEROS. El kit incluye turnos de `tercero` (un familiar que contesta).
   En una llamada real ese audio entra por el mismo camino, así que aquí
   también se evalúa.

El harness NO reimplementa nada clínico: importa las funciones del producto.
Si mañana cambian las reglas o los umbrales, esta evaluación mide lo nuevo.

Uso:
    python eval/run_v2.py --kit-root D:/TechSphere2026-Official/ParticipantArtifacts-main
    python eval/run_v2.py --kit-root ... --llm        # incluye el carril LLM
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import pandas as pd  # noqa: E402

from app.decision import assess, engine, rules  # noqa: E402
from eval.split import dividir, resumen  # noqa: E402

NIVELES = ["verde", "amarillo", "rojo"]
_EXTRACTOR_REAL = assess.extract_slots


def _extractor_desactivado(patient_text, contexto, historial=None):
    """Reproduce EXACTAMENTE la degradación real del producto cuando el
    proveedor no responde: assess captura el fallo y devuelve slots vacíos.
    Permite medir el sistema sin LLM sin tocar una línea de `app/`."""
    return assess._merge_slots(historial or {}, assess._empty_slots()), \
        {"provider": "none", "input_tokens": 0, "output_tokens": 0}


# ── Carga del kit ───────────────────────────────────────────────────────────
def cargar(kit_root: Path, dataset: Path | None, perfiles: Path | None,
           trayectorias: Path | None):
    ds = dataset or (kit_root / "dataset" / "dataset_final.xlsx")
    pf = perfiles or (kit_root / "dataset" / "perfiles_clinicos_pacientes_silver_contest.xlsx")
    tr = trayectorias or (kit_root / "dataset" / "trayectorias_postop_silver.xlsx")
    df = pd.read_excel(ds, sheet_name="result")
    perf = pd.read_excel(pf, sheet_name="result")
    proc = dict(zip(perf["paciente_id"].astype(str), perf["procedimiento"].astype(str)))
    tray = None
    if tr.exists():
        # Solo para análisis posterior. NUNCA entra al motor: sería fuga de
        # información que el agente no puede conocer durante la llamada.
        tray = pd.read_excel(tr, sheet_name="result")
    return df, proc, tray


def conversaciones(df: pd.DataFrame, incluir_tercero: bool):
    """Devuelve [(caso_id, capa, paciente_id, [textos en orden], label)]."""
    roles = {"paciente", "tercero"} if incluir_tercero else {"paciente"}
    salida = []
    for (caso, capa), g in df.groupby(["caso_id", "capa"], sort=True):
        g = g.sort_values("turno_idx")
        # Los turnos del agente no se evalúan, pero SÍ se conservan como
        # contexto: la pregunta que precede a cada respuesta es lo que resuelve
        # los pronombres del paciente ("se ve rojita" → la herida). En
        # producción ese contexto lo aporta el checklist de temas del FSM.
        turnos = []
        ultima_pregunta = ""
        for r in g.itertuples():
            hablante = str(r.hablante)
            if hablante in roles:
                turnos.append((int(r.turno_idx), hablante, str(r.texto), ultima_pregunta))
            else:
                ultima_pregunta = str(r.texto)
        salida.append({
            "caso_id": caso, "capa": capa,
            "paciente_id": str(g["paciente_id"].iloc[0]),
            "label": str(g["label_ground_truth"].iloc[0]).strip().lower(),
            "turnos": turnos,
        })
    return salida


# ── Reproducción de una conversación ────────────────────────────────────────
def evaluar_conversacion(conv, procedimiento, pausa_ms=0):
    """Recorre los turnos manteniendo estado, igual que CallSession.

    Devuelve la criticidad final de la llamada, que es lo que el acta reporta
    como `criticidad_final`: el máximo alcanzado, nunca el último nivel.
    """
    slots: dict = {}
    nivel_max = "verde"
    traza = []
    usage_total = {"llamadas": 0, "in": 0, "out": 0}
    contexto = f"Procedimiento: {procedimiento or 'desconocido'}."
    for idx, hablante, texto, pregunta_previa in conv["turnos"]:
        if pausa_ms:
            time.sleep(pausa_ms / 1000)
        d = engine.decide(texto, procedimiento, contexto, slots, turno=idx, hablante=hablante,
                          pregunta_previa=pregunta_previa)
        slots = d["slots"]
        u = d.get("usage") or {}
        if u.get("provider") not in (None, "none"):
            usage_total["llamadas"] += 1
            usage_total["in"] += int(u.get("input_tokens") or 0)
            usage_total["out"] += int(u.get("output_tokens") or 0)
        if rules.LEVELS[d["nivel_final"]] > rules.LEVELS[nivel_max]:
            nivel_max = d["nivel_final"]
        traza.append({
            "turno_idx": idx, "hablante": hablante, "texto": texto,
            "niveles": d["niveles"], "nivel_turno": d["nivel_final"],
            "reglas": [x.get("regla") or x.get("tipo") for x in d["disparos"]],
            "valores": rules.extraer_valores(texto),
            "nivel_max_tras_turno": nivel_max,
            # Si este turno corrió SIN el LLM, queda dicho aquí. Sin este
            # campo el runner contaba siempre cero turnos degradados y una
            # corrida con la cuota agotada se reportaba como si el modelo
            # hubiera participado: una métrica que no describe lo ocurrido.
            "degradado": bool(u.get("modo_degradado")),
            "motivo_degradado": u.get("motivo") or "",
        })

    # CIERRE: aquí, y solo aquí, se aplica la compuerta de verde. Durante la
    # entrevista siempre faltan dominios; lo que importa es si al terminar se
    # logró cubrir lo crítico.
    cierre = engine.cerrar_llamada(nivel_max, slots.get("_cobertura"))
    return cierre["nivel_final"], slots, traza, usage_total, cierre


# ── Métricas ────────────────────────────────────────────────────────────────
def metricas(pares):
    """pares = [(real, predicho)]"""
    m = Counter(pares)
    total = len(pares)
    out = {"matriz": {f"{a}->{b}": m[(a, b)] for a in NIVELES for b in NIVELES},
           "total": total, "por_clase": {}}
    aciertos = sum(m[(n, n)] for n in NIVELES)
    out["accuracy"] = aciertos / total if total else 0
    f1s = []
    for n in NIVELES:
        tp = m[(n, n)]
        fn = sum(m[(n, p)] for p in NIVELES if p != n)
        fp = sum(m[(r, n)] for r in NIVELES if r != n)
        rec = tp / (tp + fn) if tp + fn else 0.0
        pre = tp / (tp + fp) if tp + fp else 0.0
        f1 = 2 * pre * rec / (pre + rec) if pre + rec else 0.0
        f1s.append(f1)
        out["por_clase"][n] = {"soporte": tp + fn, "tp": tp, "fn": fn, "fp": fp,
                               "recall": rec, "precision": pre, "f1": f1}
    out["macro_f1"] = statistics.mean(f1s)
    out["asimetria"] = {
        "rojo->verde": m[("rojo", "verde")],
        "rojo->amarillo": m[("rojo", "amarillo")],
        "amarillo->verde": m[("amarillo", "verde")],
        "verde->rojo": m[("verde", "rojo")],
    }
    return out


def imprimir(titulo, met):
    print(f"\n{'─' * 78}\n{titulo}   (n={met['total']})\n{'─' * 78}")
    print(f"{'':>12} {'Pred V':>8} {'Pred A':>8} {'Pred R':>8}")
    for r in NIVELES:
        fila = "".join(f"{met['matriz'][f'{r}->{p}']:>9}" for p in NIVELES)
        print(f"{'Real ' + r.upper()[:1] + ' (' + r + ')':>12}{fila}")
    print(f"\n  accuracy {met['accuracy']:.1%}   macro-F1 {met['macro_f1']:.3f}")
    print(f"  {'clase':<10} {'sop':>5} {'recall':>9} {'precision':>10} {'F1':>7}")
    for n in NIVELES:
        c = met["por_clase"][n]
        marca = "  ← CRÍTICA" if n == "rojo" else ""
        print(f"  {n:<10} {c['soporte']:>5} {c['recall']:>8.1%} {c['precision']:>9.1%} "
              f"{c['f1']:>7.3f}{marca}")
    a = met["asimetria"]
    print(f"  asimetría clínica: ROJO→VERDE={a['rojo->verde']}  ROJO→AMARILLO={a['rojo->amarillo']}"
          f"  AMARILLO→VERDE={a['amarillo->verde']}  VERDE→ROJO={a['verde->rojo']}")


# ── Ejecución de un modo ────────────────────────────────────────────────────
def correr(convs, proc_por_paciente, usar_llm, pausa_ms=0):
    if not usar_llm:
        assess.extract_slots = _extractor_desactivado
    resultados = []
    usage = {"llamadas": 0, "in": 0, "out": 0}
    t0 = time.perf_counter()
    try:
        for i, c in enumerate(convs, 1):
            procedimiento = proc_por_paciente.get(c["paciente_id"])
            nivel, slots, traza, u, cierre = evaluar_conversacion(c, procedimiento, pausa_ms)
            for k in usage:
                usage[k] += u[k]
            resultados.append({**c, "procedimiento": procedimiento,
                               "prediccion": nivel, "slots": slots, "traza": traza,
                               "cierre": cierre})
            if usar_llm and i % 20 == 0:
                print(f"    … {i}/{len(convs)} conversaciones", flush=True)
    finally:
        assess.extract_slots = _EXTRACTOR_REAL
    return resultados, usage, time.perf_counter() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit-root", type=Path, help="raíz del kit oficial descomprimido")
    ap.add_argument("--dataset", type=Path)
    ap.add_argument("--perfiles", type=Path)
    ap.add_argument("--trayectorias", type=Path)
    ap.add_argument("--llm", action="store_true", help="incluye el carril LLM (consume API)")
    ap.add_argument("--sin-tercero", action="store_true",
                    help="excluye los turnos de terceros (por defecto se incluyen)")
    ap.add_argument("--limite", type=int)
    ap.add_argument("--particion", choices=["dev", "holdout", "todo"], default="todo",
                    help="dev para diseñar; holdout se ejecuta UNA sola vez al final")
    ap.add_argument("--solo-label", choices=NIVELES,
                    help="evalúa solo las conversaciones con ese ground truth")
    ap.add_argument("--pausa-ms", type=int, default=0,
                    help="espera entre turnos; necesario para no agotar el "
                         "límite de peticiones del proveedor en runs largos")
    ap.add_argument("--salida", type=Path, default=BASE / "eval" / "resultado_v2.json")
    args = ap.parse_args()

    if not args.kit_root and not args.dataset:
        raise SystemExit("indique --kit-root o --dataset")

    df, proc, tray = cargar(args.kit_root, args.dataset, args.perfiles, args.trayectorias)
    convs = conversaciones(df, incluir_tercero=not args.sin_tercero)
    if args.particion != "todo":
        dev, hold = dividir(df)
        print(resumen(df, dev, hold))
        elegidos = dev if args.particion == "dev" else hold
        convs = [c for c in convs if c["caso_id"] in elegidos]
    if args.solo_label:
        convs = [c for c in convs if c["label"] == args.solo_label]
    if args.limite:
        convs = convs[: args.limite]

    modo = "V2-FULL (texto + numérico + slots + histórico + LLM)" if args.llm \
        else "V2-DETERMINISTIC (texto + numérico + histórico, SIN LLM)"
    print("=" * 78)
    print(f"RONDA · harness V2 · {modo}")
    print("=" * 78)
    print(f"  conversaciones (caso_id, capa): {len(convs)}")
    print(f"  turnos evaluados              : {sum(len(c['turnos']) for c in convs)}")
    print(f"  terceros incluidos            : {not args.sin_tercero}")

    resultados, usage, dur = correr(convs, proc, args.llm, args.pausa_ms)

    pares = [(r["label"], r["prediccion"]) for r in resultados]
    imprimir("GLOBAL", metricas(pares))
    por_capa = defaultdict(list)
    for r in resultados:
        por_capa[r["capa"]].append((r["label"], r["prediccion"]))
    for capa in sorted(por_capa):
        imprimir(capa.upper(), metricas(por_capa[capa]))

    fn = [r for r in resultados if r["label"] == "rojo" and r["prediccion"] != "rojo"]
    fp = [r for r in resultados if r["label"] != "rojo" and r["prediccion"] == "rojo"]
    print(f"\n{'─' * 78}\nFALSOS NEGATIVOS ROJOS: {len(fn)}\n{'─' * 78}")
    for r in fn:
        print(f"  {r['caso_id']} | {r['capa']} | {r['paciente_id']} | {r['procedimiento']} "
              f"| predicho={r['prediccion']}")
    print(f"\nFALSOS POSITIVOS ROJOS: {len(fp)}")
    for r in fp[:20]:
        reglas = sorted({g for t in r["traza"] for g in t["reglas"]})
        print(f"  {r['caso_id']} | {r['capa']} | real={r['label']} | reglas={reglas}")
    if len(fp) > 20:
        print(f"  … y {len(fp) - 20} más")

    print(f"\n{'─' * 78}\nCOSTE\n{'─' * 78}")
    print(f"  duración                : {dur:.1f} s")
    print(f"  llamadas al modelo      : {usage['llamadas']:,}")
    print(f"  tokens entrada / salida : {usage['in']:,} / {usage['out']:,}")
    from app import config
    costo = usage["in"] / 1e6 * config.COST_INPUT_PER_M + \
        usage["out"] / 1e6 * config.COST_OUTPUT_PER_M
    print(f"  costo estimado          : ${costo:.4f} USD"
          f"  (entrada ${config.COST_INPUT_PER_M}/M, salida ${config.COST_OUTPUT_PER_M}/M)")

    args.salida.write_text(json.dumps({
        "modo": modo, "conversaciones": len(convs),
        "global": metricas(pares),
        "por_capa": {c: metricas(v) for c, v in por_capa.items()},
        "falsos_negativos_rojo": [{k: r[k] for k in
                                   ("caso_id", "capa", "paciente_id", "procedimiento",
                                    "label", "prediccion")} | {"traza": r["traza"]} for r in fn],
        "falsos_positivos_rojo": [{k: r[k] for k in
                                   ("caso_id", "capa", "label", "prediccion")} |
                                  {"reglas": sorted({g for t in r["traza"] for g in t["reglas"]})}
                                  for r in fp],
        "coste": {**usage, "duracion_s": round(dur, 1), "costo_usd": round(costo, 4)},
        "detalle": [{k: r[k] for k in ("caso_id", "capa", "paciente_id", "procedimiento",
                                       "label", "prediccion")} for r in resultados],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nresultado guardado en {args.salida}")


if __name__ == "__main__":
    main()



