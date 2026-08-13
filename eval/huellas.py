# -*- coding: utf-8 -*-
"""Huellas SHA-256 del motor clínico.

POR QUÉ EXISTE
--------------
Un número de recall no significa nada si no se puede decir qué código lo
produjo. Esto no sustituye al control de versiones: es la evidencia mínima
para que cualquiera pueda comprobar que dos benchmarks distintos salieron del
mismo motor, o explicar por qué difieren.

Solo cubre los archivos que deciden criticidad. NO incluye `.env` ni ningún
archivo con credenciales.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

# Los seis archivos que determinan la criticidad de una llamada.
ARCHIVOS_DEL_MOTOR = (
    "config/red_flags.yaml",
    "app/decision/rules.py",
    "app/decision/engine.py",
    "app/decision/composicion.py",
    "app/decision/cobertura.py",
    "app/decision/assess.py",
    "app/llm.py",
)


def huella(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(65536), b""):
            h.update(bloque)
    return h.hexdigest()


def huellas_del_motor() -> dict:
    archivos = {}
    for rel in ARCHIVOS_DEL_MOTOR:
        p = RAIZ / rel
        archivos[rel] = huella(p) if p.exists() else None
    # Huella compuesta: cambia si cambia cualquiera de los archivos. Es la que
    # se cita junto a un benchmark.
    combinada = hashlib.sha256(
        "".join(f"{k}:{v}" for k, v in sorted(archivos.items()) if v).encode()
    ).hexdigest()
    return {
        "generado": datetime.now(timezone.utc).isoformat(),
        "archivos": archivos,
        "huella_motor": combinada[:16],
    }


def imprimir() -> dict:
    d = huellas_del_motor()
    print(f"  huella del motor: {d['huella_motor']}   ({d['generado']})")
    for rel, h in sorted(d["archivos"].items()):
        print(f"    {h[:16] if h else '(ausente)':<16}  {rel}")
    return d


if __name__ == "__main__":
    d = imprimir()
    destino = RAIZ / "eval" / "huellas_motor.json"
    destino.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  guardado en {destino.relative_to(RAIZ)}")
