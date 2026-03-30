# nexpo-services — Backlog

> Không commit lên git. Claude tự cập nhật cuối mỗi session.
> Cross-project decisions: xem `nexpo-platform/.claude/PROGRESS.md`

---

## ✅ Đã làm xong

### [2026-03-28] Candidate Interview Schedule — ICS Calendar Attachment

**`handle_candidate_interview_schedule()`** cập nhật:
- Thêm file `.ics` đính kèm email chứa tất cả lịch phỏng vấn
- User mở file → tất cả meetings được add vào calendar app (Google, Apple, Outlook)
- Mỗi event có VALARM reminder 15 phút trước

**`ics_service.py` — thêm `generate_combined_ics()`:**
- Tạo 1 file ICS chứa nhiều VEVENT
- Input: list events với `meeting_id`, `summary`, `dtstart`, `duration_minutes`, `location`
- Output: UTF-8 bytes ready to attach

**Email template update:**
- Thêm địa điểm sự kiện + Google Maps link
- Thêm hotline liên hệ
- Thêm link JD Google Drive
- Note về file .ics đính kèm

### [2026-03-27] Notification Architecture Refactoring

**Refactor `main.py` thành modules** (DONE):
- `app/config.py` — env vars + ai_semaphore
- `app/models/schemas.py` — tất cả Pydantic models
- `app/services/directus.py` — directus_get/post/patch, create_notification, resolve helpers
- `app/services/qr_service.py` — generate_qr_code_bytes, inject_qr_extras, append_qr_cid_to_html
- `app/services/mailgun.py` — send_mailgun, meeting_notification_html
- `app/services/ics_service.py` — generate_meeting_ics, generate_combined_ics
- `app/services/matching_service.py` — score_match_with_gemini, keyword filters
- `app/services/notification_handlers.py` — all notification handlers
- `app/services/scheduler.py` — APScheduler instance + send_meeting_reminders
- `app/routers/qr.py`, `email.py`, `matching.py`, `notify.py`, `meeting_notifs.py`, `templates.py`
- `main.py` — slim entry point (app init + CORS + lifespan + router includes)

**Notification handlers** (`notification_handlers.py`):
- `handle_meeting()` — meeting email + in-app (scheduled/confirmed/cancelled)
- `handle_registration_qr()` — QR email + activity log
- `handle_order_facility_created()` — in-app organizer
- `handle_ticket_support_created()` — in-app organizer
- `handle_lead_captured()` — in-app exhibitor
- `handle_candidate_interview_schedule()` — bulk interview schedule email + ICS

### [2026-03] CORS fix
- Thêm `portal.nexpo.vn`, `insights.nexpo.vn`, ports 3002/3003 vào allow_origins

### [2026-03] AI Matching — Token optimization

**`POST /match/run` thay đổi:**
- `KEYWORD_THRESHOLD`: `0.05` → `0.15` — loại bỏ nhiều cặp không liên quan trước khi gọi AI
- `MAX_CANDIDATES_PER_JOB = 40` — sau keyword filter, sort by keyword score desc, chỉ lấy top-40 vào AI
- Kết quả: từ ~362 AI calls/job → tối đa 40 calls/job (giảm ~90%)

### [2026-03] Meeting Notification System

**`POST /meeting-notification`** — full notification engine:
- Nhận `meeting_id + trigger`, tự resolve email + user_id từ Directus
- Email song ngữ vi/en cho cả 3 triggers
- In-app notification qua Directus `notifications` collection

---

## 📋 Backlog

- [ ] `POST /send-email-with-qr`: thêm param `link_type: "registration"|"ticket"` — ảnh hưởng URL trong `inject_qr_extras()`
- [ ] APScheduler: expire pending ticket orders mỗi 5 phút (cần `apscheduler==3.10.4`)
- [ ] `POST /generate-email-template`: thêm case `form_purpose = "ticket_confirmation"`
- [ ] Bug B-1: Meeting reminder scheduler chỉ filter `confirmed`, bỏ sót `scheduled`
