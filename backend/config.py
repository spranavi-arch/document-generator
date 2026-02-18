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

# --- Azure OpenAI (used when AZURE_OPENAI_ENDPOINT is set) ---
AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview").strip()
AZURE_OPENAI_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini").strip()

# --- App ---
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))
DEBUG: bool = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")

# Prefer Azure if endpoint is set
USE_AZURE_OPENAI: bool = bool(AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY)
