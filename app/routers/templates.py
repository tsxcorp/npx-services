from fastapi import APIRouter, HTTPException
import httpx
from app.models.schemas import GenerateEmailTemplateRequest, GenerateEmailTemplateResponse, EmailStyleConfig
from app.config import OPENROUTER_API_KEY

router = APIRouter()


@router.post("/generate-email-template", response_model=GenerateEmailTemplateResponse)
async def generate_email_template(request: GenerateEmailTemplateRequest):
    """Generate a styled HTML email template using AI based on form fields and event context."""
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=503, detail="OpenRouter API key not configured")

    lang_instruction = {
        "vi": "Write ALL text (headings, labels, body, footer) in Vietnamese only.",
        "en": "Write ALL text (headings, labels, body, footer) in English only.",
        "bilingual": (
            "Write ALL text bilingually. Format: Vietnamese / English (separated by ' / ').\n"
            "  - Section headings: e.g. 'CHI TIẾT ĐĂNG KÝ / REGISTRATION DETAILS'\n"
            "  - Row labels: EVERY label cell MUST have both languages, e.g. 'Họ và Tên / Full Name'\n"
            "  - Greeting: e.g. 'Xin chào / Dear,'\n"
            "  - Body text: each sentence bilingual\n"
            "  - Footer: bilingual\n"
            "  ⚠️ CRITICAL: Do NOT skip bilingual labels for ANY row. Every single label cell must contain both Vietnamese and English."
        ),
    }.get(request.language, "bilingual")

    tone_instruction = {
        "professional": "Use a professional, corporate tone.",
        "friendly": "Use a warm, friendly and welcoming tone.",
        "formal": "Use a formal, official tone.",
    }.get(request.tone, "professional")

    field_list = "\n".join(
        f'  - Variable: ${{{f.id}}} | Label: "{f.label}" | Type: {f.type}'
        for f in request.fields
    ) or "  (none — use generic placeholder content)"

    form_context = "registration confirmation" if request.is_registration else (request.form_purpose or "meeting notification")

    # ── Brand settings ────────────────────────────────────────────────────────
    style = request.email_style or EmailStyleConfig()
    primary = style.primary_color or "#E94560"
    h_start = style.header_color_start or "#1a1a2e"
    h_end   = style.header_color_end   or "#0f3460"
    logo_html = (
        f'<img src="{style.logo_url}" alt="Event Logo" style="max-height:48px;max-width:180px;display:block;" />'
        if style.logo_url else
        'Nexpo Platform'
    )
    event_label_badge = (style.event_label or request.event_name.upper())[:30]
    custom_footer = style.footer_text or "This is an automated notification from Nexpo Platform."

    section_header_bg = h_end  # use darker gradient end for section headers
    form_context_title = form_context.title()

    name_field_hint = ""
    for f in request.fields:
        if any(kw in f.label.lower() for kw in ["name", "họ tên", "tên", "full name", "họ và tên"]):
            name_field_hint = f"Use ${{{f.id}}} as the recipient's name in the greeting."
            break

    prompt = f"""You are a world-class HTML email designer. Create a stunning, polished HTML email template for a {form_context} email. Think of award-winning transactional emails from top tech companies.

EVENT: {request.event_name}
LANGUAGE: {lang_instruction}
TONE: {tone_instruction}
{name_field_hint}

FORM FIELDS (insert these variables exactly as shown):
{field_list}

═══════════════════════════════════════════════
DESIGN SYSTEM — follow EXACTLY (brand colors already set for this event):
═══════════════════════════════════════════════

COLOR PALETTE:
  - Page background: #F0F4F8
  - Card background: #FFFFFF
  - Header gradient: linear-gradient(135deg, {h_start} 0%, {h_end} 100%)
  - Accent / primary: {primary}  (use for badges, highlights, confirmation badge border, CTA button)
  - Section header bar: {section_header_bg} (dark)
  - Detail row odd bg: #F8FAFC
  - Detail row even bg: #FFFFFF
  - Detail label text: #64748B (slate-500)
  - Detail value text: #1E293B (slate-900)
  - Footer bg: #1E293B
  - Footer text: #94A3B8
  - Divider color: #E2E8F0

TYPOGRAPHY:
  - Font stack: 'Segoe UI', Arial, sans-serif
  - Body font-size: 15px, line-height: 1.6, color: #334155
  - Section headings: 11px, font-weight: 700, letter-spacing: 1.5px, UPPERCASE, color: #FFFFFF
  - Detail labels: 13px, font-weight: 600, color: #64748B, UPPERCASE, letter-spacing: 0.5px
  - Detail values: 15px, font-weight: 500, color: #1E293B

SPACING: Use padding: 20px 24px for content areas. Row padding: 12px 16px. Section gap: margin-bottom: 16px.

LOGO / BRANDING in header: Use exactly this for the logo area at top of header:
  {logo_html}
  Event badge label text: "{event_label_badge}"

═══════════════════════════════════════════════
STRUCTURE — build in this exact order:
═══════════════════════════════════════════════

⚠️ CRITICAL LAYOUT RULE:
The card is a SINGLE-COLUMN layout. Every <tr> inside the card has exactly ONE <td> that spans the full width.
The ONLY place with 2 columns is inside the Registration Details rows (label 40% / value 60%).
Do NOT split the outer card, header, greeting, section headers, QR, or footer into multiple columns.

1. OUTER WRAPPER
   - <table width="100%" style="background:#F0F4F8;padding:40px 16px;">
   - One <tr><td align="center"> — single cell, full width

2. CARD CONTAINER
   - <table width="100%" style="max-width:600px;background:#FFFFFF;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">
   - Every section is a <tr> with a SINGLE <td width="100%"> — full width, no side-by-side columns

3. HEADER SECTION (inside card)
   - Background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)
   - Padding: 40px 32px 32px
   - Top: small event label badge — pill shape, border: 1.5px solid #E94560, color: #E94560, font-size: 11px, letter-spacing: 2px, padding: 4px 14px, border-radius: 20px, UPPERCASE
   - Main: event name in large bold white text (28px, font-weight: 800, margin: 16px 0 8px)
   - Sub: "{form_context_title}" subtitle in #94A3B8, 15px
   - Bottom decorative bar: 3px high table row, background: linear-gradient(90deg, #E94560, #0f3460)

4. CONFIRMATION BADGE (between header and greeting)
   - Light green confirmation strip: background: #F0FDF4, border-left: 4px solid #22C55E
   - Padding: 14px 24px, font-size: 14px, color: #166534
   - Text: checkmark ✓ + "Đăng ký thành công! (Registration Confirmed!)" or similar

5. GREETING SECTION
   - Padding: 28px 32px 0
   - Warm greeting line using the name variable if available, else generic greeting
   - 1-2 sentences confirming receipt

6. REGISTRATION DETAILS SECTION
   - Section header row: background: #0f3460, padding: 10px 24px
     Text: bilingual heading e.g. "CHI TIẾT ĐĂNG KÝ / REGISTRATION DETAILS" in white, 11px, uppercase, letter-spacing: 1.5px
   - For EACH field in the FORM FIELDS list above: alternating row background (#F8FAFC / #FFFFFF)
     - TWO-COLUMN table row: left cell (40% width) = label, right cell (60%) = variable value
     - Left cell: the field's Label text from the FORM FIELDS list, UPPERCASE, 12px, color #64748B, font-weight: 600, padding: 12px 16px
       ⚠️ If bilingual: show BOTH languages in label cell, e.g. "HỌ VÀ TÊN / FULL NAME"
       ⚠️ MUST include a row for EVERY field listed in FORM FIELDS — do not skip any field
     - Right cell: use the exact variable syntax ${{{f.id}}} from the FORM FIELDS list, 15px, color #1E293B, font-weight: 500, padding: 12px 16px
     - Thin bottom border: 1px solid #E2E8F0 (skip on last row)
   - Bottom of section: 4px gradient bar (same as header decorative bar)

7. QR CODE SECTION — MUST come IMMEDIATELY after the Registration Details section, before closing message
   ⚠️ Place this section RIGHT HERE in the flow, not at the end of the email.
   - Table row containing a td with: background: #F8FAFC, border: 1px solid #E2E8F0, border-radius: 12px, margin: 24px 32px, padding: 24px, text-align: center
   - Section label: "MÃ QR CỦA BẠN (YOUR QR CODE)" — 11px, font-weight: 700, letter-spacing: 1.5px, uppercase, color: #0f3460
   - Instruction text: small grey text (13px, color #64748B) about showing QR at event entrance
   - QR image: use actual `<img src="cid:qrcode.png" alt="QR Code" style="width:200px;height:200px;display:block;margin:0 auto;border-radius:8px;border:1px solid #E2E8F0;" />` — do NOT use a div placeholder, use this exact img tag so the QR renders at this position

8. CLOSING MESSAGE — comes AFTER QR section
   - Padding: 24px 32px
   - 1-2 sentences of closing remarks, looking forward to seeing them at the event

9. FOOTER — the very last section
   - Background: #1E293B, padding: 28px 32px
   - Top: event name in white, 14px, font-weight: 600
   - Middle: copyright line in #94A3B8, 13px
   - Divider: 1px solid #334155, margin: 12px 0
   - Bottom: "{custom_footer}" in #64748B, 12px, font-style: italic

═══════════════════════════════════════════════
DARK MODE SUPPORT — REQUIRED:
═══════════════════════════════════════════════
Many email clients (Apple Mail, iOS Mail, Outlook 2019+) apply dark mode and invert or replace colors, making text unreadable. You MUST include dark mode protection.

Add a <style> block inside <head> with these exact rules:
  <style>
    /* Force light mode on supported clients */
    :root {{ color-scheme: light only; }}
    /* Prevent iOS Mail dark mode inversion */
    @media (prefers-color-scheme: dark) {{
      body, table, td, th, p, span, a, div {{
        background-color: inherit !important;
        color: inherit !important;
      }}
      /* Re-enforce all critical colors explicitly */
      .email-body {{ background-color: #F0F4F8 !important; }}
      .card {{ background-color: #FFFFFF !important; }}
      .header {{ background-color: #1a1a2e !important; }}
      .detail-label {{ color: #64748B !important; }}
      .detail-value {{ color: #1E293B !important; }}
      .footer-section {{ background-color: #1E293B !important; }}
      .footer-text {{ color: #94A3B8 !important; }}
    }}
  </style>

Also add <meta name="color-scheme" content="light only"> in <head>.

Add these helper classes (alongside inline styles — classes are for dark mode override only):
  - class="email-body" on the outermost <table>
  - class="card" on the card container <table>
  - class="header" on the header <td>
  - class="detail-label" on every label <td> in the registration rows
  - class="detail-value" on every value <td> in the registration rows
  - class="footer-section" on the footer <td>
  - class="footer-text" on footer text elements

⚠️ IMPORTANT: Keep ALL existing inline styles (style="...") — classes are ADDITIONS, not replacements. Both inline styles AND classes must be present together.

═══════════════════════════════════════════════
STRICT RULES:
═══════════════════════════════════════════════
- Return ONLY the raw HTML. No markdown fences, no explanation, no comments outside HTML.
- ALL CSS must be inline (style="...") PLUS the dark mode <style> block described above.
- Use table/tr/td for ALL layout — no div-based layout (email client compatibility).
- Every ${{uuid}} variable must appear exactly as written — never substitute with label text.
- Use {{event_name}} (curly braces, NO dollar sign) only if referencing the event name dynamically outside the header.
- border-radius on tables: use on the outer wrapper td, not on the table element itself for Outlook compat.
- For gradient backgrounds on table cells, use: background: #1a1a2e; (solid fallback first, then background-image: linear-gradient(...))
- Always specify explicit color and background-color on EVERY td, p, span, a element — never rely on inherited/default colors.

Generate the complete HTML email now:"""

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "openai/gpt-4o",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.4,
                    "max_tokens": 6000,
                },
            )
            resp.raise_for_status()
            result = resp.json()
            html = result["choices"][0]["message"]["content"].strip()

            if html.startswith("```"):
                lines = html.split("\n")
                html = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

            return GenerateEmailTemplateResponse(html=html, success=True)

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"OpenRouter error: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {str(e)}")
