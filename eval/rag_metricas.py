# -*- coding: utf-8 -*-
"""Métricas del recuperador: ¿traemos la fuente correcta cuando existe?

Se mide ANTES de tocar embeddings, chunking o umbral. Cambiar el modelo de
embeddings porque exista uno "mejor" sin haber medido el actual es sustituir
un sistema desconocido por otro desconocido.

    Recall@k              de las preguntas con respuesta en el corpus, en
                          cuántas aparece el documento correcto entre los k
                          primeros resultados.
    Falsa recuperación    de las preguntas SIN respuesta, en cuántas el
                          sistema devuelve algo por debajo del umbral —es
                          decir, cuántas veces creería tener fundamento para
                          responder cuando no lo hay.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.rag import retrieve, store  # noqa: E402
from eval import rag_preguntas  # noqa: E402


def _norm(t: str) -> str:
    t = (t or "").lower()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return " ".join(t.split())


def _coincide(titulo_recuperado: str, doc_esperado: str) -> bool:
    """El título indexado es el nombre del archivo; se compara laxo.

    Se comparan sin espacios porque algún nombre del corpus los trae partidos
    ("Recom endaciones Programa..."), y esa diferencia hacía contar como fallo
    del recuperador algo que era un fallo de esta comparación.
    """
    a = _norm(titulo_recuperado).replace(" ", "")
    b = _norm(doc_esperado).replace(" ", "")
    return b[:36] in a or a[:36] in b


def medir(top_k: int = 5) -> dict:
    con = rag_preguntas.con_respuesta()
    sin = rag_preguntas.sin_respuesta()
    aciertos = {1: 0, 3: 0, 5: 0}
    latencias, distancias_ok = [], []
    fallos = []

    for p in con:
        t0 = time.perf_counter()
        # MISMA ruta que producción: se recuperan candidatos y se reordenan con
        # `retrieve.ordenar`. Medir `store.query` a secas evaluaría el ranking
        # vectorial crudo, que ya no es lo que el agente usa.
        candidatos = store.query(p["pregunta"], top_k=max(top_k, retrieve.CANDIDATOS_PARA_RERANK))
        resultados = retrieve.ordenar(p["pregunta"], candidatos)[:top_k]
        latencias.append((time.perf_counter() - t0) * 1000)
        titulos = [r["doc_title"] for r in resultados]
        posicion = next((i + 1 for i, t in enumerate(titulos)
                         if _coincide(t, p["doc_esperado"])), None)
        for k in (1, 3, 5):
            if posicion and posicion <= k:
                aciertos[k] += 1
        if posicion:
            distancias_ok.append(resultados[posicion - 1]["distance"])
        else:
            fallos.append((p["pregunta"], p["doc_esperado"], titulos[:2]))

    # Falsa recuperación: ¿cuántas preguntas sin respuesta pasan el umbral?
    falsas, detalle_falsas = 0, []
    for p in sin:
        registro = retrieve.recuperar(p["pregunta"])
        if registro.hay_evidencia():
            falsas += 1
            primera = next(iter(registro.evidencias.values()))
            detalle_falsas.append((p["pregunta"], primera.document_title,
                                   primera.retrieval_score))

    n = len(con) or 1
    latencias.sort()
    return {
        "n_con_respuesta": len(con),
        "n_sin_respuesta": len(sin),
        "recall": {k: aciertos[k] / n for k in (1, 3, 5)},
        "fallos": fallos,
        "falsa_recuperacion": falsas / (len(sin) or 1),
        "detalle_falsas": detalle_falsas,
        "latencia_p50": statistics.median(latencias) if latencias else 0,
        "latencia_p95": latencias[int(len(latencias) * 0.95)] if latencias else 0,
        "distancia_media_acierto": statistics.mean(distancias_ok) if distancias_ok else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-k", type=int, default=5)
    a = ap.parse_args()

    print("=" * 78)
    print("RECUPERADOR · línea base")
    print("=" * 78)
    print(f"  embeddings   : {config.EMBEDDING_MODEL}")
    print(f"  chunk        : {config.CHUNK_SIZE} / solape {config.CHUNK_OVERLAP}")
    print(f"  top_k        : {config.RAG_TOP_K}   umbral distancia: {config.RAG_MAX_DISTANCE}")
    print(f"  vectores     : {store.collection_count()}")
    print(f"  kb_version   : {retrieve.kb_version()}")
    print()

    m = medir(a.top_k)
    print(f"  preguntas con respuesta : {m['n_con_respuesta']}")
    for k in (1, 3, 5):
        print(f"    Recall@{k}  {m['recall'][k]:>6.1%}")
    print(f"  falsa recuperación (sin respuesta): {m['falsa_recuperacion']:.1%} "
          f"de {m['n_sin_respuesta']}")
    print(f"  latencia p50/p95: {m['latencia_p50']:.0f} / {m['latencia_p95']:.0f} ms")
    if m["distancia_media_acierto"] is not None:
        print(f"  distancia media en aciertos: {m['distancia_media_acierto']:.3f}")

    if m["fallos"]:
        print(f"\n  NO se recuperó la fuente correcta ({len(m['fallos'])}):")
        for pregunta, esperado, obtenidos in m["fallos"]:
            print(f"    · «{pregunta[:56]}»")
            print(f"        esperado : {esperado[:64]}")
            print(f"        obtenido : {[o[:44] for o in obtenidos]}")
    if m["detalle_falsas"]:
        print(f"\n  recuperó evidencia donde NO hay respuesta ({len(m['detalle_falsas'])}):")
        for pregunta, titulo, dist in m["detalle_falsas"]:
            print(f"    · «{pregunta[:52]}» → {titulo[:44]} (d={dist})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
