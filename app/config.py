"""Configuración central de RONDA. Todo sale de variables de entorno (.env)."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ── Modelo de lenguaje (COMPUERTA G3: solo modelos permitidos) ──────────────
# Permitidos: Gemini 1.5 Flash | Llama 3.1 70B vía Groq (o sucesor vigente
# del mismo proveedor, según la ficha técnica) | Llama 3.2 local | Phi-3.5 Mini local
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")  # fallback opcional (también permitido)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")  # groq | gemini
# Cuando el proveedor falla NO se salta a otro modelo: se entra en MODO
# DEGRADADO DETERMINISTA. Encadenar proveedores enmascara la caída y hace que
# las métricas dejen de describir lo que realmente ocurrió; además, el carril
# que sostiene la seguridad clínica (reglas + composición) no necesita LLM.
LLM_FALLBACK_A_PROVEEDOR = os.getenv("LLM_FALLBACK_A_PROVEEDOR", "0") == "1"

# ── Voz ─────────────────────────────────────────────────────────────────────
STT_MODEL = os.getenv("STT_MODEL", "whisper-large-v3")
# edge | piper | auto. En `auto` se usa TTS_PRIMARY y se cae al otro motor si
# falla, para que un corte del servicio externo no deje muda la llamada.
TTS_ENGINE = os.getenv("TTS_ENGINE", "auto")
TTS_PRIMARY = os.getenv("TTS_PRIMARY", "edge")  # motor preferido en modo auto
TTS_VOICE = os.getenv("TTS_VOICE", "es-CO-SalomeNeural")  # voz colombiana (edge)

# ── Voz local opcional (Piper) ──────────────────────────────────────────────
# No viene en requirements.txt: es un extra. Ver requirements-voz-local.txt y
# scripts/preparar_voz_local.py. Si el modelo no está, el motor se reporta
# como no disponible y `auto` sigue funcionando con edge.
PIPER_VOZ = os.getenv("PIPER_VOZ", "es_MX-ald-medium")
PIPER_MODELO = os.getenv(
    "PIPER_MODELO", str(BASE_DIR / "data" / "models" / "piper" / f"{PIPER_VOZ}.onnx")
)

# ── RAG / conocimiento vivo ─────────────────────────────────────────────────
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
# Motor de inferencia de embeddings. `fastembed` (ONNX Runtime, que ChromaDB ya
# instala) ejecuta EL MISMO modelo sin arrastrar PyTorch: son ~750 MB menos de
# descarga, la diferencia entre cumplir la compuerta de 15 minutos y no
# cumplirla. `sentence_transformers` queda disponible solo para reproducir
# experimentos previos, con requirements-legacy-embeddings.txt.
EMBEDDING_BACKEND = os.getenv("EMBEDDING_BACKEND", "fastembed")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "384"))
CHROMA_DIR = str(BASE_DIR / "data" / "chroma")
COLLECTION_NAME = "corpus_clinico"
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "900"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "4"))
# Umbral "cita o silencio": debajo de esto el agente declara el límite de su
# conocimiento en lugar de improvisar. Distancia coseno (menor = más similar).
RAG_MAX_DISTANCE = float(os.getenv("RAG_MAX_DISTANCE", "0.55"))

# ── Conversación ────────────────────────────────────────────────────────────
MAX_TURNS = int(os.getenv("MAX_TURNS", "24"))
SILENCE_REPROMPT_S = float(os.getenv("SILENCE_REPROMPT_S", "8"))

# ── Rutas de datos ──────────────────────────────────────────────────────────
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
ACTAS_DIR = DATA_DIR / "actas"
ALERTAS_DIR = DATA_DIR / "alertas"
MANIFEST_PATH = DATA_DIR / "manifest.json"
LOGS_DIR = BASE_DIR / "logs"
EVENTS_LOG = LOGS_DIR / "events.jsonl"
RED_FLAGS_PATH = BASE_DIR / "config" / "red_flags.yaml"
PROMPTS_DIR = BASE_DIR / "prompts"
PACIENTE_DEMO_PATH = BASE_DIR / "config" / "paciente_demo.json"

# ── Costos de referencia para extrapolación (USD por millón de tokens) ──────
# Precios de producción usados en el cálculo de costo/llamada del README.
COST_INPUT_PER_M = float(os.getenv("COST_INPUT_PER_M", "0.59"))
COST_OUTPUT_PER_M = float(os.getenv("COST_OUTPUT_PER_M", "0.79"))
COST_STT_PER_MIN = float(os.getenv("COST_STT_PER_MIN", "0.00185"))

for d in (DATA_DIR, UPLOADS_DIR, ACTAS_DIR, ALERTAS_DIR, LOGS_DIR):
    d.mkdir(parents=True, exist_ok=True)
