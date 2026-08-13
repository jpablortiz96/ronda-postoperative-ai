# -*- coding: utf-8 -*-
"""Evaluación completa con el carril LLM activo, reanudable.

POR QUÉ NO ES UN BUCLE SIMPLE
-----------------------------
Son 320 conversaciones y ~2.000 llamadas al proveedor. En serie tarda cerca de
una hora, y cualquier corte —un 429, una caída de red, un Ctrl-C— obligaría a
pagar de nuevo todo lo ya hecho. Por eso:

    · CHECKPOINT   cada conversación terminada se escribe a un JSONL; el
                   proceso puede morir en cualquier punto sin perder gasto.
    · REANUDACIÓN  al arrancar se leen las ya hechas y se saltan. Nunca se
                   repite una llamada ya pagada.
    · CONCURRENCIA acotada, por CONVERSACIÓN. Cada conversación mantiene su
                   propio estado (`slots`) en variables locales, así que no hay
                   forma de que el estado de un paciente contamine a otro. Los
                   turnos DENTRO de una conversación siguen siendo secuenciales
                   porque el estado se acumula turno a turno.
    · BACKOFF      vive en `app/llm.py`, que es donde está la petición: respeta
                   `Retry-After` y espera exponencial con jitter.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm  # noqa: E402
from eval import folds, huellas, run_v2  # noqa: E402

NIVELES = ("verde", "amarillo", "rojo")
_lock_escritura = threading.Lock()


def clave(conv) -> str:
    return f"{conv['caso_id']}|{conv['capa']}"


def cargar_checkpoint(ruta: Path) -> dict[str, dict]:
    if not ruta.exists():
        return {}
    hechas = {}
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            r = json.loads(linea)
        except json.JSONDecodeError:
            continue  # línea a medio escribir por un corte: se recalcula
        hechas[f"{r['caso_id']}|{r['capa']}"] = r
    return hechas


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kit-root", type=Path, required=True)
    ap.add_argument("--checkpoint", type=Path, required=True)
    ap.add_argument("--concurrencia", type=int, default=6)
    ap.add_argument("--limite", type=int)
    ap.add_argument("--solo-estimar", action="store_true",
                    help="calcula el costo y no ejecuta nada")
    a = ap.parse_args()

    h = huellas.imprimir()
    df, proc, _ = run_v2.cargar(a.kit_root, None, None, None)
    convs = run_v2.conversaciones(df, incluir_tercero=True)
    if a.limite:
        convs = convs[:a.limite]
    turnos = sum(len(c["turnos"]) for c in convs)

    # Estimación medida, no inventada: 561 tokens de entrada y 230 de salida
    # por extracción, promediados sobre 100 extracciones reales del dominio.
    p_in, p_out = 0.59, 0.79   # USD por millón
    costo = turnos * 561 / 1e6 * p_in + turnos * 230 / 1e6 * p_out
    print(f"\n  conversaciones={len(convs)}  turnos={turnos}")
    print(f"  costo estimado: ${costo:.2f} USD  (tope autorizado: $10)")
    if costo > 10:
        print("  ABORTA: la estimación supera el tope autorizado.")
        return 1
    if a.solo_estimar:
        return 0

    hechas = cargar_checkpoint(a.checkpoint)
    pendientes = [c for c in convs if clave(c) not in hechas]
    print(f"  ya completadas: {len(hechas)}   pendientes: {len(pendientes)}")
    if hechas:
        print("  (reanudando: no se repite ninguna llamada ya pagada)")

    llm.reiniciar_contadores()
    a.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    salida = a.checkpoint.open("a", encoding="utf-8")
    errores = Counter()
    t0 = time.time()

    def trabajo(conv):
        procedimiento = proc.get(conv["paciente_id"])
        nivel, slots, traza, u, cierre = run_v2.evaluar_conversacion(conv, procedimiento)
        degradados = sum(1 for t in traza if t.get("degradado"))
        motivos = Counter(t.get("motivo_degradado") for t in traza if t.get("degradado"))
        return {
            "motivos_degradado": dict(motivos),
            "turnos_totales": len(traza),
            "caso_id": conv["caso_id"], "capa": conv["capa"],
            "paciente_id": conv["paciente_id"], "label": conv["label"],
            "procedimiento": procedimiento, "prediccion": nivel,
            "cierre": cierre, "usage": u, "turnos_degradados": degradados,
        }

    completadas = 0
    with ThreadPoolExecutor(max_workers=a.concurrencia) as pool:
        futuros = {pool.submit(trabajo, c): c for c in pendientes}
        for fut in as_completed(futuros):
            conv = futuros[fut]
            try:
                r = fut.result()
            except Exception as e:  # noqa: BLE001
                errores[type(e).__name__] += 1
                print(f"    ERROR {clave(conv)}: {type(e).__name__}: {str(e)[:80]}")
                continue
            with _lock_escritura:
                salida.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
                salida.flush()   # el checkpoint solo sirve si llega al disco
                hechas[clave(conv)] = r
            completadas += 1
            if completadas % 25 == 0:
                c = llm.contadores()
                print(f"    … {completadas}/{len(pendientes)}   "
                      f"peticiones={c['peticiones']} reintentos={c['reintentos']} "
                      f"({time.time() - t0:.0f} s)", flush=True)
    salida.close()

    resultados = [hechas[clave(c)] for c in convs if clave(c) in hechas]
    cont = llm.contadores()
    tok_in = sum(r["usage"]["in"] for r in resultados)
    tok_out = sum(r["usage"]["out"] for r in resultados)
    llamadas = sum(r["usage"]["llamadas"] for r in resultados)
    degradados = sum(r.get("turnos_degradados", 0) for r in resultados)

    print("\n" + "=" * 78)
    print(f"GEMINI FULL — {len(resultados)} conversaciones   huella {h['huella_motor']}")
    print("=" * 78)
    print(f"  peticiones al proveedor : {cont['peticiones']}")
    print(f"  llamadas con éxito      : {llamadas}")
    print(f"  reintentos              : {cont['reintentos']}  "
          f"(espera acumulada {cont['esperas_s']:.1f} s)")
    turnos_tot = sum(r.get("turnos_totales", 0) for r in resultados) or 1
    motivos = Counter()
    for r in resultados:
        motivos.update(r.get("motivos_degradado") or {})
    print(f"  turnos con LLM / totales: {llamadas} / {turnos_tot}  "
          f"({llamadas / turnos_tot:.1%} de cobertura del modelo)")
    print(f"  turnos en modo degradado: {degradados}  {dict(motivos)}")
    if degradados > turnos_tot * 0.1:
        # Una corrida donde el modelo apenas participó NO es una medición del
        # modelo. Decirlo aquí evita que el número se cite como si lo fuera.
        print("  AVISO: más del 10% de los turnos corrió SIN el LLM. Estos "
              "resultados describen el motor determinista, no el carril LLM.")
    print(f"  errores no recuperados  : {sum(errores.values())}  {dict(errores)}")
    print(f"  tokens entrada / salida : {tok_in:,} / {tok_out:,}")
    print(f"  costo real              : ${tok_in / 1e6 * p_in + tok_out / 1e6 * p_out:.3f} USD")
    print(f"  duración                : {time.time() - t0:.0f} s")

    pares = [(r["label"], r["prediccion"]) for r in resultados]
    print("\n" + "-" * 78)
    from eval.estabilidad import matriz, transiciones
    print("GLOBAL")
    print(matriz(pares))
    print(transiciones(pares))
    for capa, etiqueta in (("limpia", "CAPA LIMPIA"), ("ruidosa", "CAPA RUIDOSA")):
        sub = [(r["label"], r["prediccion"]) for r in resultados if capa in str(r["capa"])]
        print(f"\n{etiqueta}")
        print(matriz(sub))
        print(transiciones(sub))

    p = folds.metricas_pareadas(resultados)
    print("\n" + "-" * 78)
    print("MÉTRICAS PAREADAS POR CASO")
    print("-" * 78)
    print(f"  casos rojos                : {p['casos_rojos']}")
    print(f"  recall rojo · limpia       : {p['recall_rojo_limpia']:.1%} "
          f"({p['detectados_limpia']}/{p['casos_rojos']})")
    print(f"  recall rojo · ruidosa      : {p['recall_rojo_ruidosa']:.1%} "
          f"({p['detectados_ruidosa']}/{p['casos_rojos']})")
    print(f"  recall rojo · PAREADO      : {p['recall_rojo_pareado']:.1%} "
          f"({p['detectados_ambas']}/{p['casos_rojos']})")
    print(f"  ROJO→VERDE                 : {len(p['fallo_catastrofico'])} "
          f"{p['fallo_catastrofico']}")
    print(f"  ROJO→AMARILLO              : {len(p['fallo_parcial'])} {p['fallo_parcial']}")
    print(f"  VERDE→ROJO                 : {len(p['verde_a_rojo'])} de {p['casos_verdes']}")
    print(f"  VERDE→AMARILLO             : {len(p['verde_a_amarillo'])}")

    asignacion = folds.repartir(df)
    print("\n  estabilidad por bloque:")
    print(f"  {'bloque':<8}{'R limpia':>10}{'R ruidosa':>11}{'R pareado':>11}{'R→V':>6}{'V→R':>6}")
    vals = []
    for b in range(folds.N_BLOQUES):
        sub = [r for r in resultados if asignacion.get(r["caso_id"]) == b]
        if not sub:
            continue
        pb = folds.metricas_pareadas(sub)
        vals.append(pb)
        print(f"  {b:<8}{pb['recall_rojo_limpia']:>9.0%}{pb['recall_rojo_ruidosa']:>10.0%}"
              f"{pb['recall_rojo_pareado']:>10.0%}"
              f"{len(pb['fallo_catastrofico']):>6}{len(pb['verde_a_rojo']):>6}")
    for k, nombre in (("recall_rojo_limpia", "recall rojo limpia"),
                      ("recall_rojo_ruidosa", "recall rojo ruidosa"),
                      ("recall_rojo_pareado", "recall rojo pareado")):
        v = [f[k] for f in vals]
        print(f"  {nombre:<22} media={sum(v)/len(v):.0%}  min={min(v):.0%}  max={max(v):.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
