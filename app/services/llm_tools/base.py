"""
Tool registry base — @tool decorator, Pydantic input model pattern, schema builder.
Each tool module registers itself here via @tool(description=...).
Phase 1a+ appends new tool files; this file is not modified after Phase 0e.
"""
from __future__ import annotations

from typing import Any, Callable, Coroutine, TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.services.llm_context import ToolContext


class ToolSpec(BaseModel):
    """Descriptor for a registered tool."""
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema for the input model
    requires_confirm: bool = False

    # Not stored in Pydantic model — injected at registration time
    execute_fn: Any = None  # Callable[[dict, ToolContext], Coroutine]

    model_config = {"arbitrary_types_allowed": True}


# Global registry — populated by @tool decorator at module import time
_REGISTRY: dict[str, ToolSpec] = {}


def tool(description: str, requires_confirm: bool = False) -> Callable:
    """
    Class decorator that registers a tool in _REGISTRY.

    Expected class shape:
        class MyTool:
            name: str = "my_tool"
            class Input(BaseModel): ...
            @staticmethod
            async def execute(args: dict, ctx: ToolContext) -> dict: ...

    The Input class is converted to OpenAI-compatible JSON Schema.
    """
    def decorator(cls: type) -> type:
        tool_name: str = cls.name  # type: ignore[attr-defined]
        input_model: type[BaseModel] = cls.Input  # type: ignore[attr-defined]
        execute_fn: Callable = cls.execute  # type: ignore[attr-defined]

        # Build JSON Schema from Pydantic model
        schema = input_model.model_json_schema()
        # Remove Pydantic title from top level (cleaner for LLM)
        schema.pop("title", None)

        _REGISTRY[tool_name] = ToolSpec(
            name=tool_name,
            description=description,
            input_schema=schema,
            requires_confirm=requires_confirm,
            execute_fn=execute_fn,
        )
        return cls

    return decorator


def get_tool(name: str) -> ToolSpec | None:
    """Look up a registered tool by name."""
    return _REGISTRY.get(name)


def list_tools() -> list[str]:
    """Return names of all registered tools."""
    return list(_REGISTRY.keys())


def build_openai_tool_schemas(tool_names: list[str] | None = None) -> list[dict[str, Any]]:
    """
    Convert registered tools to OpenAI function-calling schema format.
    If tool_names is None, includes ALL registered tools.
    LiteLLM accepts this format for `tools=` parameter.
    """
    names = tool_names if tool_names is not None else list(_REGISTRY.keys())
    schemas = []
    for name in names:
        spec = _REGISTRY.get(name)
        if not spec:
            continue
        schemas.append({
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.input_schema,
            },
        })
    return schemas
