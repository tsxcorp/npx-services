"""
Checkpoint executor — commit-per-tool pattern for NexClaude tool calls.
Each tool execution is recorded as a committed checkpoint.
If the request is aborted (disconnect), already-committed work is preserved
and the SSE `cancelled` event reports how many tools completed.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from app.services.llm_context import ToolContext


@dataclass
class Checkpoint:
    """Record of a single successfully executed tool call."""
    tool_name: str
    input: dict[str, Any]
    output: dict[str, Any]
    committed_at: float  # Unix timestamp


@dataclass
class CheckpointExecutor:
    """
    Executes tool calls one at a time, recording each as a checkpoint.
    Thread-safe within a single async request — not shared across requests.
    """
    checkpoints: list[Checkpoint] = field(default_factory=list)

    async def run(
        self,
        tool_fn: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
        args: dict[str, Any],
        ctx: ToolContext,
        abort: asyncio.Event,
        tool_name: str | None = None,
    ) -> dict[str, Any]:
        """
        Execute one tool call and record the result as a checkpoint.

        Args:
            tool_fn: async function `(args, ctx) → dict`
            args: tool input (already parsed from LLM JSON)
            ctx: immutable per-request context
            abort: event set when client disconnects
            tool_name: explicit tool identifier for checkpoint metadata.
                       L-5 fix: `tool_fn.__name__` is always "execute" for
                       @staticmethod on decorator-registered tool classes,
                       so the gateway MUST pass this explicitly.

        Returns a result dict with:
          - `status`: "committed" | "cancelled_before_start" | "error"
          - `result`: tool output (only when status == "committed")
          - `error`: error message (only when status == "error")
        """
        if abort.is_set():
            return {"status": "cancelled_before_start"}

        resolved_name = tool_name or getattr(tool_fn, "__name__", "unknown")

        try:
            result = await tool_fn(args, ctx)
            self.checkpoints.append(
                Checkpoint(
                    tool_name=resolved_name,
                    input=args,
                    output=result,
                    committed_at=time.time(),
                )
            )
            return {"status": "committed", "result": result}
        except asyncio.CancelledError:
            # Propagate cancellation — caller handles it
            raise
        except Exception as exc:
            # Tool error — record failure but do NOT add to checkpoints
            return {"status": "error", "error": str(exc)}

    @property
    def committed_count(self) -> int:
        return len(self.checkpoints)

    def summary(self) -> dict[str, Any]:
        """Return a summary suitable for SSE `cancelled` or `done` events."""
        return {
            "committed": self.committed_count,
            "tools": [c.tool_name for c in self.checkpoints],
        }
