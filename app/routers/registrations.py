"""
Registration lifecycle endpoints.

POST /registrations/from-submission
  Called by nexpo-public after creating a form_submissions row.
  Orchestrates: create registration → parse profile fields → send QR email.
  Replaces Directus flows db94c530, b14d0ff5, 0fe4a75a.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.registration_processor import process_form_submission

router = APIRouter()


class FromSubmissionRequest(BaseModel):
    submission_id: str
    form_id: str
    group_id: str | None = None


@router.post("/registrations/from-submission")
async def registrations_from_submission(req: FromSubmissionRequest):
    try:
        return await process_form_submission(
            submission_id=req.submission_id,
            form_id=req.form_id,
            group_id=req.group_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed: {e}")
