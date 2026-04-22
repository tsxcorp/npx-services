"""
SSE event formatter for NexClaude streaming responses.
All events follow the format consumed by the nexpo-admin SSE proxy (Phase 0c).

Event types:
  text_delta   — incremental text content chunk
  tool_call    — tool invocation with name + args
  tool_result  — tool execution result
  checkpoint   — committed checkpoint summary
  cancelled    — stream aborted mid-way, N tools committed
  error        — unrecoverable error
  done         — stream complete, includes usage + provider info
"""
from __future__ import annotations

import json
from typing import Any


def sse(event: str, data: dict[str, Any]) -> bytes:
    """
    Format a single SSE event as bytes.
    Format: `event: <name>\\ndata: <json>\\n\\n`
    """
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def sse_text_delta(text: str) -> bytes:
    return sse("text_delta", {"text": text})


def sse_tool_call(name: str, args: dict[str, Any]) -> bytes:
    return sse("tool_call", {"name": name, "args": args})


def sse_tool_result(name: str, result: dict[str, Any]) -> bytes:
    return sse("tool_result", {"name": name, "result": result})


def sse_checkpoint(committed: int, tools: list[str]) -> bytes:
    return sse("checkpoint", {"committed": committed, "tools": tools})


def sse_cancelled(committed: int) -> bytes:
    return sse("cancelled", {"committed": committed})


def sse_error(message: str, detail: str = "") -> bytes:
    return sse("error", {"message": message, "detail": detail})


def sse_done(step: int, provider: str, usage: dict[str, Any] | None = None) -> bytes:
    payload: dict[str, Any] = {"step": step, "provider": provider}
    if usage:
        payload["usage"] = usage
    return sse("done", payload)
