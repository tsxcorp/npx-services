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
import logging

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def expire_pending_orders() -> None:
    """
    APScheduler job — runs every 5 minutes.
    Finds ticket_orders that have been in 'pending' status for > 30 minutes.
    Marks them as 'cancelled' and rolls back quantity_sold on ticket_classes.
    """
    if not DIRECTUS_ADMIN_TOKEN:
        return

    now = datetime.now(timezone.utc)
    expire_before = (now - timedelta(minutes=30)).isoformat()

    try:
        resp = await directus_get(
            "/items/ticket_orders"
            "?filter[status][_eq]=pending"
            f"&filter[date_created][_lt]={expire_before}"
            "&fields[]=id,ticket_class_id,quantity"
            "&limit=100"
        )
        orders = resp.get("data", [])
    except Exception as exc:
        logger.warning("[expire_orders] Failed to fetch pending orders: %s", exc)
        return

    for order in orders:
        order_id = order.get("id")
        ticket_class_id = order.get("ticket_class_id")
        quantity = int(order.get("quantity") or 1)

        try:
            # Mark order as cancelled
            await directus_patch(f"/items/ticket_orders/{order_id}", {"status": "cancelled"})
            logger.info("[expire_orders] Cancelled order %s", order_id)
        except Exception as exc:
            logger.error("[expire_orders] Failed to cancel order %s: %s", order_id, exc)
            continue

        if ticket_class_id:
            try:
                # Rollback: decrement quantity_sold
                tc_resp = await directus_get(f"/items/ticket_classes/{ticket_class_id}?fields[]=quantity_sold")
                current_sold = int((tc_resp.get("data") or {}).get("quantity_sold") or 0)
                new_sold = max(0, current_sold - quantity)
                await directus_patch(
                    f"/items/ticket_classes/{ticket_class_id}",
                    {"quantity_sold": new_sold},
                )
                logger.info("[expire_orders] Rolled back qty_sold for class %s (%d → %d)", ticket_class_id, current_sold, new_sold)
            except Exception as exc:
                logger.error("[expire_orders] Failed to rollback inventory for %s: %s", ticket_class_id, exc)




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
