"""
Load env from backend folder. Used by docgen when run from project root or docgen.
"""
import os
from pathlib import Path

_backend_env = Path(__file__).resolve().parent.parent / "backend" / ".env"
if _backend_env.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_backend_env)
    except ModuleNotFoundError:
        pass

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview").strip()
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini").strip()

USE_AZURE_OPENAI = bool(AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY)
