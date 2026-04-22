"""
Visual tools — GenerateBanner, SaveImageToEvent, ExtractBrandFromLogo.
These tools handle media generation and image management for events.

GenerateBanner calls the in-process image provider directly (no HTTP roundtrip)
since both tools and image_router live in nexpo-services.

SaveImageToEvent uses a preview/commit HMAC split:
  - preview: returns thumbnail data URI + signed commit payload
  - commit: uploads base64 to Directus files (admin token) + patches event (user token)

ExtractBrandFromLogo is stubbed for Phase 1c (vision endpoint not yet implemented).
"""
from __future__ import annotations

import base64
import io
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from app.services.image_nano_banana import ImageProviderSafetyError
from app.services.image_prompt_builder import build_prompt
from app.services.image_router import route as image_route
from app.services.llm_tools.base import tool
from app.services.llm_tools.tool_signing import SignedPayload, sign_payload, verify_payload
from app.settings import settings

if TYPE_CHECKING:
    from app.services.llm_context import ToolContext

# Maximum raw (decoded) image size accepted for upload — 8 MB
_MAX_IMAGE_BYTES = 8 * 1024 * 1024

# Allowed target fields for patching events — safelist prevents arbitrary field injection
_ALLOWED_TARGET_FIELDS = frozenset(["cover_image", "card_image"])

# Default mood matrix for 4-variant banner generation
_DEFAULT_MOODS = ["professional", "vibrant", "minimal", "tech"]


# ── GenerateBannerTool ────────────────────────────────────────────────────────

@tool(
    description=(
        "Generate event banner image variants using AI. "
        "Calls the in-process image provider with mood-based prompts. "
        "Returns an image grid with base64 images the user can preview. "
        "After selecting an image, call save_image_to_event to persist it."
    ),
)
class GenerateBannerTool:
    name = "generate_banner"

    class Input(BaseModel):
        event_name: str = Field(..., description="Event name used as the core prompt")
        event_id: str | None = Field(None, description="Optional event ID for brand kit lookup")
        mood: Literal["professional", "vibrant", "minimal", "luxury", "tech"] | None = Field(
            None,
            description="Mood for all variants. If omitted, generates one of each default mood.",
        )
        brand_color: str | None = Field(
            None,
            description="Hex brand color override (e.g. #4F80FF). Used if no brand kit is available.",
        )
        variants: int = Field(4, ge=1, le=4, description="Number of image variants to generate (max 4)")

    @staticmethod
    async def execute(args: dict[str, Any], ctx: "ToolContext") -> dict[str, Any]:
        event_name: str = args["event_name"]
        variants: int = min(int(args.get("variants", 4)), 4)
        mood_arg: str | None = args.get("mood")

        # Build mood list — single mood repeated, or slice of default matrix
        if mood_arg:
            moods = [mood_arg] * variants
        else:
            moods = _DEFAULT_MOODS[:variants]

        # Build brand kit dict for prompt injection
        brand_kit_dict: dict[str, Any] | None = None
        if ctx.brand_kit:
            brand_kit_dict = {
                "primary_color": ctx.brand_kit.primary_color,
                "secondary_color": ctx.brand_kit.secondary_color,
                "voice": ctx.brand_kit.voice_tone,
                "font_style": f"{ctx.brand_kit.font_heading} for headlines",
            }
        elif args.get("brand_color"):
            # Fallback: caller-supplied color override when no brand kit is configured
            brand_kit_dict = {"primary_color": args["brand_color"]}

        # Route to appropriate provider — in-process call, no HTTP roundtrip
        provider = image_route("event-banner", "standard")
        prompts = [build_prompt(event_name, m, brand_kit_dict) for m in moods]

        try:
            images = await provider.generate_batch(
                prompts=prompts,
                aspect_ratio="16:9",
                image_size="2K",
                moods=moods,
                seed=None,
            )
        except ImageProviderSafetyError as exc:
            return {
                "action": "error",
                "kind": "safety_block",
                "message": str(exc),
            }

        return {
            "action": "display",
            "kind": "image_grid",
            "images": [
                {
                    "id": str(uuid4()),
                    "base64": img.base64,
                    "mime_type": img.mime_type,
                    "mood": img.mood,
                    "seed": img.seed,
                    "width": img.width,
                    "height": img.height,
                }
                for img in images
            ],
            "total_cost_usd": sum(img.cost_usd for img in images),
            "event_id": args.get("event_id"),
        }


# ── SaveImageToEventTool ──────────────────────────────────────────────────────

@tool(
    description=(
        "Preview saving a generated image as the event cover or card image. "
        "Returns a signed preview with a thumbnail. "
        "After user confirms, call save_image_to_event_commit with the signed_commit payload."
    ),
    requires_confirm=True,
)
class SaveImageToEventTool:
    name = "save_image_to_event"

    class Input(BaseModel):
        event_id: str = Field(..., description="ID of the event to update")
        image_base64: str = Field(..., description="Base64-encoded image data")
        as_cover: bool = Field(True, description="Whether to set as cover image")
        target_field: str = Field(
            "cover_image",
            description="Event field to update: 'cover_image' or 'card_image'",
        )

    @staticmethod
    async def execute(args: dict[str, Any], ctx: "ToolContext") -> dict[str, Any]:
        event_id: str = args["event_id"]
        image_b64: str = args["image_base64"]
        target_field: str = args.get("target_field", "cover_image")

        # Validate target field against safelist — prevents arbitrary field injection
        if target_field not in _ALLOWED_TARGET_FIELDS:
            return {
                "action": "error",
                "kind": "invalid_field",
                "message": f"target_field must be one of {sorted(_ALLOWED_TARGET_FIELDS)}.",
            }

        # Validate base64 size — reject oversized payloads before signing
        try:
            raw_bytes = base64.b64decode(image_b64, validate=True)
        except Exception:
            return {
                "action": "error",
                "kind": "invalid_base64",
                "message": "image_base64 is not valid base64-encoded data.",
            }

        if len(raw_bytes) > _MAX_IMAGE_BYTES:
            return {
                "action": "error",
                "kind": "payload_too_large",
                "message": f"Image exceeds 8 MB limit ({len(raw_bytes) / 1024 / 1024:.1f} MB). Compress or resize before uploading.",
            }

        # Build a data URI thumbnail for preview display (truncated for safety)
        # Use first 256 chars of base64 as a "preview" marker — real thumbnail shown by UI
        thumbnail_data_uri = f"data:image/png;base64,{image_b64[:256]}..."

        commit_payload: dict[str, Any] = {
            "event_id": event_id,
            "image_base64": image_b64,
            "target_field": target_field,
            "tenant_id": ctx.tenant_id,
            "user_id": ctx.user_id,
        }
        signed = sign_payload(commit_payload)

        return {
            "action": "preview",
            "kind": "image_save",
            "preview": {
                "event_id": event_id,
                "target_field": target_field,
                "thumbnail": thumbnail_data_uri,
                "size_kb": round(len(raw_bytes) / 1024, 1),
            },
            "signed_commit": signed.model_dump(),
            "commit_tool": "save_image_to_event_commit",
            "confirm_label": "Lưu ảnh vào sự kiện",
        }


# ── SaveImageToEventCommitTool ────────────────────────────────────────────────

@tool(
    description=(
        "Commit saving an image to an event after user confirms. "
        "Uploads image to Directus files then patches the event field. "
        "Pass the signed_commit from save_image_to_event output exactly as received."
    ),
)
class SaveImageToEventCommitTool:
    name = "save_image_to_event_commit"

    class Input(BaseModel):
        signed_commit: dict[str, Any] = Field(
            ..., description="Signed commit payload returned by save_image_to_event"
        )

    @staticmethod
    async def execute(args: dict[str, Any], ctx: "ToolContext") -> dict[str, Any]:
        # Parse signed payload
        try:
            signed = SignedPayload.model_validate(args["signed_commit"])
        except Exception:
            return {
                "action": "error",
                "kind": "invalid_payload",
                "message": "signed_commit is malformed or missing required fields.",
            }

        if not verify_payload(signed):
            return {
                "action": "error",
                "kind": "invalid_signature",
                "message": "Commit payload signature is invalid or expired. Request a fresh preview.",
            }

        if str(signed.payload.get("tenant_id", "")) != str(ctx.tenant_id):
            return {
                "action": "error",
                "kind": "tenant_mismatch",
                "message": "Commit payload tenant does not match your active session.",
            }

        payload = signed.payload
        event_id: str = payload["event_id"]
        image_b64: str = payload["image_base64"]
        target_field: str = payload.get("target_field", "cover_image")

        # Re-validate target field — defense in depth even after signature check
        if target_field not in _ALLOWED_TARGET_FIELDS:
            return {
                "action": "error",
                "kind": "invalid_field",
                "message": f"target_field must be one of {sorted(_ALLOWED_TARGET_FIELDS)}.",
            }

        # Decode image bytes
        try:
            image_bytes = base64.b64decode(image_b64, validate=True)
        except Exception:
            return {
                "action": "error",
                "kind": "invalid_base64",
                "message": "image_base64 in commit payload could not be decoded.",
            }

        if len(image_bytes) > _MAX_IMAGE_BYTES:
            return {
                "action": "error",
                "kind": "payload_too_large",
                "message": "Image exceeds 8 MB limit.",
            }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # Upload to Directus files using admin token — file upload requires elevated perms
                # User token scoped to events collection cannot create file assets
                upload_resp = await client.post(
                    f"{settings.directus_url}/files",
                    headers={"Authorization": f"Bearer {settings.directus_admin_token}"},
                    files={"file": ("banner.png", io.BytesIO(image_bytes), "image/png")},
                )
                upload_resp.raise_for_status()
                file_data = upload_resp.json().get("data", {})
                file_id = file_data.get("id")

                if not file_id:
                    return {
                        "action": "error",
                        "kind": "upload_failed",
                        "message": "Directus file upload succeeded but returned no file ID.",
                    }

                # Patch event with user token — user must own the event
                patch_resp = await client.patch(
                    f"{settings.directus_url}/items/events/{event_id}",
                    headers={"Authorization": f"Bearer {ctx.user_token}"},
                    json={target_field: file_id},
                )
                patch_resp.raise_for_status()

        except httpx.HTTPStatusError as exc:
            return {
                "action": "error",
                "kind": "directus_error",
                "message": f"Failed to save image: HTTP {exc.response.status_code}",
            }
        except Exception as exc:
            return {
                "action": "error",
                "kind": "network_error",
                "message": f"Failed to reach Directus: {exc}",
            }

        return {
            "action": "committed",
            "kind": "image_save",
            "entity_id": file_id,
            "link": f"/events/{event_id}",
        }


# ── ExtractBrandFromLogoTool ──────────────────────────────────────────────────

@tool(
    description=(
        "Extract brand colors and typography from a logo image. "
        "STUB: Phase 1c will implement the vision endpoint. "
        "For now, set brand kit manually via /settings/brand-kits."
    ),
)
class ExtractBrandFromLogoTool:
    name = "extract_brand_from_logo"

    class Input(BaseModel):
        logo_file_id: str | None = Field(
            None,
            description="Directus file ID of the logo. Defaults to tenant logo if omitted.",
        )

    @staticmethod
    async def execute(args: dict[str, Any], ctx: "ToolContext") -> dict[str, Any]:
        # Phase 1c will implement: POST /services/vision/brand-kit with file bytes
        # returning {colors: [...], fonts: [...], voice: str}
        return {
            "action": "error",
            "kind": "not_implemented",
            "message": (
                "Brand extraction is wired for Phase 1c. "
                "Please set brand kit manually via /settings/brand-kits for now."
            ),
        }
