"""Cliente de modelo de lenguaje.

COMPUERTA G3: el modelo que razona debe ser uno de los permitidos.
Primario: Gemini Flash (`GEMINI_MODEL`, por defecto gemini-3.6-flash).
Alternativa configurable: Llama 3.3 70B vía Groq (`LLM_PROVIDER=groq`), sucesor
vigente de Llama 3.1 70B en el mismo proveedor, tal como autoriza la ficha
técnica del reto. Groq se sigue usando siempre para STT (Whisper), con
independencia de este ajuste.

QUÉ PASA CUANDO EL PROVEEDOR CAE
--------------------------------
No se salta a otro modelo. Se lanza `LlmNoDisponible` y el motor entra en MODO
DEGRADADO DETERMINISTA: siguen vivos los carriles de reglas, numérico,
histórico y de composición, que son los que sostienen la seguridad clínica. El
turno queda marcado con `modo_degradado=true` y el motivo del fallo
(cuota_agotada, timeout, credencial_invalida, modelo_inexistente…).

Encadenar proveedores sería peor: enmascara la caída, y las métricas dejarían
de describir lo que de verdad ocurrió durante la llamada. Puede habilitarse
con `LLM_FALLBACK_A_PROVEEDOR=1` para una demo, nunca por defecto.

Toda invocación devuelve (texto, usage) para que la observabilidad registre
tokens reales, nunca estimados — incluidos los tokens de razonamiento, que se
facturan como salida pero no vienen en `candidatesTokenCount`.
"""
from __future__ import annotations

import json
import random
import threading
import time

import httpx

from . import config, observability

_groq_client = None
_http_client = None

# Reintentos ante límite de tasa o caída transitoria del proveedor. Se
# respeta `Retry-After` cuando el servidor lo envía; si no, espera
# exponencial con jitter para no sincronizar todos los hilos en el mismo
# instante y volver a saturar. Un 429 NO es una caída: reintentar es correcto.
# Un 401 o un 404 sí lo son, y ahí se degrada de inmediato sin insistir.
REINTENTOS_MAX = 4
ESPERA_BASE_S = 1.5
ESTADOS_REINTENTABLES = (429, 500, 502, 503, 504)

_contadores = {"reintentos": 0, "esperas_s": 0.0, "peticiones": 0}
_candado = threading.Lock()


def contadores() -> dict:
    with _candado:
        return dict(_contadores)


def reiniciar_contadores() -> None:
    with _candado:
        _contadores.update({"reintentos": 0, "esperas_s": 0.0, "peticiones": 0})


def _anotar(clave, valor=1):
    with _candado:
        _contadores[clave] += valor

# Mínimo de razonamiento que acepta el modelo (0 devuelve 400). Se deja como
# constante para poder subirlo si alguna tarea lo necesitara.
THINKING_BUDGET = 128


def _http():
    """Cliente HTTP reutilizado: mantiene viva la conexión con el fallback."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(timeout=60)
    return _http_client


def _groq():
    global _groq_client
    if _groq_client is None:
        from groq import Groq

        _groq_client = Groq(api_key=config.GROQ_API_KEY)
    return _groq_client


class LlmNoDisponible(RuntimeError):
    """El proveedor no respondió. Quien llame debe degradar, no reintentar otro
    modelo: la seguridad clínica no depende del LLM."""

    def __init__(self, motivo: str, proveedor: str, original: Exception | None = None):
        super().__init__(motivo)
        self.motivo = motivo
        self.proveedor = proveedor
        self.original = original


def _motivo_de(e: Exception) -> str:
    """Clasifica el fallo para que el evento diga POR QUÉ se degradó."""
    if isinstance(e, LlmNoDisponible):
        return e.motivo
    if isinstance(e, httpx.TimeoutException):
        return "timeout"
    status = getattr(getattr(e, "response", None), "status_code", None) \
        or getattr(e, "status_code", None)
    if status == 429:
        return "cuota_agotada"
    if status in (401, 403):
        return "credencial_invalida"
    if status == 404:
        return "modelo_inexistente"
    if isinstance(status, int) and status >= 500:
        return "error_del_proveedor"
    if isinstance(e, httpx.HTTPError):
        return "red"
    return "desconocido"


def chat(
    messages: list[dict],
    json_mode: bool = False,
    temperature: float = 0.3,
    max_tokens: int = 400,
) -> tuple[str, dict]:
    """Invoca el modelo permitido. Devuelve (texto, usage_dict).

    Ante cualquier fallo lanza `LlmNoDisponible`. NO encadena proveedores por
    defecto: el sistema degrada a modo determinista, que es una respuesta
    honesta y auditable, en vez de aparentar normalidad con otro modelo.
    """
    primario = config.LLM_PROVIDER if config.LLM_PROVIDER in ("groq", "gemini") else "groq"
    if primario == "groq" and not config.GROQ_API_KEY:
        primario = "gemini"
    orden = [primario]
    if config.LLM_FALLBACK_A_PROVEEDOR:
        otro = "gemini" if primario == "groq" else "groq"
        if (otro == "gemini" and config.GEMINI_API_KEY) or (otro == "groq" and config.GROQ_API_KEY):
            orden.append(otro)

    ultimo: Exception | None = None
    motivo = "sin_credencial"
    for proveedor in orden:
        try:
            if proveedor == "groq":
                return _chat_groq(messages, json_mode, temperature, max_tokens)
            return _chat_gemini(messages, json_mode, temperature, max_tokens)
        except Exception as e:  # noqa: BLE001 — se reclasifica y se relanza
            ultimo, motivo = e, _motivo_de(e)
            observability.log_error(
                "llm_error", e,
                {"proveedor": proveedor,
                 "modelo": config.GROQ_MODEL if proveedor == "groq" else config.GEMINI_MODEL,
                 "motivo": motivo,
                 "quedan_proveedores": proveedor != orden[-1]},
            )
    raise LlmNoDisponible(motivo, orden[-1], ultimo)


def _chat_groq(messages, json_mode, temperature, max_tokens) -> tuple[str, dict]:
    kwargs = dict(
        model=config.GROQ_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = _groq().chat.completions.create(**kwargs)
    usage = {
        "provider": "groq",
        "model": config.GROQ_MODEL,
        "input_tokens": resp.usage.prompt_tokens,
        "output_tokens": resp.usage.completion_tokens,
    }
    return resp.choices[0].message.content or "", usage


def _espera_sugerida(respuesta, intento: int) -> float:
    """Cuánto esperar antes de reintentar. Manda el servidor si lo dice."""
    cabecera = respuesta.headers.get("Retry-After") if respuesta is not None else None
    if cabecera:
        try:
            return min(float(cabecera), 60.0)
        except ValueError:
            pass
    return min(ESPERA_BASE_S * (2 ** intento) + random.uniform(0, 0.5), 30.0)


def _peticion_con_reintento(url: str, body: dict) -> httpx.Response:
    ultimo = None
    for intento in range(REINTENTOS_MAX):
        _anotar("peticiones")
        try:
            r = _http().post(url, json=body, timeout=60)
        except httpx.TimeoutException as e:
            ultimo = e
            if intento == REINTENTOS_MAX - 1:
                raise
            espera = _espera_sugerida(None, intento)
        else:
            if r.status_code not in ESTADOS_REINTENTABLES:
                return r
            if intento == REINTENTOS_MAX - 1:
                return r  # se agotaron los intentos: que lo clasifique el motivo
            espera = _espera_sugerida(r, intento)
        _anotar("reintentos")
        _anotar("esperas_s", espera)
        time.sleep(espera)
    raise ultimo if ultimo else RuntimeError("reintentos agotados")


def _chat_gemini(messages, json_mode, temperature, max_tokens) -> tuple[str, dict]:
    """Vía REST (sin SDK extra). Convierte formato OpenAI → Gemini."""
    if not config.GEMINI_API_KEY:
        raise LlmNoDisponible("sin_credencial", "gemini")
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    contents = []
    for m in messages:
        if m["role"] == "system":
            continue
        role = "user" if m["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    body: dict = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            # Los modelos de razonamiento gastan presupuesto en pensar ANTES de
            # emitir texto: sin acotarlo, la respuesta llegaba truncada o
            # vacía. En un agente de voz el razonamiento extendido no aporta
            # —la decisión clínica la toman los carriles deterministas— y sí
            # cuesta latencia y dinero. 128 es el mínimo que acepta el modelo:
            # con 0 la API responde 400 INVALID_ARGUMENT.
            "thinkingConfig": {"thinkingBudget": THINKING_BUDGET},
        },
    }
    if system_parts:
        body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}"
    )
    r = _peticion_con_reintento(url, body)
    if r.status_code == 400 and "thinkingConfig" in r.text:
        # El modelo no admite el control de razonamiento: se reintenta sin él
        # en vez de degradar, que sería perder el turno por un detalle de API.
        body["generationConfig"].pop("thinkingConfig", None)
        r = _peticion_con_reintento(url, body)
    r.raise_for_status()
    data = r.json()
    candidatos = data.get("candidates") or []
    partes = (candidatos[0].get("content", {}).get("parts") if candidatos else None) or []
    text = "".join(p.get("text", "") for p in partes)
    if not text.strip():
        # Sin este control, un corte por presupuesto o un bloqueo de seguridad
        # llegaba como KeyError y el evento decía "desconocido".
        razon = (candidatos[0].get("finishReason") if candidatos else None) or \
            (data.get("promptFeedback", {}).get("blockReason")) or "sin_candidatos"
        raise LlmNoDisponible(f"respuesta_vacia:{str(razon).lower()}", "gemini")
    meta = data.get("usageMetadata", {})
    # Los tokens de razonamiento se facturan como salida pero NO vienen dentro
    # de candidatesTokenCount. Sumarlos es la diferencia entre un costo real y
    # uno inventado a la baja: en respuestas cortas son la mayor parte.
    pensamiento = meta.get("thoughtsTokenCount", 0) or 0
    usage = {
        "provider": "gemini",
        "model": config.GEMINI_MODEL,
        "input_tokens": meta.get("promptTokenCount", 0),
        "output_tokens": (meta.get("candidatesTokenCount", 0) or 0) + pensamiento,
        "thinking_tokens": pensamiento,
    }
    return text, usage


def chat_json(messages: list[dict], **kw) -> tuple[dict, dict]:
    """Invoca en modo JSON y parsea con tolerancia a fences de markdown."""
    text, usage = chat(messages, json_mode=True, **kw)
    clean = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(clean), usage
    except json.JSONDecodeError:
        start, end = clean.find("{"), clean.rfind("}")
        if start >= 0 and end > start:
            return json.loads(clean[start : end + 1]), usage
        raise
