"""
LLM Gateway — unified streaming interface over LiteLLM.
Handles provider fallback, tool calling loop, checkpoint execution, and SSE emission.
All LLM API keys are resolved here; no other module touches provider credentials.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncGenerator

from app.services.llm_checkpoint_executor import CheckpointExecutor
from app.services.llm_context import ToolContext
from app.services.llm_provider_registry import estimate_cost
from app.services.sse_events import (
    sse_cancelled,
    sse_done,
    sse_error,
    sse_text_delta,
    sse_tool_call,
    sse_tool_result,
)
from app.settings import settings

try:
    import litellm
    _LITELLM_AVAILABLE = True
except ImportError:
    _LITELLM_AVAILABLE = False


class TransientError(Exception):
    """Raised when a provider call fails in a way that warrants trying the next provider."""


def _configure_litellm_keys() -> None:
    """
    Set provider API keys as env vars — LiteLLM reads from os.environ.
    Called once at gateway construction time.
    """
    if settings.openrouter_api_key:
        os.environ.setdefault("OPENROUTER_API_KEY", settings.openrouter_api_key)
    if settings.google_ai_api_key:
        # LiteLLM reads GEMINI_API_KEY for gemini/ prefix models
        os.environ.setdefault("GEMINI_API_KEY", settings.google_ai_api_key)


GATEWAY_PREFIX = "gateway/"


def _build_attempt_order(primary: str, fallbacks: list[str]) -> list[str]:
    """
    Build provider attempt list: [gateway?, primary, primary (retry), *fallbacks].
    Primary is tried twice (with backoff) before falling back. When
    `AI_GATEWAY_URL` is set, prepend a gateway attempt — dev-only path.
    """
    attempts: list[str] = []
    if settings.ai_gateway_url and settings.ai_gateway_chat_model:
        attempts.append(f"{GATEWAY_PREFIX}{settings.ai_gateway_chat_model}")
    attempts.extend([primary, primary])
    attempts.extend(fallbacks)
    return attempts


def _extract_usage(response_obj: Any) -> dict[str, int]:
    """Extract token usage from a LiteLLM response object."""
    try:
        usage = response_obj.usage
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }
    except AttributeError:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


async def _accumulate_stream(
    stream: Any,
) -> tuple[str, list[dict[str, Any]], dict[str, int]]:
    """
    Consume a LiteLLM streaming response and accumulate:
    - full text content
    - tool_calls list (assembled from delta chunks)
    - token usage (from final chunk)
    Returns (full_content, tool_calls, usage)
    """
    full_content = ""
    # tool_calls indexed by tool_call index for proper chunk assembly
    tool_calls_map: dict[int, dict[str, Any]] = {}
    usage: dict[str, int] = {}

    async for chunk in stream:
        choice = chunk.choices[0] if chunk.choices else None
        if not choice:
            continue

        delta = choice.delta

        # Accumulate text
        if delta.content:
            full_content += delta.content

        # Accumulate tool_call deltas
        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index if hasattr(tc_delta, "index") else 0
                if idx not in tool_calls_map:
                    tool_calls_map[idx] = {
                        "id": getattr(tc_delta, "id", "") or "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                tc = tool_calls_map[idx]
                if tc_delta.id:
                    tc["id"] = tc_delta.id
                fn = tc_delta.function
                if fn:
                    if fn.name:
                        tc["function"]["name"] += fn.name
                    if fn.arguments:
                        tc["function"]["arguments"] += fn.arguments

        # Capture final usage if present
        if hasattr(chunk, "usage") and chunk.usage:
            usage = _extract_usage(chunk)

    tool_calls = [tool_calls_map[i] for i in sorted(tool_calls_map.keys())]
    return full_content, tool_calls, usage


class LLMGateway:
    """
    Unified LLM gateway with streaming, tool calling, and provider fallback.

    Usage:
        gateway = LLMGateway(settings)
        async for event_bytes in gateway.stream(messages, tools, ctx, system_prompt, abort):
            yield event_bytes
    """

    def __init__(self) -> None:
        if not _LITELLM_AVAILABLE:
            raise RuntimeError(
                "LiteLLM is not installed. Run: pip install 'litellm>=1.50.0'"
            )
        _configure_litellm_keys()
        # Suppress LiteLLM verbose logging in production
        litellm.set_verbose = False  # type: ignore[attr-defined]
        # State surfaced to caller after stream() completes (read in router's finally block).
        # These capture the FINAL state of the successful provider attempt so the router
        # can persist the full conversation + correct cost + correct provider.
        self._last_messages: list[dict[str, Any]] = []
        self._last_provider: str = ""
        self._last_usage: dict[str, int] = {}
        self._last_cost: float = 0.0

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: dict[str, Any],  # name → ToolSpec
        ctx: ToolContext,
        system_prompt: str,
        abort: asyncio.Event,
    ) -> AsyncGenerator[bytes, None]:
        """
        Async generator that yields SSE event bytes.
        Implements fallback chain: primary → primary_retry → fallback1 → fallback2 → ...
        On provider error, tries next provider after 500ms backoff on first retry.
        """
        primary = settings.nexclaude_primary_model
        fallbacks = settings.nexclaude_fallback_chain
        attempts = _build_attempt_order(primary, fallbacks)
        executor = CheckpointExecutor()
        last_error: Exception | None = None

        # Reset per-run state so a reused gateway doesn't leak previous run's data.
        self._last_messages = []
        self._last_provider = ""
        self._last_usage = {}
        self._last_cost = 0.0

        for attempt_idx, provider_id in enumerate(attempts):
            if abort.is_set():
                yield sse_cancelled(executor.committed_count)
                return

            # Backoff only on first retry (attempt_idx == 1, same provider as 0)
            if attempt_idx == 1:
                await asyncio.sleep(settings.nexclaude_retry_backoff_ms / 1000)

            try:
                async for event_bytes in self._stream_one(
                    provider_id=provider_id,
                    messages=list(messages),  # copy so retries start fresh
                    tools=tools,
                    system_prompt=system_prompt,
                    executor=executor,
                    abort=abort,
                    ctx=ctx,
                ):
                    yield event_bytes
                # On success, _stream_one has already populated self._last_*
                # (messages, provider, usage, cost). Router reads them in finally.
                return  # Success

            except TransientError as exc:
                last_error = exc
                continue
            except asyncio.CancelledError:
                yield sse_cancelled(executor.committed_count)
                return

        # All providers exhausted
        yield sse_error(
            message="All LLM providers failed",
            detail=str(last_error) if last_error else "unknown error",
        )

    async def _stream_one(
        self,
        provider_id: str,
        messages: list[dict[str, Any]],
        tools: dict[str, Any],
        system_prompt: str,
        executor: CheckpointExecutor,
        abort: asyncio.Event,
        ctx: ToolContext,
    ) -> AsyncGenerator[bytes, None]:
        """
        Single-provider streaming loop with tool calling.
        Runs up to nexclaude_max_steps iterations (text + tool calls per step).
        Raises TransientError on provider failure so caller can try next provider.
        """
        from app.services.llm_tools.base import build_openai_tool_schemas

        tool_schemas = build_openai_tool_schemas(list(tools.keys())) if tools else []
        full_messages = [{"role": "system", "content": system_prompt}, *messages]
        accumulated_usage: dict[str, int] = {}

        for step in range(settings.nexclaude_max_steps):
            if abort.is_set():
                yield sse_cancelled(executor.committed_count)
                return

            try:
                # Route gateway attempts via custom OpenAI-compatible endpoint.
                # LiteLLM treats "openai/<model>" + api_base as generic OAI API.
                if provider_id.startswith(GATEWAY_PREFIX):
                    model_id = "openai/" + provider_id[len(GATEWAY_PREFIX):]
                    api_base = settings.ai_gateway_url.rstrip("/")
                    api_key = settings.ai_gateway_api_key or "sk-local"
                else:
                    model_id = provider_id
                    api_base = None
                    api_key = None

                kwargs: dict[str, Any] = {
                    "model": model_id,
                    "messages": full_messages,
                    "stream": True,
                    "timeout": settings.nexclaude_timeout_seconds,
                }
                if api_base:
                    kwargs["api_base"] = api_base
                    kwargs["api_key"] = api_key
                if tool_schemas:
                    kwargs["tools"] = tool_schemas
                    kwargs["tool_choice"] = "auto"

                response = await litellm.acompletion(**kwargs)  # type: ignore[attr-defined]

            except Exception as exc:
                raise TransientError(f"Provider {provider_id} failed: {exc}") from exc

            try:
                full_content, tool_calls, usage = await _accumulate_stream(response)
            except Exception as exc:
                raise TransientError(f"Stream read failed for {provider_id}: {exc}") from exc

            # Merge usage
            for k, v in usage.items():
                accumulated_usage[k] = accumulated_usage.get(k, 0) + v

            # Emit text content as delta events (whole text at once for non-streaming accumulation)
            if full_content:
                # Split into words for smoother UX when using accumulation mode
                yield sse_text_delta(full_content)

            if not tool_calls:
                # No tool calls — conversation is done. Append final assistant turn
                # so full_messages contains the COMPLETE conversation for persistence.
                if full_content:
                    full_messages.append({
                        "role": "assistant",
                        "content": full_content,
                    })
                cost = estimate_cost(
                    provider_id,
                    accumulated_usage.get("prompt_tokens", 0),
                    accumulated_usage.get("completion_tokens", 0),
                )
                # C-3 + H-1 + H-2 fix: expose final state to router.
                # Strip the system prompt from persisted messages (system is rebuilt per request).
                self._last_messages = [m for m in full_messages if m.get("role") != "system"]
                self._last_provider = provider_id
                self._last_usage = accumulated_usage
                self._last_cost = round(cost, 6)
                yield sse_done(step=step, provider=provider_id, usage={
                    **accumulated_usage, "cost_usd": round(cost, 6)
                })
                return

            # Execute tool calls — append assistant turn with tool_calls to conversation
            full_messages.append({
                "role": "assistant",
                "content": full_content or "",
                "tool_calls": tool_calls,
            })

            for tc in tool_calls:
                tool_name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}

                yield sse_tool_call(name=tool_name, args=args)

                # Look up tool in registry
                tool_spec = tools.get(tool_name)
                if not tool_spec:
                    result = {"status": "error", "error": f"Unknown tool: {tool_name}"}
                else:
                    result = await executor.run(
                        tool_fn=tool_spec.execute_fn,
                        args=args,
                        ctx=ctx,
                        abort=abort,
                        tool_name=tool_name,
                    )

                yield sse_tool_result(name=tool_name, result=result)

                full_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": json.dumps(result),
                })

            # Loop continues — model will respond to tool results

        # Max steps reached without finishing
        cost = estimate_cost(
            provider_id,
            accumulated_usage.get("prompt_tokens", 0),
            accumulated_usage.get("completion_tokens", 0),
        )
        # C-3 + H-1 + H-2 fix: expose final state even on max-steps exit.
        self._last_messages = [m for m in full_messages if m.get("role") != "system"]
        self._last_provider = provider_id
        self._last_usage = accumulated_usage
        self._last_cost = round(cost, 6)
        yield sse_done(
            step=settings.nexclaude_max_steps,
            provider=provider_id,
            usage={**accumulated_usage, "cost_usd": round(cost, 6)},
        )
