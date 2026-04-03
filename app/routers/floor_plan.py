"""Floor plan AI-powered zone detection endpoint."""
from fastapi import APIRouter, HTTPException
from app.config import GOOGLE_AI_API_KEY
from app.models.schemas import DetectZonesRequest, DetectZonesResponse
from app.services.gemini_service import detect_zones_from_image

router = APIRouter(prefix="/floor-plan", tags=["floor-plan"])


@router.post("/detect-zones", response_model=DetectZonesResponse)
async def detect_zones(request: DetectZonesRequest):
    """Analyze a floor plan image with Gemini vision to detect named zones/halls."""
    if not GOOGLE_AI_API_KEY:
        raise HTTPException(status_code=503, detail="Google AI API key not configured")

    if not request.image_url and not request.image_base64:
        raise HTTPException(status_code=400, detail="Either image_url or image_base64 must be provided")

    try:
        result = await detect_zones_from_image(request.image_url, request.image_base64)
        return result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Zone detection failed: {str(e)}")
