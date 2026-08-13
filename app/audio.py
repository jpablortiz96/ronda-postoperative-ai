"""Medición de la duración real del audio de entrada.

El costo de STT se factura por segundos de audio, así que la duración no puede
estimarse a partir del tamaño del archivo: con Opus (VBR) el tamaño depende del
contenido, no del tiempo. Aquí se lee la duración declarada por el propio
contenedor WebM que produce MediaRecorder.

Estrategia:
  1. Segment > Info > Duration, si el contenedor la trae.
  2. Si no (MediaRecorder suele emitir WebM "en vivo", sin duración final),
     se toma el timecode del último Cluster más el desplazamiento del último
     bloque que contiene.

Si el contenedor no es WebM o no se puede leer, devuelve None —nunca 0—, para
que las métricas distingan "no medido" de "cero segundos".
"""
from __future__ import annotations

# Identificadores EBML/Matroska relevantes (con su bit marcador).
ID_SEGMENT = 0x18538067
ID_INFO = 0x1549A966
ID_TIMECODE_SCALE = 0x2AD7B1
ID_DURATION = 0x4489
ID_CLUSTER = 0x1F43B675
ID_TIMECODE = 0xE7
ID_SIMPLE_BLOCK = 0xA3
ID_BLOCK_GROUP = 0xA0
ID_BLOCK = 0xA1

_CONTENEDORES = {ID_SEGMENT, ID_INFO, ID_CLUSTER, ID_BLOCK_GROUP}
# Duración típica de un paquete Opus de MediaRecorder.
_MS_POR_PAQUETE = 20


def duracion_segundos(datos: bytes) -> float | None:
    """Duración del audio en segundos, o None si no se puede determinar."""
    try:
        return _duracion_webm(datos)
    except Exception:
        return None


# ── Lectura EBML ────────────────────────────────────────────────────────────
def _leer_vint(buf: bytes, pos: int, conservar_marcador: bool) -> tuple[int, int]:
    primero = buf[pos]
    if primero == 0:
        raise ValueError("vint inválido")
    longitud, mascara = 1, 0x80
    while not (primero & mascara):
        mascara >>= 1
        longitud += 1
        if longitud > 8:
            raise ValueError("vint demasiado largo")
    valor = primero if conservar_marcador else (primero & (mascara - 1))
    for i in range(1, longitud):
        valor = (valor << 8) | buf[pos + i]
    return valor, longitud


def _es_tamano_desconocido(valor: int, longitud: int) -> bool:
    return valor == (1 << (7 * longitud)) - 1


def _entero(buf: bytes) -> int:
    return int.from_bytes(buf, "big") if buf else 0


def _flotante(buf: bytes) -> float:
    import struct

    if len(buf) == 4:
        return struct.unpack(">f", buf)[0]
    if len(buf) == 8:
        return struct.unpack(">d", buf)[0]
    return 0.0


def _duracion_webm(datos: bytes) -> float | None:
    if not datos[:4] == b"\x1aE\xdf\xa3":  # cabecera EBML
        return None

    escala_ns = 1_000_000  # TimecodeScale por defecto: 1 ms
    duracion_declarada = None
    ultimo_cluster_ms = None
    fin = len(datos)

    def recorrer(inicio: int, limite: int) -> None:
        nonlocal escala_ns, duracion_declarada, ultimo_cluster_ms
        pos = inicio
        cluster_actual = None
        while pos < limite:
            id_elem, n_id = _leer_vint(datos, pos, True)
            pos += n_id
            tam, n_tam = _leer_vint(datos, pos, False)
            pos += n_tam
            if _es_tamano_desconocido(tam, n_tam):
                fin_elem = limite
            else:
                fin_elem = min(pos + tam, limite)

            if id_elem in _CONTENEDORES:
                if id_elem == ID_CLUSTER:
                    cluster_actual = None
                recorrer(pos, fin_elem)
            else:
                carga = datos[pos:fin_elem]
                if id_elem == ID_TIMECODE_SCALE:
                    escala_ns = _entero(carga) or escala_ns
                elif id_elem == ID_DURATION:
                    duracion_declarada = _flotante(carga)
                elif id_elem == ID_TIMECODE:
                    cluster_actual = _entero(carga)
                    ultimo_cluster_ms = max(ultimo_cluster_ms or 0, cluster_actual)
                elif id_elem in (ID_SIMPLE_BLOCK, ID_BLOCK) and cluster_actual is not None:
                    # pista (vint) + 2 bytes de timecode relativo con signo
                    _, n_pista = _leer_vint(carga, 0, False)
                    if len(carga) >= n_pista + 2:
                        rel = int.from_bytes(carga[n_pista:n_pista + 2], "big", signed=True)
                        ultimo_cluster_ms = max(ultimo_cluster_ms or 0, cluster_actual + rel)
            pos = fin_elem

    recorrer(0, fin)

    if duracion_declarada:
        return round(duracion_declarada * escala_ns / 1e9, 3)
    if ultimo_cluster_ms is not None:
        ms = ultimo_cluster_ms * (escala_ns / 1e6) + _MS_POR_PAQUETE
        return round(ms / 1000, 3)
    return None
