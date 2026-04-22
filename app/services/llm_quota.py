"""
NexClaude quota enforcement — reads nexclaude_usage from Directus.
TIER_LIMITS is a placeholder; Phase 1d fills actual per-tier limits.
Raises QuotaExceededError → router returns HTTP 402.
"""
from __future__ import annotations

from typing import Any, Literal

import httpx
from fastapi import HTTPException

from app.settings import settings

# Phase 1d fills this with real limits per tier
# Format: { tier: { "messages_per_day": N, "tokens_per_month": N } }
TIER_LIMITS: dict[str, dict[str, int]] = {}  # Phase 1d fills

QuotaType = Literal["message", "token"]


class QuotaExceededError(Exception):
    """Raised when a tenant has exceeded their NexClaude usage quota."""
    def __init__(self, tier: str, quota_type: QuotaType, used: int, limit: int) -> None:
        self.tier = tier
        self.quota_type = quota_type
        self.used = used
        self.limit = limit
        super().__init__(f"Quota exceeded: {quota_type} used={used} limit={limit} tier={tier}")


async def _fetch_usage(tenant_id: str) -> dict[str, Any]:
    """Fetch current usage record for a tenant from Directus nexclaude_usage."""
    url = f"{settings.directus_url}/items/nexclaude_usage"
    headers = {"Authorization": f"Bearer {settings.directus_admin_token}"}
    params = {
        "filter[tenant_id][_eq]": tenant_id,
        "limit": "1",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, headers=headers, params=params)

    if resp.status_code == 404 or not settings.directus_admin_token:
        return {}

    if resp.status_code >= 400:
        # Non-fatal: if quota service is down, allow through (fail open)
        return {}

    data = resp.json().get("data", [])
    return data[0] if data else {}


async def check_quota(
    tenant_id: str,
    tier: str,
    quota_type: QuotaType = "message",
) -> None:
    """
    Check if tenant has exceeded their quota.
    Fails open (allows request) if TIER_LIMITS is empty (Phase 0e placeholder)
    or if Directus call fails — quota enforcement is non-critical path.

    Raises:
        QuotaExceededError: if limit is configured AND tenant is over quota
        HTTPException(402): router catches QuotaExceededError and converts it
    """
    if not TIER_LIMITS:
        # Phase 1d not yet deployed — allow all requests
        return

    limits = TIER_LIMITS.get(tier, {})
    if not limits:
        return  # Tier not configured → allow

    limit = limits.get(f"{quota_type}s_per_day", 0)
    if limit <= 0:
        return  # No limit set → allow

    try:
        usage = await _fetch_usage(tenant_id)
        used = int(usage.get(f"daily_{quota_type}s", 0))
        if used >= limit:
            raise QuotaExceededError(tier=tier, quota_type=quota_type, used=used, limit=limit)
    except QuotaExceededError:
        raise
    except Exception:
        # Fail open — quota check is best-effort
        pass


async def increment_usage(
    tenant_id: str,
    tokens_used: int,
    messages_used: int = 1,
) -> None:
    """
    Increment usage counters in nexclaude_usage via Directus.
    Non-fatal: logs but does not raise on failure.
    Uses upsert pattern (PATCH with on_duplicate_key).
    """
    if not settings.directus_admin_token:
        return

    url = f"{settings.directus_url}/items/nexclaude_usage"
    headers = {
        "Authorization": f"Bearer {settings.directus_admin_token}",
        "Content-Type": "application/json",
    }

    try:
        # Fetch existing record first
        existing = await _fetch_usage(tenant_id)
        record_id = existing.get("id")

        payload = {
            "tenant_id": tenant_id,
            "daily_messages": int(existing.get("daily_messages", 0)) + messages_used,
            "daily_tokens": int(existing.get("daily_tokens", 0)) + tokens_used,
            "total_messages": int(existing.get("total_messages", 0)) + messages_used,
            "total_tokens": int(existing.get("total_tokens", 0)) + tokens_used,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            if record_id:
                await client.patch(
                    f"{url}/{record_id}",
                    headers=headers,
                    json=payload,
                )
            else:
                await client.post(url, headers=headers, json=payload)
    except Exception:
        # Non-fatal — usage tracking must not block chat responses
        pass
