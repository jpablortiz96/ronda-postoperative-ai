# -*- coding: utf-8 -*-
"""Experimentos OFFLINE de recuperación. No tocan producción.

Todo se ejecuta contra el mismo conjunto de evaluación y el mismo índice; lo
único que cambia es cómo se ordenan y filtran los candidatos. Ninguna
estrategia se entrena ni se ajusta sobre el conjunto: son reglas fijas que se
miden una vez.

    A  línea base            ranking vectorial tal cual
    B  procedure-aware       prioriza documentos del procedimiento del paciente
    C  rerank lexical        reordena los candidatos por solape de términos
    D  B + C

El criterio de adopción está en `CRITERIO` y es estricto a propósito: si
ninguna estrategia lo cumple, se conserva la línea base. Mejorar el Recall@1
rompiendo el @3, el @5 o la tasa de falsa recuperación no es una mejora.
"""
from __future__ import annotations

import math
import re
import statistics
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.rag import store  # noqa: E402
from eval import rag_preguntas  # noqa: E402

CANDIDATOS = 12   # se recuperan de más y cada estrategia reordena


def _norm(t: str) -> str:
    t = (t or "").lower()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return t


def _coincide(titulo: str, esperado: str) -> bool:
    a = _norm(titulo).replace(" ", "")
    b = _norm(esperado).replace(" ", "")
    return b[:36] in a or a[:36] in b


# ── Vocabulario de procedimiento ────────────────────────────────────────────
# RONDA conoce el procedimiento del paciente. Estos términos NO salen de las
# preguntas del conjunto de evaluación: son los nombres clínicos del
# procedimiento y su órgano, que el propio perfil del paciente ya contiene.
TERMINOS_PROCEDIMIENTO = {
    "Apendicectomía": ("apendic", "apendice", "appendect", "appendic"),
    "Colecistectomía": ("colecist", "vesicula", "biliar", "cholecyst", "gallbladder"),
    "Colectomía": ("colect", "colon", "colorrectal", "colorectal", "bowel"),
    "Reemplazo de cadera/rodilla": ("rodilla", "cadera", "articular", "artroplast",
                                    "knee", "hip", "joint"),
    "Mastectomía": ("mastect", "mama", "seno", "breast"),
}


def _es_del_procedimiento(titulo: str, procedimiento: str) -> bool:
    t = _norm(titulo)
    return any(term in t for term in TERMINOS_PROCEDIMIENTO.get(procedimiento, ()))


# ── C · rerank lexical ──────────────────────────────────────────────────────
_PALABRA = re.compile(r"[a-z0-9]{4,}")
_VACIAS = {"para", "puedo", "debo", "como", "cuando", "cuanto", "cuanta", "donde",
           "sobre", "esto", "esta", "este", "todo", "hacer", "tengo", "tener",
           "despues", "antes", "porque", "cual", "cuales", "algun", "alguna"}


def _terminos(texto: str) -> Counter:
    return Counter(w for w in _PALABRA.findall(_norm(texto)) if w not in _VACIAS)


def _puntaje_lexical(pregunta: str, texto: str) -> float:
    """Solape de términos, ponderado por rareza (BM25 simplificado).

    No añade dependencias: los documentos clínicos comparten terminología
    concreta ("piscina", "sumergir", "fibra", "estreñimiento") que el modelo
    de embeddings multilingüe pequeño diluye.
    """
    q, d = _terminos(pregunta), _terminos(texto)
    if not q or not d:
        return 0.0
    total = sum(d.values())
    puntaje = 0.0
    for term, _ in q.items():
        if term in d:
            # Frecuencia normalizada, saturada: repetir no multiplica.
            tf = d[term] / total
            puntaje += math.log1p(tf * 100) * (1 + len(term) / 12)
    return puntaje / math.sqrt(len(q))


# ── Estrategias ─────────────────────────────────────────────────────────────
def estrategia_a(pregunta, candidatos, procedimiento):
    return candidatos


def estrategia_b(pregunta, candidatos, procedimiento):
    """Documentos del procedimiento primero; el resto NO se descarta."""
    propios = [c for c in candidatos if _es_del_procedimiento(c["doc_title"], procedimiento)]
    otros = [c for c in candidatos if c not in propios]
    return propios + otros


def estrategia_c(pregunta, candidatos, procedimiento):
    return sorted(candidatos, key=lambda c: -_puntaje_lexical(pregunta, c["text"]))


def estrategia_d(pregunta, candidatos, procedimiento):
    reordenados = estrategia_c(pregunta, candidatos, procedimiento)
    return estrategia_b(pregunta, reordenados, procedimiento)


ESTRATEGIAS = {
    "A · línea base (vectorial)": estrategia_a,
    "B · procedure-aware": estrategia_b,
    "C · rerank lexical": estrategia_c,
    "D · B + C": estrategia_d,
}


def evaluar(estrategia) -> dict:
    con = rag_preguntas.con_respuesta()
    sin = rag_preguntas.sin_respuesta()
    aciertos = {1: 0, 3: 0, 5: 0}
    latencias = []

    for p in con:
        t0 = time.perf_counter()
        candidatos = store.query(p["pregunta"], top_k=CANDIDATOS)
        ordenados = estrategia(p["pregunta"], candidatos, p["procedimiento"])
        latencias.append((time.perf_counter() - t0) * 1000)
        pos = next((i + 1 for i, c in enumerate(ordenados)
                    if _coincide(c["doc_title"], p["doc_esperado"])), None)
        for k in (1, 3, 5):
            if pos and pos <= k:
                aciertos[k] += 1

    # Falsa recuperación: se mide sobre el top_k de PRODUCCIÓN y con el mismo
    # umbral, porque es lo que decide si el agente cree tener fundamento.
    falsas = 0
    for p in sin:
        candidatos = store.query(p["pregunta"], top_k=CANDIDATOS)
        ordenados = estrategia(p["pregunta"], candidatos, p["procedimiento"])
        top = ordenados[:config.RAG_TOP_K]
        if any(c["distance"] <= config.RAG_MAX_DISTANCE for c in top):
            falsas += 1

    n = len(con) or 1
    latencias.sort()
    return {
        "recall": {k: aciertos[k] / n for k in (1, 3, 5)},
        "falsa": falsas / (len(sin) or 1),
        "p50": statistics.median(latencias) if latencias else 0,
        "p95": latencias[int(len(latencias) * 0.95)] if latencias else 0,
    }


CRITERIO = """Adoptar solo si TODAS se cumplen frente a la línea base:
  1. Recall@1 mejora de forma material (>= +10 puntos)
  2. Recall@3 no empeora
  3. Recall@5 no empeora
  4. falsa recuperación no aumenta
  5-7. hot-swap, alta y olvido siguen funcionando (suite aparte)
  8. latencia sigue siendo apropiada"""


def main() -> int:
    print("=" * 82)
    print("EXPERIMENTOS OFFLINE DE RECUPERACIÓN — no modifican producción")
    print("=" * 82)
    print(f"  vectores={store.collection_count()}  candidatos por consulta={CANDIDATOS}")
    print(f"  top_k producción={config.RAG_TOP_K}  umbral={config.RAG_MAX_DISTANCE}\n")

    print(f"  {'estrategia':<28}{'R@1':>7}{'R@3':>7}{'R@5':>7}{'falsa':>8}"
          f"{'p50':>8}{'p95':>8}")
    print("  " + "-" * 72)
    resultados = {}
    for nombre, fn in ESTRATEGIAS.items():
        r = evaluar(fn)
        resultados[nombre] = r
        print(f"  {nombre:<28}{r['recall'][1]:>6.0%}{r['recall'][3]:>7.0%}"
              f"{r['recall'][5]:>7.0%}{r['falsa']:>8.0%}"
              f"{r['p50']:>7.0f}m{r['p95']:>7.0f}m")

    base = resultados["A · línea base (vectorial)"]
    print("\n" + "-" * 82)
    print("CRITERIO DE ADOPCIÓN")
    print("-" * 82)
    for linea in CRITERIO.splitlines():
        print("  " + linea)
    print()
    ganadora = None
    for nombre, r in resultados.items():
        if nombre.startswith("A"):
            continue
        d1 = r["recall"][1] - base["recall"][1]
        cumple = (d1 >= 0.10
                  and r["recall"][3] >= base["recall"][3]
                  and r["recall"][5] >= base["recall"][5]
                  and r["falsa"] <= base["falsa"])
        motivo = []
        if d1 < 0.10:
            motivo.append(f"R@1 solo {d1:+.0%}")
        if r["recall"][3] < base["recall"][3]:
            motivo.append(f"R@3 baja {r['recall'][3] - base['recall'][3]:+.0%}")
        if r["recall"][5] < base["recall"][5]:
            motivo.append(f"R@5 baja {r['recall'][5] - base['recall'][5]:+.0%}")
        if r["falsa"] > base["falsa"]:
            motivo.append(f"falsa sube {r['falsa'] - base['falsa']:+.0%}")
        print(f"  {nombre:<28} {'CUMPLE' if cumple else 'no cumple'}"
              + ("" if cumple else "  (" + "; ".join(motivo) + ")"))
        if cumple and (ganadora is None or r["recall"][1] > resultados[ganadora]["recall"][1]):
            ganadora = nombre

    print()
    if ganadora:
        print(f"  → candidata a producción: {ganadora}")
        print("    (falta validar hot-swap, alta y olvido antes de adoptarla)")
    else:
        print("  → NINGUNA cumple el criterio. Se CONSERVA la línea base.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
