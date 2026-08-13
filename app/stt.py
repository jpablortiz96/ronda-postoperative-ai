"""Transcripción de voz a texto vía Whisper Large V3 en Groq.

Recibe los bytes del audio capturado en el navegador (webm/opus) y devuelve
el texto en español. Groq procesa el audio en milisegundos, clave para la
fluidez que exige el criterio de calidad de conversación.

POR QUÉ `verbose_json` Y NO `text`
----------------------------------
Con `response_format="text"` el SDK intenta interpretar la respuesta como JSON.
Si la transcripción completa es una cifra desnuda —"9", "38", "39.5"— el
parseo tiene éxito y el SDK devuelve un `int`/`float`, no una cadena. Eso
borraba justo los datos que gobiernan los umbrales de escalamiento: el dolor
de 0 a 10 y la temperatura. Un paciente que contesta "nueve" perdía su 9.

`verbose_json` devuelve una estructura explícita, y `_texto_de()` normaliza
cualquier forma que llegue sin volver a perder información.
"""
from __future__ import annotations

import io

from . import config

PROMPT_SESGO = (
    "Llamada de seguimiento postoperatorio en Colombia. Vocabulario: "
    "herida, puntos, fiebre, sangrado, dolor, maluco, desaliento, "
    "hinchazón, pus, mareo, acetaminofén."
)


def _texto_de(respuesta) -> str:
    """Extrae el texto de la respuesta del proveedor, venga como venga.

    Acepta cadena, número (una cifra desnuda es una transcripción válida),
    diccionario u objeto con atributo `text`. Ante un tipo inesperado devuelve
    cadena vacía en lugar de la repr del objeto: es preferible no transcribir
    a inyectar "<Transcription object at 0x...>" en la conversación clínica.
    """
    if respuesta is None or isinstance(respuesta, bool):
        return ""
    if isinstance(respuesta, str):
        return respuesta.strip()
    if isinstance(respuesta, (int, float)):
        return str(respuesta).strip()
    if isinstance(respuesta, dict):
        return _texto_de(respuesta.get("text"))
    if hasattr(respuesta, "text"):
        return _texto_de(respuesta.text)
    return ""


def tiene_contenido(texto: str) -> bool:
    """¿La transcripción contiene algo pronunciado, o solo puntuación?

    Ante audio sin habla, Whisper no devuelve vacío: alucina. Se han observado
    ".", "Gracias por ver el video." y "Subtitulado por ...". Esta comprobación
    solo descarta el caso inequívoco —ningún carácter alfanumérico—, para no
    tocar respuestas cortas legítimas: "Sí", "No", "9", "39.5", "Bien".
    """
    return any(c.isalnum() for c in texto)


_cliente = None


def _groq():
    """Cliente reutilizado entre turnos: crear uno por turno rehacía el
    handshake TLS con la API en cada intervención del paciente."""
    global _cliente
    if _cliente is None:
        from groq import Groq

        _cliente = Groq(api_key=config.GROQ_API_KEY)
    return _cliente


def transcribe_detallado(audio_bytes: bytes, filename: str = "turno.webm") -> tuple[str, dict]:
    """Devuelve (texto, metadatos de calidad). Los bytes van tal cual, sin
    transcodificar: Chrome → WebM/Opus → Groq Whisper."""
    client = _groq()
    buf = io.BytesIO(audio_bytes)
    buf.name = filename
    resp = client.audio.transcriptions.create(
        file=buf,
        model=config.STT_MODEL,
        language="es",
        response_format="verbose_json",
        prompt=PROMPT_SESGO,  # sesgo de contexto: léxico postoperatorio
    )
    datos = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    segmentos = datos.get("segments") or []
    meta = {
        "duracion_stt_s": datos.get("duration"),
        # Señal de CALIDAD ACÚSTICA, nunca clínica. Se registra pero no decide:
        # medido sobre silencio da 0,07-0,21 y sobre voz real 0,0001-0,013,
        # rangos demasiado próximos para un umbral fiable.
        "no_speech_prob": (segmentos[0].get("no_speech_prob") if segmentos else None),
        "segmentos": len(segmentos),
    }
    return _texto_de(resp), meta


def transcribe(audio_bytes: bytes, filename: str = "turno.webm") -> str:
    """Transcribe los bytes tal cual llegan del navegador, sin transcodificar."""
    return transcribe_detallado(audio_bytes, filename)[0]
