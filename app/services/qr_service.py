"""QR code generation and HTML injection utilities."""
import io
import re
import qrcode


def generate_qr_code_bytes(content_qr: str) -> bytes:
    """Generate a QR code PNG from content_qr and return raw bytes."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(content_qr)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer.getvalue()


def inject_qr_extras(html: str, content_qr: str, link_type: str = "registration") -> str:
    """
    Inject UUID display text + Insight Hub button right after the QR img tag.
    link_type: "registration" → insights.nexpo.vn/{content_qr}
                "ticket"       → insights.nexpo.vn/ticket/{content_qr}
    If the extras are already injected (idempotent), skip.
    """
    if link_type == "ticket":
        insight_url = f"https://insights.nexpo.vn/ticket/{content_qr}"
        label = "Ticket ID"
    else:
        insight_url = f"https://insights.nexpo.vn/{content_qr}"
        label = "Mã đăng ký / Registration ID"

    if insight_url in html:
        return html  # already injected

    extras = (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:12px;">'
        '<tr><td align="center">'
        f'<p style="margin:0 0 4px;font-size:11px;font-weight:700;letter-spacing:1px;'
        f'text-transform:uppercase;color:#64748B;font-family:\'Segoe UI\',Arial,sans-serif;">'
        f'{label}</p>'
        f'<p style="margin:0;font-size:13px;font-family:\'Courier New\',monospace;'
        f'color:#1E293B;background:#F1F5F9;padding:6px 14px;border-radius:6px;'
        f'letter-spacing:0.5px;display:inline-block;">{content_qr}</p>'
        '</td></tr>'
        '<tr><td align="center" style="padding-top:20px;">'
        f'<a href="{insight_url}" target="_blank" '
        'style="display:inline-block;background:linear-gradient(135deg,#1a1a2e 0%,#0f3460 100%);'
        'color:#FFFFFF;text-decoration:none;font-size:14px;font-weight:700;'
        'font-family:\'Segoe UI\',Arial,sans-serif;padding:12px 28px;border-radius:8px;'
        'letter-spacing:0.3px;">'
        'Access Insight Hub&nbsp;|&nbsp;Truy c&#7853;p C&#7893;ng th&#244;ng tin s&#7921; ki&#7879;n'
        '</a>'
        '</td></tr>'
        '</table>'
    )

    qr_img_pattern = re.compile(
        r'(<img[^>]*src=["\']cid:qrcode\.png["\'][^>]*/?>)',
        re.IGNORECASE,
    )
    match = qr_img_pattern.search(html)
    if match:
        insert_pos = match.end()
        return html[:insert_pos] + extras + html[insert_pos:]

    if '</body>' in html:
        return html.replace('</body>', f'{extras}</body>', 1)
    return html + extras


def append_qr_cid_to_html(html: str) -> str:
    """
    Append <img src="cid:qrcode.png"> to HTML.
    If template already has exactly one CID tag, return as-is.
    """
    qr_pattern = re.compile(
        r'<(?:div[^>]*>\s*)?<img[^>]*src=["\']cid:qrcode\.png["\'][^>]*/?>(?:\s*</div>)?',
        re.IGNORECASE,
    )
    matches = qr_pattern.findall(html)
    if len(matches) > 1:
        html_stripped = qr_pattern.sub('', html)
        first_tag = matches[0]
        if '</body>' in html_stripped:
            return html_stripped.replace('</body>', f'{first_tag}</body>', 1)
        elif '</html>' in html_stripped:
            return html_stripped.replace('</html>', f'{first_tag}</html>', 1)
        return html_stripped + first_tag
    if 'cid:qrcode.png' in html:
        return html
    qr_img_tag = (
        '<div style="text-align:center;margin:24px 0;">'
        '<img src="cid:qrcode.png" alt="QR Code" '
        'style="width:200px;height:200px;border:1px solid #ccc;border-radius:8px;" />'
        '</div>'
    )
    if '</body>' in html:
        return html.replace('</body>', f'{qr_img_tag}</body>', 1)
    elif '</html>' in html:
        return html.replace('</html>', f'{qr_img_tag}</html>', 1)
    return html + qr_img_tag
