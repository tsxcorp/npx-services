"""
APScheduler setup and scheduled jobs.
Import `scheduler` and call scheduler.start() / scheduler.shutdown() from lifespan.
"""
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.config import DIRECTUS_ADMIN_TOKEN, MAILGUN_API_KEY, MAILGUN_DOMAIN, MAILGUN_API_URL, PORTAL_URL
from app.services.directus import directus_get, directus_patch
from app.services.directus import resolve_visitor_email, resolve_exhibitor_email
from app.services.mailgun import send_mailgun, meeting_notification_html
import httpx

scheduler = AsyncIOScheduler()


async def send_meeting_reminders() -> None:
    """
    APScheduler job — runs every hour.
    Finds confirmed meetings scheduled 23-25h from now with reminder_sent IS NULL.
    Sends bilingual reminder emails to exhibitor + visitor, then marks reminder_sent.
    """
    if not DIRECTUS_ADMIN_TOKEN:
        return

    now = datetime.now(timezone.utc)
    window_start = (now + timedelta(hours=23)).isoformat()
    window_end = (now + timedelta(hours=25)).isoformat()

    try:
        resp = await directus_get(
            "/items/meetings"
            f"?filter[status][_eq]=confirmed"
            f"&filter[scheduled_at][_gte]={window_start}"
            f"&filter[scheduled_at][_lte]={window_end}"
            f"&filter[reminder_sent][_null]=true"
            "&fields[]=id,scheduled_at,location,meeting_category,event_id,"
            "registration_id,exhibitor_id,job_requirement_id.job_title"
            "&limit=100"
        )
        meetings = resp.get("data", [])
    except Exception:
        return

    for meeting in meetings:
        meeting_id = meeting.get("id")
        event_id = str(meeting.get("event_id", ""))
        registration_id = str(meeting.get("registration_id", ""))
        exhibitor_id = str(meeting.get("exhibitor_id", ""))
        meeting_category = meeting.get("meeting_category") or "talent"
        job_title = (meeting.get("job_requirement_id") or {}).get("job_title") or "vị trí này / this position"
        tab = "hiring" if meeting_category == "talent" else "business"
        portal_url = f"{PORTAL_URL}/meetings?event={event_id}&tab={tab}"

        scheduled_at = meeting.get("scheduled_at", "")
        try:
            dt = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
            time_str = dt.strftime("%d/%m/%Y %H:%M")
        except Exception:
            time_str = scheduled_at

        location_str = meeting.get("location") or ""

        visitor_email, visitor_name = await resolve_visitor_email(registration_id)
        exhibitor_email, company_name = await resolve_exhibitor_email(exhibitor_id, event_id)

        reminder_body = [
            "<strong>Nhắc nhở / Reminder:</strong> Cuộc họp của bạn sẽ diễn ra vào ngày mai.",
            "<strong>Reminder:</strong> Your meeting is scheduled for tomorrow.",
            f"<strong>Vị trí / Position:</strong> {job_title}",
        ]
        if time_str:
            reminder_body.append(f"<strong>Thời gian / When:</strong> {time_str}")
        if location_str:
            reminder_body.append(f"<strong>Địa điểm / Where:</strong> {location_str}")

        sent_count = 0

        if exhibitor_email:
            subject = f"[Nexpo] Nhắc lịch gặp mặt ngày mai / Meeting reminder tomorrow — {visitor_name or 'Ứng viên'}"
            html = meeting_notification_html(
                "Nhắc lịch gặp mặt / Meeting Reminder",
                reminder_body + ["Vui lòng chuẩn bị trước để buổi gặp mặt diễn ra suôn sẻ. / Please prepare in advance for a smooth meeting."],
                cta_label="Xem lịch họp / View Meeting", cta_url=portal_url,
            )
            if await send_mailgun(exhibitor_email, subject, html):
                sent_count += 1

        if visitor_email:
            subject = f"[Nexpo] Nhắc lịch gặp mặt ngày mai / Meeting reminder tomorrow — {company_name or 'Exhibitor'}"
            html = meeting_notification_html(
                "Nhắc lịch gặp mặt / Meeting Reminder",
                reminder_body + ["Chúc bạn buổi gặp mặt thành công! / We look forward to seeing you!"],
            )
            if await send_mailgun(visitor_email, subject, html):
                sent_count += 1

        if sent_count > 0 and meeting_id:
            try:
                await directus_patch(
                    f"/items/meetings/{meeting_id}",
                    {"reminder_sent": datetime.now(timezone.utc).isoformat()},
                )
            except Exception:
                pass
