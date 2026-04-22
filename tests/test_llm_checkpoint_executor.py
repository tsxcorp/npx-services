"""
Tests for llm_checkpoint_executor.py — checkpoint recording, abort handling.
Run: cd nexpo-services && python -m pytest tests/test_llm_checkpoint_executor.py -v
"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services.llm_checkpoint_executor import CheckpointExecutor, Checkpoint
from app.services.llm_context import ToolContext


# Fixture: minimal ToolContext for testing
@pytest.fixture
def mock_ctx():
    return ToolContext(
        user_token="test-jwt",
        user_id="user-123",
        user_name="Test User",
        user_email="test@example.com",
        tenant_id="tenant-456",
        tenant_name="Test Org",
        tenant_tier="pro",
        features=["nexclaude"],
        locale="en",
        current_route="/events",
    )


@pytest.mark.asyncio
async def test_happy_path_tool_succeeds(mock_ctx):
    """Tool executes successfully → status='committed', result in response."""
    executor = CheckpointExecutor()
    abort = asyncio.Event()

    async def dummy_tool(args, ctx):
        return {"status": "ok", "message": "success"}

    # Set tool name for checkpoint tracking
    dummy_tool.__name__ = "dummy"

    result = await executor.run(dummy_tool, {"input": "test"}, mock_ctx, abort)

    assert result["status"] == "committed"
    assert result["result"]["message"] == "success"
    assert executor.committed_count == 1
    assert len(executor.checkpoints) == 1


@pytest.mark.asyncio
async def test_abort_before_start(mock_ctx):
    """Abort event set before execution → status='cancelled_before_start'."""
    executor = CheckpointExecutor()
    abort = asyncio.Event()
    abort.set()  # Pre-set abort

    async def dummy_tool(args, ctx):
        return {"status": "ok"}

    result = await executor.run(dummy_tool, {"input": "test"}, mock_ctx, abort)

    assert result["status"] == "cancelled_before_start"
    assert executor.committed_count == 0
    assert len(executor.checkpoints) == 0


@pytest.mark.asyncio
async def test_tool_raises_exception(mock_ctx):
    """Tool raises exception → status='error', error message captured."""
    executor = CheckpointExecutor()
    abort = asyncio.Event()

    async def failing_tool(args, ctx):
        raise ValueError("Tool execution failed")

    failing_tool.__name__ = "failing"

    result = await executor.run(failing_tool, {"input": "test"}, mock_ctx, abort)

    assert result["status"] == "error"
    assert "Tool execution failed" in result["error"]
    assert executor.committed_count == 0
    assert len(executor.checkpoints) == 0


@pytest.mark.asyncio
async def test_multi_tool_sequence_with_abort(mock_ctx):
    """Execute 3 tools, abort after 2nd → 2 committed, 3rd cancelled."""
    executor = CheckpointExecutor()
    abort = asyncio.Event()
    call_count = 0

    async def sequential_tool(args, ctx):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            abort.set()  # Abort after 2nd tool
        return {"result": f"tool-{call_count}"}

    sequential_tool.__name__ = "seq_tool"

    # Execute first tool
    r1 = await executor.run(sequential_tool, {"n": 1}, mock_ctx, abort)
    assert r1["status"] == "committed"
    assert executor.committed_count == 1

    # Execute second tool
    r2 = await executor.run(sequential_tool, {"n": 2}, mock_ctx, abort)
    assert r2["status"] == "committed"
    assert executor.committed_count == 2

    # Third tool sees abort set
    r3 = await executor.run(sequential_tool, {"n": 3}, mock_ctx, abort)
    assert r3["status"] == "cancelled_before_start"
    assert executor.committed_count == 2


@pytest.mark.asyncio
async def test_checkpoint_contains_metadata(mock_ctx):
    """Committed checkpoint captures tool_name, input, output, timestamp."""
    executor = CheckpointExecutor()
    abort = asyncio.Event()

    async def named_tool(args, ctx):
        return {"output": "result"}

    named_tool.__name__ = "my_tool"

    result = await executor.run(named_tool, {"arg1": "val1"}, mock_ctx, abort)

    assert result["status"] == "committed"
    assert executor.committed_count == 1

    cp = executor.checkpoints[0]
    assert isinstance(cp, Checkpoint)
    assert cp.tool_name == "my_tool"
    assert cp.input == {"arg1": "val1"}
    assert cp.output == {"output": "result"}
    assert cp.committed_at > 0


@pytest.mark.asyncio
async def test_summary_returns_committed_info(mock_ctx):
    """summary() returns committed count + tool names."""
    executor = CheckpointExecutor()
    abort = asyncio.Event()

    async def tool_a(args, ctx):
        return {"ok": True}

    async def tool_b(args, ctx):
        return {"ok": True}

    tool_a.__name__ = "tool_a"
    tool_b.__name__ = "tool_b"

    await executor.run(tool_a, {}, mock_ctx, abort)
    await executor.run(tool_b, {}, mock_ctx, abort)

    summary = executor.summary()
    assert summary["committed"] == 2
    assert summary["tools"] == ["tool_a", "tool_b"]


@pytest.mark.asyncio
async def test_tool_with_json_result(mock_ctx):
    """Tool returns complex nested JSON → captured correctly."""
    executor = CheckpointExecutor()
    abort = asyncio.Event()

    async def json_tool(args, ctx):
        return {
            "status": "ok",
            "data": {
                "user": "alice",
                "scores": [10, 20, 30],
                "metadata": {"key": "value"},
            },
        }

    json_tool.__name__ = "json_tool"

    result = await executor.run(json_tool, {}, mock_ctx, abort)

    assert result["status"] == "committed"
    assert result["result"]["data"]["user"] == "alice"
    assert result["result"]["data"]["scores"] == [10, 20, 30]
