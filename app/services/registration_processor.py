"""
Process a newly created form_submissions row into a registration.

Replaces Directus flows:
- db94c530  "Trigger create form submissions registration"  — submission → registration
- b14d0ff5  "[Nexpo] Sync Submission → Registration Profile" — parse answers → fill profile
- 0fe4a75a  "Trigger create Registrations" + QR email         — via handle_registration_qr

Single entry point: `process_form_submission(submission_id, form_id, group_id=None)`.
"""
from __future__ import annotations

import logging
import re
import unicodedata

from app.services.directus import directus_get, directus_patch, directus_post
from app.services.notification_handlers import handle_registration_qr

logger = logging.getLogger(__name__)


# ── Answer parser (ported from Directus flow b14d0ff5 parse_fields) ──────────

def _normalize(value: object) -> str:
    s = unicodedata.normalize("NFD", str(value or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.lower().replace("_", " ").replace("-", " ")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _has_phrase(value: str, phrases: list[str]) -> bool:
    n = _normalize(value)
    return any(n == p or p in n for p in phrases)


_COMPANY = [
    "company", "company name", "ten cong ty", "cong ty", "organization",
    "organisation", "ten to chuc", "to chuc", "business", "business name",
    "enterprise", "doanh nghiep", "school", "truong", "university", "college",
]
_FULL_NAME = [
    "ho va ten", "ho ten", "full name", "fullname", "name", "ten day du",
    "ho ten day du", "your name", "ten nguoi tham du", "nguoi dang ky",
]
_FIRST_NAME = ["first name", "firstname", "ten", "given name"]
_LAST_NAME = ["last name", "lastname", "ho", "family name", "surname"]
_PHONE = [
    "dien thoai", "phone", "mobile", "tel", "so dt", "sdt", "zalo",
    "so dien thoai", "phone number", "contact number", "lien he",
]
_PHONE_RE = re.compile(r"^\+?[\d\s\-().]{9,}$")
# Field types that are semantically known — skip from fallback
_SKIP_TYPES = {
    "email", "company_block", "attendee_type", "dietary", "consent",
    "yesno", "decision", "file", "image", "rating", "signature",
    "select", "multiselect", "collection_picker", "date", "datetime", "number",
}


def parse_profile_fields(answers: list[dict]) -> dict:
    """Extract full_name / email / phone_number / company from form answers.

    Strategy (4-pass):
      1. Semantic field types: email→email, company_block→company
      2. Explicit flags: is_email_contact=true
      3. Name heuristics: keyword matching on field.name
      4. Fallback: first unmatched input/textarea → full_name
    """
    full_name = first_name = last_name = phone = email = company = None
    # Track unmatched text fields for fallback (field_type in input/textarea)
    unmatched_text_fields: list[str] = []
    matched_field_ids: set[str] = set()

    for ans in answers:
        field = ans.get("field") or {}
        val_raw = ans.get("value")
        if not val_raw or not str(val_raw).strip():
            continue
        val = str(val_raw).strip()
        field_id = field.get("id", "")
        fname = _normalize(field.get("name", ""))
        ftype = (field.get("type") or "").lower().strip()
        is_email_flag = field.get("is_email_contact") is True

        # Pass 1+2: semantic types + explicit flags
        if not email and (ftype == "email" or is_email_flag):
            email = val
            matched_field_ids.add(field_id)
            continue
        if ftype == "company_block":
            # company_block stores JSON with sub-fields; extract company name
            if not company:
                company = _extract_company_from_block(val)
            matched_field_ids.add(field_id)
            continue

        # Pass 3: name heuristics
        if not phone and (_has_phrase(fname, _PHONE) or _PHONE_RE.match(val)):
            phone = val
            matched_field_ids.add(field_id)
            continue
        if _has_phrase(fname, _COMPANY):
            if not company:
                company = val
            matched_field_ids.add(field_id)
            continue
        if not full_name and _has_phrase(fname, _FULL_NAME):
            full_name = val
            matched_field_ids.add(field_id)
            continue
        if not first_name and any(fname == p for p in _FIRST_NAME):
            first_name = val
            matched_field_ids.add(field_id)
            continue
        if not last_name and any(fname == p for p in _LAST_NAME):
            last_name = val
            matched_field_ids.add(field_id)
            continue

        # Detect email by value pattern (@ in value, not yet matched)
        if not email and "@" in val and ftype in ("input", "textarea", ""):
            email = val
            matched_field_ids.add(field_id)
            continue

        # Collect unmatched text fields for fallback
        if ftype not in _SKIP_TYPES and field_id not in matched_field_ids:
            unmatched_text_fields.append(val)

    # Compose full_name from parts
    if not full_name:
        if first_name and last_name:
            full_name = f"{last_name} {first_name}".strip()
        else:
            full_name = first_name or last_name

    # Pass 4: fallback — if still no full_name, use first unmatched text field
    if not full_name and unmatched_text_fields:
        full_name = unmatched_text_fields[0]

    payload: dict = {}
    if full_name:
        payload["full_name"] = full_name
    if phone:
        payload["phone_number"] = phone
    if email:
        payload["email"] = email
    # Note: company is parsed but NOT saved to registrations (no column).
    # It remains accessible via form_answers for display purposes.
    return payload


def _extract_company_from_block(val: str) -> str | None:
    """Parse company_block JSON value — extracts company_name sub-field."""
    import json
    try:
        obj = json.loads(val)
        if isinstance(obj, dict):
            return obj.get("company_name") or obj.get("company") or obj.get("name") or None
    except (json.JSONDecodeError, TypeError):
        pass
    # Plain string — return as-is if not empty
    return val if val and not val.startswith("{") else None


# ── Main orchestrator ────────────────────────────────────────────────────────

async def process_form_submission(
    submission_id: str,
    form_id: str,
    group_id: str | None = None,
) -> dict:
    """
    Full pipeline replacing 3 Directus flows.

    1. Read form (is_registration?, event_id, tenant_id)
    2. If is_registration=true: create `registrations` row linked to submission
    3. Parse answers → patch registration profile (full_name/email/phone)
    4. Dispatch QR email via existing handler
    """
    # 1. Form config
    form_resp = await directus_get(
        f"/items/forms/{form_id}?fields[]=id,is_registration,event_id,tenant_id"
    )
    form = form_resp.get("data") or {}
    if not form:
        raise ValueError(f"Form {form_id} not found")
    if not form.get("is_registration"):
        return {"status": "skipped", "reason": "form.is_registration=false"}

    event_id = form.get("event_id")
    tenant_id = form.get("tenant_id")

    # 2. Create registration
    reg_payload: dict = {
        "submissions": submission_id,
        "event_id": event_id,
        "tenant_id": tenant_id,
    }
    if group_id:
        reg_payload["group_id"] = group_id
    reg_resp = await directus_post("/items/registrations", reg_payload)
    registration_id = (reg_resp.get("data") or {}).get("id")
    if not registration_id:
        raise RuntimeError(f"Failed to create registration for submission {submission_id}")

    # 3. Parse profile fields from submission answers
    try:
        ans_resp = await directus_get(
            f"/items/form_answers"
            f"?filter[submission][_eq]={submission_id}"
            f"&fields[]=value,field.name,field.type,field.is_email_contact"
            f"&limit=-1"
        )
        answers = ans_resp.get("data") or []
        profile = parse_profile_fields(answers)
        if profile:
            await directus_patch(f"/items/registrations/{registration_id}", profile)
    except Exception as exc:
        logger.warning(f"[registration_processor] parse/patch failed: {exc}")

    # 4. Send QR email (legacy handler — uses form.template_email + answers)
    try:
        email_result = await handle_registration_qr(
            registration_id=str(registration_id),
            triggered_by="form_submission",
        )
    except Exception as exc:
        logger.error(f"[registration_processor] QR email failed for {registration_id}: {exc}")
        email_result = {"status": "error", "error": str(exc)}

    return {
        "status": "ok",
        "registration_id": registration_id,
        "event_id": event_id,
        "email": email_result,
    }
