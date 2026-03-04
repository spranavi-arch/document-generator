"""
API keys and app settings. Loads from .env when present.
"""
import os
from pathlib import Path

# Load .env from backend directory when present (optional: requires python-dotenv)
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ModuleNotFoundError:
        pass  # .env not loaded; use system env vars or install: pip install python-dotenv

# --- OpenAI (for standard OpenAI API) ---
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "").strip()

# --- Azure OpenAI (preferred when endpoint + key are set) ---
AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview").strip()
AZURE_OPENAI_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini").strip()

# --- Gemini (used when GEMINI_API_KEY is set and Azure is not) ---
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip()

# Prefer Azure, then Gemini, then OpenAI
USE_AZURE_OPENAI: bool = bool(AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY)
USE_GEMINI: bool = bool(not USE_AZURE_OPENAI and GEMINI_API_KEY)

# --- App ---
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))
DEBUG: bool = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")
