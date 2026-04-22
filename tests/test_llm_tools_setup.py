"""
Tests for llm_tools/setup_tools.py — CreateEvent, CreateForm, ListEvents, NavigateTo.
All HTTP calls mocked via monkeypatch; no real network traffic.
Run: cd nexpo-services && python -m pytest tests/test_llm_tools_setup.py -v
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.llm_tools.base import get_tool
from app.services.llm_tools.tool_signing import SignedPayload, sign_payload


# ── Fixtures ──────────────────────────────────────────────────────────────────

class MockCtx:
    """Minimal ToolContext mock — only fields used by setup_tools."""
    tenant_id = "tenant-123"
    user_id = "user-456"
    user_token = "Bearer test-jwt"


@pytest.fixture
def ctx() -> MockCtx:
    return MockCtx()


def _make_httpx_response(status_code: int, body: dict[str, Any]) -> MagicMock:
    """Build a MagicMock that mimics httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


# ── CreateEventTool ───────────────────────────────────────────────────────────

class TestCreateEventTool:
    @pytest.mark.asyncio
    async def test_returns_preview_with_signed_commit(self, ctx: MockCtx) -> None:
        """create_event returns action=preview with a signed_commit dict."""
        spec = get_tool("create_event")
        assert spec is not None

        args = {
            "name": "Tech Fair 2026",
            "type": "offline",
            "start_date": "2026-05-15",
            "end_date": "2026-05-17",
            "location": "Hà Nội",
        }
        result = await spec.execute_fn(args, ctx)

        assert result["action"] == "preview"
        assert result["kind"] == "event"
        assert "signed_commit" in result
        assert result["commit_tool"] == "create_event_commit"
        assert "confirm_label" in result

        signed_commit = result["signed_commit"]
        assert "payload" in signed_commit
        assert "signature" in signed_commit
        assert "expires_at" in signed_commit
        assert signed_commit["payload"]["tenant_id"] == "tenant-123"

    @pytest.mark.asyncio
    async def test_preview_contains_event_fields(self, ctx: MockCtx) -> None:
        """Preview dict contains submitted event fields."""
        spec = get_tool("create_event")
        assert spec is not None

        args = {
            "name": "Career Fair",
            "type": "hybrid",
            "start_date": "2026-06-01",
            "end_date": "2026-06-02",
            "category": "education",
        }
        result = await spec.execute_fn(args, ctx)

        preview = result["preview"]
        assert preview["name"] == "Career Fair"
        assert preview["type"] == "hybrid"
        assert preview["category"] == "education"


class TestCreateEventCommitTool:
    @pytest.mark.asyncio
    async def test_valid_signature_calls_directus_and_returns_committed(
        self, ctx: MockCtx
    ) -> None:
        """Valid signed_commit → POST Directus → returns committed with entity_id."""
        spec = get_tool("create_event_commit")
        assert spec is not None

        # Build a valid signed payload
        payload = {
            "name": "Tech Fair 2026",
            "type": "offline",
            "start_date": "2026-05-15",
            "end_date": "2026-05-17",
            "tenant_id": ctx.tenant_id,
            "user_id": ctx.user_id,
        }
        signed = sign_payload(payload)

        directus_response = _make_httpx_response(200, {"data": {"id": "evt-123"}})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=directus_response)

        with patch("app.services.llm_tools.setup_tools.httpx.AsyncClient", return_value=mock_client):
            result = await spec.execute_fn({"signed_commit": signed.model_dump()}, ctx)

        assert result["action"] == "committed"
        assert result["kind"] == "event"
        assert result["entity_id"] == "evt-123"
        assert result["link"] == "/events/evt-123"

    @pytest.mark.asyncio
    async def test_expired_signature_returns_error(self, ctx: MockCtx) -> None:
        """Expired signed_commit returns action=error kind=invalid_signature."""
        spec = get_tool("create_event_commit")
        assert spec is not None

        payload = {"name": "Old Event", "tenant_id": ctx.tenant_id, "user_id": ctx.user_id}
        signed = sign_payload(payload, ttl_seconds=-1)  # already expired

        result = await spec.execute_fn({"signed_commit": signed.model_dump()}, ctx)

        assert result["action"] == "error"
        assert result["kind"] == "invalid_signature"

    @pytest.mark.asyncio
    async def test_tenant_mismatch_returns_error(self, ctx: MockCtx) -> None:
        """signed_commit with wrong tenant_id returns action=error kind=tenant_mismatch."""
        spec = get_tool("create_event_commit")
        assert spec is not None

        # Sign with a different tenant
        payload = {"name": "Hijack", "tenant_id": "evil-tenant", "user_id": "evil-user"}
        signed = sign_payload(payload)

        result = await spec.execute_fn({"signed_commit": signed.model_dump()}, ctx)

        assert result["action"] == "error"
        assert result["kind"] == "tenant_mismatch"

    @pytest.mark.asyncio
    async def test_malformed_signed_commit_returns_error(self, ctx: MockCtx) -> None:
        """Completely malformed signed_commit returns invalid_payload error."""
        spec = get_tool("create_event_commit")
        assert spec is not None

        result = await spec.execute_fn({"signed_commit": {"garbage": True}}, ctx)

        assert result["action"] == "error"
        assert result["kind"] in ("invalid_payload", "invalid_signature")

    @pytest.mark.asyncio
    async def test_directus_http_error_returns_error(self, ctx: MockCtx) -> None:
        """Directus 403 → structured error, no exception propagated."""
        spec = get_tool("create_event_commit")
        assert spec is not None

        payload = {"name": "Fail Event", "tenant_id": ctx.tenant_id, "user_id": ctx.user_id}
        signed = sign_payload(payload)

        directus_response = _make_httpx_response(403, {"errors": [{"message": "Forbidden"}]})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=directus_response)

        with patch("app.services.llm_tools.setup_tools.httpx.AsyncClient", return_value=mock_client):
            result = await spec.execute_fn({"signed_commit": signed.model_dump()}, ctx)

        assert result["action"] == "error"
        assert result["kind"] == "directus_error"


# ── ListEventsTool ────────────────────────────────────────────────────────────

class TestListEventsTool:
    @pytest.mark.asyncio
    async def test_returns_data_table_shape(self, ctx: MockCtx) -> None:
        """list_events returns action=display kind=data_table with columns and rows."""
        spec = get_tool("list_events")
        assert spec is not None

        events = [
            {"id": "e1", "name": "Event A", "status": "published", "date_start": "2026-05-01",
             "date_end": "2026-05-02", "type": "offline", "location": "HCM", "category": "tech"},
        ]
        directus_response = _make_httpx_response(200, {"data": events})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=directus_response)

        with patch("app.services.llm_tools.setup_tools.httpx.AsyncClient", return_value=mock_client):
            result = await spec.execute_fn({"limit": 10}, ctx)

        assert result["action"] == "display"
        assert result["kind"] == "data_table"
        assert isinstance(result["columns"], list)
        assert isinstance(result["rows"], list)
        assert len(result["rows"]) == 1
        assert result["rows"][0]["name"] == "Event A"

    @pytest.mark.asyncio
    async def test_builds_status_filter_param(self, ctx: MockCtx) -> None:
        """list_events with status='draft' sends filter[status][_eq]=draft to Directus."""
        spec = get_tool("list_events")
        assert spec is not None

        directus_response = _make_httpx_response(200, {"data": []})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=directus_response)

        with patch("app.services.llm_tools.setup_tools.httpx.AsyncClient", return_value=mock_client):
            await spec.execute_fn({"status": "draft", "limit": 5}, ctx)

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params", {})
        assert params.get("filter[status][_eq]") == "draft"
        assert params.get("limit") == "5"

    @pytest.mark.asyncio
    async def test_builds_upcoming_filter_param(self, ctx: MockCtx) -> None:
        """list_events with upcoming=True adds filter[date_start][_gte] param."""
        spec = get_tool("list_events")
        assert spec is not None

        directus_response = _make_httpx_response(200, {"data": []})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=directus_response)

        with patch("app.services.llm_tools.setup_tools.httpx.AsyncClient", return_value=mock_client):
            await spec.execute_fn({"upcoming": True, "limit": 10}, ctx)

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params", {})
        assert "filter[date_start][_gte]" in params

    @pytest.mark.asyncio
    async def test_uses_user_token_in_authorization_header(self, ctx: MockCtx) -> None:
        """list_events passes Authorization header with user_token."""
        spec = get_tool("list_events")
        assert spec is not None

        directus_response = _make_httpx_response(200, {"data": []})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=directus_response)

        with patch("app.services.llm_tools.setup_tools.httpx.AsyncClient", return_value=mock_client):
            await spec.execute_fn({"limit": 10}, ctx)

        call_kwargs = mock_client.get.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert "Authorization" in headers
        assert ctx.user_token in headers["Authorization"]

    @pytest.mark.asyncio
    async def test_directus_error_returns_error_dict(self, ctx: MockCtx) -> None:
        """Directus 500 → structured error dict, not exception."""
        spec = get_tool("list_events")
        assert spec is not None

        directus_response = _make_httpx_response(500, {"errors": []})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=directus_response)

        with patch("app.services.llm_tools.setup_tools.httpx.AsyncClient", return_value=mock_client):
            result = await spec.execute_fn({"limit": 10}, ctx)

        assert result["action"] == "error"
        assert result["kind"] == "directus_error"


# ── NavigateToTool ────────────────────────────────────────────────────────────

class TestNavigateToTool:
    @pytest.mark.asyncio
    async def test_returns_link_with_reason(self, ctx: MockCtx) -> None:
        """navigate_to returns action=link with href and label from reason."""
        spec = get_tool("navigate_to")
        assert spec is not None

        result = await spec.execute_fn({"route": "/events", "reason": "Xem danh sách sự kiện"}, ctx)

        assert result["action"] == "link"
        assert result["href"] == "/events"
        assert result["label"] == "Xem danh sách sự kiện"

    @pytest.mark.asyncio
    async def test_label_defaults_to_route_when_no_reason(self, ctx: MockCtx) -> None:
        """navigate_to with no reason uses route as label."""
        spec = get_tool("navigate_to")
        assert spec is not None

        result = await spec.execute_fn({"route": "/settings/brand-kits"}, ctx)

        assert result["action"] == "link"
        assert result["label"] == "/settings/brand-kits"

    @pytest.mark.asyncio
    async def test_rejects_route_without_leading_slash(self, ctx: MockCtx) -> None:
        """navigate_to rejects route that does not start with /."""
        spec = get_tool("navigate_to")
        assert spec is not None

        result = await spec.execute_fn({"route": "events"}, ctx)

        assert result["action"] == "error"
        assert result["kind"] == "invalid_route"

    @pytest.mark.asyncio
    async def test_rejects_path_traversal_route(self, ctx: MockCtx) -> None:
        """navigate_to rejects path traversal attempts."""
        spec = get_tool("navigate_to")
        assert spec is not None

        result = await spec.execute_fn({"route": "/../etc/passwd"}, ctx)

        assert result["action"] == "error"
        assert result["kind"] == "invalid_route"

    @pytest.mark.asyncio
    async def test_no_http_calls_made(self, ctx: MockCtx) -> None:
        """navigate_to is a pure function — no httpx calls should occur."""
        spec = get_tool("navigate_to")
        assert spec is not None

        with patch("httpx.AsyncClient") as mock_http:
            result = await spec.execute_fn({"route": "/events/123"}, ctx)

        # AsyncClient should never be instantiated for navigate_to
        mock_http.assert_not_called()
        assert result["action"] == "link"
