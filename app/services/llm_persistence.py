"""
NexClaude persistence — writes chat messages and usage to Directus.
Uses admin token (server-side only) for all writes.
Sanitizes tool_calls/results before persistence to strip sensitive fields.
"""
from __future__ import annotations

import copy
import json
import time
from typing import Any

import httpx

from app.settings import settings

# Keys stripped from tool_calls and tool_results before writing to Directus
_SENSITIVE_KEYS = frozenset({"user_token", "admin_token", "api_key", "token", "password", "secret"})

# Max characters for auto-generated thread title
_TITLE_MAX_CHARS = 64


def _sanitize_value(value: Any) -> Any:
    """Recursively strip sensitive keys from dicts/lists."""
    if isinstance(value, dict):
        return {k: _sanitize_value(v) for k, v in value.items() if k not in _SENSITIVE_KEYS}
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    return value


def sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Return a deep-sanitized copy of messages list.
    Strips sensitive keys from tool_calls function arguments and tool results.
    """
    sanitized = []
    for msg in messages:
        msg_copy = copy.deepcopy(msg)

        # Sanitize tool_calls in assistant messages
        if "tool_calls" in msg_copy and isinstance(msg_copy["tool_calls"], list):
            for tc in msg_copy["tool_calls"]:
                if isinstance(tc, dict) and "function" in tc:
                    fn = tc["function"]
                    if isinstance(fn.get("arguments"), str):
                        try:
                            args = json.loads(fn["arguments"])
                            fn["arguments"] = json.dumps(_sanitize_value(args))
                        except (json.JSONDecodeError, TypeError):
                            pass

        # Sanitize tool result content
        if msg_copy.get("role") == "tool" and isinstance(msg_copy.get("content"), str):
            try:
                result = json.loads(msg_copy["content"])
                msg_copy["content"] = json.dumps(_sanitize_value(result))
            except (json.JSONDecodeError, TypeError):
                pass

        sanitized.append(msg_copy)
    return sanitized


def _make_auto_title(messages: list[dict[str, Any]]) -> str:
    """Generate a thread title from the first user message."""
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                return content.strip()[:_TITLE_MAX_CHARS]
            if isinstance(content, list):
                # Multi-part content — extract first text part
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text = part.get("text", "")
                        if text.strip():
                            return text.strip()[:_TITLE_MAX_CHARS]
    return f"Conversation {int(time.time())}"


async def _directus_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to Directus with admin token. Returns response data or empty dict on failure."""
    if not settings.directus_admin_token:
        return {}
    url = f"{settings.directus_url}{path}"
    headers = {
        "Authorization": f"Bearer {settings.directus_admin_token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json().get("data", {})
    except Exception:
        return {}


async def _directus_patch(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """PATCH to Directus with admin token."""
    if not settings.directus_admin_token:
        return {}
    url = f"{settings.directus_url}{path}"
    headers = {
        "Authorization": f"Bearer {settings.directus_admin_token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.patch(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json().get("data", {})
    except Exception:
        return {}


async def ensure_thread(
    thread_id: str | None,
    tenant_id: str,
    user_id: str,
    messages: list[dict[str, Any]],
) -> str | None:
    """
    Return thread_id — either the provided one or a newly created thread.
    Returns None if Directus write fails (non-fatal).
    """
    if thread_id:
        return thread_id

    title = _make_auto_title(messages)
    result = await _directus_post(
        "/items/nexclaude_threads",
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "title": title,
            "status": "active",
        },
    )
    created_id = result.get("id")
    return str(created_id) if created_id else None


async def write_message_to_thread(
    thread_id: str | None,
    tenant_id: str,
    user_id: str,
    messages: list[dict[str, Any]],
    provider: str,
    usage: dict[str, Any],
    cost_usd: float,
) -> None:
    """
    Persist the full sanitized message history to nexclaude_messages.
    Sanitizes tool_calls/results before writing.
    Non-fatal: any error is silently swallowed (chat must not fail due to persistence).
    """
    if not settings.directus_admin_token:
        return

    resolved_thread_id = await ensure_thread(thread_id, tenant_id, user_id, messages)
    if not resolved_thread_id:
        return  # Thread creation failed — skip message write

    safe_messages = sanitize_messages(messages)

    await _directus_post(
        "/items/nexclaude_messages",
        {
            "thread_id": resolved_thread_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "messages": safe_messages,
            "provider": provider,
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "cost_usd": round(cost_usd, 6),
        },
    )
