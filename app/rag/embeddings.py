# -*- coding: utf-8 -*-
"""Producción de embeddings: un modelo, dos motores de inferencia.

POR QUÉ EXISTE ESTE MÓDULO
--------------------------
`sentence-transformers` arrastra PyTorch. Medido en una instalación limpia:
torch 524 MB, transformers 113 MB y scipy 115 MB, para un total de ~1 GB de
descarga. Eso hacía imposible la compuerta G2 (levantar el proyecto en 15
minutos siguiendo solo el README).

FastEmbed ejecuta EL MISMO MODELO sobre ONNX Runtime —que ChromaDB ya instala
de todas formas— y no necesita PyTorch. El identificador del modelo no cambia:
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384 dimensiones,
licencia Apache 2.0. Son los mismos pesos por otro intérprete.

LO QUE NO CAMBIA
----------------
El troceado, el reordenamiento léxico, el umbral de distancia, la compuerta de
evidencia, `kb_version`, el intercambio en caliente y el olvido verificable
siguen exactamente igual. Aquí solo cambia QUIÉN multiplica las matrices.

INCOMPATIBILIDAD DE ÍNDICES
---------------------------
Un índice construido con un modelo no se puede consultar con otro: los vectores
viven en espacios distintos y las distancias dejan de significar nada. Por eso
`firma()` identifica backend + modelo + dimensión, se guarda junto al índice y
se compara al arrancar. Ante una discrepancia el sistema avisa en vez de
devolver vecinos sin sentido.
"""
from __future__ import annotations

import threading

from .. import config

_modelo = None
_lock = threading.Lock()

FASTEMBED = "fastembed"
SENTENCE_TRANSFORMERS = "sentence_transformers"


def backend() -> str:
    """Motor de inferencia activo. FastEmbed es el predeterminado público."""
    return (config.EMBEDDING_BACKEND or FASTEMBED).strip().lower()


def firma() -> str:
    """Identidad del espacio vectorial: backend, modelo y dimensión.

    Se guarda con el índice. Si cambia, el índice anterior es inservible y hay
    que reconstruirlo — no es una preferencia, es aritmética.
    """
    return f"{backend()}|{config.EMBEDDING_MODEL}|{dimension()}"


def dimension() -> int:
    """Dimensión del vector. 384 para el MiniLM multilingüe que usamos."""
    return int(getattr(config, "EMBEDDING_DIM", 384) or 384)


def _cargar():
    global _modelo
    with _lock:
        if _modelo is not None:
            return _modelo
        if backend() == SENTENCE_TRANSFORMERS:
            # Ruta heredada: solo para reproducir experimentos anteriores.
            # No se instala en el quick start (ver requirements-legacy-embeddings.txt).
            from sentence_transformers import SentenceTransformer

            _modelo = ("st", SentenceTransformer(config.EMBEDDING_MODEL))
        else:
            from fastembed import TextEmbedding

            _modelo = ("fe", TextEmbedding(model_name=config.EMBEDDING_MODEL))
        return _modelo


def _normalizar(v: list[float]) -> list[float]:
    """Norma L2 a 1. El almacén compara por coseno y da por hecho vectores
    unitarios; `sentence-transformers` lo hacía con `normalize_embeddings=True`
    y aquí se mantiene explícito para no depender del valor por defecto de
    ninguna librería."""
    s = sum(x * x for x in v) ** 0.5
    return [x / s for x in v] if s else v


def embed(texts: list[str]) -> list[list[float]]:
    """Vectoriza una lista de textos. Devuelve vectores unitarios."""
    if not texts:
        return []
    tipo, modelo = _cargar()
    if tipo == "st":
        return modelo.encode(texts, normalize_embeddings=True).tolist()
    return [_normalizar(v.tolist()) for v in modelo.embed(texts)]


def precalentar() -> None:
    """Carga el modelo antes de la primera llamada, para que el primer turno
    del paciente no pague la descarga ni la inicialización."""
    try:
        embed(["precalentamiento"])
    except Exception:
        # Un fallo aquí no puede tumbar el arranque: se verá en /api/salud.
        pass
