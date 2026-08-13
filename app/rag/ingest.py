"""Ingesta de conocimiento vivo.

Flujo: archivo → texto (con fallback OCR para PDFs escaneados, como el de
Appendicitis/ del kit oficial) → chunking → embeddings → ChromaDB → manifiesto.

El manifiesto (data/manifest.json) es el registro auditable del conocimiento:
qué documento entró, con qué hash SHA-256, cuándo, en cuántos chunks, y qué
tombstone quedó cuando se eliminó. Es la evidencia de trazabilidad que pide
el criterio de 20 pts de RAG y conocimiento vivo.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import unicodedata
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from . import store

_manifest_lock = threading.Lock()

STOPWORDS = set(
    """de la que el en y a los del se las por un para con no una su al lo como
    mas pero sus le ya o este si porque esta entre cuando muy sin sobre tambien
    me hasta hay donde quien desde todo nos durante todos uno les ni contra
    otros ese eso ante ellos e esto mi antes algunos que unos yo otro otras
    otra el tanto esa estos mucho quienes nada muchos cual poco ella estar
    estas algunas algo nosotros the of and to in is for with on that as are
    was be this by an or from at it patient patients""".split()
)


# ── Manifiesto ──────────────────────────────────────────────────────────────
def load_manifest() -> dict:
    if config.MANIFEST_PATH.exists():
        return json.loads(config.MANIFEST_PATH.read_text(encoding="utf-8"))
    return {"documentos": {}, "tombstones": []}


def save_manifest(m: dict) -> None:
    config.MANIFEST_PATH.write_text(
        json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── Extracción de texto ─────────────────────────────────────────────────────
def extract_text(path: Path) -> tuple[str, str]:
    """Devuelve (texto, metodo). Métodos: txt | pdf | pdf+ocr."""
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="replace"), "txt"
    if suffix == ".pdf":
        text = _extract_pdf_text(path)
        if len(text.strip()) >= 120:
            return text, "pdf"
        # PDF escaneado sin capa de texto → OCR (opcional, degradación limpia)
        ocr = _extract_pdf_ocr(path)
        if ocr:
            return ocr, "pdf+ocr"
        return text, "pdf"
    raise ValueError(f"Formato no soportado: {suffix} (usa .pdf, .txt o .md)")


def _extract_pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return _reunir_letras_sueltas(
        "\n".join((page.extract_text() or "") for page in reader.pages))


# Algunos PDF —los hechos con herramientas de diseño, que en este corpus son
# justo los planes de cuidado para pacientes— guardan cada letra como un
# objeto de texto independiente. `extract_text` los devuelve así:
#
#   'P L A N  D E  C U I D A D O  E N  C A S A'
#
# Con UN espacio entre letras y DOS entre palabras. Esa distinción es la que
# permite reconstruir el texto: colapsar los espacios antes de mirarla —como
# hacía `chunk_text`— destruye la única pista disponible y deja el documento
# convertido en ruido.
#
# Indexado sin reparar, el documento es irrecuperable: sus embeddings no se
# parecen a ninguna pregunta escrita en español normal. Medido: el plan de
# apendicectomía fallaba las 10 preguntas construidas sobre él (Recall@5 = 0).


def _reunir_letras_sueltas(texto: str) -> str:
    """Reconstruye las líneas que vienen con las letras separadas.

    Solo actúa cuando la línea está dominada por tokens de una sola letra,
    que en español no ocurre de forma natural. Una frase normal no se toca.
    """
    salida = []
    for linea in texto.split("\n"):
        tokens = linea.split()
        sueltos = sum(1 for t in tokens if len(t) == 1 and t.isalpha())
        if len(tokens) < 4 or sueltos / len(tokens) <= 0.5:
            salida.append(linea)
            continue
        # Dos o más espacios = frontera de palabra; uno solo = dentro de la
        # palabra. Se reconstruye respetando esa convención.
        palabras = [re.sub(r"\s+", "", p) for p in re.split(r"\s{2,}", linea.strip())]
        salida.append(" ".join(p for p in palabras if p))
    return "\n".join(salida)


def _extract_pdf_ocr(path: Path) -> str:
    """OCR opcional. Requiere: pip install pytesseract pdf2image + binarios
    tesseract-ocr (con idioma spa) y poppler. Si no están, devuelve ''."""
    try:
        import pytesseract
        from pdf2image import convert_from_path

        pages = convert_from_path(str(path), dpi=200)
        return "\n".join(pytesseract.image_to_string(p, lang="spa+eng") for p in pages)
    except Exception:
        return ""


# ── Chunking ────────────────────────────────────────────────────────────────
def chunk_text(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    chunks, start = [], 0
    size, overlap = config.CHUNK_SIZE, config.CHUNK_OVERLAP
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # corta en el último fin de oración o salto de línea del tramo
            cut = max(text.rfind(". ", start, end), text.rfind("\n", start, end))
            if cut > start + int(size * 0.5):
                end = cut + 1
        chunk = text[start:end].strip()
        if len(chunk) > 40:
            chunks.append(chunk)
        if end >= len(text):
            # El tramo ya cerró el documento. Sin este corte, `start` avanzaba
            # de a un carácter y reemitía la cola ~110 veces por documento,
            # inundando el top-k con fragmentos casi idénticos.
            break
        start = max(end - overlap, start + 1)
    return chunks


def _sample_terms(text: str, n: int = 8) -> list[str]:
    """Términos característicos del documento, usados por la consulta sonda
    del botón 'Verificar olvido'."""
    words = re.findall(r"[a-záéíóúñü]{5,}", text.lower())
    words = [
        unicodedata.normalize("NFC", w) for w in words if w not in STOPWORDS
    ]
    return [w for w, _ in Counter(words).most_common(n)]


# ── API pública ─────────────────────────────────────────────────────────────
def ingest_file(path: Path, original_name: str) -> dict:
    raw = path.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    manifest = _with_lock(load_manifest)

    # Deduplicación por hash: el kit oficial trae documentos repetidos
    for doc_id, doc in manifest["documentos"].items():
        if doc["sha256"] == sha256 and doc["estado"] == "disponible":
            return {**doc, "doc_id": doc_id, "duplicado": True}

    doc_id = uuid.uuid4().hex[:12]
    entry = {
        "titulo": original_name,
        "sha256": sha256,
        "estado": "procesando",
        "metodo_extraccion": None,
        "chunks": 0,
        "terminos_sonda": [],
        "ingresado": datetime.now(timezone.utc).isoformat(),
    }
    manifest["documentos"][doc_id] = entry
    _with_lock(save_manifest, manifest)

    try:
        text, method = extract_text(path)
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError(
                "No se pudo extraer texto. Si es un PDF escaneado, instala el "
                "extra de OCR (ver README §OCR)."
            )
        n = store.add_chunks(doc_id, original_name, chunks)
        entry.update(
            estado="disponible",
            metodo_extraccion=method,
            chunks=n,
            terminos_sonda=_sample_terms(text),
        )
    except Exception as e:
        entry.update(estado="error", error=str(e))
    manifest["documentos"][doc_id] = entry
    _with_lock(save_manifest, manifest)
    return {**entry, "doc_id": doc_id, "duplicado": False}


def delete_document(doc_id: str) -> dict:
    manifest = load_manifest()
    doc = manifest["documentos"].get(doc_id)
    if not doc:
        raise KeyError(doc_id)
    store.delete_doc(doc_id)
    tombstone = {
        "doc_id": doc_id,
        "titulo": doc["titulo"],
        "sha256": doc["sha256"],
        "eliminado": datetime.now(timezone.utc).isoformat(),
        "terminos_sonda": doc.get("terminos_sonda", []),
    }
    manifest["tombstones"].append(tombstone)
    doc["estado"] = "eliminado"
    _with_lock(save_manifest, manifest)
    return tombstone


def verify_forgotten(doc_id: str) -> dict:
    manifest = load_manifest()
    doc = manifest["documentos"].get(doc_id)
    terms = (doc or {}).get("terminos_sonda", [])
    if not terms:
        for t in manifest["tombstones"]:
            if t["doc_id"] == doc_id:
                terms = t.get("terminos_sonda", [])
    return store.probe_forgotten(doc_id, terms)


def list_documents() -> list[dict]:
    manifest = load_manifest()
    return [
        {"doc_id": doc_id, **doc}
        for doc_id, doc in manifest["documentos"].items()
        if doc["estado"] != "eliminado"
    ]


def _with_lock(fn, *args):
    with _manifest_lock:
        return fn(*args)
