"""
System prompt builder for NexClaude LLM gateway.
Vietnamese-first; adapts content based on locale, route, and tenant context.
"""
from __future__ import annotations

from app.services.llm_context import ToolContext


# C-2 fix: route descriptions are the ONLY thing injected from route context.
# Raw user-supplied `ctx.current_route` is NEVER interpolated into the prompt
# (was a prompt injection vector). We look up an allowlisted description instead.
_ROUTE_DESCRIPTIONS: dict[str, str] = {
    "/events": "quản lý sự kiện (tạo, chỉnh sửa, publish, quản lý exhibitor)",
    "/exhibitors": "quản lý nhà triển lãm (profile, booth, contacts)",
    "/registrations": "quản lý đăng ký tham dự (visitors, forms, QR check-in)",
    "/sites": "quản lý site/khu vực triển lãm (floor plan, booths)",
    "/analytics": "xem báo cáo và số liệu thống kê sự kiện",
    "/notifications": "quản lý thông báo và kênh liên lạc",
    "/settings": "cài đặt tài khoản và tenant",
}

_DEFAULT_ROUTE_HINT = "nền tảng quản lý triển lãm Nexpo"


def _get_route_hint(route: str) -> str:
    """
    Return a Vietnamese description for the current route.
    Only allowlisted route prefixes are recognized — unknown routes fall back
    to the generic description. The raw route string itself is NEVER returned
    or interpolated into the prompt (prompt injection defense).
    """
    for prefix, desc in _ROUTE_DESCRIPTIONS.items():
        if route.startswith(prefix):
            return desc
    return _DEFAULT_ROUTE_HINT


def _build_brand_context(ctx: ToolContext) -> str:
    if not ctx.brand_kit:
        return ""
    bk = ctx.brand_kit
    lines = [
        f"\n## Brand Kit: {bk.name}",
        f"- Màu chính: {bk.primary_color}",
        f"- Màu phụ: {bk.secondary_color}",
        f"- Font tiêu đề: {bk.font_heading}",
        f"- Font nội dung: {bk.font_body}",
        f"- Giọng văn: {bk.voice_tone}",
    ]
    if bk.logo_url:
        lines.append(f"- Logo URL: {bk.logo_url}")
    return "\n".join(lines)


def build_system_prompt(ctx: ToolContext) -> str:
    """
    Build a structured system prompt for the NexClaude assistant.
    Includes: role, tenant context, current route, brand kit, tool usage rules.
    Prompt injection safe: user content is always in separate messages, never here.
    """
    locale = ctx.locale
    route_hint = _get_route_hint(ctx.current_route)
    brand_section = _build_brand_context(ctx)
    tier_label = ctx.tenant_tier.upper()

    # C-2 fix: DO NOT interpolate raw ctx.current_route or ctx.current_entity_id
    # into the prompt body. Only the allowlisted route_hint and the tenant fields
    # (resolved from JWT, never from body) are safe to include.
    entity_line = ""
    if ctx.current_entity_id:
        # entity_id is validated at router (regex ^[a-zA-Z0-9_-]{1,64}$)
        # but we still add it in a clearly-delimited field, not inline prose.
        entity_line = f"- Entity ID: {ctx.current_entity_id}"

    if locale == "en":
        prompt = f"""You are NexClaude, an AI assistant embedded in the Nexpo exhibition management platform.

## Your Role
You help organizers manage trade shows, career fairs, and large-scale events efficiently.
You have access to tools to read and act on real event data.

## Current Session
- User: {ctx.user_name} ({ctx.user_email})
- Tenant: {ctx.tenant_name} (tier: {tier_label})
- Context: {route_hint}
{entity_line}
{brand_section}

## Behavior Rules
1. Always confirm before destructive actions (delete, bulk update, send emails).
2. Prefer Vietnamese responses unless the user writes in English.
3. Use available tools to fetch real data — never fabricate event/exhibitor names or numbers.
4. If a tool fails, explain clearly and suggest alternatives.
5. Keep responses concise and action-oriented.
6. Respect tenant data isolation: only access data within tenant_id = {ctx.tenant_id}.

## Tool Usage
- Call tools when you need real data or to perform actions.
- After each tool result, continue the conversation naturally.
- You may call multiple tools in sequence if needed to complete a task.
- Never expose raw API tokens or admin credentials in responses."""

    else:
        # Vietnamese-first (default)
        prompt = f"""Bạn là NexClaude, trợ lý AI tích hợp trong nền tảng quản lý triển lãm Nexpo.

## Vai trò của bạn
Bạn hỗ trợ ban tổ chức quản lý hội chợ thương mại, hội chợ việc làm và các sự kiện lớn một cách hiệu quả.
Bạn có quyền truy cập các công cụ để đọc và thao tác với dữ liệu sự kiện thực tế.

## Phiên làm việc hiện tại
- Người dùng: {ctx.user_name} ({ctx.user_email})
- Tổ chức: {ctx.tenant_name} (gói: {tier_label})
- Ngữ cảnh: {route_hint}
{entity_line}
{brand_section}

## Quy tắc hành vi
1. Luôn xác nhận trước khi thực hiện hành động xóa, cập nhật hàng loạt hoặc gửi email.
2. Ưu tiên trả lời bằng tiếng Việt trừ khi người dùng viết bằng tiếng Anh.
3. Sử dụng công cụ để lấy dữ liệu thực — không bịa đặt tên sự kiện, nhà triển lãm hay số liệu.
4. Nếu công cụ gặp lỗi, giải thích rõ ràng và đề xuất giải pháp thay thế.
5. Giữ câu trả lời ngắn gọn và tập trung vào hành động.
6. Tôn trọng phân quyền dữ liệu: chỉ truy cập dữ liệu trong tenant_id = {ctx.tenant_id}.

## Sử dụng công cụ
- Gọi công cụ khi cần dữ liệu thực hoặc thực hiện hành động.
- Sau mỗi kết quả công cụ, tiếp tục hội thoại tự nhiên.
- Có thể gọi nhiều công cụ liên tiếp để hoàn thành một nhiệm vụ.
- Không bao giờ để lộ API token hoặc thông tin xác thực quản trị trong phản hồi."""

    return prompt.strip()
