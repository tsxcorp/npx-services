"""
LLM tool context — per-request user/tenant identity resolved from user JWT.
`resolve_tool_context()` is the ONLY place that validates the JWT (via Directus /users/me).
Never trust tenant_id from request body — always resolve from JWT.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from fastapi import HTTPException

from app.settings import settings

# Fields fetched from Directus /users/me — basic user info only.
# Tenant resolution is done via a SEPARATE query to /items/tenant_users
# (same pattern as nexpo-admin/src/actions/auth-actions.ts:fetchUserTenants).
_USERS_ME_FIELDS = "id,first_name,last_name,email,language"


@dataclass
class BrandKit:
    """Resolved brand kit for the current context (event or tenant default)."""
    id: str
    name: str
    primary_color: str = "#4F80FF"
    secondary_color: str = "#06043E"
    font_heading: str = "Be Vietnam Pro"
    font_body: str = "Be Vietnam Pro"
    voice_tone: str = "professional"  # professional | friendly | formal
    logo_url: str | None = None
    is_default: bool = False


@dataclass
class ToolContext:
    """
    Immutable per-request context injected into every tool call.
    Built by resolve_tool_context() — never constructed from request body.
    """
    user_token: str          # original JWT — for user-scoped Directus reads
    user_id: str
    user_name: str           # first_name + last_name
    user_email: str
    tenant_id: str
    tenant_name: str
    tenant_tier: Literal["free", "starter", "pro", "enterprise"]
    features: list[str]      # feature flags from tenant row
    locale: Literal["vi", "en"]
    current_route: str       # e.g. "/events", "/exhibitors/123"
    current_entity_id: str | None = None
    brand_kit: BrandKit | None = None
    extra: dict[str, Any] = field(default_factory=dict)


async def _directus_get(
    path: str,
    *,
    token: str,
    params: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> Any:
    """Low-level async Directus GET helper. Returns parsed JSON `data` field."""
    url = f"{settings.directus_url}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers, params=params)
    if resp.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="invalid_jwt")
    resp.raise_for_status()
    body = resp.json()
    return body.get("data", body)


async def _resolve_brand_kit(
    tenant_id: str,
    event_id: str | None,
    user_token: str,
) -> BrandKit | None:
    """
    Resolve brand kit with priority:
    1. Event-specific brand_kit (if event_id given)
    2. Tenant default brand_kit (is_default=True, is_active=True)
    3. Any active brand_kit for the tenant
    Returns None if tenant has no brand kit configured.
    """
    try:
        if event_id:
            event = await _directus_get(
                f"/items/events/{event_id}",
                token=user_token,
                params={"fields": "brand_kit_id.*"},
            )
            bk = event.get("brand_kit_id")
            if bk and isinstance(bk, dict):
                return _parse_brand_kit(bk)

        # Fetch tenant default
        rows = await _directus_get(
            "/items/brand_kits",
            token=user_token,
            params={
                "filter[tenant_id][_eq]": tenant_id,
                "filter[is_default][_eq]": "true",
                "filter[is_active][_eq]": "true",
                "limit": "1",
            },
        )
        if rows:
            return _parse_brand_kit(rows[0])

        # Any active brand
        any_rows = await _directus_get(
            "/items/brand_kits",
            token=user_token,
            params={
                "filter[tenant_id][_eq]": tenant_id,
                "filter[is_active][_eq]": "true",
                "limit": "1",
                "sort": "-date_created",
            },
        )
        if any_rows:
            return _parse_brand_kit(any_rows[0])
    except (HTTPException, httpx.HTTPStatusError):
        # Brand kit is optional — degrade gracefully
        pass

    return None


def _parse_brand_kit(data: dict[str, Any]) -> BrandKit:
    return BrandKit(
        id=str(data.get("id", "")),
        name=data.get("name", "Default"),
        primary_color=data.get("primary_color", "#4F80FF"),
        secondary_color=data.get("secondary_color", "#06043E"),
        font_heading=data.get("font_heading", "Be Vietnam Pro"),
        font_body=data.get("font_body", "Be Vietnam Pro"),
        voice_tone=data.get("voice_tone", "professional"),
        logo_url=data.get("logo_url"),
        is_default=bool(data.get("is_default", False)),
    )


def _detect_locale(user_data: dict[str, Any]) -> Literal["vi", "en"]:
    """Detect locale from user preferences or default to vi."""
    lang = user_data.get("language") or "vi"
    return "en" if str(lang).startswith("en") else "vi"


def _extract_first_active_tenant(
    tenant_users: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return first active tenant_user entry that has a valid tenant object."""
    for tu in tenant_users:
        if tu.get("is_active") is False:
            continue
        tenant = tu.get("tenant_id")
        if tenant and isinstance(tenant, dict):
            return tu
    return None


def _match_tenant_hint(
    tenant_users: list[dict[str, Any]],
    tenant_id_hint: str,
) -> dict[str, Any] | None:
    """
    Return the active tenant_user entry matching the hinted tenant_id.
    Returns None if no active membership exists for that tenant.
    """
    for tu in tenant_users:
        if tu.get("is_active") is False:
            continue
        tenant = tu.get("tenant_id")
        if tenant and isinstance(tenant, dict):
            if str(tenant.get("id", "")) == str(tenant_id_hint):
                return tu
    return None


async def resolve_tool_context(
    user_jwt: str,
    route: str,
    entity_id: str | None = None,
    tenant_id_hint: str | None = None,
) -> ToolContext:
    """
    Validate user JWT via Directus /users/me and build ToolContext.

    Args:
        user_jwt: User JWT from Authorization header
        route: Current admin route (already validated by router regex)
        entity_id: Current entity ID (already validated by router regex)
        tenant_id_hint: Optional tenant_id from UI tenant switcher. If provided,
            MUST match an active tenant membership for the user; otherwise 403.
            If omitted, falls back to first active tenant.

    Raises:
        HTTPException(401) — JWT missing or invalid
        HTTPException(403) — user has no active tenant OR hint doesn't match any membership
    """
    if not user_jwt:
        raise HTTPException(status_code=401, detail="missing_jwt")

    user = await _directus_get(
        "/users/me",
        token=user_jwt,
        params={"fields": _USERS_ME_FIELDS},
    )

    user_id = str(user.get("id", ""))

    # Resolve tenant via separate query to tenant_users collection.
    # This mirrors nexpo-admin/src/actions/auth-actions.ts:fetchUserTenants
    # which uses { tenant: ['id','name',...] } relation (field is 'tenant', not 'tenant_id').
    tenant_users_rows: list[dict[str, Any]] = await _directus_get(
        "/items/tenant_users",
        token=user_jwt,
        params={
            "fields": "id,role_type,is_active,tenant.id,tenant.name,tenant.features,tenant.subscription_tier",
            "filter[user][_eq]": user_id,
            "filter[is_active][_eq]": "true",
        },
    )
    # Directus returns list directly (not wrapped in 'data' — _directus_get strips 'data')
    if not isinstance(tenant_users_rows, list):
        tenant_users_rows = []

    # Normalize: map 'tenant' (relation field) to 'tenant_id' for consistency with helpers
    for tu in tenant_users_rows:
        if "tenant" in tu and "tenant_id" not in tu:
            tu["tenant_id"] = tu.pop("tenant")

    if tenant_id_hint:
        active_tu = _match_tenant_hint(tenant_users_rows, tenant_id_hint)
        if not active_tu:
            raise HTTPException(status_code=403, detail="tenant_membership_required")
    else:
        active_tu = _extract_first_active_tenant(tenant_users_rows)

    if not active_tu:
        raise HTTPException(status_code=403, detail="no_active_tenant")

    tenant: dict[str, Any] = active_tu["tenant_id"]  # expanded dict
    tenant_id = str(tenant.get("id", ""))
    tenant_name = str(tenant.get("name", ""))
    role_type_raw = active_tu.get("role_type", "viewer")
    # role_type may be string ("owner","admin","editor","viewer") or int — handle both
    role_type = role_type_raw if isinstance(role_type_raw, str) else str(role_type_raw)

    # Map tenant subscription plan → tier label for quota
    tier = _role_to_tier(role_type, tenant)  # role_type now str ("owner","admin",...)

    features: list[str] = tenant.get("features") or []

    locale = _detect_locale(user)
    first = user.get("first_name") or ""
    last = user.get("last_name") or ""
    user_name = f"{first} {last}".strip() or user.get("email", "")

    # Resolve brand kit (non-blocking if fails)
    brand_kit = await _resolve_brand_kit(tenant_id, entity_id, user_jwt)

    return ToolContext(
        user_token=user_jwt,
        user_id=str(user.get("id", "")),
        user_name=user_name,
        user_email=user.get("email", ""),
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        tenant_tier=tier,
        features=features,
        locale=locale,
        current_route=route or "/",
        current_entity_id=entity_id,
        brand_kit=brand_kit,
    )


def _role_to_tier(
    role_type: str,
    tenant: dict[str, Any],
) -> Literal["free", "starter", "pro", "enterprise"]:
    """
    Map tenant subscription tier to NexClaude tier.
    Reads tenant.subscription_tier (set in Directus admin panel).
    Phase 1d will refine with nexclaude_settings lookup.
    """
    plan = str(tenant.get("subscription_tier") or tenant.get("subscription_plan") or "free").lower()
    if "enterprise" in plan:
        return "enterprise"
    if "pro" in plan:
        return "pro"
    if "starter" in plan:
        return "starter"
    return "free"
