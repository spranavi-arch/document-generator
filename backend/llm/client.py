from config import (
    USE_GEMINI,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    USE_AZURE_OPENAI,
    OPENAI_API_KEY,
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_DEPLOYMENT,
)

if USE_GEMINI:
    from google import genai as _genai_sdk
    from google.genai import types as _genai_types
    _vertex_client = _genai_sdk.Client(vertexai=True, api_key=GEMINI_API_KEY)
    _client = None
    _model = None
elif USE_AZURE_OPENAI:
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


def _gemini_generate(
    prompt: str, max_tokens: int = 4096, json_mode: bool = False, temperature: float | None = None
) -> str:
    config_kw = {"max_output_tokens": max_tokens}
    if temperature is not None:
        config_kw["temperature"] = temperature
    if json_mode:
        config_kw["response_mime_type"] = "application/json"
    config = _genai_types.GenerateContentConfig(**config_kw)
    response = _vertex_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=config,
    )
    if not response or not getattr(response, "text", None):
        return ""
    return (response.text or "").strip()


class LLMClient:
    def generate(
        self,
        prompt: str,
        max_tokens: int = 4096,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> str:
        if USE_GEMINI:
            return _gemini_generate(prompt, max_tokens=max_tokens, json_mode=json_mode, temperature=temperature)
        kwargs = {
            "model": _model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if temperature is not None:
            kwargs["temperature"] = temperature
        response = _client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""
