"""
email_template_ai_v2.py — AI-powered EmailTemplateDoc generation endpoint (V2).

POST /generate-email-template/v2
  Body: { prompt, module, context? }
  Response: { doc: EmailTemplateDoc }

Auth: requires valid Directus user JWT via Authorization header.
Uses the same _require_user_jwt dep as broadcast.py.
Does NOT touch the legacy V1 /generate-email-template endpoint.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.routers.broadcast import _require_user_jwt
from app.services.openrouter_email_doc import generate_email_doc

router = APIRouter()


class GenerateDocRequest(BaseModel):
    prompt: str = Field(..., min_length=5, max_length=3000, description="Vietnamese description of the email to generate")
    module: str = Field(..., description="Template module: 'meeting' | 'form' | 'broadcast'")
    context: dict[str, Any] | None = Field(default=None, description="Optional context: event_name, event_date, etc.")


@router.post("/generate-email-template/v2")
async def generate_email_template_v2(
    req: GenerateDocRequest,
    _user_id: str = Depends(_require_user_jwt),
) -> dict[str, Any]:
    """Generate an EmailTemplateDoc JSON from a Vietnamese prompt.

    Auth: requires valid Directus user JWT in Authorization header.
    Returns: { doc: EmailTemplateDoc } — structured block-based doc for V2 builder.
    """
    doc = await generate_email_doc(
        prompt=req.prompt,
        module=req.module,
        context=req.context or {},
    )
    return {"doc": doc}
