"""
Broadcast campaign router.

Endpoints:
  POST /broadcast/send          Resolve audience → chunked Mailgun bulk send
  POST /mailgun/events          Webhook — ingest tracking events (opens/clicks/bounces)
  POST /email/preview-send      "Send test to my inbox" for email template editor

All handlers use DIRECTUS_ADMIN_TOKEN via `directus_get/post/patch` helpers.
Plan-tier caps enforced: free=1k, starter=10k, pro=50k, enterprise=unlimited.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.services.directus import directus_get, directus_post, directus_patch
from app.services.mailgun import send_mailgun, wrap_email_body
from app.services.handlers.template_render import safe_substitute, build_context
from app.services.mjml_compile import compile_mjml
from app.config import DIRECTUS_URL

# Base URL for nexpo-insight (used in unsubscribe links).
# Override in dev via INSIGHTS_BASE_URL env var.
INSIGHTS_BASE_URL = os.getenv("INSIGHTS_BASE_URL", "https://insights.nexpo.vn")

# ── Auth: validate caller is a logged-in Directus user ───────────────────────
# Auth model: caller must supply a valid Directus user JWT in Authorization header.
# nexpo-admin always has a user session; anonymous / external callers are rejected.
# FIX 2: preview-send was fully unauthenticated — added _require_user_jwt dep.
_bearer = HTTPBearer(auto_error=False)


async def _require_user_jwt(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """Validate Bearer JWT against Directus /users/me. Returns user_id on success."""
    if not creds or not creds.credentials:
        raise HTTPException(status_code=401, detail="missing_authorization")
    token = creds.credentials
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{DIRECTUS_URL}/users/me?fields[]=id",
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail="invalid_or_expired_token")
        user_id: str = resp.json().get("data", {}).get("id", "")
        if not user_id:
            raise HTTPException(status_code=401, detail="user_not_resolved")
        return user_id
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("JWT validation failed: %s", exc)
        raise HTTPException(status_code=401, detail="auth_check_failed")

log = logging.getLogger(__name__)
router = APIRouter()

# ── Plan-tier recipient caps ──────────────────────────────────────────────────
RECIPIENT_CAPS: dict[str, int] = {
    "free": 1_000,
    "starter": 10_000,
    "pro": 50_000,
    "enterprise": 10_000_000,  # effectively unlimited
}

CHUNK_SIZE = int(os.getenv("BROADCAST_CHUNK_SIZE", "50"))  # per Mailgun batch
CHUNK_DELAY_MS = int(os.getenv("BROADCAST_CHUNK_DELAY_MS", "200"))  # throttle


# ── Pydantic models ───────────────────────────────────────────────────────────

class SendRequest(BaseModel):
    campaign_id: str


class PreviewSendRequest(BaseModel):
    template_id: str
    to_email: str
    sample_context: dict[str, Any] | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _fetch_campaign(campaign_id: str) -> dict[str, Any]:
    resp = await directus_get(
        f"/items/broadcast_campaigns/{campaign_id}"
        "?fields[]=id,tenant_id,event_id,email_template_id,name,status,"
        "audience_filter,sender_name,attachments,tracking_opens,tracking_clicks,unsubscribe_required"
    )
    camp = resp.get("data") or {}
    if not camp:
        raise HTTPException(404, f"campaign {campaign_id} not found")
    return camp


async def _fetch_template(template_id: str) -> dict[str, Any]:
    resp = await directus_get(
        f"/items/email_templates/{template_id}"
        "?fields[]=id,event_id,tenant_id,module,subject,sender_name,mjml_source,html_compiled"
    )
    t = resp.get("data") or {}
    if not t:
        raise HTTPException(404, f"template {template_id} not found")
    return t


async def _fetch_tenant_plan(tenant_id: int) -> str:
    try:
        resp = await directus_get(f"/items/tenants/{tenant_id}?fields[]=subscription_plan")
        return (resp.get("data") or {}).get("subscription_plan") or "free"
    except Exception:
        return "free"


async def _resolve_audience(campaign: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve audience_filter DSL → list of {email, registration_id/exhibitor_id}."""
    f = campaign.get("audience_filter") or {}
    aud_type = f.get("type", "registrations")
    filters = f.get("filters") or {}
    event_id = campaign.get("event_id")
    if not event_id:
        return []

    if aud_type == "registrations":
        query = f"/items/registrations?filter[event_id][_eq]={event_id}&fields[]=id,email,full_name&limit=-1"
        # Extra filters — ticket_class, checked_in etc. (MVP: simple equality match)
        for key, val in filters.items():
            if val is None:
                continue
            if isinstance(val, bool):
                val = "true" if val else "false"
            query += f"&filter[{key}][_eq]={val}"
        resp = await directus_get(query)
        rows = resp.get("data") or []
        return [
            {"registration_id": r["id"], "email": r.get("email"), "name": r.get("full_name")}
            for r in rows if r.get("email")
        ]

    if aud_type == "exhibitors":
        # Exhibitors linked to event via exhibitor_events
        query = (
            f"/items/exhibitor_events?filter[event_id][_eq]={event_id}"
            "&fields[]=exhibitor_id.id,exhibitor_id.email&limit=-1"
        )
        resp = await directus_get(query)
        rows = resp.get("data") or []
        out = []
        for row in rows:
            ex = row.get("exhibitor_id")
            if isinstance(ex, dict) and ex.get("email"):
                out.append({"exhibitor_id": ex["id"], "email": ex["email"]})
        return out

    return []


async def _update_campaign_status(campaign_id: str, patch: dict[str, Any]) -> None:
    try:
        await directus_patch(f"/items/broadcast_campaigns/{campaign_id}", patch)
    except Exception as exc:
        log.error("campaign status update failed: %s", exc)


async def _create_recipient(campaign_id: str, aud: dict[str, Any]) -> str | None:
    """Create broadcast_recipients row. Returns id, or None on failure."""
    try:
        payload = {
            "campaign_id": campaign_id,
            "registration_id": aud.get("registration_id"),
            "exhibitor_id": aud.get("exhibitor_id"),
            "email": aud["email"],
            "status": "pending",
        }
        resp = await directus_post("/items/broadcast_recipients", payload)
        return (resp.get("data") or {}).get("id")
    except Exception as exc:
        log.warning("recipient create failed: %s", exc)
        return None


# ── POST /broadcast/send ──────────────────────────────────────────────────────

@router.post("/broadcast/send")
async def broadcast_send(req: SendRequest) -> dict[str, Any]:
    """Resolve audience → chunked Mailgun send with plan-tier cap + tracking."""
    campaign = await _fetch_campaign(req.campaign_id)
    if campaign.get("status") in ("sending", "sent"):
        raise HTTPException(409, f"campaign already {campaign['status']}")

    if not campaign.get("email_template_id"):
        raise HTTPException(400, "campaign has no email_template_id")

    template = await _fetch_template(campaign["email_template_id"])
    if template.get("module") != "broadcast":
        raise HTTPException(422, "linked template is not a broadcast module")

    # Mark sending
    await _update_campaign_status(req.campaign_id, {"status": "sending"})

    # Resolve audience + enforce plan cap
    audience = await _resolve_audience(campaign)
    plan = await _fetch_tenant_plan(campaign["tenant_id"])
    cap = RECIPIENT_CAPS.get(plan, 1_000)
    if len(audience) > cap:
        await _update_campaign_status(req.campaign_id, {"status": "failed"})
        raise HTTPException(
            402,
            f"audience size {len(audience)} exceeds {plan} plan cap ({cap}). Upgrade to send more.",
        )
    if not audience:
        await _update_campaign_status(req.campaign_id, {"status": "failed"})
        raise HTTPException(400, "audience resolved to zero recipients")

    # Build shared HTML body (substitute once for event-level, per-recipient for recipient vars)
    base_mjml = template.get("mjml_source") or ""
    base_html = template.get("html_compiled") or ""
    subject_tmpl = template.get("subject") or campaign.get("name") or "Broadcast"
    sender_name = campaign.get("sender_name") or template.get("sender_name") or "Nexpo"

    # Fetch event context for header/brand shell
    ev_ctx = await build_context(module="broadcast", event_id=str(campaign["event_id"]))
    email_style_resp = await directus_get(
        f"/items/events/{campaign['event_id']}?fields[]=email_style"
    )
    email_style = (email_style_resp.get("data") or {}).get("email_style") or {}

    sent = 0
    failed = 0

    # ── Pre-fetch tenant-level unsubscribe blocklist (one query, not per-recipient) ──
    unsubscribed_emails: set[str] = set()
    try:
        unsub_resp = await directus_get(
            f"/items/broadcast_unsubscribes?filter[tenant_id][_eq]={campaign['tenant_id']}"
            "&fields[]=email&limit=-1"
        )
        unsubscribed_emails = {
            row["email"].lower()
            for row in (unsub_resp.get("data") or [])
            if row.get("email")
        }
        if unsubscribed_emails:
            log.info("broadcast: %d unsubscribed emails will be skipped for tenant %s",
                     len(unsubscribed_emails), campaign["tenant_id"])
    except Exception as exc:
        log.warning("broadcast: failed to fetch unsubscribed emails: %s", exc)

    # Auto-inject unsubscribe footer if template doesn't already contain the {{unsubscribe_url}}
    # token. Applied to whichever source will be rendered (html_compiled preferred, mjml fallback).
    _HTML_UNSUB_FOOTER = (
        '<div style="text-align:center;font-size:11px;color:#94a3b8;padding:8px 20px;">'
        'Bạn nhận email này vì đã đăng ký tại sự kiện. '
        '<a href="{{unsubscribe_url}}" style="color:#94a3b8;">Hủy đăng ký</a>'
        '</div>'
    )
    _MJML_UNSUB_FOOTER = (
        '<mj-section padding="8px 0 0 0">'
        '<mj-column>'
        '<mj-text align="center" font-size="11px" color="#94a3b8" padding="8px 20px">'
        'Bạn nhận email này vì đã đăng ký tại sự kiện. '
        '<a href="{{unsubscribe_url}}" style="color:#94a3b8;">Hủy đăng ký</a>'
        '</mj-text>'
        '</mj-column>'
        '</mj-section>'
        '</mjml-body>'
    )
    if base_html and "{{unsubscribe_url}}" not in base_html:
        base_html = base_html.replace("</body>", f"{_HTML_UNSUB_FOOTER}</body>")
    if base_mjml and "{{unsubscribe_url}}" not in base_mjml:
        base_mjml = base_mjml.replace("</mjml-body>", _MJML_UNSUB_FOOTER)

    async def dispatch(aud: dict[str, Any]) -> None:
        nonlocal sent, failed

        # Skip recipients who have previously unsubscribed for this tenant
        if aud["email"].lower() in unsubscribed_emails:
            log.info("broadcast: skipped unsubscribed: %s", aud["email"])
            recipient_id = await _create_recipient(req.campaign_id, aud)
            if recipient_id:
                await directus_patch(
                    f"/items/broadcast_recipients/{recipient_id}",
                    {"status": "unsubscribed"},
                )
            return

        recipient_id = await _create_recipient(req.campaign_id, aud)
        try:
            # Build unsubscribe URL pointing to nexpo-insight /unsubscribe/{id}
            unsub_url = (
                f"{INSIGHTS_BASE_URL}/unsubscribe/{recipient_id}"
                f"?campaign={req.campaign_id}"
                if recipient_id
                else ""
            )
            # Determine recipient type from audience resolution
            recipient_type = "exhibitor" if aud.get("exhibitor_id") else "visitor"
            ctx = {
                **ev_ctx,
                "recipient": {
                    "full_name": aud.get("name"),
                    "email": aud["email"],
                    "type": recipient_type,
                },
                "unsubscribe_url": unsub_url,
            }
            rendered_html = safe_substitute(base_html or base_mjml, ctx, module="broadcast")
            wrapped = wrap_email_body(rendered_html, email_style)
            subject = safe_substitute(subject_tmpl, ctx, module="broadcast", escape_html=False)
            ok = await send_mailgun(
                to=aud["email"],
                subject=subject,
                html=wrapped,
                from_email=f"{sender_name} <noreply@m.nexpo.vn>",
            )
            if ok:
                sent += 1
                if recipient_id:
                    await directus_patch(
                        f"/items/broadcast_recipients/{recipient_id}",
                        {"status": "sent", "sent_at": datetime.now(timezone.utc).isoformat()},
                    )
            else:
                failed += 1
                if recipient_id:
                    await directus_patch(
                        f"/items/broadcast_recipients/{recipient_id}",
                        {"status": "failed", "error_message": "mailgun send returned false"},
                    )
        except Exception as exc:
            failed += 1
            log.warning("send failed for %s: %s", aud.get("email"), exc)
            if recipient_id:
                try:
                    await directus_patch(
                        f"/items/broadcast_recipients/{recipient_id}",
                        {"status": "failed", "error_message": str(exc)[:400]},
                    )
                except Exception:
                    pass

    # Chunked send with throttle
    for i in range(0, len(audience), CHUNK_SIZE):
        batch = audience[i : i + CHUNK_SIZE]
        await asyncio.gather(*(dispatch(aud) for aud in batch))
        if i + CHUNK_SIZE < len(audience):
            await asyncio.sleep(CHUNK_DELAY_MS / 1000)

    final_status = "sent" if failed == 0 else ("sent" if sent > 0 else "failed")
    await _update_campaign_status(
        req.campaign_id,
        {
            "status": final_status,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "sent_count": sent,
            "failed_count": failed,
        },
    )

    return {"ok": True, "campaign_id": req.campaign_id, "sent": sent, "failed": failed, "total": len(audience)}


# ── GET /broadcast/recipient/<id> ─────────────────────────────────────────────

@router.get("/broadcast/recipient/{recipient_id}")
async def get_broadcast_recipient(recipient_id: str) -> dict[str, Any]:
    """Fetch broadcast recipient details for the unsubscribe confirmation page.

    Public endpoint (no auth) — recipient_id is a UUID, opaque enough for MVP.
    Returns: { email, campaign_name, sender_name, tenant_id }
    """
    try:
        resp = await directus_get(
            f"/items/broadcast_recipients/{recipient_id}"
            "?fields[]=id,email,campaign_id.name,campaign_id.sender_name,campaign_id.tenant_id"
        )
        row = resp.get("data") or {}
        if not row:
            raise HTTPException(404, "recipient not found")
        camp = row.get("campaign_id") or {}
        return {
            "id": row["id"],
            "email": row.get("email") or "",
            "campaign_name": camp.get("name") or "",
            "sender_name": camp.get("sender_name") or "Nexpo",
            "tenant_id": camp.get("tenant_id"),
        }
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("get_broadcast_recipient failed: %s", exc)
        raise HTTPException(500, "lookup failed")


# ── POST /mailgun/events (webhook) ────────────────────────────────────────────

MG_EVENT_MAP: dict[str, str] = {
    "delivered": "delivered",
    "opened": "opened",
    "clicked": "clicked",
    "failed": "bounced",  # Mailgun "failed" = bounce
    "complained": "complained",
    "unsubscribed": "unsubscribed",
}


@router.post("/mailgun/events")
async def mailgun_webhook(request: Request) -> dict[str, Any]:
    """Ingest Mailgun webhook events → update broadcast_recipients + email_events."""
    body = await request.json()
    event = body.get("event-data") or {}
    mg_event = event.get("event") or ""
    our_event = MG_EVENT_MAP.get(mg_event)
    if not our_event:
        return {"ok": True, "ignored": mg_event}

    message_id = (event.get("message") or {}).get("headers", {}).get("message-id", "")
    recipient = event.get("recipient") or ""
    ts = event.get("timestamp")
    timestamp_iso = (
        datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else datetime.now(timezone.utc).isoformat()
    )

    # Persist to email_events (analytics — always)
    try:
        await directus_post(
            "/items/email_events",
            {
                "event_type": our_event,
                "recipient_email": recipient,
                "message_id": message_id,
                "timestamp": timestamp_iso,
                "meta": {
                    "user_agent": event.get("user-agent"),
                    "url": event.get("url"),
                    "reason": event.get("reason"),
                    "severity": event.get("severity"),
                },
            },
        )
    except Exception as exc:
        log.warning("email_events insert failed: %s", exc)

    # Correlate with broadcast_recipients by message_id
    if message_id:
        try:
            resp = await directus_get(
                f"/items/broadcast_recipients?filter[mailgun_message_id][_eq]={message_id}"
                "&fields[]=id&limit=1"
            )
            recipients = resp.get("data") or []
            if recipients:
                patch: dict[str, Any] = {"status": our_event}
                if our_event == "delivered":
                    patch["delivered_at"] = timestamp_iso
                elif our_event == "opened":
                    patch["opened_at"] = timestamp_iso
                elif our_event == "clicked":
                    patch["clicked_at"] = timestamp_iso
                await directus_patch(f"/items/broadcast_recipients/{recipients[0]['id']}", patch)
        except Exception as exc:
            log.warning("broadcast_recipients update failed: %s", exc)

    return {"ok": True, "event": our_event}


# ── POST /email/preview-send (test send) ──────────────────────────────────────

@router.post("/email/preview-send")
async def email_preview_send(
    req: PreviewSendRequest,
    _user_id: str = Depends(_require_user_jwt),
) -> dict[str, Any]:
    """Send a template to organizer's own inbox for preview. No persistence.

    Auth: requires valid Directus user JWT in Authorization header.
    Tenant ownership check: asserts template.tenant_id is in caller's active tenants.
    """
    template = await _fetch_template(req.template_id)

    # Tenant ownership check — template must belong to a tenant the caller is a member of
    template_tenant_id = template.get("tenant_id")
    if template_tenant_id:
        try:
            qs = (
                f"/items/tenant_users"
                f"?filter[user][_eq]={_user_id}"
                f"&filter[tenant][_eq]={template_tenant_id}"
                f"&filter[is_active][_eq]=true"
                f"&fields[]=id&limit=1"
            )
            resp = await directus_get(qs)
            if not resp.get("data"):
                raise HTTPException(status_code=403, detail="template_not_owned_by_caller")
        except HTTPException:
            raise
        except Exception as exc:
            log.warning("tenant ownership check failed: %s", exc)
            raise HTTPException(status_code=403, detail="ownership_check_failed")

    module = template.get("module") or "meeting"
    event_id = template.get("event_id")

    # Build a best-effort preview context. Merge hydrated Directus data with
    # any caller-supplied overrides so the preview resembles a real send.
    hydrated_ctx: dict[str, Any] = {}
    if event_id:
        try:
            hydrated_ctx = await build_context(module, event_id=str(event_id))
        except Exception as exc:
            log.warning("preview build_context failed: %s", exc)
    # Fill in fake recipient / meeting / form sample data where real data absent,
    # so template variables resolve to something meaningful instead of bare tokens.
    # Use explicit None check — build_context can set keys to None, which defeats setdefault.
    def _fill(key: str, default: dict[str, Any]) -> None:
        if not hydrated_ctx.get(key):
            hydrated_ctx[key] = default

    _fill("recipient", {
        "full_name": "Nguyễn Văn A (Preview)",
        "email": req.to_email,
        "phone_number": "0900000000",
        "company": "Nexpo Preview Co.",
        "badge_id": "PREVIEW-001",
    })
    if module == "meeting":
        _fill("meeting", {
            "scheduled_at": "13:00 29/03/2026",
            "location": "Booth A1",
            "meeting_type": "business",
            "duration_minutes": 30,
            "portal_url": "https://portal.nexpo.vn/meetings",
        })
        _fill("exhibitor", {
            "name": "ACME Corp (Preview)",
            "booth": "A1",
            "booth_code": "A1",
            "email": "exhibitor@example.com",
        })
        _fill("visitor", dict(hydrated_ctx["recipient"]))
    if module == "broadcast":
        if not hydrated_ctx.get("unsubscribe_url"):
            hydrated_ctx["unsubscribe_url"] = f"{INSIGHTS_BASE_URL}/unsubscribe/preview?campaign=preview"

    # Caller overrides win
    if req.sample_context:
        hydrated_ctx.update(req.sample_context)

    html_compiled = template.get("html_compiled") or ""
    if not html_compiled or html_compiled.strip().lower().startswith("<mjml"):
        # Client (mjml-browser) failed to compile or saved empty html. Try the
        # server-side mjml CLI fallback. Persist the result back so subsequent
        # sends skip this work.
        mjml_src = template.get("mjml_source") or ""
        compiled = compile_mjml(mjml_src) if mjml_src else None
        if compiled:
            html_compiled = compiled
            log.info("preview-send: server-side mjml fallback used (template_id=%s)", req.template_id)
            try:
                await directus_patch(
                    f"/items/email_templates/{req.template_id}",
                    {"html_compiled": compiled},
                )
            except Exception as exc:
                log.warning("preview-send: failed to persist server-side compile: %s", exc)
        else:
            log.warning("preview-send: html_compiled missing AND server-side compile failed — template_id=%s", req.template_id)
            raise HTTPException(
                status_code=422,
                detail="template html_compiled is empty and server-side MJML compile failed — re-save the template in the builder",
            )

    rendered = safe_substitute(html_compiled, hydrated_ctx, module=module)

    # Pull event brand for shell
    email_style: dict[str, Any] = {}
    if event_id:
        try:
            resp = await directus_get(
                f"/items/events/{event_id}?fields[]=email_style"
            )
            email_style = (resp.get("data") or {}).get("email_style") or {}
        except Exception:
            pass

    wrapped = wrap_email_body(rendered, email_style)
    raw_subject = template.get("subject") or "Email template test"
    resolved_subject = safe_substitute(raw_subject, hydrated_ctx, module=module, escape_html=False)
    subject = f"[PREVIEW] {resolved_subject}"
    ok = await send_mailgun(
        to=req.to_email,
        subject=subject,
        html=wrapped,
        from_email="Nexpo Preview <noreply@m.nexpo.vn>",
    )
    if not ok:
        raise HTTPException(502, "mailgun send failed")
    return {"ok": True, "to": req.to_email}
