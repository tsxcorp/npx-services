"""
Meeting notification endpoint — delegates to unified handler.
"""
from fastapi import APIRouter, HTTPException
from app.models.schemas import MeetingNotificationRequest
from app.config import DIRECTUS_ADMIN_TOKEN
from app.services.notification_handlers import handle_meeting

router = APIRouter()


@router.post("/meeting-notification")
async def send_meeting_notification(request: MeetingNotificationRequest):
    """Send meeting notification emails and in-app notifications."""
    if not DIRECTUS_ADMIN_TOKEN:
        raise HTTPException(status_code=500, detail="DIRECTUS_ADMIN_TOKEN not configured")
    try:
        result = await handle_meeting(
            meeting_id=request.meeting_id,
            trigger=request.trigger,
            event_name=request.event_name,
        )
        return {"success": True, "trigger": request.trigger, **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Meeting notification error: {str(e)}")
