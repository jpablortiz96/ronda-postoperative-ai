# -*- coding: utf-8 -*-
"""Siembra la base de conocimiento de RONDA con el corpus clínico oficial.

POR QUÉ HACE FALTA
------------------
El índice vectorial y el manifiesto son artefactos de ejecución: no se
versionan, porque se reconstruyen. Los PDFs del corpus tampoco se redistribuyen
en este repositorio — pertenecen al kit oficial del reto y conservan los
derechos de sus autores. Sin este paso, una instalación recién clonada arranca
con cero documentos y RONDA se abstiene ante cualquier pregunta clínica: el
sistema funcionaría, pero no tendría nada que citar.

Este script descarga los seis documentos desde el repositorio OFICIAL, verifica
su SHA-256 contra el declarado en `config/bootstrap_corpus.json` y los ingiere
por la MISMA ruta que usa la consola en producción. Nada de atajos: si el
bootstrap funciona, la subida manual también.

USO
---
    python scripts/bootstrap_official_corpus.py

Sin conexión, o si el repositorio oficial ha movido los archivos, se puede
sembrar desde una copia local del kit ya descargado:

    python scripts/bootstrap_official_corpus.py --from-directory <ruta_al_kit>

Es idempotente: ejecutarlo dos veces no duplica documentos.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from app.rag import ingest, retrieve  # noqa: E402

MANIFIESTO = RAIZ / "config" / "bootstrap_corpus.json"
TIEMPO_ESPERA = 60


def sha256_de(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as fh:
        for bloque in iter(lambda: fh.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def ya_indexado(titulo: str) -> bool:
    """¿Existe ya un documento activo con ese título?"""
    for d in ingest.load_manifest().get("documentos", {}).values():
        if d.get("estado") == "disponible" and d.get("titulo") == titulo:
            return True
    return False


def descargar(base_url: str, ruta_upstream: str, destino: Path) -> None:
    # Cada segmento se codifica por separado: los nombres traen espacios y
    # tildes, y una barra codificada rompería la ruta.
    url = base_url + "/".join(
        urllib.parse.quote(p) for p in ruta_upstream.split("/"))
    with urllib.request.urlopen(url, timeout=TIEMPO_ESPERA) as r, \
            open(destino, "wb") as fh:
        fh.write(r.read())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from-directory", metavar="RUTA",
                    help="Copia local del kit oficial ya descargado, en lugar "
                         "de bajarlo de GitHub.")
    ap.add_argument("--force", action="store_true",
                    help="Reindexar aunque el documento ya esté activo.")
    args = ap.parse_args()

    cfg = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    documentos = cfg["documentos"]
    origen_local = Path(args.from_directory).resolve() if args.from_directory else None

    print("\n  RONDA · siembra del corpus clínico oficial")
    print(f"  origen: {origen_local or cfg['upstream_repo']}")
    print(f"  documentos declarados: {len(documentos)}\n")

    t0 = time.time()
    indexados = omitidos = fallidos = 0
    temporal = Path(tempfile.mkdtemp(prefix="ronda_corpus_"))

    try:
        for i, doc in enumerate(documentos, 1):
            titulo = doc["title"]
            print(f"  [{i}/{len(documentos)}] {titulo[:62]}")

            if ya_indexado(titulo) and not args.force:
                print("        ya está en la base de conocimiento, se omite")
                omitidos += 1
                continue

            destino = temporal / f"{i}.pdf"
            try:
                if origen_local:
                    fuente = origen_local / doc["upstream_path"]
                    if not fuente.exists():
                        # El kit suele venir dentro de una carpeta con el
                        # nombre del repositorio; se prueba también así.
                        alterna = origen_local / "ParticipantArtifacts-main" / doc["upstream_path"]
                        fuente = alterna if alterna.exists() else fuente
                    if not fuente.exists():
                        raise FileNotFoundError(fuente)
                    destino.write_bytes(fuente.read_bytes())
                    print("        copiado desde la copia local")
                else:
                    descargar(cfg["base_url"], doc["upstream_path"], destino)
                    print(f"        descargado ({destino.stat().st_size:,} bytes)")
            except (urllib.error.URLError, FileNotFoundError, OSError) as e:
                print(f"        NO SE PUDO OBTENER: {type(e).__name__}")
                fallidos += 1
                continue

            # La huella no es decorativa: si el archivo remoto cambió, este es
            # un documento distinto del que se usó para medir el sistema, y
            # conviene saberlo antes de indexarlo.
            real = sha256_de(destino)
            if doc.get("sha256") and real != doc["sha256"]:
                print(f"        AVISO: sha256 distinto del declarado ({real[:12]}…)")
                print("               se indexa igualmente, pero el corpus no es "
                      "idéntico al evaluado")

            try:
                # Misma función que usa POST /api/docs desde la consola: si el
                # bootstrap indexa, la subida manual también.
                res = ingest.ingest_file(destino, titulo)
                trozos = res.get("chunks") if isinstance(res, dict) else res
                print(f"        indexado · {trozos} fragmentos")
                indexados += 1
            except Exception as e:  # noqa: BLE001
                print(f"        FALLO AL INDEXAR: {type(e).__name__}: {e}")
                fallidos += 1
            finally:
                destino.unlink(missing_ok=True)
    finally:
        # Los PDFs no se quedan en el proyecto: se descargan, se ingieren y se
        # borran. Lo que persiste es el índice, no el material con derechos.
        for resto in temporal.glob("*"):
            resto.unlink(missing_ok=True)
        temporal.rmdir()

    activos = [d for d in ingest.load_manifest().get("documentos", {}).values()
               if d.get("estado") == "disponible"]
    print(f"\n  indexados {indexados} · omitidos {omitidos} · fallidos {fallidos}")
    print(f"  documentos activos: {len(activos)}")
    print(f"  kb_version: {retrieve.kb_version()[:20]}…")
    print(f"  tiempo: {time.time() - t0:.1f}s")

    # Comprobación final: la base no solo tiene documentos, responde.
    consulta = cfg.get("consulta_de_verificacion")
    if consulta:
        reg = retrieve.recuperar(consulta)
        if reg.hay_evidencia():
            ev = next(iter(reg.evidencias.values()))
            print(f"\n  verificación · «{consulta}»")
            print(f"    responde citando: {ev.document_title[:58]}")
        else:
            print(f"\n  VERIFICACIÓN FALLIDA: «{consulta}» no recupera evidencia")
            return 1

    if fallidos:
        print("\n  Algún documento no pudo obtenerse. Con el kit oficial ya "
              "descargado en local:")
        print("    python scripts/bootstrap_official_corpus.py --from-directory <ruta>")
        return 1
    print("\n  Base de conocimiento lista.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
