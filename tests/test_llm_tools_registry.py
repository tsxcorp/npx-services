"""
Tests for llm_tools/base.py — @tool decorator, registry, schema building.
Updated in Phase 1a: stub_ping removed; tests now reference 'navigate_to' (pure function, no HTTP).
Run: cd nexpo-services && python -m pytest tests/test_llm_tools_registry.py -v
"""
import pytest
from pydantic import BaseModel

from app.services.llm_tools.base import (
    tool,
    ToolSpec,
    _REGISTRY,
    get_tool,
    list_tools,
    build_openai_tool_schemas,
)


class TestToolDecorator:
    """Tests for @tool decorator and registry population."""

    def test_navigate_to_registered(self):
        """navigate_to tool is registered in _REGISTRY after import."""
        # navigate_to is auto-registered by setup_tools.py at import time
        assert "navigate_to" in _REGISTRY
        assert _REGISTRY["navigate_to"] is not None

    def test_get_tool_returns_tool_spec(self):
        """get_tool('navigate_to') returns valid ToolSpec."""
        spec = get_tool("navigate_to")

        assert spec is not None
        assert isinstance(spec, ToolSpec)
        assert spec.name == "navigate_to"
        assert spec.description is not None
        assert spec.execute_fn is not None

    def test_get_tool_nonexistent_returns_none(self):
        """get_tool('nonexistent') returns None."""
        spec = get_tool("nonexistent_tool_xyz")
        assert spec is None

    def test_list_tools_includes_navigate_to(self):
        """list_tools() includes 'navigate_to'."""
        tools = list_tools()
        assert isinstance(tools, list)
        assert "navigate_to" in tools

    def test_ping_not_registered(self):
        """stub_ping is removed in Phase 1a — 'ping' must not be in registry."""
        assert "ping" not in _REGISTRY

    def test_tool_spec_has_input_schema(self):
        """Registered tool has JSON schema for inputs."""
        spec = get_tool("navigate_to")
        assert spec is not None

        schema = spec.input_schema
        assert isinstance(schema, dict)
        assert "properties" in schema

    def test_tool_spec_requires_confirm_default(self):
        """ToolSpec.requires_confirm defaults to False for navigate_to."""
        spec = get_tool("navigate_to")
        assert spec is not None
        assert spec.requires_confirm is False

    def test_create_event_requires_confirm(self):
        """create_event has requires_confirm=True."""
        spec = get_tool("create_event")
        assert spec is not None
        assert spec.requires_confirm is True

    def test_all_phase_1a_tools_registered(self):
        """All 10 Phase 1a tools are registered."""
        expected = {
            "create_event",
            "create_event_commit",
            "create_form",
            "create_form_commit",
            "list_events",
            "navigate_to",
            "generate_banner",
            "save_image_to_event",
            "save_image_to_event_commit",
            "extract_brand_from_logo",
        }
        registered = set(list_tools())
        assert expected.issubset(registered)


class TestBuildOpenaiToolSchemas:
    """Tests for build_openai_tool_schemas — LiteLLM format."""

    def test_build_single_tool_schema(self):
        """build_openai_tool_schemas(['navigate_to']) returns OpenAI format."""
        schemas = build_openai_tool_schemas(["navigate_to"])

        assert len(schemas) == 1
        schema = schemas[0]
        assert schema["type"] == "function"
        assert "function" in schema

        fn = schema["function"]
        assert fn["name"] == "navigate_to"
        assert "description" in fn
        assert "parameters" in fn

    def test_build_all_tools_when_none(self):
        """build_openai_tool_schemas(None) returns ALL registered tools."""
        schemas = build_openai_tool_schemas(None)

        assert len(schemas) > 0
        assert any(s["function"]["name"] == "navigate_to" for s in schemas)

    def test_build_nonexistent_tool_skipped(self):
        """build_openai_tool_schemas(['nonexistent']) gracefully returns empty."""
        schemas = build_openai_tool_schemas(["nonexistent_xyz_tool"])
        assert schemas == []

    def test_build_mixed_existing_nonexistent(self):
        """Tool list with mix of existing + nonexistent → only existing included."""
        schemas = build_openai_tool_schemas(["navigate_to", "nonexistent"])

        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "navigate_to"

    def test_schema_parameters_valid_json_schema(self):
        """Returned schema has valid JSON Schema format."""
        schemas = build_openai_tool_schemas(["navigate_to"])

        fn = schemas[0]["function"]
        params = fn["parameters"]

        assert isinstance(params, dict)
        assert "properties" in params or "required" in params or len(params) > 0

    def test_navigate_to_input_schema_has_route(self):
        """navigate_to tool schema includes 'route' parameter."""
        schemas = build_openai_tool_schemas(["navigate_to"])

        params = schemas[0]["function"]["parameters"]
        assert "properties" in params
        assert "route" in params["properties"]


class TestNavigateToToolExecution:
    """Tests for navigate_to tool execution — pure function, no HTTP."""

    @pytest.mark.asyncio
    async def test_navigate_to_returns_link(self):
        """navigate_to.execute() returns {action: link, href, label}."""
        spec = get_tool("navigate_to")
        assert spec is not None

        class MockCtx:
            tenant_id = "test-tenant"
            user_id = "test-user"

        ctx = MockCtx()
        args = {"route": "/events", "reason": "View all events"}

        result = await spec.execute_fn(args, ctx)

        assert isinstance(result, dict)
        assert result["action"] == "link"
        assert result["href"] == "/events"
        assert result["label"] == "View all events"

    @pytest.mark.asyncio
    async def test_navigate_to_with_special_chars_in_reason(self):
        """navigate_to echoes Unicode reason correctly."""
        spec = get_tool("navigate_to")
        assert spec is not None

        class MockCtx:
            tenant_id = "tenant"
            user_id = "user"

        ctx = MockCtx()
        args = {"route": "/events/123", "reason": "Xem chi tiết sự kiện 🎪"}

        result = await spec.execute_fn(args, ctx)

        assert result["label"] == "Xem chi tiết sự kiện 🎪"
        assert result["href"] == "/events/123"


class TestCustomToolRegistration:
    """Tests for registering custom tools with @tool decorator."""

    def test_custom_tool_registration(self):
        """Decorator registers custom tool in _REGISTRY."""
        @tool(description="Test custom tool")
        class TestTool:
            name = "test_custom"

            class Input(BaseModel):
                value: str

            @staticmethod
            async def execute(args, ctx):
                return {"result": args["value"]}

        assert "test_custom" in _REGISTRY
        spec = _REGISTRY["test_custom"]
        assert spec.name == "test_custom"
        assert spec.description == "Test custom tool"

    def test_custom_tool_requires_confirm(self):
        """Decorator respects requires_confirm flag."""
        @tool(description="Confirm tool", requires_confirm=True)
        class ConfirmTool:
            name = "confirm_test"

            class Input(BaseModel):
                x: str

            @staticmethod
            async def execute(args, ctx):
                return {}

        spec = _REGISTRY["confirm_test"]
        assert spec.requires_confirm is True

    def test_custom_tool_schema_from_pydantic(self):
        """Custom tool Input model → JSON Schema."""
        @tool(description="Schema test")
        class SchemaTool:
            name = "schema_test"

            class Input(BaseModel):
                field1: str
                field2: int

            @staticmethod
            async def execute(args, ctx):
                return {}

        spec = _REGISTRY["schema_test"]
        schema = spec.input_schema

        assert "properties" in schema
        assert "field1" in schema["properties"]
        assert "field2" in schema["properties"]

    @pytest.mark.asyncio
    async def test_custom_tool_execution(self):
        """Custom tool execute_fn can be called."""
        @tool(description="Exec test")
        class ExecTool:
            name = "exec_test"

            class Input(BaseModel):
                x: int

            @staticmethod
            async def execute(args, ctx):
                return {"doubled": args["x"] * 2}

        spec = _REGISTRY["exec_test"]

        class MockCtx:
            pass

        result = await spec.execute_fn({"x": 5}, MockCtx())
        assert result["doubled"] == 10
