"""Almacén vectorial del conocimiento vivo (ChromaDB persistente).

Responsabilidades:
- Indexar chunks con embeddings multilingües (español médico).
- Borrado DURO por doc_id (compuerta G5: eliminar = olvidar de verdad).
- Consulta sonda para el botón "Verificar olvido" de la consola.
"""
from __future__ import annotations

import threading

import chromadb

from .. import config

_lock = threading.Lock()
_client = None
_collection = None
_embedder = None


def _get_collection():
    global _client, _collection
    with _lock:
        if _collection is None:
            _client = chromadb.PersistentClient(path=config.CHROMA_DIR)
            _collection = _client.get_or_create_collection(
                name=config.COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
            )
    return _collection


def embed(texts: list[str]) -> list[list[float]]:
    """Vectoriza texto. El motor lo elige `rag.embeddings`, no este módulo.

    Se delega para que el almacén no sepa —ni le importe— si detrás hay ONNX o
    PyTorch. Lo único que exige es que los vectores sean unitarios y tengan
    siempre la misma dimensión.
    """
    from . import embeddings

    return embeddings.embed(texts)


def add_chunks(doc_id: str, doc_title: str, chunks: list[str]) -> int:
    col = _get_collection()
    ids = [f"{doc_id}::{i}" for i in range(len(chunks))]
    metadatas = [
        {"doc_id": doc_id, "doc_title": doc_title, "chunk_index": i}
        for i in range(len(chunks))
    ]
    embeddings = embed(chunks)
    col.add(ids=ids, documents=chunks, metadatas=metadatas, embeddings=embeddings)
    return len(chunks)


def delete_doc(doc_id: str) -> None:
    """Borrado duro: los vectores del documento dejan de existir."""
    _get_collection().delete(where={"doc_id": doc_id})


def query(text: str, top_k: int | None = None) -> list[dict]:
    """Devuelve [{text, doc_id, doc_title, chunk_index, distance}] ordenados."""
    col = _get_collection()
    if col.count() == 0:
        return []
    k = min(top_k or config.RAG_TOP_K, col.count())
    res = col.query(query_embeddings=embed([text]), n_results=k)
    out = []
    for doc, meta, dist in zip(
        res["documents"][0], res["metadatas"][0], res["distances"][0]
    ):
        out.append(
            {
                "text": doc,
                "doc_id": meta["doc_id"],
                "doc_title": meta["doc_title"],
                "chunk_index": meta["chunk_index"],
                "distance": round(float(dist), 4),
            }
        )
    return out


def probe_forgotten(doc_id: str, sample_terms: list[str]) -> dict:
    """Prueba observable de olvido para la consola.

    1) Cuenta directa de vectores restantes con ese doc_id.
    2) Consulta sonda con los términos característicos del documento y
       verificación de que ningún resultado provenga de él.
    """
    col = _get_collection()
    remaining = col.get(where={"doc_id": doc_id})
    remaining_count = len(remaining.get("ids", []))
    probe_hits = []
    if sample_terms and col.count() > 0:
        results = query(" ".join(sample_terms), top_k=8)
        probe_hits = [r for r in results if r["doc_id"] == doc_id]
    return {
        "doc_id": doc_id,
        "vectores_restantes": remaining_count,
        "consulta_sonda": " ".join(sample_terms),
        "coincidencias_del_documento": len(probe_hits),
        "olvidado": remaining_count == 0 and len(probe_hits) == 0,
    }


def collection_count() -> int:
    return _get_collection().count()
