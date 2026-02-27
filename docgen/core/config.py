"""
Load env from backend folder or project root. Used by docgen when run from project root or docgen.
Encapsulates configuration in a Config class (OOP).
"""
import os
from pathlib import Path


class Config:
    """
    Holds OpenAI/Azure configuration loaded from backend/.env or project root .env.
    Single responsibility: load and expose environment-based settings.
    """

    _project_root = Path(__file__).resolve().parent.parent.parent
    _backend_env = _project_root / "backend" / ".env"
    _root_env = _project_root / ".env"

    def __init__(self):
        self._load_env()
        self._openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self._azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
        self._azure_api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip()
        self._azure_api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview").strip()
        self._azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini").strip()
        self._use_azure_openai = bool(self._azure_endpoint and self._azure_api_key)
        
        self._gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self._gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview").strip()
        self._gemini_vertex_ai = os.getenv("GEMINI_VERTEX_AI", "true").lower() == "true"
        self._gemini_thinking_level = os.getenv("GEMINI_THINKING_LEVEL", "HIGH").strip()
        self._llm_provider = os.getenv("LLM_PROVIDER", "azure").strip().lower()

        self._azure_search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT", "").strip()
        self._azure_search_key = os.getenv("AZURE_SEARCH_KEY", "").strip()
        self._azure_search_index = os.getenv("AZURE_SEARCH_INDEX", "clx-chat-with-case-01").strip()

    def _load_env(self) -> None:
        try:
            from dotenv import load_dotenv
            if self._backend_env.exists():
                load_dotenv(self._backend_env)
            if self._root_env.exists():
                load_dotenv(self._root_env, override=True)
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

    @property
    def GEMINI_API_KEY(self) -> str:
        return self._gemini_api_key

    @property
    def GEMINI_MODEL(self) -> str:
        return self._gemini_model
        
    @property
    def GEMINI_VERTEX_AI(self) -> bool:
        return self._gemini_vertex_ai

    @property
    def GEMINI_THINKING_LEVEL(self) -> str:
        return self._gemini_thinking_level

    @property
    def LLM_PROVIDER(self) -> str:
        return self._llm_provider

    @property
    def AZURE_SEARCH_ENDPOINT(self) -> str:
        return self._azure_search_endpoint

    @property
    def AZURE_SEARCH_KEY(self) -> str:
        return self._azure_search_key

    @property
    def AZURE_SEARCH_INDEX(self) -> str:
        return self._azure_search_index


# Singleton-like default instance for backward compatibility
_default_config = Config()

OPENAI_API_KEY = _default_config.OPENAI_API_KEY
AZURE_OPENAI_ENDPOINT = _default_config.AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_API_KEY = _default_config.AZURE_OPENAI_API_KEY
AZURE_OPENAI_API_VERSION = _default_config.AZURE_OPENAI_API_VERSION
AZURE_OPENAI_DEPLOYMENT = _default_config.AZURE_OPENAI_DEPLOYMENT
USE_AZURE_OPENAI = _default_config.USE_AZURE_OPENAI
GEMINI_API_KEY = _default_config.GEMINI_API_KEY
GEMINI_MODEL = _default_config.GEMINI_MODEL
GEMINI_VERTEX_AI = _default_config.GEMINI_VERTEX_AI
GEMINI_THINKING_LEVEL = _default_config.GEMINI_THINKING_LEVEL
LLM_PROVIDER = _default_config.LLM_PROVIDER
AZURE_SEARCH_ENDPOINT = _default_config.AZURE_SEARCH_ENDPOINT
AZURE_SEARCH_KEY = _default_config.AZURE_SEARCH_KEY
AZURE_SEARCH_INDEX = _default_config.AZURE_SEARCH_INDEX