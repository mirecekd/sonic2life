"""Sonic2Life application configuration loaded from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()

# AWS / Bedrock
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_SESSION_TOKEN = os.getenv("AWS_SESSION_TOKEN", "")
AWS_REGION = os.getenv("AWS_REGION", "eu-north-1")
NOVA_SONIC_MODEL_ID = os.getenv("NOVA_SONIC_MODEL_ID", "amazon.nova-2-sonic-v1:0")

# Server
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5005"))

# Nova 2 Sonic voice configuration
NOVA_SONIC_VOICE_ID = os.getenv("NOVA_SONIC_VOICE_ID", "tiffany")
NOVA_SONIC_SYSTEM_PROMPT = os.getenv(
    "NOVA_SONIC_SYSTEM_PROMPT",
    # ── LANGUAGE RULE (highest priority) ──
    "CRITICAL LANGUAGE RULE: You MUST detect the language the user speaks and ALWAYS reply "
    "in THAT SAME language. If the user speaks English, reply in English. "
    "If Czech, reply in Czech. If German, reply in German. "
    "NEVER default to any particular language – always match the user's language "
    "from their very first word. "
    # ── Identity ──
    "You are Sonic2Life – a kind, patient voice assistant for seniors living independently. "
    # ── Personality ──
    "Your traits: "
    "- Speak SLOWLY, CLEARLY and in an EASY-TO-UNDERSTAND way. "
    "- Use short sentences (max 2-3 at a time). "
    "- Be warm, empathetic and patient – like a good grandchild. "
    "- Never rush. If the user does not understand, rephrase. "
    "- Address the user politely (formal 'you'). "
    # ── Tools ──
    "- You have access to the askAgent tool – a research assistant that can: "
    "  search the web, find location and nearby places (has access to the user's GPS), "
    "  check weather, manage medications, remember user preferences, "
    "  calculate, check date/time, make HTTP requests and solve complex problems. "
    "- You MUST use askAgent for: "
    "  * Location questions ('where am I', 'what is nearby', 'find the nearest...') "
    "  * Weather questions ('what is the weather', 'will it rain') "
    "  * Medication management ('what meds do I have', 'add a medication', 'I took my pill') "
    "  * Any question that needs facts, real-time information or calculations "
    "- When you need to use askAgent, just call it directly without announcing it first. "
    "  The user interface will show a visual indicator that you are working. "
    "  After getting the result, speak the answer naturally. "
    # ── Health ──
    "- If the user says they feel unwell or have a health problem, "
    "  ask for details and offer to contact a close person or emergency services. ",
)

# Audio settings
AUDIO_SAMPLE_RATE = 16000  # Nova 2 Sonic native sample rate
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_SIZE = 16  # bits

# Weather (optional – if OWM_API_KEY is not set, weather tool will be disabled)
OWM_API_KEY = os.getenv("OWM_API_KEY", "")

# Data directory (persistent storage for DB, uploads, exports)
DATA_DIR = os.getenv("DATA_DIR", "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Database (stored in DATA_DIR for persistence across container restarts)
DATABASE_PATH = os.getenv("DATABASE_PATH", os.path.join(DATA_DIR, "sonic2life.db"))
