"""Descarga la voz local de RONDA (motor Piper). Opcional.

    python scripts/preparar_voz_local.py

El modelo NO se versiona: son ~61 MB de binario que no aportan nada al
historial y sí penalizan el clonado del repositorio. Se descarga bajo demanda
a data/models/piper/, ruta ignorada por git.

Sin este paso, RONDA funciona igual con el motor remoto (TTS_ENGINE=edge, el
valor por defecto). El motor local solo hace falta para TTS_ENGINE=piper o
para que TTS_ENGINE=auto tenga a dónde caer si el servicio remoto falla.

Origen: proyecto piper-voices de Rhasspy, publicado bajo licencia MIT.
Voz es_MX-ald-medium (español de México, calidad media).
"""
from __future__ import annotations

import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import config  # noqa: E402

ORIGEN = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_MX/"
          "ald/medium")
ARCHIVOS = [f"{config.PIPER_VOZ}.onnx", f"{config.PIPER_VOZ}.onnx.json"]


def descargar(destino: Path) -> bool:
    destino.mkdir(parents=True, exist_ok=True)
    for nombre in ARCHIVOS:
        salida = destino / nombre
        if salida.exists() and salida.stat().st_size > 0:
            print(f"  ya existe: {nombre} ({salida.stat().st_size / 1e6:.1f} MB)")
            continue
        url = f"{ORIGEN}/{nombre}"
        print(f"  descargando {nombre} …")
        t0 = time.perf_counter()
        try:
            urllib.request.urlretrieve(url, salida)
        except Exception as e:
            print(f"  ERROR descargando {nombre}: {type(e).__name__}: {e}")
            return False
        mb = salida.stat().st_size / 1e6
        print(f"    {mb:.1f} MB en {time.perf_counter() - t0:.1f} s")
    return True


if __name__ == "__main__":
    destino = Path(config.PIPER_MODELO).parent
    print(f"Voz local: {config.PIPER_VOZ}")
    print(f"Destino  : {destino}")
    try:
        import piper  # noqa: F401
    except ImportError:
        print("\nFalta el paquete del motor local. Instálelo con:")
        print("    pip install -r requirements-voz-local.txt")
        sys.exit(1)

    if not descargar(destino):
        sys.exit(1)
    print("\nComprobando que el modelo carga…")
    t0 = time.perf_counter()
    from piper import PiperVoice

    PiperVoice.load(config.PIPER_MODELO)
    print(f"  carga correcta en {(time.perf_counter() - t0) * 1000:.0f} ms")
    print("\nListo. Active el motor local con TTS_ENGINE=piper,")
    print("o TTS_ENGINE=auto con TTS_PRIMARY=edge para usarlo solo como respaldo.")
