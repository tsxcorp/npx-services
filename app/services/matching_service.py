"""AI and keyword-based matching logic."""
import json
import httpx
from typing import List
from app.config import OPENROUTER_API_KEY, ai_semaphore


async def score_match_with_gemini(job: dict, visitor_profile: dict, model: str = "openai/gpt-4o-mini") -> dict:
    """Use OpenRouter to score how well a visitor matches a job requirement."""
    if not OPENROUTER_API_KEY:
        return _simple_score_match(job, visitor_profile)

    prompt = f"""You are a hiring assistant. Score how well this job seeker matches the job requirement.

JOB REQUIREMENT:
- Title: {job.get('job_title', 'N/A')}
- Description: {job.get('description', 'N/A')}
- Requirements: {job.get('requirements', 'N/A')}
- Skills needed: {json.dumps(job.get('skills', []))}
- Experience level: {job.get('experience_level', 'N/A')}
- Employment type: {job.get('employment_type', 'N/A')}

JOB SEEKER PROFILE:
{json.dumps(visitor_profile, ensure_ascii=False, indent=2)}

Respond ONLY with valid JSON in this exact format:
{{
  "score": <float 0.0-1.0>,
  "matched_criteria": {{
    "skills_match": <float 0.0-1.0>,
    "experience_match": <float 0.0-1.0>,
    "role_match": <float 0.0-1.0>
  }},
  "reasoning": "<1-2 sentence explanation>"
}}"""

    try:
        async with ai_semaphore:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.1,
                        "max_tokens": 512,
                    },
                )
                resp.raise_for_status()
                result = resp.json()
                text = result["choices"][0]["message"]["content"].strip()
                if text.startswith("```"):
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                parsed = json.loads(text.strip())
                return {
                    "score": float(parsed.get("score", 0.5)),
                    "matched_criteria": parsed.get("matched_criteria", {}),
                    "ai_reasoning": parsed.get("reasoning", ""),
                }
    except Exception:
        return _simple_score_match(job, visitor_profile)


def _simple_score_match(job: dict, visitor_profile: dict) -> dict:
    """Fallback: keyword-based matching when OpenRouter unavailable."""
    job_text = " ".join([
        str(job.get("job_title", "")),
        str(job.get("description", "")),
        str(job.get("requirements", "")),
        " ".join(job.get("skills", []) or []),
    ]).lower()

    profile_text = json.dumps(visitor_profile, ensure_ascii=False).lower()

    job_words = set(job_text.split())
    profile_words = set(profile_text.split())
    stopwords = {"the", "a", "an", "and", "or", "for", "to", "of", "in", "is", "are", "với", "và", "của"}
    job_words -= stopwords
    profile_words -= stopwords

    if not job_words:
        score = 0.5
    else:
        overlap = len(job_words & profile_words)
        score = min(overlap / max(len(job_words), 1) * 2, 1.0)

    return {
        "score": round(score, 2),
        "matched_criteria": {"keyword_overlap": round(score, 2)},
        "ai_reasoning": f"Keyword-based score: {round(score * 100)}% overlap (OpenRouter key not configured)",
    }


def keyword_prefilter_score(job: dict, profile: dict) -> float:
    """Fast keyword overlap check before calling AI. Returns 0.0-1.0."""
    job_text = " ".join(filter(None, [
        str(job.get("job_title") or ""),
        str(job.get("description") or ""),
        str(job.get("skills") or ""),
        str(job.get("requirements") or ""),
        str(job.get("employment_type") or ""),
        str(job.get("experience_level") or ""),
    ])).lower()
    candidate_text = " ".join(str(v) for v in profile.values() if v).lower()
    if not job_text or not candidate_text:
        return 0.0
    job_words = set(job_text.split())
    candidate_words = set(candidate_text.split())
    if not job_words:
        return 0.0
    overlap = job_words & candidate_words
    return len(overlap) / len(job_words)


async def extract_visitor_profile(submission: dict, matching_fields: List[dict]) -> dict:
    """Extract visitor profile from form submission answers."""
    profile = {}
    answers = submission.get("answers", []) or []
    for answer in answers:
        field_id = None
        if isinstance(answer.get("field"), dict):
            field_id = answer["field"].get("id")
        elif isinstance(answer.get("field"), str):
            field_id = answer["field"]

        matching_field = next((f for f in matching_fields if str(f.get("id")) == str(field_id)), None)
        if matching_field and matching_field.get("use_for_matching"):
            attr = matching_field.get("matching_attribute", "other")
            label = None
            for t in (matching_field.get("translations") or []):
                if t.get("languages_code") in ("en-US", "vi-VN"):
                    label = t.get("label")
                    break
            key = attr if attr else (label or field_id or "field")
            profile[key] = answer.get("value")
    return profile
