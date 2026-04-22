"""
Pydantic Settings — single source of truth for all environment variables.
Replaces ad-hoc os.getenv() in config.py.
New code should import from here; config.py is a backward-compat shim.
"""
from __future__ import annotations

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    from pydantic import Field
    _PYDANTIC_SETTINGS_AVAILABLE = True
except ImportError:
    _PYDANTIC_SETTINGS_AVAILABLE = False


if _PYDANTIC_SETTINGS_AVAILABLE:
    class Settings(BaseSettings):
        model_config = SettingsConfigDict(
            env_file='.env',
            extra='ignore',
            case_sensitive=False,
        )

        # ── Mailgun ─────────────────────────────────────────────────────
        mailgun_api_key: str = Field(default='', alias='MAILGUN_API_KEY')
        mailgun_domain: str = Field(default='', alias='MAILGUN_DOMAIN')
        mailgun_api_url: str = Field(default='https://api.mailgun.net', alias='MAILGUN_API_URL')

        # ── Directus ─────────────────────────────────────────────────────
        directus_url: str = Field(default='https://app.nexpo.vn', alias='DIRECTUS_URL')
        directus_admin_token: str = Field(default='', alias='DIRECTUS_ADMIN_TOKEN')

        # ── LLM providers ────────────────────────────────────────────────
        openrouter_api_key: str = Field(default='', alias='OPENROUTER_API_KEY')
        google_ai_api_key: str = Field(default='', alias='GOOGLE_AI_API_KEY')
        novita_api_key: str = Field(default='', alias='NOVITA_API_KEY')
        openai_api_key: str = Field(default='', alias='OPENAI_API_KEY')

        # ── AI Gateway (9router — DEV ONLY) ──────────────────────────────
        # When set, AI calls try this OpenAI-compatible endpoint first, then
        # fall back to direct providers above. Leave empty in staging/prod.
        ai_gateway_url: str = Field(default='', alias='AI_GATEWAY_URL')
        ai_gateway_api_key: str = Field(default='sk-local', alias='AI_GATEWAY_API_KEY')
        ai_gateway_text_model: str = Field(
            default='gc/gemini-2.5-pro',
            alias='AI_GATEWAY_TEXT_MODEL',
        )
        ai_gateway_chat_model: str = Field(
            default='cc/claude-sonnet-4-5',
            alias='AI_GATEWAY_CHAT_MODEL',
        )

        # ── App URLs ─────────────────────────────────────────────────────
        portal_url: str = Field(default='https://portal.nexpo.vn', alias='PORTAL_URL')
        admin_url: str = Field(default='https://platform.nexpo.vn', alias='ADMIN_URL')

        # ── NexClaude gateway config ──────────────────────────────────────
        nexclaude_primary_model: str = Field(
            default='openrouter/anthropic/claude-sonnet-4-5',
            alias='NEXCLAUDE_PRIMARY_MODEL',
        )
        nexclaude_fallback_chain: list[str] = Field(
            default=[
                'openrouter/google/gemini-2.5-pro',
                'openrouter/openai/gpt-4o',
                'gemini/gemini-2.5-pro',
            ],
            alias='NEXCLAUDE_FALLBACK_CHAIN',
        )
        nexclaude_max_steps: int = Field(default=5, alias='NEXCLAUDE_MAX_STEPS')
        nexclaude_timeout_seconds: int = Field(default=60, alias='NEXCLAUDE_TIMEOUT_SECONDS')
        nexclaude_retry_backoff_ms: int = Field(default=500, alias='NEXCLAUDE_RETRY_BACKOFF_MS')

        # HMAC signing key for hard-action commit payloads (Phase 1a+)
        # Must be a strong random string (at least 32 bytes, base64 or hex).
        # Rotate quarterly. NEVER commit the real value.
        nexclaude_signing_secret: str = Field(
            default='dev-only-insecure-change-in-production',
            alias='NEXCLAUDE_SIGNING_SECRET',
        )

    # Singleton — fails fast at import time if required vars are missing
    settings = Settings()

else:
    # Graceful degradation if pydantic-settings is not installed
    import os
    from types import SimpleNamespace

    settings = SimpleNamespace(  # type: ignore[assignment]
        mailgun_api_key=os.getenv('MAILGUN_API_KEY', ''),
        mailgun_domain=os.getenv('MAILGUN_DOMAIN', ''),
        mailgun_api_url=os.getenv('MAILGUN_API_URL', 'https://api.mailgun.net'),
        directus_url=os.getenv('DIRECTUS_URL', 'https://app.nexpo.vn'),
        directus_admin_token=os.getenv('DIRECTUS_ADMIN_TOKEN', ''),
        openrouter_api_key=os.getenv('OPENROUTER_API_KEY', ''),
        google_ai_api_key=os.getenv('GOOGLE_AI_API_KEY', ''),
        novita_api_key=os.getenv('NOVITA_API_KEY', ''),
        openai_api_key=os.getenv('OPENAI_API_KEY', ''),
        ai_gateway_url=os.getenv('AI_GATEWAY_URL', ''),
        ai_gateway_api_key=os.getenv('AI_GATEWAY_API_KEY', 'sk-local'),
        ai_gateway_text_model=os.getenv('AI_GATEWAY_TEXT_MODEL', 'gc/gemini-2.5-pro'),
        ai_gateway_chat_model=os.getenv('AI_GATEWAY_CHAT_MODEL', 'cc/claude-sonnet-4-5'),
        portal_url=os.getenv('PORTAL_URL', 'https://portal.nexpo.vn'),
        admin_url=os.getenv('ADMIN_URL', 'https://platform.nexpo.vn'),
        nexclaude_primary_model='openrouter/anthropic/claude-sonnet-4-5',
        nexclaude_fallback_chain=[
            'openrouter/google/gemini-2.5-pro',
            'openrouter/openai/gpt-4o',
            'gemini/gemini-2.5-pro',
        ],
        nexclaude_max_steps=5,
        nexclaude_timeout_seconds=60,
        nexclaude_retry_backoff_ms=500,
        nexclaude_signing_secret=os.getenv(
            'NEXCLAUDE_SIGNING_SECRET',
            'dev-only-insecure-change-in-production',
        ),
    )
