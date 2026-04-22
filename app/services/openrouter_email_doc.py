"""
openrouter_email_doc.py — Generate EmailTemplateDoc JSON via AI provider fallback chain.

Uses the existing `generate_text` helper (Gemini → Novita → OpenRouter fallback).
Returns a parsed dict matching the EmailTemplateDoc schema defined in nexpo-admin.
"""
from __future__ import annotations

import json
import logging
import random
import string
from typing import Any

from fastapi import HTTPException

from app.services.text_generator import generate_text, AllProvidersFailedError
from app.services.handlers.template_render import ALLOWED_KEYS_BY_MODULE

log = logging.getLogger(__name__)

# ── Few-shot examples (extracted from starter templates) ────────────────────

_EXAMPLE_CONFIRMATION = {
    "version": 1,
    "settings": {
        "backgroundColor": "#f4f5f7",
        "contentWidth": 600,
        "fontFamily": "'Be Vietnam Pro', -apple-system, sans-serif",
        "containerBackground": "#ffffff",
        "containerBorderColor": "#e5e7eb",
        "containerBorderRadius": 12,
        "containerShadow": "sm",
        "bodyPadding": "24px 32px",
        "brandColor": "#4F80FF",
        "textColor": "#1a1a1a",
        "mutedColor": "#6b7280",
    },
    "blocks": [
        {
            "id": "conf-s1",
            "type": "section",
            "props": {"backgroundColor": "#4F80FF", "padding": "24px 32px"},
            "children": [
                {
                    "id": "conf-h1",
                    "type": "heading",
                    "props": {"text": "{{event.name}}", "level": 2, "color": "#ffffff", "align": "center"},
                }
            ],
        },
        {
            "id": "conf-s2",
            "type": "section",
            "props": {"backgroundColor": "#ffffff", "padding": "32px 32px 16px"},
            "children": [
                {
                    "id": "conf-h2",
                    "type": "heading",
                    "props": {"text": "✅ Đã xác nhận đăng ký!", "level": 1, "color": "#1a1a1a", "align": "center"},
                },
                {
                    "id": "conf-t1",
                    "type": "text",
                    "props": {
                        "html": "<p style=\"text-align:center\">Xin chào <strong>{{visitor.full_name}}</strong>,<br/>Chúng tôi đã nhận được đơn đăng ký của bạn tham dự <strong>{{event.name}}</strong>.</p>",
                        "color": "#1a1a1a",
                        "fontSize": "15px",
                        "lineHeight": "1.7",
                        "align": "center",
                    },
                },
            ],
        },
        {
            "id": "conf-s4",
            "type": "section",
            "props": {"backgroundColor": "#ffffff", "padding": "24px 32px"},
            "children": [
                {
                    "id": "conf-btn",
                    "type": "button",
                    "props": {
                        "label": "Xem chi tiết sự kiện",
                        "href": "{{event.portal_url}}",
                        "backgroundColor": "#4F80FF",
                        "color": "#ffffff",
                        "padding": "14px 32px",
                        "borderRadius": "8px",
                        "fontWeight": "600",
                    },
                }
            ],
        },
        {
            "id": "conf-s5",
            "type": "section",
            "props": {"backgroundColor": "#f4f5f7", "padding": "20px 32px"},
            "children": [
                {
                    "id": "conf-tf",
                    "type": "text",
                    "props": {
                        "html": "<p style=\"text-align:center;color:#9ca3af;font-size:12px\">© {{event.name}} — Powered by Nexpo</p>",
                        "color": "#6b7280",
                        "fontSize": "13px",
                        "lineHeight": "1.6",
                    },
                }
            ],
        },
    ],
}

_EXAMPLE_WELCOME = {
    "version": 1,
    "settings": {
        "backgroundColor": "#f4f5f7",
        "contentWidth": 600,
        "fontFamily": "'Be Vietnam Pro', -apple-system, sans-serif",
        "containerBackground": "#ffffff",
        "containerBorderColor": "#e5e7eb",
        "containerBorderRadius": 12,
        "containerShadow": "sm",
        "bodyPadding": "24px 32px",
        "brandColor": "#4F80FF",
        "textColor": "#1a1a1a",
        "mutedColor": "#6b7280",
    },
    "blocks": [
        {
            "id": "wel-s1",
            "type": "section",
            "props": {"backgroundColor": "#4F80FF", "padding": "40px 32px"},
            "children": [
                {
                    "id": "wel-h1",
                    "type": "heading",
                    "props": {"text": "Chào mừng đến với {{event.name}}! 🎉", "level": 1, "color": "#ffffff", "align": "center"},
                }
            ],
        },
        {
            "id": "wel-s2",
            "type": "section",
            "props": {"backgroundColor": "#ffffff", "padding": "32px 32px 16px"},
            "children": [
                {
                    "id": "wel-t1",
                    "type": "text",
                    "props": {
                        "html": "<p>Cảm ơn bạn đã đăng ký. Chúng tôi đã chuẩn bị rất nhiều điều thú vị cho sự kiện này.</p>",
                        "color": "#1a1a1a",
                        "fontSize": "15px",
                        "lineHeight": "1.7",
                    },
                }
            ],
        },
        {
            "id": "wel-s3",
            "type": "section",
            "props": {"backgroundColor": "#ffffff", "padding": "28px 32px"},
            "children": [
                {
                    "id": "wel-btn",
                    "type": "button",
                    "props": {
                        "label": "Khám phá chương trình",
                        "href": "{{event.portal_url}}",
                        "backgroundColor": "#4F80FF",
                        "color": "#ffffff",
                        "padding": "14px 36px",
                        "borderRadius": "8px",
                        "fontWeight": "600",
                    },
                }
            ],
        },
        {
            "id": "wel-s4",
            "type": "section",
            "props": {"backgroundColor": "#f4f5f7", "padding": "20px 32px"},
            "children": [
                {
                    "id": "wel-tf",
                    "type": "text",
                    "props": {
                        "html": "<p style=\"text-align:center;color:#9ca3af;font-size:12px\">© {{event.name}} — Powered by Nexpo</p>",
                        "color": "#6b7280",
                        "fontSize": "12px",
                        "lineHeight": "1.6",
                    },
                }
            ],
        },
    ],
}


# ── ID generator ─────────────────────────────────────────────────────────────

def _rand_id(prefix: str) -> str:
    """Generate a block ID like 'section-abc123'."""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{prefix}-{suffix}"


# ── Prompt builder ───────────────────────────────────────────────────────────

def _build_system_prompt(module: str) -> str:
    """Build the system prompt with schema explanation + few-shot examples."""
    allowed_keys = sorted(ALLOWED_KEYS_BY_MODULE.get(module, set()))
    whitelist_str = ", ".join(f"{{{{{k}}}}}" for k in allowed_keys) if allowed_keys else "(không có)"

    ex1 = json.dumps(_EXAMPLE_CONFIRMATION, ensure_ascii=False, indent=2)
    ex2 = json.dumps(_EXAMPLE_WELCOME, ensure_ascii=False, indent=2)

    return f"""Bạn là AI tạo email template cho Nexpo platform — nền tảng quản lý triển lãm và sự kiện doanh nghiệp tại Việt Nam.

Output CHỈ là JSON hợp lệ theo schema EmailTemplateDoc. KHÔNG có markdown, KHÔNG có giải thích, KHÔNG có code fence.

Schema EmailTemplateDoc:
{{
  "version": 1,
  "settings": {{
    "backgroundColor": "#f4f5f7",
    "contentWidth": 600,
    "fontFamily": "'Be Vietnam Pro', -apple-system, sans-serif",
    "containerBackground": "#ffffff",
    "containerBorderColor": "#e5e7eb",
    "containerBorderRadius": 12,
    "containerShadow": "sm",
    "bodyPadding": "24px 32px",
    "brandColor": "#4F80FF",
    "textColor": "#1a1a1a",
    "mutedColor": "#6b7280"
  }},
  "blocks": [...]
}}

Block types (type field):
- "section": container với children[] (mảng leaf blocks). Props: backgroundColor, padding, borderColor, borderWidth, borderStyle, borderRadius, shadow
- "columns": layout nhiều cột với columnChildren[][] (mảng các mảng leaf blocks). Props: count (1/2/3), gap
- "heading": tiêu đề. Props: text (string), level (1/2/3), color, align ("left"/"center"/"right")
- "text": đoạn văn HTML. Props: html (string HTML với <p>, <strong>, <em>), color, fontSize, fontWeight, align, lineHeight
- "button": nút bấm. Props: label, href, backgroundColor, color, padding, borderRadius, fontWeight, fontSize
- "image": ảnh. Props: src, alt, width, href
- "divider": đường kẻ ngang. Props: borderColor, borderWidth, padding
- "spacer": khoảng trắng. Props: height (ví dụ "16px")

Quy tắc ID: dùng format "type-xxxxxx" (6 ký tự ngẫu nhiên). Ví dụ: "section-ab12cd", "heading-xy9z01".
Quy tắc màu: dùng brandColor #4F80FF cho nút primary và accent colors.
Quy tắc nội dung: Tiếng Việt là ưu tiên. Tối đa 8 blocks.

Module hiện tại: {module}
Variables được phép dùng (dùng chính xác như viết, ví dụ {{{{event.name}}}}):
{whitelist_str}

--- Ví dụ 1 (email xác nhận đăng ký) ---
{ex1}

--- Ví dụ 2 (email chào mừng) ---
{ex2}

Output JSON only. Không có markdown. Không có giải thích."""


def _validate_doc(doc: dict[str, Any]) -> bool:
    """Minimal validation: doc must have settings dict and blocks list."""
    if not isinstance(doc, dict):
        return False
    if not isinstance(doc.get("settings"), dict):
        return False
    if not isinstance(doc.get("blocks"), list):
        return False
    return True


def _strip_fences(text: str) -> str:
    """Remove markdown code fences from AI output."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove opening fence (```json or ```)
        start = 1
        # Remove closing fence
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[start:end]).strip()
    return text


# ── Main generator ───────────────────────────────────────────────────────────

async def generate_email_doc(
    prompt: str,
    module: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Call AI provider to generate an EmailTemplateDoc JSON.

    Args:
        prompt: Vietnamese user description of the desired email.
        module: 'meeting' | 'form' | 'broadcast' — determines variable whitelist.
        context: Optional extra context (event_name, etc.) merged into user message.

    Returns:
        Parsed EmailTemplateDoc dict.

    Raises:
        HTTPException(400) on invalid module.
        HTTPException(502) if AI fails or returns unparseable JSON after 1 retry.
    """
    valid_modules = {"meeting", "form", "broadcast"}
    if module not in valid_modules:
        raise HTTPException(status_code=400, detail=f"module must be one of {valid_modules}")

    # Cap input prompt at 3000 chars
    user_prompt = prompt[:3000]

    # Inject context hints if provided
    context_lines: list[str] = []
    if context.get("event_name"):
        context_lines.append(f"Tên sự kiện: {context['event_name']}")
    if context.get("event_date"):
        context_lines.append(f"Ngày sự kiện: {context['event_date']}")
    if context_lines:
        user_prompt = "\n".join(context_lines) + "\n\n" + user_prompt

    system_prompt = _build_system_prompt(module)
    full_prompt = f"{system_prompt}\n\n--- Yêu cầu của người dùng ---\n{user_prompt}"

    # First attempt
    raw_text, provider = await _call_ai(full_prompt)
    doc = _try_parse(raw_text)

    if doc is None:
        log.warning("generate_email_doc: parse failed on first attempt (provider=%s), retrying...", provider)
        # Retry with stricter reminder appended
        retry_prompt = full_prompt + "\n\nQUAN TRỌNG: Output ONLY valid JSON. No markdown. No explanation. Start with { immediately."
        raw_text, provider = await _call_ai(retry_prompt)
        doc = _try_parse(raw_text)

    if doc is None or not _validate_doc(doc):
        log.error("generate_email_doc: both attempts failed to produce valid JSON")
        raise HTTPException(
            status_code=502,
            detail="AI không thể tạo template hợp lệ. Vui lòng thử lại với mô tả khác.",
        )

    # Ensure version field is present
    doc.setdefault("version", 1)

    log.info("generate_email_doc: success (module=%s, provider=%s, blocks=%d)", module, provider, len(doc.get("blocks", [])))
    return doc


async def _call_ai(prompt: str) -> tuple[str, str]:
    """Call AI provider fallback chain. Raises HTTPException on total failure."""
    try:
        return await generate_text(prompt, temperature=0.7, max_tokens=4000)
    except AllProvidersFailedError as exc:
        log.error("generate_email_doc: all AI providers failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="AI service không khả dụng. Vui lòng thử lại sau.",
        )


def _try_parse(text: str) -> dict[str, Any] | None:
    """Attempt to parse AI output as JSON. Returns None on failure."""
    cleaned = _strip_fences(text)
    # Find the outermost JSON object if AI prepended text
    start = cleaned.find("{")
    if start > 0:
        cleaned = cleaned[start:]
    # Find matching closing brace
    end = cleaned.rfind("}")
    if end != -1:
        cleaned = cleaned[: end + 1]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        log.warning("_try_parse: JSONDecodeError: %s (text preview: %s)", exc, text[:200])
        return None
