"""
LLM provider registry — static config for all supported providers.
Defines cost rates, capability flags, and fallback ordering.
LiteLLM model IDs must use provider-prefix format: `openrouter/...` or `gemini/...`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderConfig:
    model: str                     # LiteLLM model ID
    cost_input_per_million: float  # USD per 1M input tokens
    cost_output_per_million: float # USD per 1M output tokens
    supports_tools: bool
    supports_vision: bool
    api_key_env: str               # env var LiteLLM reads for this provider


# Default provider registry — ordered from most preferred to least
DEFAULT_PROVIDERS: dict[str, ProviderConfig] = {
    "openrouter/anthropic/claude-sonnet-4-5": ProviderConfig(
        model="openrouter/anthropic/claude-sonnet-4-5",
        cost_input_per_million=3.0,
        cost_output_per_million=15.0,
        supports_tools=True,
        supports_vision=True,
        api_key_env="OPENROUTER_API_KEY",
    ),
    "openrouter/google/gemini-2.5-pro": ProviderConfig(
        model="openrouter/google/gemini-2.5-pro",
        cost_input_per_million=1.25,
        cost_output_per_million=10.0,
        supports_tools=True,
        supports_vision=True,
        api_key_env="OPENROUTER_API_KEY",
    ),
    "openrouter/openai/gpt-4o": ProviderConfig(
        model="openrouter/openai/gpt-4o",
        cost_input_per_million=2.5,
        cost_output_per_million=10.0,
        supports_tools=True,
        supports_vision=True,
        api_key_env="OPENROUTER_API_KEY",
    ),
    # Direct Google AI — bypasses OpenRouter; uses GOOGLE_AI_API_KEY
    "gemini/gemini-2.5-pro": ProviderConfig(
        model="gemini/gemini-2.5-pro",
        cost_input_per_million=1.25,
        cost_output_per_million=10.0,
        supports_tools=True,
        supports_vision=True,
        api_key_env="GEMINI_API_KEY",  # LiteLLM reads GEMINI_API_KEY for gemini/ prefix
    ),
}


def get_provider(model_id: str) -> ProviderConfig | None:
    """Look up a provider config by model ID. Returns None if unknown."""
    return DEFAULT_PROVIDERS.get(model_id)


def estimate_cost(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Return estimated USD cost for a completion. Returns 0.0 if provider unknown."""
    cfg = get_provider(model_id)
    if not cfg:
        return 0.0
    return (
        input_tokens * cfg.cost_input_per_million / 1_000_000
        + output_tokens * cfg.cost_output_per_million / 1_000_000
    )
