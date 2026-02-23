"""
LLM client for OpenAI/Azure. Uses Config and encapsulates client/model in the class (OOP).
"""
from docgen.config import Config


class LLMClient:
    """
    Encapsulates OpenAI or Azure OpenAI client and model.
    Client and model are set in __init__ from Config (dependency injection / single source of truth).
    """

    def __init__(self, config: Config | None = None):
        cfg = config or Config()
        if cfg.USE_AZURE_OPENAI:
            from openai import AzureOpenAI
            self._client = AzureOpenAI(
                azure_endpoint=cfg.AZURE_OPENAI_ENDPOINT,
                api_key=cfg.AZURE_OPENAI_API_KEY,
                api_version=cfg.AZURE_OPENAI_API_VERSION,
            )
            self._model = cfg.AZURE_OPENAI_DEPLOYMENT
        else:
            from openai import OpenAI
            self._client = OpenAI(api_key=cfg.OPENAI_API_KEY)
            self._model = "gpt-4o-mini"

    def generate(
        self,
        prompt: str|list[str],
        max_tokens: int = 4096,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> str:
        try:
            if isinstance(prompt, list):
                messages = [{"role": "user", "content": p} for p in prompt]
                messages[0]["role"] = "system"
            else:
                messages = [{"role": "user", "content": prompt}]

            kwargs = {
                "model": self._model,
                "messages": messages,
                "max_completion_tokens": max_tokens,
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
        except Exception as e:
            print(f"Error generating: {e}")
            return ""
