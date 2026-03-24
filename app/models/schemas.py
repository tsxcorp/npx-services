from pydantic import BaseModel
from typing import Optional, List


# ── QR ────────────────────────────────────────────────────────────────────────

class QRCodeRequest(BaseModel):
    text: str

class QRCodeResponse(BaseModel):
    qr_code_base64: str
    file_name: str
    success: bool
    message: str


# ── Email ─────────────────────────────────────────────────────────────────────

class EmailRequest(BaseModel):
    from_email: str
    to: str
    subject: str
    html: str
    content_qr: str

class EmailResponse(BaseModel):
    success: bool
    message: str
    message_id: str = None

class BulkEmailRecipient(BaseModel):
    email: str
    content_qr: str
    full_name: Optional[str] = None

class BulkEmailRequest(BaseModel):
    from_email: Optional[str] = None
    sender_name: Optional[str] = "Nexpo"
    subject: str
    html: str
    recipients: List[BulkEmailRecipient]

class BulkEmailResponse(BaseModel):
    sent: int
    failed: int
    errors: List[str]

class PlainEmailRequest(BaseModel):
    to: str
    subject: str
    html: str
    from_email: Optional[str] = None
    sender_name: Optional[str] = "Nexpo"


# ── Meeting Notifications ─────────────────────────────────────────────────────

class MeetingNotificationRequest(BaseModel):
    meeting_id: str
    trigger: str   # "scheduled" | "confirmed" | "cancelled"
    event_name: Optional[str] = None


# ── AI Matching ───────────────────────────────────────────────────────────────

class MatchRunRequest(BaseModel):
    event_id: int
    job_requirement_id: Optional[str] = None
    exhibitor_id: Optional[str] = None
    score_threshold: float = 0.5
    max_candidates_per_job: int = 40
    keyword_threshold: float = 0.15
    rescore_pending: bool = True
    ai_model: str = "openai/gpt-4o-mini"

class MatchSuggestion(BaseModel):
    job_requirement_id: str
    registration_id: str
    exhibitor_id: str
    score: float
    matched_criteria: dict
    ai_reasoning: str

class MatchRunResponse(BaseModel):
    success: bool
    message: str
    suggestions_created: int
    suggestions: List[MatchSuggestion] = []


# ── Email Template Generation ─────────────────────────────────────────────────

class EmailTemplateField(BaseModel):
    id: str
    label: str
    type: str

class GenerateEmailTemplateRequest(BaseModel):
    event_name: str
    form_purpose: Optional[str] = "registration"
    is_registration: bool = True
    language: str = "bilingual"
    tone: str = "professional"
    fields: List[EmailTemplateField] = []

class GenerateEmailTemplateResponse(BaseModel):
    html: str
    success: bool
