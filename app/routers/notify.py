"""
Unified notification endpoint.

POST /notify
  { "type": "<notification_type>", "context": { ...type-specific fields } }

All notification logic lives in app/services/notification_handlers.py.
FE apps should use this endpoint — POST /meeting-notification is kept as a legacy alias.

Supported types:
  meeting.scheduled       context: { meeting_id }
  meeting.confirmed       context: { meeting_id }
  meeting.cancelled       context: { meeting_id }
  order.facility.created  context: { order_id, event_id }
  ticket.support.created  context: { ticket_id, event_id }
  lead.captured           context: { user_id, attendee_name, attendee_email, attendee_company, event_id }
"""
from fastapi import APIRouter, HTTPException
from app.models.schemas import NotifyRequest
from app.services.notification_handlers import (
    handle_meeting,
    handle_order_facility_created,
    handle_ticket_support_created,
    handle_lead_captured,
)

router = APIRouter()

MEETING_TRIGGERS = {"meeting.scheduled", "meeting.confirmed", "meeting.cancelled"}


@router.post("/notify")
async def notify(request: NotifyRequest):
    t = request.type
    ctx = request.context

    try:
        if t in MEETING_TRIGGERS:
            meeting_id = ctx.get("meeting_id")
            if not meeting_id:
                raise HTTPException(status_code=422, detail="context.meeting_id required")
            trigger = t.split(".", 1)[1]  # "scheduled" | "confirmed" | "cancelled"
            result = await handle_meeting(meeting_id=str(meeting_id), trigger=trigger)

        elif t == "order.facility.created":
            order_id = ctx.get("order_id")
            event_id = ctx.get("event_id")
            if not order_id or not event_id:
                raise HTTPException(status_code=422, detail="context.order_id and context.event_id required")
            result = await handle_order_facility_created(order_id=str(order_id), event_id=str(event_id))

        elif t == "ticket.support.created":
            ticket_id = ctx.get("ticket_id")
            event_id = ctx.get("event_id")
            if not ticket_id or not event_id:
                raise HTTPException(status_code=422, detail="context.ticket_id and context.event_id required")
            result = await handle_ticket_support_created(ticket_id=str(ticket_id), event_id=str(event_id))

        elif t == "lead.captured":
            user_id = ctx.get("user_id", "")
            if not user_id:
                raise HTTPException(status_code=422, detail="context.user_id required")
            result = await handle_lead_captured(
                user_id=str(user_id),
                attendee_name=str(ctx.get("attendee_name", "Khách tham quan")),
                attendee_email=str(ctx.get("attendee_email", "")),
                attendee_company=str(ctx.get("attendee_company", "")),
                event_id=str(ctx.get("event_id", "")),
            )

        else:
            raise HTTPException(status_code=422, detail=f"Unknown notification type: {t!r}")

        return {"ok": True, "type": t, **result}

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Notification error: {str(e)}")
