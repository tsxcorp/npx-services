"""
Setup tools — CreateEvent, CreateForm, ListEvents, NavigateTo.
These tools handle core admin setup tasks: event/form creation and navigation.

Each tool follows the @tool decorator pattern from llm_tools/base.py:
  - Nested Input(BaseModel) defines the JSON schema for the LLM to call
  - execute(args, ctx) is async and receives ToolContext — never ctx fields in Input
  - Hard actions (create_event, create_form) use HMAC-signed preview/commit split
  - Returns structured dicts that Phase 1b renders as preview cards, tables, or links
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

import httpx
from pydantic import BaseModel, Field

from app.services.llm_tools.base import tool
from app.services.llm_tools.tool_signing import SignedPayload, sign_payload, verify_payload
from app.settings import settings

if TYPE_CHECKING:
    from app.services.llm_context import ToolContext

# Regex for safe route validation — mirrors Phase 0c SSE proxy regex
_SAFE_ROUTE_RE = re.compile(r"^/[a-zA-Z0-9/_\-]{0,200}$")


# ── CreateEventTool ───────────────────────────────────────────────────────────

@tool(
    description=(
        "Preview creating a new event. Returns a signed preview card for user confirmation. "
        "After user confirms, call create_event_commit with the signed_commit payload."
    ),
    requires_confirm=True,
)
class CreateEventTool:
    name = "create_event"

    class Input(BaseModel):
        name: str = Field(..., description="Event name")
        category: str | None = Field(None, description="Event category (e.g. 'tech', 'career')")
        type: Literal["offline", "online", "hybrid"] = Field(..., description="Event format")
        start_date: str = Field(..., description="Start date in ISO 8601 format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)")
        end_date: str = Field(..., description="End date in ISO 8601 format")
        location: str | None = Field(None, description="Physical location (for offline/hybrid)")
        description: str | None = Field(None, description="Short event description")

    @staticmethod
    async def execute(args: dict[str, Any], ctx: "ToolContext") -> dict[str, Any]:
        preview = {
            "name": args.get("name"),
            "category": args.get("category"),
            "type": args.get("type"),
            "start_date": args.get("start_date"),
            "end_date": args.get("end_date"),
            "location": args.get("location"),
            "description": args.get("description"),
        }
        commit_payload: dict[str, Any] = {
            **preview,
            "tenant_id": ctx.tenant_id,
            "user_id": ctx.user_id,
        }
        signed = sign_payload(commit_payload)
        return {
            "action": "preview",
            "kind": "event",
            "preview": preview,
            "signed_commit": signed.model_dump(),
            "commit_tool": "create_event_commit",
            "confirm_label": "Tạo sự kiện",
        }


# ── CreateEventCommitTool ─────────────────────────────────────────────────────

@tool(
    description=(
        "Commit creating a new event after user confirms the preview. "
        "Pass the signed_commit from create_event output exactly as received."
    ),
)
class CreateEventCommitTool:
    name = "create_event_commit"

    class Input(BaseModel):
        signed_commit: dict[str, Any] = Field(
            ..., description="Signed commit payload returned by create_event"
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

        # Verify HMAC + expiration
        if not verify_payload(signed):
            return {
                "action": "error",
                "kind": "invalid_signature",
                "message": "Commit payload signature is invalid or expired. Request a fresh preview.",
            }

        # Defense-in-depth: tenant must match resolved context
        if str(signed.payload.get("tenant_id", "")) != str(ctx.tenant_id):
            return {
                "action": "error",
                "kind": "tenant_mismatch",
                "message": "Commit payload tenant does not match your active session.",
            }

        # Build Directus event payload — strip signing metadata
        event_data = {
            k: v
            for k, v in signed.payload.items()
            if k not in ("tenant_id", "user_id", "exp")
        }
        # Map date fields to Directus field names
        if "start_date" in event_data:
            event_data["date_start"] = event_data.pop("start_date")
        if "end_date" in event_data:
            event_data["date_end"] = event_data.pop("end_date")

        # Scope event to tenant
        event_data["tenant_id"] = ctx.tenant_id

        # Create event via Directus with user token (row-level scoping enforced by Directus)
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    f"{settings.directus_url}/items/events",
                    headers={"Authorization": f"Bearer {ctx.user_token}"},
                    json=event_data,
                )
                resp.raise_for_status()
                event = resp.json().get("data", {})
        except httpx.HTTPStatusError as exc:
            return {
                "action": "error",
                "kind": "directus_error",
                "message": f"Failed to create event: HTTP {exc.response.status_code}",
            }
        except Exception as exc:
            return {
                "action": "error",
                "kind": "network_error",
                "message": f"Failed to reach Directus: {exc}",
            }

        event_id = event.get("id")

        # TODO: Call nexpo-admin's /api/events/{id}/setup-folders after creation.
        # setup-folders is nexpo-admin internal (creates Drive folder structure).
        # It is triggered automatically when the organizer first navigates to the event
        # in nexpo-admin. Skipping here to avoid admin-internal coupling in services.

        return {
            "action": "committed",
            "kind": "event",
            "entity_id": event_id,
            "link": f"/events/{event_id}",
        }


# ── ListEventsTool ────────────────────────────────────────────────────────────

@tool(
    description=(
        "List events for the current tenant. Supports filtering by status and upcoming events. "
        "Returns a data table the UI can render as a list."
    ),
)
class ListEventsTool:
    name = "list_events"

    class Input(BaseModel):
        status: Literal["draft", "published", "archived"] | None = Field(
            None, description="Filter by event status"
        )
        upcoming: bool | None = Field(
            None, description="If true, only return events with start date >= today"
        )
        limit: int = Field(10, ge=1, le=100, description="Max events to return (default 10)")

    @staticmethod
    async def execute(args: dict[str, Any], ctx: "ToolContext") -> dict[str, Any]:
        params: dict[str, Any] = {
            "fields": "id,name,date_start,date_end,status,category,type,location",
            "limit": str(args.get("limit", 10)),
        }

        status = args.get("status")
        if status:
            params["filter[status][_eq]"] = status

        upcoming = args.get("upcoming")
        if upcoming:
            today = datetime.now(timezone.utc).date().isoformat()
            params["filter[date_start][_gte]"] = today

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    f"{settings.directus_url}/items/events",
                    headers={"Authorization": f"Bearer {ctx.user_token}"},
                    params=params,
                )
                resp.raise_for_status()
                rows_raw: list[dict[str, Any]] = resp.json().get("data", [])
        except httpx.HTTPStatusError as exc:
            return {
                "action": "error",
                "kind": "directus_error",
                "message": f"Failed to list events: HTTP {exc.response.status_code}",
            }
        except Exception as exc:
            return {
                "action": "error",
                "kind": "network_error",
                "message": f"Failed to reach Directus: {exc}",
            }

        columns = [
            {"key": "name", "label": "Tên sự kiện"},
            {"key": "status", "label": "Trạng thái"},
            {"key": "date_start", "label": "Ngày bắt đầu"},
            {"key": "date_end", "label": "Ngày kết thúc"},
            {"key": "type", "label": "Hình thức"},
            {"key": "location", "label": "Địa điểm"},
        ]
        rows = [
            {col["key"]: row.get(col["key"]) for col in columns} | {"id": row.get("id")}
            for row in rows_raw
        ]

        return {
            "action": "display",
            "kind": "data_table",
            "columns": columns,
            "rows": rows,
        }


# ── CreateFormTool ────────────────────────────────────────────────────────────

@tool(
    description=(
        "Preview creating a registration or non-registration form for an event. "
        "Returns a signed preview for user confirmation. "
        "After confirmation, call create_form_commit with the signed_commit payload."
    ),
    requires_confirm=True,
)
class CreateFormTool:
    name = "create_form"

    class Input(BaseModel):
        event_id: str = Field(..., description="ID of the event to attach the form to")
        form_type: Literal["registration", "non-registration"] = Field(
            ..., description="Form type"
        )
        fields: list[dict[str, Any]] = Field(
            ...,
            description=(
                "List of form fields. Each field: "
                "{key: str, label: str, type: str, required: bool}"
            ),
        )

    @staticmethod
    async def execute(args: dict[str, Any], ctx: "ToolContext") -> dict[str, Any]:
        preview = {
            "event_id": args.get("event_id"),
            "form_type": args.get("form_type"),
            "fields": args.get("fields", []),
            "field_count": len(args.get("fields", [])),
        }
        commit_payload: dict[str, Any] = {
            **preview,
            "tenant_id": ctx.tenant_id,
            "user_id": ctx.user_id,
        }
        signed = sign_payload(commit_payload)
        return {
            "action": "preview",
            "kind": "form",
            "preview": preview,
            "signed_commit": signed.model_dump(),
            "commit_tool": "create_form_commit",
            "confirm_label": "Tạo form",
        }


# ── CreateFormCommitTool ──────────────────────────────────────────────────────

@tool(
    description=(
        "Commit creating a form after user confirms the preview. "
        "Pass the signed_commit from create_form output exactly as received."
    ),
)
class CreateFormCommitTool:
    name = "create_form_commit"

    class Input(BaseModel):
        signed_commit: dict[str, Any] = Field(
            ..., description="Signed commit payload returned by create_form"
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
        form_data = {
            "event_id": payload.get("event_id"),
            "form_type": payload.get("form_type"),
            "tenant_id": ctx.tenant_id,
        }
        fields: list[dict[str, Any]] = payload.get("fields", [])

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                # Create form row
                form_resp = await client.post(
                    f"{settings.directus_url}/items/forms",
                    headers={"Authorization": f"Bearer {ctx.user_token}"},
                    json=form_data,
                )
                form_resp.raise_for_status()
                form = form_resp.json().get("data", {})
                form_id = form.get("id")

                # Batch-create form fields linked to the new form
                if fields and form_id:
                    field_rows = [
                        {
                            "form_id": form_id,
                            "key": f.get("key"),
                            "label": f.get("label"),
                            "type": f.get("type", "text"),
                            "required": f.get("required", False),
                            "sort": idx,
                        }
                        for idx, f in enumerate(fields)
                    ]
                    fields_resp = await client.post(
                        f"{settings.directus_url}/items/form_fields",
                        headers={"Authorization": f"Bearer {ctx.user_token}"},
                        json=field_rows,
                    )
                    fields_resp.raise_for_status()

        except httpx.HTTPStatusError as exc:
            return {
                "action": "error",
                "kind": "directus_error",
                "message": f"Failed to create form: HTTP {exc.response.status_code}",
            }
        except Exception as exc:
            return {
                "action": "error",
                "kind": "network_error",
                "message": f"Failed to reach Directus: {exc}",
            }

        return {
            "action": "committed",
            "kind": "form",
            "entity_id": form_id,
            "link": f"/events/{payload.get('event_id')}/forms/{form_id}",
        }


# ── NavigateToTool ────────────────────────────────────────────────────────────

@tool(
    description=(
        "Suggest navigation to a route in the admin panel. "
        "No side effects — the UI renders a clickable link card (LinkPill). "
        "Use this when the user asks to go somewhere or when a workflow step requires navigation."
    ),
)
class NavigateToTool:
    name = "navigate_to"

    class Input(BaseModel):
        route: str = Field(..., description="Admin route path, must start with /")
        reason: str | None = Field(None, description="Why the user should navigate here")

    @staticmethod
    async def execute(args: dict[str, Any], ctx: "ToolContext") -> dict[str, Any]:
        route: str = args.get("route", "")

        # Validate route is safe — must start with / and match allowed chars
        if not route.startswith("/") or not _SAFE_ROUTE_RE.match(route):
            return {
                "action": "error",
                "kind": "invalid_route",
                "message": f"Route '{route}' is not a valid admin route. Must start with / and contain only safe path characters.",
            }

        label = args.get("reason") or route
        return {
            "action": "link",
            "href": route,
            "label": label,
        }
