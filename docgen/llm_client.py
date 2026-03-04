"""
LLM client for OpenAI/Azure/Gemini. Uses Config and encapsulates client/model in the class (OOP).
"""
from docgen.config import Config
from docgen.utils import remove_control_chars


class LLMClient:
    """
    Encapsulates OpenAI, Azure OpenAI, or Gemini client and model.
    Client and model are set in __init__ from Config (dependency injection / single source of truth).
    """

    def __init__(self, config: Config | None = None):
        cfg = config or Config()
        if cfg.USE_GEMINI:
            from google import genai as _genai_sdk
            from google.genai import types as _genai_types
            self._vertex_client = _genai_sdk.Client(vertexai=True, api_key=cfg.GEMINI_API_KEY)
            self._gemini_model_name = cfg.GEMINI_MODEL
            self._genai_types = _genai_types
            self._client = None
            self._model = None
            self._genai = None
            self._gemini_model = None
        elif cfg.USE_AZURE_OPENAI:
            from openai import AzureOpenAI
            self._client = AzureOpenAI(
                azure_endpoint=cfg.AZURE_OPENAI_ENDPOINT,
                api_key=cfg.AZURE_OPENAI_API_KEY,
                api_version=cfg.AZURE_OPENAI_API_VERSION,
            )
            self._model = cfg.AZURE_OPENAI_DEPLOYMENT
            self._genai = None
            self._gemini_model = None
            self._vertex_client = None
            self._gemini_model_name = None
            self._genai_types = None
        else:
            from openai import OpenAI
            self._client = OpenAI(api_key=cfg.OPENAI_API_KEY)
            self._model = "gpt-4o-mini"
            self._genai = None
            self._gemini_model = None
            self._vertex_client = None
            self._gemini_model_name = None
            self._genai_types = None

    def generate(
        self,
        prompt: str,
        max_tokens: int = 4096,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> str:
        # Remove ASCII control characters (NUL, etc.) so they are never sent to the API
        prompt = remove_control_chars(prompt or "")
        if self._vertex_client is not None:
            config_kw = {"max_output_tokens": max_tokens}
            if temperature is not None:
                config_kw["temperature"] = temperature
            if json_mode:
                config_kw["response_mime_type"] = "application/json"
            config = self._genai_types.GenerateContentConfig(**config_kw)
            response = self._vertex_client.models.generate_content(
                model=self._gemini_model_name,
                contents=prompt,
                config=config,
            )
            if not response or not getattr(response, "text", None):
                return ""
            return (response.text or "").strip()
        kwargs = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if temperature is not None:
            kwargs["temperature"] = temperature
        try:
            response = self._client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as e:
            from openai import APIConnectionError, APIError, APIStatusError
            if isinstance(e, (APIConnectionError, APIError, APIStatusError)):
                msg = str(e).strip() or type(e).__name__
                if "connection" in msg.lower() or "getaddrinfo" in msg.lower():
                    msg += " Check AZURE_OPENAI_ENDPOINT (or OPENAI_API_KEY) and network/VPN/DNS."
                raise RuntimeError(f"Cannot reach OpenAI/Azure: {msg}") from e
            raise