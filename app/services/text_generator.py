"""
Unified text-generation helper with provider fallback.
Order: Google AI (Gemini direct) → OpenRouter → Novita.ai

Used by features that need a single-shot LLM completion (e.g. email template
generation). For streaming + tool-calling, use `llm_gateway.LLMGateway`.
"""
from __future__ import annotations

import logging
import httpx

from app.config import GOOGLE_AI_API_KEY, OPENROUTER_API_KEY, NOVITA_API_KEY
from app.settings import settings

logger = logging.getLogger(__name__)


# Per-provider default model — chosen for HTML/long-form generation quality
GEMINI_MODEL = "gemini-2.5-flash"
OPENROUTER_MODEL = "openai/gpt-4o"
NOVITA_MODEL = "deepseek/deepseek-v3-0324"


class AllProvidersFailedError(RuntimeError):
    """Raised when every configured provider returned an error."""


async def _try_gemini(prompt: str, temperature: float, max_tokens: int) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GOOGLE_AI_API_KEY)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[types.Part.from_text(text=prompt)],
        config=types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        ),
    )
    text = (response.text or "").strip()
    if not text:
        raise ValueError("Gemini returned empty response")
    return text


async def _try_openai_compatible(
    api_key: str,
    base_url: str,
    model: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
) -> str:
    async with httpx.AsyncClient(timeout=180) as http:
        resp = await http.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            },
        )
        resp.raise_for_status()
        result = resp.json()

    text = (result["choices"][0]["message"]["content"] or "").strip()
    if not text:
        raise ValueError("Provider returned empty response")
    return text


async def generate_text(
    prompt: str,
    *,
    temperature: float = 0.4,
    max_tokens: int = 6000,
) -> tuple[str, str]:
    """
    Run the prompt through the fallback chain. Returns (text, provider_name).
    Raises AllProvidersFailedError if every configured provider fails.
    """
    providers: list[tuple[str, callable]] = []

    # AI Gateway (9router) — dev-only, tried FIRST when configured
    if settings.ai_gateway_url:
        gateway_url = settings.ai_gateway_url.rstrip("/")
        gateway_model = settings.ai_gateway_text_model
        providers.append((
            f"AI Gateway ({gateway_model})",
            lambda: _try_openai_compatible(
                settings.ai_gateway_api_key, gateway_url,
                gateway_model, prompt, temperature, max_tokens,
            ),
        ))

    if GOOGLE_AI_API_KEY:
        providers.append((
            "Google AI (Gemini)",
            lambda: _try_gemini(prompt, temperature, max_tokens),
        ))
    if NOVITA_API_KEY:
        providers.append((
            "Novita.ai",
            lambda: _try_openai_compatible(
                NOVITA_API_KEY, "https://api.novita.ai/v3/openai",
                NOVITA_MODEL, prompt, temperature, max_tokens,
            ),
        ))
    if OPENROUTER_API_KEY:
        providers.append((
            "OpenRouter",
            lambda: _try_openai_compatible(
                OPENROUTER_API_KEY, "https://openrouter.ai/api/v1",
                OPENROUTER_MODEL, prompt, temperature, max_tokens,
            ),
        ))

    if not providers:
        raise AllProvidersFailedError(
            "No AI provider configured. Set GOOGLE_AI_API_KEY, OPENROUTER_API_KEY, or NOVITA_API_KEY."
        )

    last_error: Exception | None = None
    for name, call_fn in providers:
        try:
            logger.info(f"text_generator: trying {name}...")
            text = await call_fn()
            logger.info(f"text_generator: succeeded via {name}")
            return text, name
        except Exception as exc:
            logger.warning(f"text_generator: {name} failed: {type(exc).__name__}: {exc}")
            last_error = exc
            continue

    raise AllProvidersFailedError(f"All AI providers failed. Last error: {last_error}")
