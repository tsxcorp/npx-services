"""
AI vision service for floor plan zone detection.
Fallback chain: Gemini direct → OpenRouter → Novita.ai
"""
import json
import base64
import logging
import httpx
from app.config import GOOGLE_AI_API_KEY, OPENROUTER_API_KEY, NOVITA_API_KEY
from app.models.schemas import DetectZonesResponse

logger = logging.getLogger(__name__)

ZONE_DETECTION_PROMPT = """You are analyzing a floor plan / venue layout image for an exhibition management system.

Detect all distinct zones, halls, rooms, and labeled areas visible in the image.

For each zone, provide:
- name: The text label visible on the zone (or descriptive name if unlabeled)
- type: One of: exhibition_hall, meeting_room, lobby, entrance, exit, parking, stage, food_court, restroom, storage, corridor, outdoor, other
- bounds: Bounding box as percentages of image dimensions (0-100)
  - x_pct: left edge percentage from image left
  - y_pct: top edge percentage from image top
  - w_pct: width as percentage of image width
  - h_pct: height as percentage of image height
- confidence: 0.0 to 1.0 how confident you are in this detection

Return ONLY valid JSON with this exact structure:
{
  "zones": [
    {"name": "...", "type": "...", "bounds": {"x_pct": 0, "y_pct": 0, "w_pct": 0, "h_pct": 0}, "confidence": 0.0}
  ],
  "scale_hint": {"estimated_total_area_sqm": null}
}

Rules:
- Only detect zones large enough to contain exhibition booths (skip tiny labels, legends, compass roses)
- Minimum zone size: 5% of image in either dimension
- Prefer rectangular bounds even if zone shape is irregular
- If text labels are visible, use them as zone names
- Estimate total venue area if any scale indicators are visible
"""


async def _fetch_image_as_base64(image_url: str) -> tuple[str, str]:
    """Download image and return (base64_data, mime_type)."""
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(image_url)
        resp.raise_for_status()
        mime = resp.headers.get("content-type", "image/png")
        return base64.b64encode(resp.content).decode(), mime


async def _try_gemini(image_b64: str, mime_type: str) -> DetectZonesResponse:
    """Call Gemini direct API."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GOOGLE_AI_API_KEY)
    image_bytes = base64.b64decode(image_b64)

    parts = [
        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        types.Part.from_text(text=ZONE_DETECTION_PROMPT),
    ]

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=parts,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )

    data = json.loads(response.text)
    return DetectZonesResponse(**data)


async def _try_openai_compatible(
    api_key: str,
    base_url: str,
    model: str,
    image_b64: str,
    mime_type: str,
    use_json_format: bool = True,
) -> DetectZonesResponse:
    """Call OpenAI-compatible vision API (OpenRouter, Novita.ai, etc.)."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: dict = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
                    },
                    {"type": "text", "text": ZONE_DETECTION_PROMPT},
                ],
            }
        ],
        "temperature": 0.2,
    }
    if use_json_format:
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=60) as http:
        resp = await http.post(f"{base_url}/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
        result = resp.json()

    raw_text = result["choices"][0]["message"]["content"]

    # Try direct JSON parse; fallback to extracting JSON block from text
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        # Extract JSON from markdown code block or mixed text
        import re
        match = re.search(r'\{[\s\S]*"zones"[\s\S]*\}', raw_text)
        if not match:
            raise ValueError(f"Could not extract JSON from AI response")
        data = json.loads(match.group())

    return DetectZonesResponse(**data)


async def detect_zones_from_image(image_url: str | None, image_base64: str | None) -> DetectZonesResponse:
    """
    Detect zones from floor plan image using AI vision.
    Fallback chain: Gemini direct → OpenRouter (Gemini) → Novita.ai (Gemini)
    """
    # Prepare image data
    if image_url:
        img_b64, mime = await _fetch_image_as_base64(image_url)
    elif image_base64:
        img_b64, mime = image_base64, "image/png"
    else:
        raise ValueError("Either image_url or image_base64 must be provided")

    # Build provider chain based on available keys
    providers: list[tuple[str, object]] = []
    if GOOGLE_AI_API_KEY:
        providers.append(("Gemini Direct", lambda: _try_gemini(img_b64, mime)))
    if NOVITA_API_KEY:
        providers.append(("Novita.ai", lambda: _try_openai_compatible(
            NOVITA_API_KEY, "https://api.novita.ai/openai",
            "qwen/qwen2.5-vl-72b-instruct", img_b64, mime,
        )))
    if OPENROUTER_API_KEY:
        providers.append(("OpenRouter", lambda: _try_openai_compatible(
            OPENROUTER_API_KEY, "https://openrouter.ai/api/v1",
            "google/gemini-2.0-flash-001", img_b64, mime,
        )))

    if not providers:
        raise ValueError("No AI provider configured. Set GOOGLE_AI_API_KEY, OPENROUTER_API_KEY, or NOVITA_API_KEY.")

    # Try each provider in order
    last_error = None
    for name, call_fn in providers:
        try:
            logger.info(f"Trying zone detection via {name}...")
            result = await call_fn()
            logger.info(f"Zone detection succeeded via {name}: {len(result.zones)} zones")
            return result
        except Exception as e:
            logger.warning(f"{name} failed: {e}")
            last_error = e
            continue

    raise ValueError(f"All AI providers failed. Last error: {last_error}")
