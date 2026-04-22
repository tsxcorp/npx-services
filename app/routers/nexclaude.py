"""
NexClaude streaming chat router.
POST /llm/nexclaude/stream — SSE streaming endpoint for the NexClaude assistant.

Security:
- User JWT validated via Directus /users/me (resolve_tool_context)
- tenant_id resolved from JWT, never trusted from request body
- Quota checked before LLM call (fails open if quota service is down)
- Admin token never exposed to client
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.services.llm_context import resolve_tool_context
from app.services.llm_gateway import LLMGateway
from app.services.llm_persistence import write_message_to_thread
from app.services.llm_quota import QuotaExceededError, check_quota, increment_usage
from app.services.llm_system_prompt import build_system_prompt
from app.services.sse_events import sse_error
from app.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm/nexclaude", tags=["nexclaude"])

# Regex validators for untrusted request-body fields (security: prevent prompt injection)
_ROUTE_PATTERN = re.compile(r"^/[a-zA-Z0-9/_\-]{0,200}$")
_ENTITY_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")
_TENANT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


class ChatMessage(BaseModel):
    role: str
    content: str | list[Any]

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in ("user", "assistant", "system", "tool"):
            raise ValueError(f"invalid role: {v}")
        return v


class StreamRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=100)
    thread_id: str | None = Field(default=None, max_length=64)
    route: str = Field(default="/", description="Current admin route", max_length=200)
    entity_id: str | None = Field(default=None, description="Current entity ID", max_length=64)
    tenant_id: str | None = Field(
        default=None,
        description="Active tenant hint from UI tenant switcher. MUST match a tenant the user is a member of; otherwise 403.",
        max_length=64,
    )

    @field_validator("route")
    @classmethod
    def _validate_route(cls, v: str) -> str:
        if not _ROUTE_PATTERN.match(v):
            raise ValueError("route must match ^/[a-zA-Z0-9/_-]{0,200}$")
        return v

    @field_validator("entity_id")
    @classmethod
    def _validate_entity_id(cls, v: str | None) -> str | None:
        if v is not None and not _ENTITY_ID_PATTERN.match(v):
            raise ValueError("entity_id must match ^[a-zA-Z0-9_-]{1,64}$")
        return v

    @field_validator("tenant_id")
    @classmethod
    def _validate_tenant_id(cls, v: str | None) -> str | None:
        if v is not None and not _TENANT_ID_PATTERN.match(v):
            raise ValueError("tenant_id must match ^[a-zA-Z0-9_-]{1,64}$")
        return v


def _load_tools_for_ctx() -> dict[str, Any]:
    """
    Load all registered tools from the tool registry.
    Returns mapping of tool_name → ToolSpec.
    Phase 1a+ adds more tools by appending to llm_tools/__init__.py.
    """
    from app.services.llm_tools.base import _REGISTRY
    return dict(_REGISTRY)


@router.post("/stream")
async def stream_chat(request: Request) -> StreamingResponse:
    """
    Stream a NexClaude chat response as Server-Sent Events.

    Headers required:
        Authorization: Bearer <user-jwt>

    Body: StreamRequest JSON

    SSE events emitted:
        text_delta   — incremental text chunk
        tool_call    — tool invocation
        tool_result  — tool execution result
        cancelled    — aborted mid-stream
        error        — unrecoverable error
        done         — stream complete with usage info
    """
    # Extract JWT from Authorization header
    auth_header = request.headers.get("authorization", "")
    user_jwt = auth_header.removeprefix("Bearer ").strip()
    if not user_jwt:
        raise HTTPException(status_code=401, detail="missing_jwt")

    # Parse and validate request body
    try:
        body_json = await request.json()
        body = StreamRequest(**body_json)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc

    # Validate JWT and resolve user/tenant context (raises 401 if invalid)
    # C-1 fix: pass tenant_id hint from body to honor UI tenant switcher.
    # Membership is verified inside resolve_tool_context; hint never overrides auth.
    try:
        ctx = await resolve_tool_context(
            user_jwt=user_jwt,
            route=body.route,
            entity_id=body.entity_id,
            tenant_id_hint=body.tenant_id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"JWT validation failed: {exc}") from exc

    # Quota pre-check (fails open if quota system is not configured)
    try:
        await check_quota(ctx.tenant_id, ctx.tenant_tier, "message")
    except QuotaExceededError as exc:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "quota_exceeded",
                "tier": exc.tier,
                "used": exc.used,
                "limit": exc.limit,
            },
        ) from exc

    # Build context for tools and system prompt
    tools = _load_tools_for_ctx()
    system_prompt = build_system_prompt(ctx)
    messages = [m.model_dump() for m in body.messages]

    gateway = LLMGateway()
    abort = asyncio.Event()

    async def generate() -> Any:
        """
        Async generator driving the SSE stream.
        Persists messages and increments usage after streaming completes.
        Uses `finally` to ensure cleanup even on disconnect.
        """
        try:
            async for event_bytes in gateway.stream(
                messages=messages,
                tools=tools,
                ctx=ctx,
                system_prompt=system_prompt,
                abort=abort,
            ):
                yield event_bytes

        except asyncio.CancelledError:
            abort.set()
            yield sse_error("stream_cancelled", "Client disconnected")
        except Exception as exc:
            logger.exception("nexclaude.stream.unhandled_error")
            yield sse_error("internal_error", str(exc))
        finally:
            # C-3 + H-1 + H-2 fix: read final state from gateway AFTER stream completes
            # Gateway exposes _last_messages (full conversation incl. assistant + tool turns),
            # _last_provider (succeeded provider from fallback chain),
            # _last_usage (token counts), _last_cost (USD cost estimate)
            final_messages = getattr(gateway, "_last_messages", messages)
            final_provider = getattr(gateway, "_last_provider", settings.nexclaude_primary_model)
            final_usage = getattr(gateway, "_last_usage", {})
            final_cost = getattr(gateway, "_last_cost", 0.0)
            total_tokens = final_usage.get("total_tokens", 0)

            # Persist messages and update usage counters asynchronously
            # Non-fatal: persistence errors must not surface to client
            try:
                await write_message_to_thread(
                    thread_id=body.thread_id,
                    tenant_id=ctx.tenant_id,
                    user_id=ctx.user_id,
                    messages=final_messages,
                    provider=final_provider,
                    usage=final_usage,
                    cost_usd=final_cost,
                )
                await increment_usage(
                    tenant_id=ctx.tenant_id,
                    tokens_used=total_tokens,
                    messages_used=1,
                )
            except Exception:
                logger.exception("nexclaude.persistence.failed_silently")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable Nginx buffering for SSE
            "Connection": "keep-alive",
        },
    )
