"""
Load env from backend folder. Used by docgen when run from project root or docgen.
Encapsulates configuration in a Config class (OOP).
"""
import os
from pathlib import Path


class Config:
    """
    Holds OpenAI/Azure configuration loaded from backend/.env.
    Single responsibility: load and expose environment-based settings.
    """

    _backend_env = Path(__file__).resolve().parent.parent / "backend" / ".env"

    def __init__(self):
        self._load_env()
        self._openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self._azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
        self._azure_api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
        self._azure_api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview").strip()
        self._azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini").strip()
        self._use_azure_openai = bool(self._azure_endpoint and self._azure_api_key)

    def _load_env(self) -> None:
        if self._backend_env.exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(self._backend_env)
            except ModuleNotFoundError:
                pass

    @property
    def OPENAI_API_KEY(self) -> str:
        return self._openai_api_key

    @property
    def AZURE_OPENAI_ENDPOINT(self) -> str:
        return self._azure_endpoint

    @property
    def AZURE_OPENAI_API_KEY(self) -> str:
        return self._azure_api_key

    @property
    def AZURE_OPENAI_API_VERSION(self) -> str:
        return self._azure_api_version

    @property
    def AZURE_OPENAI_DEPLOYMENT(self) -> str:
        return self._azure_deployment

    @property
    def USE_AZURE_OPENAI(self) -> bool:
        return self._use_azure_openai


# Singleton-like default instance for backward compatibility
_default_config = Config()

OPENAI_API_KEY = _default_config.OPENAI_API_KEY
AZURE_OPENAI_ENDPOINT = _default_config.AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_API_KEY = _default_config.AZURE_OPENAI_API_KEY
AZURE_OPENAI_API_VERSION = _default_config.AZURE_OPENAI_API_VERSION
AZURE_OPENAI_DEPLOYMENT = _default_config.AZURE_OPENAI_DEPLOYMENT
USE_AZURE_OPENAI = _default_config.USE_AZURE_OPENAI
