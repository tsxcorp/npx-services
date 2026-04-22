"""
Tool registry barrel — imports all tool modules to trigger @tool registration.
Phase 1a+ appends import lines here; existing lines are never modified.

Import order matters: base must be first, then real tools.
"""
from app.services.llm_tools.base import (  # noqa: F401
    _REGISTRY,
    tool,
    get_tool,
    list_tools,
    build_openai_tool_schemas,
)

# ── Phase 0e: stub removed in Phase 1a (real tools registered below) ─────────

# ── Phase 1a: real tools ──────────────────────────────────────────────────────
from app.services.llm_tools import setup_tools as setup_tools  # noqa: F401
from app.services.llm_tools import visual_tools as visual_tools  # noqa: F401

# ── Phase 2b: read tools (append here) ───────────────────────────────────────
# from app.services.llm_tools import read_tools as read_tools  # noqa: F401

# ── Phase 3a: action tools (append here) ─────────────────────────────────────
# from app.services.llm_tools import action_tools as action_tools  # noqa: F401

# ── Phase 3b: analytics tools (append here) ──────────────────────────────────
# from app.services.llm_tools import analytics_tools as analytics_tools  # noqa: F401
