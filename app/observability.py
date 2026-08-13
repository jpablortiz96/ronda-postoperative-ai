"""Observabilidad de RONDA.

Cada turno de voz emite UN evento JSONL con:
- t_audio_recibido, t_stt_fin, t_llm_fin, t_tts_primer_byte (epoch ms)
- latencia_ms: t_tts_primer_byte - t_audio_recibido
  (la definición exacta de la rúbrica: desde que el paciente termina de hablar
  hasta que empieza a sonar el audio del agente)
- tokens de entrada/salida, invocaciones al modelo, consultas RAG

scripts/metrics.py calcula P50/P95 y costo/llamada DESDE ESTOS LOGS, de modo
que las métricas del README nunca puedan ser inconsistentes con los logs.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone

from . import config

_lock = threading.Lock()

# Patrones de credencial que JAMÁS deben aterrizar en el log. El mensaje de
# error de Gemini, por ejemplo, incluye la URL completa con ?key=<API_KEY>.
_PATRONES_SECRETO = [
    re.compile(r"(?i)([?&](?:key|api_key|access_token|token)=)[^&\s'\"]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(\"?(?:api_key|authorization)\"?\s*[:=]\s*\"?)[^\s,\"}]+"),
    re.compile(r"\b(gsk_|sk-|AIza)[A-Za-z0-9._\-]{8,}"),
]


def redactar(texto: str) -> str:
    """Elimina credenciales de un texto antes de persistirlo."""
    for patron in _PATRONES_SECRETO:
        texto = patron.sub(lambda m: m.group(1) + "[REDACTADO]", texto)
    return texto


def now_ms() -> int:
    return int(time.time() * 1000)


def log_event(event: dict) -> None:
    event.setdefault("ts", datetime.now(timezone.utc).isoformat())
    line = redactar(json.dumps(event, ensure_ascii=False))
    with _lock:
        with open(config.EVENTS_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def log_error(tipo: str, exc: BaseException, contexto: dict | None = None) -> None:
    """Registra un fallo operativo como evento estructurado (no un stack trace).

    El mensaje se redacta antes de escribirse y se recorta: interesa el tipo de
    fallo y su contexto operativo, no volcar contenido del paciente.
    """
    log_event(
        {
            "tipo": tipo,
            "error_clase": f"{type(exc).__module__}.{type(exc).__name__}",
            "error_mensaje": redactar(str(exc))[:300],
            **(contexto or {}),
        }
    )


class TurnTimer:
    """Cronómetro de un turno de voz."""

    def __init__(self, session_id: str, turno: int):
        self.session_id = session_id
        self.turno = turno
        self.marks: dict[str, int] = {"t_audio_recibido": now_ms()}
        self.model_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.rag_queries = 0
        # None = no medido (≠ 0.0 s de audio). Lo fija el handler del WS con
        # la duración real leída del contenedor.
        self.audio_in_s: float | None = None

    def mark(self, name: str) -> None:
        self.marks[name] = now_ms()

    def add_usage(self, usage: dict) -> None:
        if not usage:
            return
        self.model_calls += 1
        self.input_tokens += int(usage.get("input_tokens", 0) or 0)
        self.output_tokens += int(usage.get("output_tokens", 0) or 0)

    def etapas(self) -> dict:
        """Duración de cada etapa del turno, en ms. Solo las que se midieron."""
        m = self.marks
        base = m["t_audio_recibido"]
        pares = [
            ("stt_ms", "t_audio_recibido", "t_stt_fin"),
            ("decision_ms", "t_stt_fin", "t_decision_fin"),
            ("generacion_ms", "t_decision_fin", "t_llm_fin"),
            ("tts_primer_chunk_ms", "t_llm_fin", "t_tts_primer_byte"),
            ("tts_resto_ms", "t_tts_primer_byte", "t_tts_fin"),
        ]
        out = {}
        for nombre, ini, fin in pares:
            if ini in m and fin in m:
                out[nombre] = m[fin] - m[ini]
        if "t_tts_fin" in m:
            out["tts_total_ms"] = m["t_tts_fin"] - m["t_llm_fin"]
        out["servidor_total_ms"] = (m.get("t_tts_fin", base)) - base
        return out

    def close(self, extra: dict | None = None) -> dict:
        # Latencia del SERVIDOR hasta el primer audio reproducible. NO es la
        # latencia percibida: excluye el endpointing del VAD, la subida y el
        # arranque de reproducción en el navegador, que se suman aparte.
        lat = None
        if "t_tts_primer_byte" in self.marks:
            lat = self.marks["t_tts_primer_byte"] - self.marks["t_audio_recibido"]
        event = {
            "tipo": "turno",
            "session_id": self.session_id,
            "turno": self.turno,
            **self.marks,
            **self.etapas(),
            "servidor_a_primer_audio_ms": lat,
            "latencia_ms": lat,
            "invocaciones_modelo": self.model_calls,
            "tokens_entrada": self.input_tokens,
            "tokens_salida": self.output_tokens,
            "consultas_rag": self.rag_queries,
            "audio_entrada_s": (None if self.audio_in_s is None
                                else round(self.audio_in_s, 2)),
        }
        if extra:
            event.update(extra)
        log_event(event)
        return event
