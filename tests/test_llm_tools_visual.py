"""
Tests for llm_tools/visual_tools.py — GenerateBanner, SaveImageToEvent, ExtractBrandFromLogo.
Image provider mocked via monkeypatch; no real Gemini/Directus calls.
Run: cd nexpo-services && python -m pytest tests/test_llm_tools_visual.py -v
"""
from __future__ import annotations

import base64
import io
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.image_nano_banana import ImageProviderSafetyError
from app.services.image_types import GeneratedImage
from app.services.llm_tools.base import get_tool
from app.services.llm_tools.tool_signing import sign_payload


# ── Fixtures ──────────────────────────────────────────────────────────────────

class MockCtx:
    """Minimal ToolContext mock for visual tools tests."""
    tenant_id = "tenant-123"
    user_id = "user-456"
    user_token = "Bearer test-jwt"
    brand_kit = None  # most tests don't need brand kit


def _make_fake_image(mood: str = "professional") -> GeneratedImage:
    """Return a minimal GeneratedImage for mocking provider output."""
    return GeneratedImage(
        base64="aGVsbG8=",  # base64("hello")
        mime_type="image/png",
        width=1536,
        height=864,
        cost_usd=0.04,
        provider="gemini-2.5-flash-image",
        mood=mood,
        seed=None,
    )


def _make_httpx_response(status_code: int, body: dict[str, Any]) -> MagicMock:
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


@pytest.fixture
def ctx() -> MockCtx:
    return MockCtx()


# ── GenerateBannerTool ────────────────────────────────────────────────────────

class TestGenerateBannerTool:
    @pytest.mark.asyncio
    async def test_returns_image_grid_with_4_images(self, ctx: MockCtx) -> None:
        """generate_banner returns action=display kind=image_grid with 4 images."""
        spec = get_tool("generate_banner")
        assert spec is not None

        fake_images = [_make_fake_image(m) for m in ["professional", "vibrant", "minimal", "tech"]]
        mock_provider = AsyncMock()
        mock_provider.generate_batch = AsyncMock(return_value=fake_images)

        with patch("app.services.llm_tools.visual_tools.image_route", return_value=mock_provider):
            result = await spec.execute_fn(
                {"event_name": "Tech Fair 2026", "variants": 4}, ctx
            )

        assert result["action"] == "display"
        assert result["kind"] == "image_grid"
        assert len(result["images"]) == 4
        assert "total_cost_usd" in result

    @pytest.mark.asyncio
    async def test_calls_provider_with_correct_default_moods(self, ctx: MockCtx) -> None:
        """generate_banner passes default mood matrix to generate_batch."""
        spec = get_tool("generate_banner")
        assert spec is not None

        fake_images = [_make_fake_image(m) for m in ["professional", "vibrant", "minimal", "tech"]]
        mock_provider = AsyncMock()
        mock_provider.generate_batch = AsyncMock(return_value=fake_images)

        with patch("app.services.llm_tools.visual_tools.image_route", return_value=mock_provider):
            await spec.execute_fn({"event_name": "Career Expo", "variants": 4}, ctx)

        call_kwargs = mock_provider.generate_batch.call_args.kwargs
        moods = call_kwargs.get("moods", [])
        assert "professional" in moods
        assert "vibrant" in moods
        assert "minimal" in moods
        assert "tech" in moods

    @pytest.mark.asyncio
    async def test_single_mood_repeated_when_specified(self, ctx: MockCtx) -> None:
        """When mood is specified, all variants use that mood."""
        spec = get_tool("generate_banner")
        assert spec is not None

        fake_images = [_make_fake_image("luxury")] * 2
        mock_provider = AsyncMock()
        mock_provider.generate_batch = AsyncMock(return_value=fake_images)

        with patch("app.services.llm_tools.visual_tools.image_route", return_value=mock_provider):
            result = await spec.execute_fn(
                {"event_name": "Luxury Gala", "mood": "luxury", "variants": 2}, ctx
            )

        call_kwargs = mock_provider.generate_batch.call_args.kwargs
        moods = call_kwargs.get("moods", [])
        assert all(m == "luxury" for m in moods)
        assert len(result["images"]) == 2

    @pytest.mark.asyncio
    async def test_safety_block_returns_error(self, ctx: MockCtx) -> None:
        """ImageProviderSafetyError → action=error kind=safety_block."""
        spec = get_tool("generate_banner")
        assert spec is not None

        mock_provider = AsyncMock()
        mock_provider.generate_batch = AsyncMock(
            side_effect=ImageProviderSafetyError("Content blocked by safety filter.")
        )

        with patch("app.services.llm_tools.visual_tools.image_route", return_value=mock_provider):
            result = await spec.execute_fn({"event_name": "Blocked Event"}, ctx)

        assert result["action"] == "error"
        assert result["kind"] == "safety_block"
        assert "message" in result

    @pytest.mark.asyncio
    async def test_image_grid_entries_have_required_fields(self, ctx: MockCtx) -> None:
        """Each image in the grid has id, base64, mime_type, mood, width, height."""
        spec = get_tool("generate_banner")
        assert spec is not None

        fake_images = [_make_fake_image("minimal")]
        mock_provider = AsyncMock()
        mock_provider.generate_batch = AsyncMock(return_value=fake_images)

        with patch("app.services.llm_tools.visual_tools.image_route", return_value=mock_provider):
            result = await spec.execute_fn({"event_name": "Minimal Conf", "variants": 1}, ctx)

        img = result["images"][0]
        assert "id" in img
        assert "base64" in img
        assert "mime_type" in img
        assert "mood" in img
        assert "width" in img
        assert "height" in img

    @pytest.mark.asyncio
    async def test_uses_brand_kit_when_available(self, ctx: MockCtx) -> None:
        """When ctx.brand_kit is set, brand_kit_dict is passed to build_prompt."""
        spec = get_tool("generate_banner")
        assert spec is not None

        class BrandKit:
            primary_color = "#FF0000"
            secondary_color = "#00FF00"
            voice_tone = "friendly"
            font_heading = "Inter"

        ctx_with_kit = MockCtx()
        ctx_with_kit.brand_kit = BrandKit()

        fake_images = [_make_fake_image("professional")]
        mock_provider = AsyncMock()
        mock_provider.generate_batch = AsyncMock(return_value=fake_images)

        captured_prompts: list[str] = []

        def capture_build_prompt(base: str, mood: str, brand_kit: dict | None) -> str:
            if brand_kit:
                captured_prompts.append(brand_kit.get("primary_color", ""))
            return f"prompt for {mood}"

        with (
            patch("app.services.llm_tools.visual_tools.image_route", return_value=mock_provider),
            patch("app.services.llm_tools.visual_tools.build_prompt", side_effect=capture_build_prompt),
        ):
            await spec.execute_fn({"event_name": "Branded Event", "variants": 1}, ctx_with_kit)

        assert "#FF0000" in captured_prompts


# ── SaveImageToEventTool ──────────────────────────────────────────────────────

class TestSaveImageToEventTool:
    @pytest.mark.asyncio
    async def test_preview_returns_signed_commit(self, ctx: MockCtx) -> None:
        """save_image_to_event returns action=preview with signed_commit."""
        spec = get_tool("save_image_to_event")
        assert spec is not None

        valid_b64 = base64.b64encode(b"fake image data").decode()
        result = await spec.execute_fn(
            {"event_id": "evt-1", "image_base64": valid_b64, "as_cover": True},
            ctx,
        )

        assert result["action"] == "preview"
        assert result["kind"] == "image_save"
        assert "signed_commit" in result
        assert result["commit_tool"] == "save_image_to_event_commit"

    @pytest.mark.asyncio
    async def test_rejects_oversized_base64(self, ctx: MockCtx) -> None:
        """Image exceeding 8 MB raw → action=error kind=payload_too_large."""
        spec = get_tool("save_image_to_event")
        assert spec is not None

        # 9 MB of zeros, base64-encoded
        big_bytes = b"\x00" * (9 * 1024 * 1024)
        big_b64 = base64.b64encode(big_bytes).decode()

        result = await spec.execute_fn(
            {"event_id": "evt-1", "image_base64": big_b64}, ctx
        )

        assert result["action"] == "error"
        assert result["kind"] == "payload_too_large"

    @pytest.mark.asyncio
    async def test_rejects_invalid_base64(self, ctx: MockCtx) -> None:
        """Non-base64 string → action=error kind=invalid_base64."""
        spec = get_tool("save_image_to_event")
        assert spec is not None

        result = await spec.execute_fn(
            {"event_id": "evt-1", "image_base64": "not-valid-base64!!!"}, ctx
        )

        assert result["action"] == "error"
        assert result["kind"] == "invalid_base64"

    @pytest.mark.asyncio
    async def test_rejects_invalid_target_field(self, ctx: MockCtx) -> None:
        """target_field not in safelist → action=error kind=invalid_field."""
        spec = get_tool("save_image_to_event")
        assert spec is not None

        valid_b64 = base64.b64encode(b"data").decode()
        result = await spec.execute_fn(
            {
                "event_id": "evt-1",
                "image_base64": valid_b64,
                "target_field": "admin_token",  # injection attempt
            },
            ctx,
        )

        assert result["action"] == "error"
        assert result["kind"] == "invalid_field"


class TestSaveImageToEventCommitTool:
    @pytest.mark.asyncio
    async def test_valid_commit_uploads_and_patches_event(self, ctx: MockCtx) -> None:
        """Valid signed_commit uploads file then patches event field."""
        spec = get_tool("save_image_to_event_commit")
        assert spec is not None

        valid_b64 = base64.b64encode(b"png image bytes").decode()
        commit_payload = {
            "event_id": "evt-99",
            "image_base64": valid_b64,
            "target_field": "cover_image",
            "tenant_id": ctx.tenant_id,
            "user_id": ctx.user_id,
        }
        signed = sign_payload(commit_payload)

        upload_resp = _make_httpx_response(200, {"data": {"id": "file-abc"}})
        patch_resp = _make_httpx_response(200, {"data": {"id": "evt-99"}})

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        # First call = upload, second call = patch
        mock_client.post = AsyncMock(return_value=upload_resp)
        mock_client.patch = AsyncMock(return_value=patch_resp)

        with patch("app.services.llm_tools.visual_tools.httpx.AsyncClient", return_value=mock_client):
            result = await spec.execute_fn({"signed_commit": signed.model_dump()}, ctx)

        assert result["action"] == "committed"
        assert result["kind"] == "image_save"
        assert result["entity_id"] == "file-abc"
        assert result["link"] == "/events/evt-99"

    @pytest.mark.asyncio
    async def test_expired_signature_returns_error(self, ctx: MockCtx) -> None:
        """Expired commit payload returns invalid_signature error."""
        spec = get_tool("save_image_to_event_commit")
        assert spec is not None

        valid_b64 = base64.b64encode(b"data").decode()
        commit_payload = {
            "event_id": "evt-1",
            "image_base64": valid_b64,
            "target_field": "cover_image",
            "tenant_id": ctx.tenant_id,
            "user_id": ctx.user_id,
        }
        signed = sign_payload(commit_payload, ttl_seconds=-1)

        result = await spec.execute_fn({"signed_commit": signed.model_dump()}, ctx)

        assert result["action"] == "error"
        assert result["kind"] == "invalid_signature"

    @pytest.mark.asyncio
    async def test_directus_upload_error_returns_structured_error(self, ctx: MockCtx) -> None:
        """Directus file upload failure → action=error kind=directus_error."""
        spec = get_tool("save_image_to_event_commit")
        assert spec is not None

        valid_b64 = base64.b64encode(b"data").decode()
        commit_payload = {
            "event_id": "evt-1",
            "image_base64": valid_b64,
            "target_field": "cover_image",
            "tenant_id": ctx.tenant_id,
            "user_id": ctx.user_id,
        }
        signed = sign_payload(commit_payload)

        fail_resp = _make_httpx_response(413, {"errors": [{"message": "Too large"}]})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=fail_resp)

        with patch("app.services.llm_tools.visual_tools.httpx.AsyncClient", return_value=mock_client):
            result = await spec.execute_fn({"signed_commit": signed.model_dump()}, ctx)

        assert result["action"] == "error"
        assert result["kind"] == "directus_error"


# ── ExtractBrandFromLogoTool ──────────────────────────────────────────────────

class TestExtractBrandFromLogoTool:
    @pytest.mark.asyncio
    async def test_returns_not_implemented(self, ctx: MockCtx) -> None:
        """extract_brand_from_logo returns action=error kind=not_implemented (Phase 1c stub)."""
        spec = get_tool("extract_brand_from_logo")
        assert spec is not None

        result = await spec.execute_fn({}, ctx)

        assert result["action"] == "error"
        assert result["kind"] == "not_implemented"
        assert "message" in result

    @pytest.mark.asyncio
    async def test_stub_makes_no_http_calls(self, ctx: MockCtx) -> None:
        """extract_brand_from_logo stub does not make any network calls."""
        spec = get_tool("extract_brand_from_logo")
        assert spec is not None

        with patch("httpx.AsyncClient") as mock_http:
            await spec.execute_fn({"logo_file_id": "file-123"}, ctx)

        mock_http.assert_not_called()
