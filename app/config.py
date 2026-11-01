"""Runtime configuration, read from environment variables (and an optional .env file)."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # python-dotenv is optional
    pass

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"

# ----------------------------------------------------------------------------- #
# Medallion data layout (bronze -> silver -> gold), mirroring the MLE pipeline.
#   source/   hand-authored raw product data (versioned input to the pipeline)
#   datamart/bronze/   raw ingestion, copied verbatim (demo catalog + live-store feeds)
#   datamart/silver/   cleaned, typed, controlled-vocabulary, scope-filtered
#   datamart/gold/     query-ready serving catalog + a per-size stock store
# `main.py` (or pipeline.build_demo) rebuilds datamart/ from source/. The app reads gold.
# ----------------------------------------------------------------------------- #
DATA_DIR = ROOT / "data"
SOURCE_DIR = DATA_DIR / "source"
DATAMART_DIR = DATA_DIR / "datamart"
BRONZE_DIR = DATAMART_DIR / "bronze"
SILVER_DIR = DATAMART_DIR / "silver"
GOLD_DIR = DATAMART_DIR / "gold"

DEMO_SOURCE = SOURCE_DIR / "demo_catalog.json"
CATALOG_PATH = GOLD_DIR / "catalog.json"   # gold serving catalog the agents query
STOCK_PATH = GOLD_DIR / "stock.json"       # gold stock store (per-product, per-size)

# LLM_MODE: "claude" (Anthropic API) | "ollama" (local model) | "mock" (deterministic, no LLM)
#           | "auto" (Claude if ANTHROPIC_API_KEY is set, else mock).
LLM_MODE = os.getenv("LLM_MODE", "auto").lower()
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5")
# Server-side refusal fallbacks (beta). Set to 0 if your account/region rejects the beta header.
CLAUDE_FALLBACKS = os.getenv("CLAUDE_FALLBACKS", "1") == "1"
# Local model via Ollama (https://ollama.com): run `ollama pull llama3.2`, then LLM_MODE=ollama.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
# Any OpenAI-compatible endpoint (OpenAI, OpenRouter, Azure, Groq, a LiteLLM proxy): LLM_MODE=openai.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "openai/gpt-4o-mini")

# "cardnetwork" = Visa/Mastercard-rails gateway with 3-D Secure OTP/2FA (hardened checkout);
# "mock" = the simpler single-shot provider.
PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "cardnetwork")
CURRENCY = "SGD"
SHIPPING_FLAT = 6.0
FREE_SHIPPING_OVER = 100.0
TAX_RATE = 0.09


def provider() -> str:
    """Which LLM backend is active: 'claude', 'ollama', 'openai' or 'mock'."""
    if LLM_MODE in ("claude", "ollama", "openai", "mock"):
        return LLM_MODE
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):  # auto
        return "claude"
    return "mock"


def use_claude() -> bool:
    return provider() == "claude"


def use_llm() -> bool:
    """True when a real LLM (Claude, a local Ollama model, or an OpenAI-compatible endpoint)
    powers the reasoning agents."""
    return provider() in ("claude", "ollama", "openai")
