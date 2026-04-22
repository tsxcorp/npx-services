"""
Backward-compat shim. New code must import from `app.settings`.
This file preserves all module-level names that existing services import.
DO NOT add new vars here — add to settings.py instead.
"""
import asyncio

from app.settings import settings

# ── LLM providers ────────────────────────────────────────────────────────────
OPENROUTER_API_KEY: str = settings.openrouter_api_key
GOOGLE_AI_API_KEY: str = settings.google_ai_api_key
NOVITA_API_KEY: str = settings.novita_api_key
OPENAI_API_KEY: str = settings.openai_api_key

# ── Directus ─────────────────────────────────────────────────────────────────
DIRECTUS_URL: str = settings.directus_url
DIRECTUS_ADMIN_TOKEN: str = settings.directus_admin_token

# ── Mailgun ───────────────────────────────────────────────────────────────────
MAILGUN_API_KEY: str = settings.mailgun_api_key
MAILGUN_DOMAIN: str = settings.mailgun_domain
MAILGUN_API_URL: str = settings.mailgun_api_url

# ── App URLs ──────────────────────────────────────────────────────────────────
PORTAL_URL: str = settings.portal_url
ADMIN_URL: str = settings.admin_url

# ── Concurrency primitive (preserved — matching_service imports this) ─────────
# Global semaphore: max 5 concurrent AI scoring calls across ALL matching requests
# Prevents OpenRouter rate-limit (429) when multiple exhibitors run simultaneously
ai_semaphore = asyncio.Semaphore(5)
