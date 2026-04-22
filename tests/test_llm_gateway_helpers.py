"""
Tests for llm_gateway.py helper functions — pure functions without LiteLLM calls.
Run: cd nexpo-services && python -m pytest tests/test_llm_gateway_helpers.py -v
"""
import pytest

from app.services.llm_gateway import (
    _build_attempt_order,
    _extract_usage,
    _accumulate_stream,
)


class TestBuildAttemptOrder:
    """Tests for _build_attempt_order — retry policy."""

    def test_primary_twice_then_fallbacks(self):
        """Primary is tried twice before falling back."""
        primary = "claude"
        fallbacks = ["gemini", "gpt4"]

        result = _build_attempt_order(primary, fallbacks)

        assert result == ["claude", "claude", "gemini", "gpt4"]

    def test_empty_fallbacks(self):
        """Works with no fallbacks — just primary twice."""
        primary = "claude"
        fallbacks = []

        result = _build_attempt_order(primary, fallbacks)

        assert result == ["claude", "claude"]

    def test_single_fallback(self):
        """Primary twice, then single fallback."""
        primary = "primary"
        fallbacks = ["backup"]

        result = _build_attempt_order(primary, fallbacks)

        assert result == ["primary", "primary", "backup"]

    def test_many_fallbacks(self):
        """Primary twice, then all fallbacks in order."""
        primary = "p1"
        fallbacks = ["p2", "p3", "p4", "p5"]

        result = _build_attempt_order(primary, fallbacks)

        assert result == ["p1", "p1", "p2", "p3", "p4", "p5"]


class TestExtractUsage:
    """Tests for _extract_usage — handle missing/None attributes."""

    def test_extract_from_complete_response(self):
        """Response with all usage fields → exact extraction."""
        class MockUsage:
            prompt_tokens = 100
            completion_tokens = 50
            total_tokens = 150

        class MockResponse:
            usage = MockUsage()

        result = _extract_usage(MockResponse())

        assert result == {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }

    def test_missing_usage_attribute(self):
        """Response missing .usage field → zeros."""
        class MockResponse:
            pass

        result = _extract_usage(MockResponse())

        assert result == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def test_none_usage_field(self):
        """Response has usage=None → zeros."""
        class MockResponse:
            usage = None

        result = _extract_usage(MockResponse())

        assert result == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def test_partial_usage_fields(self):
        """Usage missing some fields → treats as 0."""
        class MockUsage:
            prompt_tokens = 75
            # completion_tokens missing
            total_tokens = None

        class MockResponse:
            usage = MockUsage()

        result = _extract_usage(MockResponse())

        assert result == {
            "prompt_tokens": 75,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def test_usage_fields_are_zero(self):
        """Explicitly zero values → preserved."""
        class MockUsage:
            prompt_tokens = 0
            completion_tokens = 0
            total_tokens = 0

        class MockResponse:
            usage = MockUsage()

        result = _extract_usage(MockResponse())

        assert result == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }


class TestAccumulateStream:
    """Tests for _accumulate_stream — text + tool_calls assembly."""

    @pytest.mark.asyncio
    async def test_text_only_no_tool_calls(self):
        """Stream with only text deltas → full_content assembled."""
        # Mock async iterator
        async def mock_stream():
            class MockDelta:
                content = "Hello "
                tool_calls = None

            class MockChoice:
                delta = MockDelta()

            class MockChunk:
                choices = [MockChoice()]
                usage = None

            yield MockChunk()

            MockDelta.content = "world"
            yield MockChunk()

            MockDelta.content = "!"
            yield MockChunk()

        full_content, tool_calls, usage = await _accumulate_stream(mock_stream())

        assert full_content == "Hello world!"
        assert tool_calls == []
        assert usage == {}

    @pytest.mark.asyncio
    async def test_tool_calls_assembly(self):
        """Stream with tool_call deltas → assembled correctly."""
        async def mock_stream():
            # First chunk — tool call starts
            class ToolCallDelta1:
                index = 0
                id = "call_1"

                class Function:
                    name = "ping"
                    arguments = ""

                function = Function()

            class MockDelta1:
                content = None
                tool_calls = [ToolCallDelta1()]

            class MockChoice1:
                delta = MockDelta1()

            class MockChunk1:
                choices = [MockChoice1()]
                usage = None

            yield MockChunk1()

            # Second chunk — arguments accumulate
            class ToolCallDelta2:
                index = 0
                id = None

                class Function:
                    name = None
                    arguments = '{"message'

                function = Function()

            class MockDelta2:
                content = None
                tool_calls = [ToolCallDelta2()]

            class MockChoice2:
                delta = MockDelta2()

            class MockChunk2:
                choices = [MockChoice2()]
                usage = None

            yield MockChunk2()

            # Third chunk — arguments complete
            class ToolCallDelta3:
                index = 0
                id = None

                class Function:
                    name = None
                    arguments = '": "hello"}'

                function = Function()

            class MockDelta3:
                content = None
                tool_calls = [ToolCallDelta3()]

            class MockChoice3:
                delta = MockDelta3()

            class MockChunk3:
                choices = [MockChoice3()]
                usage = None

            yield MockChunk3()

        full_content, tool_calls, usage = await _accumulate_stream(mock_stream())

        assert full_content == ""
        assert len(tool_calls) == 1
        assert tool_calls[0]["id"] == "call_1"
        assert tool_calls[0]["function"]["name"] == "ping"
        assert tool_calls[0]["function"]["arguments"] == '{"message": "hello"}'

    @pytest.mark.asyncio
    async def test_final_usage_extracted(self):
        """Final chunk with usage → captured in result."""
        async def mock_stream():
            class MockDelta:
                content = "text"
                tool_calls = None

            class MockChoice:
                delta = MockDelta()

            class MockUsage:
                prompt_tokens = 50
                completion_tokens = 100
                total_tokens = 150

            class MockChunk:
                choices = [MockChoice()]
                usage = MockUsage()

            yield MockChunk()

        full_content, tool_calls, usage = await _accumulate_stream(mock_stream())

        assert usage["prompt_tokens"] == 50
        assert usage["completion_tokens"] == 100
        assert usage["total_tokens"] == 150

    @pytest.mark.asyncio
    async def test_multiple_tool_calls(self):
        """Stream with multiple tool_calls → all assembled."""
        async def mock_stream():
            # First tool call
            class ToolCallDelta1:
                index = 0
                id = "call_1"

                class Function:
                    name = "ping"
                    arguments = "{}"

                function = Function()

            class MockDelta1:
                content = None
                tool_calls = [ToolCallDelta1()]

            class MockChoice1:
                delta = MockDelta1()

            class MockChunk1:
                choices = [MockChoice1()]
                usage = None

            yield MockChunk1()

            # Second tool call (different index)
            class ToolCallDelta2:
                index = 1
                id = "call_2"

                class Function:
                    name = "search"
                    arguments = '{"q":"test"}'

                function = Function()

            class MockDelta2:
                content = None
                tool_calls = [ToolCallDelta2()]

            class MockChoice2:
                delta = MockDelta2()

            class MockChunk2:
                choices = [MockChoice2()]
                usage = None

            yield MockChunk2()

        full_content, tool_calls, usage = await _accumulate_stream(mock_stream())

        assert len(tool_calls) == 2
        assert tool_calls[0]["function"]["name"] == "ping"
        assert tool_calls[1]["function"]["name"] == "search"

    @pytest.mark.asyncio
    async def test_mixed_text_and_tool_calls(self):
        """Stream with both text and tool_calls → both accumulated."""
        async def mock_stream():
            class ToolCallDelta:
                index = 0
                id = "call_1"

                class Function:
                    name = "tool"
                    arguments = "{}"

                function = Function()

            # Text chunk
            class MockDelta1:
                content = "Hello"
                tool_calls = None

            class MockChoice1:
                delta = MockDelta1()

            class MockChunk1:
                choices = [MockChoice1()]
                usage = None

            yield MockChunk1()

            # Tool chunk
            class MockDelta2:
                content = None
                tool_calls = [ToolCallDelta()]

            class MockChoice2:
                delta = MockDelta2()

            class MockChunk2:
                choices = [MockChoice2()]
                usage = None

            yield MockChunk2()

        full_content, tool_calls, usage = await _accumulate_stream(mock_stream())

        assert full_content == "Hello"
        assert len(tool_calls) == 1
