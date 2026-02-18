from docgen.config import (
    USE_AZURE_OPENAI,
    OPENAI_API_KEY,
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_DEPLOYMENT,
)

if USE_AZURE_OPENAI:
    from openai import AzureOpenAI
    _client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
    )
    _model = AZURE_OPENAI_DEPLOYMENT
else:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENAI_API_KEY)
    _model = "gpt-4o-mini"


class LLMClient:
    def generate(
        self,
        prompt: str,
        max_tokens: int = 4096,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> str:
        kwargs = {
            "model": _model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if temperature is not None:
            kwargs["temperature"] = temperature
        try:
            response = _client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as e:
            from openai import APIConnectionError, APIError, APIStatusError
            if isinstance(e, (APIConnectionError, APIError, APIStatusError)):
                msg = str(e).strip() or type(e).__name__
                if "connection" in msg.lower() or "getaddrinfo" in msg.lower():
                    msg += " Check AZURE_OPENAI_ENDPOINT (or OPENAI_API_KEY) and network/VPN/DNS."
                raise RuntimeError(f"Cannot reach OpenAI/Azure: {msg}") from e
            raise
