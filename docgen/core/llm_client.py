"""
LLM client for OpenAI/Azure/Gemini. Uses Config and encapsulates client/model in the class (OOP).
"""
from docgen.core.config import Config
import time
import os

class LLMClient:
    """
    Encapsulates OpenAI, Azure OpenAI, or Gemini client and model.
    Client and model are set in __init__ from Config (dependency injection / single source of truth).
    """

    def __init__(self, config: Config | None = None, provider: str | None = None):
        cfg = config or Config()
        self._config = cfg
        # Provider precedence: argument > config > default(azure)
        self._provider = (provider or cfg.LLM_PROVIDER or "azure").lower()
        self._client = None
        self._model = None

        if self._provider == "gemini":
            try:
                from google import genai
                # Use VERTEX_AI env var to toggle vertexai mode if needed, default to False (AI Studio)
                # or use snippet's vertexai=True if user explicitly set GOOGLE_CLOUD_API_KEY?
                # For now, we use the API Key from config.
                is_vertex = cfg.GEMINI_VERTEX_AI
                self._client = genai.Client(
                    api_key=cfg.GEMINI_API_KEY,
                    vertexai=is_vertex
                )
                self._model = cfg.GEMINI_MODEL
            except ImportError:
                print("Error: google-genai package not found. Run 'pip install google-genai'")
                raise
        elif self._provider == "azure":
            if cfg.USE_AZURE_OPENAI:
                from openai import AzureOpenAI
                self._client = AzureOpenAI(
                    azure_endpoint=cfg.AZURE_OPENAI_ENDPOINT,
                    api_key=cfg.AZURE_OPENAI_API_KEY,
                    api_version=cfg.AZURE_OPENAI_API_VERSION,
                )
                self._model = cfg.AZURE_OPENAI_DEPLOYMENT
            else:
                # Fallback to standard OpenAI if azure requested but not configured
                from openai import OpenAI
                self._client = OpenAI(api_key=cfg.OPENAI_API_KEY)
                self._model = "gpt-4o-mini"
        else:
            # Default to OpenAI if 'openai' or unknown
            from openai import OpenAI
            self._client = OpenAI(api_key=cfg.OPENAI_API_KEY)
            self._model = "gpt-4o-mini"

    def generate(
        self,
        prompt: str|list[str],
        max_tokens: int = 4096,
        json_mode: bool = False,
        temperature: float | None = None,
        response_schema: dict | None = None,
    ) -> str:
        """
        Generates text from the LLM. 
        Retries up to 3 times on failure with exponential backoff.
        """
        retries = 4
        last_error = None
        
        for attempt in range(retries):
            try:
                if self._provider == "gemini":
                    return self._generate_gemini(prompt, max_tokens, json_mode, temperature, response_schema)
                else:
                    return self._generate_openai(prompt, max_tokens, json_mode, temperature, response_schema)
            except Exception as e:
                last_error = e
                print(f"LLM generation failed (attempt {attempt+1}/{retries}): {e}")
                if attempt < retries - 1:
                    time.sleep(5 ** attempt) # Exponential backoff: 1s, 2s, 4s...
        
        raise last_error

    def _generate_openai(self, prompt, max_tokens, json_mode, temperature, response_schema) -> str:
        if isinstance(prompt, list):
            # First element is system prompt
            messages = [{"role": "user", "content": p} for p in prompt]
            if len(messages) > 0:
                messages[0]["role"] = "system"
        else:
            messages = [{"role": "user", "content": prompt}]

        kwargs = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        
        if response_schema:
            # Use strict structured outputs if available
            # Note: The schema passed must be a full json schema object.
            # For simplicity, we assume the caller provides the inner schema and we wrap it if needed,
            # or the caller provides the full {type: json_schema, json_schema: ...} structure.
            # Let's assume the caller provides a standard JSON Schema dict.
            
            # OpenAI requires { "type": "json_schema", "json_schema": { "name": "...", "schema": ... } }
            # We'll construct a default wrapper if it's just a raw schema dict.
            if "type" in response_schema and response_schema["type"] == "json_schema":
                 kwargs["response_format"] = response_schema
            else:
                 kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "extraction_result",
                        "schema": response_schema,
                        "strict": True
                    }
                }
        elif json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        if temperature is not None:
            kwargs["temperature"] = temperature
            
        try:
                response = self._client.chat.completions.create(**kwargs)
                finish_reason = response.choices[0].finish_reason
                if finish_reason != "stop":
                    print(f"[LLM] Warning: finish_reason is '{finish_reason}'. Output might be truncated.")
                return response.choices[0].message.content
        except Exception as e:
            from openai import APIConnectionError, APIError, APIStatusError, RateLimitError
            # Handle rate limiting specifically
            if isinstance(e, RateLimitError):
                print(f"Rate limit hit (429). Sleeping for 20s before retry...")
                time.sleep(20)
                raise # Re-raise to trigger the outer retry loop
                
            if isinstance(e, (APIConnectionError, APIError, APIStatusError)):
                msg = str(e).strip() or type(e).__name__
                if "connection" in msg.lower() or "getaddrinfo" in msg.lower():
                    msg += " Check AZURE_OPENAI_ENDPOINT (or OPENAI_API_KEY) and network/VPN/DNS."
                raise RuntimeError(f"Cannot reach OpenAI/Azure: {msg}") from e
            raise

    def _generate_gemini(self, prompt, max_tokens, json_mode, temperature, response_schema) -> str:
        from google.genai import types
        
        system_instruction = None
        contents = []
        
        if isinstance(prompt, list):
            if len(prompt) > 0:
                system_instruction = prompt[0]
                if len(prompt) > 1:
                    # Join the rest as user content
                    user_text = "\n\n".join(prompt[1:])
                    contents = [types.Content(role="user", parts=[types.Part.from_text(text=user_text)])]
                else:
                    # If only system prompt provided, use empty user content to satisfy API
                    contents = [types.Content(role="user", parts=[types.Part.from_text(text=" ")])]
        else:
            contents = [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])]

        config_kwargs = {
            "temperature": temperature if temperature is not None else 0.7,
            "top_p": 0.95,
            "max_output_tokens": max_tokens,
            "response_mime_type": "application/json" if json_mode else "text/plain",
            "safety_settings": [
                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF")
            ]
        }

        if response_schema:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = response_schema
        
        # Add thinking config if available and appropriate
        if "thinking" in self._model.lower() or "gemini-3.1-pro" in self._model.lower():
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level=self._config.GEMINI_THINKING_LEVEL
            )
        
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        generate_content_config = types.GenerateContentConfig(**config_kwargs)
        
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=generate_content_config
            )
            print(f"Gemini response RAW: {response}")
            return response.text
        except Exception as e:
            # Check for 429 in Gemini (usually in exceptions.ResourceExhausted or similar)
            # The google.genai library exceptions might vary, but checking string is a safe fallback
            if "429" in str(e) or "ResourceExhausted" in str(e) or "Quota exceeded" in str(e):
                print(f"Gemini Rate limit hit (429). Sleeping for 20s before retry...")
                time.sleep(20)
            raise
