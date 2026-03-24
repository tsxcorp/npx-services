from typing import List
from fastapi import APIRouter, HTTPException
import httpx
from app.models.schemas import MatchRunRequest, MatchRunResponse, MatchSuggestion
from app.config import DIRECTUS_ADMIN_TOKEN, ADMIN_URL
from app.services.directus import directus_get, directus_post, directus_patch, create_notification
from app.services.matching_service import (
    score_match_with_gemini,
    keyword_prefilter_score,
    extract_visitor_profile,
)

router = APIRouter()


@router.post("/match/run", response_model=MatchRunResponse)
async def run_job_matching(request: MatchRunRequest):
    """
    Run AI job matching for an event.
    Fetches job requirements + visitor submissions, scores with AI,
    and creates/updates job_match_suggestions in Directus.
    """
    if not DIRECTUS_ADMIN_TOKEN:
        raise HTTPException(status_code=500, detail="DIRECTUS_ADMIN_TOKEN not configured")

    event_id = request.event_id

    try:
        # 1. Fetch job requirements
        if request.job_requirement_id:
            job_filter = f"filter[id][_eq]={request.job_requirement_id}"
        elif request.exhibitor_id:
            job_filter = f"filter[event_id][_eq]={event_id}&filter[status][_eq]=published&filter[exhibitor_id][_eq]={request.exhibitor_id}"
        else:
            job_filter = f"filter[event_id][_eq]={event_id}&filter[status][_eq]=published"

        jobs_resp = await directus_get(
            f"/items/job_requirements?{job_filter}"
            "&fields[]=id,job_title,description,requirements,skills,experience_level,employment_type,exhibitor_id"
            "&limit=100"
        )
        jobs = jobs_resp.get("data", [])
        if not jobs:
            return MatchRunResponse(success=True, message="No published job requirements found", suggestions_created=0)

        # 2. Form fields for matching
        fields_resp = await directus_get(
            f"/items/form_fields?filter[event_id][_eq]={event_id}&filter[use_for_matching][_eq]=true"
            "&fields[]=id,name,use_for_matching,matching_attribute,translations.languages_code,translations.label"
            "&limit=200"
        )
        matching_fields = fields_resp.get("data", [])

        # 3. Tier 1: registration submissions
        regs_resp = await directus_get(
            f"/items/registrations?filter[event_id][_eq]={event_id}"
            "&filter[submissions][_nnull]=true"
            "&fields[]=id,submissions.id,submissions.form,submissions.answers.value,submissions.answers.field.id"
            "&limit=500"
        )
        registrations = regs_resp.get("data", [])

        form_ids_resp = await directus_get(
            f"/items/form_fields?filter[event_id][_eq]={event_id}&filter[use_for_matching][_eq]=true"
            "&fields[]=form_id&limit=50"
        )
        form_ids = {item.get("form_id") for item in form_ids_resp.get("data", []) if item.get("form_id")}

        tier1_by_registration: dict = {}
        for reg in registrations:
            sub = reg.get("submissions")
            if not sub or not isinstance(sub, dict):
                continue
            sub_form = sub.get("form")
            if form_ids and sub_form not in form_ids:
                continue
            profile = await extract_visitor_profile(
                {"answers": sub.get("answers") or []}, matching_fields
            )
            if profile:
                tier1_by_registration[reg["id"]] = profile

        # 4. Tier 2: candidate profile form submissions
        candidate_form_resp = await directus_get(
            f"/items/forms?filter[event_id][_eq]={event_id}"
            "&filter[linked_module][_eq]=candidate_profiles"
            "&fields[]=id&limit=1"
        )
        candidate_forms = candidate_form_resp.get("data", [])
        tier2_by_registration: dict = {}
        tier2_matching_fields: list = []

        if candidate_forms:
            candidate_form_id = candidate_forms[0]["id"]
            t2_fields_resp = await directus_get(
                f"/items/form_fields?filter[form_id][_eq]={candidate_form_id}&filter[use_for_matching][_eq]=true"
                "&fields[]=id,name,use_for_matching,matching_attribute,translations.languages_code,translations.label"
                "&limit=200"
            )
            tier2_matching_fields = t2_fields_resp.get("data", [])
            t2_subs_resp = await directus_get(
                f"/items/form_submissions?filter[form][_eq]={candidate_form_id}"
                "&filter[registration_id][_nnull]=true"
                "&fields[]=id,registration_id,answers.value,answers.field.id"
                "&limit=1000"
            )
            for sub in t2_subs_resp.get("data", []):
                reg_id = sub.get("registration_id")
                if not reg_id:
                    continue
                reg_id = reg_id if isinstance(reg_id, str) else str(reg_id)
                profile = await extract_visitor_profile(sub, tier2_matching_fields)
                if profile:
                    tier2_by_registration[reg_id] = profile

        # Merge tier 1 + tier 2 (tier 2 wins on overlap)
        all_registration_ids = set(tier1_by_registration.keys()) | set(tier2_by_registration.keys())
        submissions = []
        for reg_id in all_registration_ids:
            merged = {**(tier1_by_registration.get(reg_id) or {}), **(tier2_by_registration.get(reg_id) or {})}
            if merged:
                submissions.append({"registration_id": reg_id, "answers": [], "_merged_profile": merged})

        if not submissions:
            return MatchRunResponse(success=True, message="No visitor profiles found for matching", suggestions_created=0)

        # Pre-load existing suggestions to avoid N+1 queries
        existing_resp = await directus_get(
            f"/items/job_match_suggestions?filter[event_id][_eq]={event_id}"
            "&fields[]=id,job_requirement_id,registration_id,status&limit=2000"
        )
        existing_map: dict = {}
        for s in existing_resp.get("data", []):
            key = (str(s.get("job_requirement_id", "")), str(s.get("registration_id", "")))
            existing_map[key] = {"id": s["id"], "status": s.get("status", "pending")}

        suggestions_created = 0
        suggestions_updated = 0
        all_suggestions: List[MatchSuggestion] = []
        suggestions_by_exhibitor: dict[str, int] = {}
        SCORE_THRESHOLD = max(0.1, min(0.95, request.score_threshold))
        KEYWORD_THRESHOLD = max(0.0, min(0.5, request.keyword_threshold))
        MAX_CANDIDATES_PER_JOB = max(5, min(200, request.max_candidates_per_job))

        for job in jobs:
            exhibitor_id = job.get("exhibitor_id")

            scored_submissions = []
            for submission in submissions:
                registration_id = submission.get("registration_id")
                if not registration_id:
                    continue
                visitor_profile = submission.get("_merged_profile") or await extract_visitor_profile(submission, matching_fields)
                if not visitor_profile:
                    continue
                kw_score = keyword_prefilter_score(job, visitor_profile)
                if kw_score >= KEYWORD_THRESHOLD:
                    scored_submissions.append((kw_score, submission, visitor_profile))

            scored_submissions.sort(key=lambda x: x[0], reverse=True)
            top_submissions = scored_submissions[:MAX_CANDIDATES_PER_JOB]

            for kw_score, submission, visitor_profile in top_submissions:
                registration_id = submission.get("registration_id")
                score_result = await score_match_with_gemini(job, visitor_profile, model=request.ai_model)
                score = score_result["score"]
                if score < SCORE_THRESHOLD:
                    continue

                reg_id_str = str(registration_id) if isinstance(registration_id, (str, int)) else str(registration_id.get("id", ""))
                suggestion = MatchSuggestion(
                    job_requirement_id=str(job["id"]),
                    registration_id=reg_id_str,
                    exhibitor_id=str(exhibitor_id) if exhibitor_id else "",
                    score=score,
                    matched_criteria=score_result["matched_criteria"],
                    ai_reasoning=score_result["ai_reasoning"],
                )
                all_suggestions.append(suggestion)

                key = (suggestion.job_requirement_id, suggestion.registration_id)
                existing = existing_map.get(key)
                suggestion_data = {
                    "event_id": event_id,
                    "job_requirement_id": suggestion.job_requirement_id,
                    "registration_id": suggestion.registration_id,
                    "exhibitor_id": suggestion.exhibitor_id if suggestion.exhibitor_id else None,
                    "score": round(score, 4),
                    "matched_criteria": suggestion.matched_criteria,
                    "ai_reasoning": suggestion.ai_reasoning,
                }

                if existing:
                    if existing["status"] not in ("pending",):
                        continue
                    if not request.rescore_pending:
                        continue
                    await directus_patch(f"/items/job_match_suggestions/{existing['id']}", suggestion_data)
                    suggestions_updated += 1
                else:
                    await directus_post("/items/job_match_suggestions", {**suggestion_data, "status": "pending"})
                    suggestions_created += 1
                    ex_id = str(exhibitor_id) if exhibitor_id else ""
                    if ex_id:
                        suggestions_by_exhibitor[ex_id] = suggestions_by_exhibitor.get(ex_id, 0) + 1

        # In-app notifications — gửi cho organizer của event, link trỏ về admin app
        if suggestions_by_exhibitor:
            try:
                event_resp = await directus_get(f"/items/events/{event_id}?fields[]=user_created")
                organizer_user_id = (event_resp.get("data") or {}).get("user_created")
                if organizer_user_id:
                    total_new = sum(suggestions_by_exhibitor.values())
                    await create_notification(
                        user_id=organizer_user_id,
                        title=f"{total_new} gợi ý matching mới từ AI",
                        body=f"{len(suggestions_by_exhibitor)} exhibitor(s) có ứng viên phù hợp mới",
                        link=f"{ADMIN_URL}/events/{event_id}/talent-matching/ai",
                        notif_type="matching_complete",
                    )
            except Exception:
                pass

        total_candidates = len(submissions)
        return MatchRunResponse(
            success=True,
            message=f"Matching complete. {suggestions_created} new, {suggestions_updated} refreshed. "
                    f"Checked {len(jobs)} job(s) × top-{MAX_CANDIDATES_PER_JOB} of {total_candidates} candidates "
                    f"(min score {int(SCORE_THRESHOLD*100)}%, keyword {int(KEYWORD_THRESHOLD*100)}%, model {request.ai_model}).",
            suggestions_created=suggestions_created,
            suggestions=all_suggestions,
        )

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Directus error: {e.response.status_code} {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Matching error: {str(e)}")
