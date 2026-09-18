"""AI second opinion on jobs the deterministic scorer already accepted, and tailored application notes.

The scorer stays the authority: the review only adds a summary, concerns and a 0-10 fit, and may veto a
*Possible* match it finds clearly irrelevant. Every field is validated and clamped before it is stored,
so a confused model can at worst produce an empty review.
"""
from __future__ import annotations

import html

DEFAULT_PROFILE = (
    "Geomatics engineer, about 3 years of experience. LiDAR point-cloud processing and classification (TerraScan, "
    "MicroStation, LAStools), photogrammetry (Agisoft Metashape, orthomosaics, DTM/DSM), GIS and cartography (ArcGIS Pro "
    "geodatabase design and data validation, QGIS advanced, Smallworld for fibre networks), Python automation and ArcGIS "
    "toolboxes, AutoCAD, FME, GIS for mineral exploration (mineral tenure, claims, staking analysis). Team-lead experience. "
    "Engineering diploma in geomatics and topography. Speaks Arabic, French and English. Based in Tunisia; will relocate "
    "anywhere if the employer sponsors the visa, or work remotely. Not interested in internships, pure sales, or senior "
    "management roles."
)

REVIEW_SYSTEM = """You screen job postings for ONE candidate and answer with ONLY a JSON object, no other text.
Candidate: {profile}

JSON keys:
"fit": integer 0-10, how well the job matches the candidate's actual skills and level (0 = unrelated field, 5 = partly, 10 = ideal)
"summary": one English sentence, at most 25 words, saying what the job is and why it does or does not fit (translate if needed)
"concerns": one short English phrase with the main obstacle for this candidate, or "" if none (examples: "requires 8+ years", "German required", "licensed land surveyor only", "US citizens only", "no sponsorship")
"years": minimum years of experience required as an integer, or null
"sponsorship": "offered", "not_offered" or "unknown" (visa / work permit sponsorship)
"languages": list of languages the posting REQUIRES, e.g. ["English","French"]
"requirements": up to 5 short phrases with the key technical requirements"""

PITCH_SYSTEM = """You write a short application note for ONE candidate. Write in the language of the job title (French title -> French).
Candidate: {profile}
Rules: 110-150 words, first person, concrete, no clichés, no invented facts or employers, no placeholders such as [Name].
Mention 2-3 of the candidate's real skills that match the requirements, and one honest sentence about relocation or remote work.
Output the note only."""

SPONSORSHIP = {"offered", "not_offered", "unknown"}


def _short(value, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def clean_review(data: dict | None) -> dict | None:
    if not isinstance(data, dict):
        return None
    try:
        fit = max(0, min(10, int(round(float(data.get("fit"))))))
    except (TypeError, ValueError):
        return None  # a review without a usable fit is not a review
    years = data.get("years")
    try:
        years = max(0, min(40, int(years))) if years is not None else None
    except (TypeError, ValueError):
        years = None
    sponsorship = str(data.get("sponsorship") or "unknown").lower().replace(" ", "_")
    concerns = _short(data.get("concerns"), 90)
    if concerns.lower() in {"none", "n/a", "no", "null", "-"}:
        concerns = ""
    listify = lambda v: [_short(x, 60) for x in (v if isinstance(v, list) else []) if str(x).strip()]  # noqa: E731
    return {"fit": fit, "summary": _short(data.get("summary"), 220), "concerns": concerns, "years": years,
            "sponsorship": sponsorship if sponsorship in SPONSORSHIP else "unknown",
            "languages": listify(data.get("languages"))[:5], "requirements": listify(data.get("requirements"))[:5]}


def review_job(ai, profile: str, *, title: str, company: str | None, location: str, description: str) -> dict | None:
    posting = (f"Title: {title}\nCompany: {company or 'unknown'}\nLocation: {location or 'unknown'}\n\n"
               f"{(description or '')[:3500]}")
    return clean_review(ai.chat_json(REVIEW_SYSTEM.format(profile=profile or DEFAULT_PROFILE), posting))


def write_pitch(ai, profile: str, rec: dict, description: str = "") -> str | None:
    review = rec.get("ai") or {}
    facts = [f"Job title: {rec.get('title')}", f"Company: {rec.get('company') or 'unknown'}",
             f"Location: {rec.get('location_raw') or 'unknown'}",
             "Key requirements: " + "; ".join(review.get("requirements") or rec.get("matched_skills") or []),
             "Matched skills: " + ", ".join(s.split(" (")[0] for s in rec.get("matched_skills") or []),
             "Domains: " + ", ".join(rec.get("matched_domains") or [])]
    if review.get("summary"):
        facts.append("About the job: " + review["summary"])
    if description:
        facts.append("Posting text (excerpt): " + description[:2500])
    text = ai.chat(PITCH_SYSTEM.format(profile=profile or DEFAULT_PROFILE), "\n".join(facts), max_tokens=420)
    return html.escape(text.strip()) if text else None
