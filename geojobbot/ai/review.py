"""AI second opinion on jobs the deterministic scorer already accepted, and tailored application notes.

The scorer stays the authority: the review only adds a summary, concerns and a 0-10 fit, and may veto a
*Possible* match it finds clearly irrelevant. Every field is validated and clamped before it is stored,
so a confused model can at worst produce an empty review.
"""
from __future__ import annotations

import html
import re

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
"requirements": up to 5 short phrases with the key technical requirements
"restricted": true only if the posting REQUIRES citizenship, a security clearance, permanent residency or an existing right to work in the country, or says it does not sponsor; false otherwise. "Must be authorized to work" alone is true; equal-opportunity boilerplate is not.
"deadline": the application closing date as YYYY-MM-DD if the posting states one, else null"""

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
    deadline = str(data.get("deadline") or "")[:10]
    if not re.fullmatch(r"20\d{2}-[01]\d-[0-3]\d", deadline):
        deadline = None
    listify = lambda v: [_short(x, 60) for x in (v if isinstance(v, list) else []) if str(x).strip()]  # noqa: E731
    return {"fit": fit, "summary": _short(data.get("summary"), 220), "concerns": concerns, "years": years,
            "sponsorship": sponsorship if sponsorship in SPONSORSHIP else "unknown",
            "languages": listify(data.get("languages"))[:5], "requirements": listify(data.get("requirements"))[:5],
            "restricted": data.get("restricted") is True, "deadline": deadline}


def decisions_note(prefs: dict | None, limit: int = 6) -> str:
    """The candidate's own recent decisions, as calibration for the fit score (titles and employers only)."""
    if not prefs:
        return ""
    applied = sorted((prefs.get("applied") or {}).values(), key=lambda a: a.get("at") or "", reverse=True)[:limit]
    hidden = sorted((prefs.get("hidden_info") or {}).values(), key=lambda a: a.get("at") or "", reverse=True)[:limit]
    name = lambda a: " at ".join(x for x in (_short(a.get("title"), 60), _short(a.get("company"), 40)) if x)  # noqa: E731
    parts = []
    if applied:
        parts.append("Jobs this candidate recently APPLIED to (they are a good fit): " + "; ".join(name(a) for a in applied if name(a)))
    if hidden:
        parts.append("Jobs this candidate DISMISSED (a poor fit for them): " + "; ".join(name(a) for a in hidden if name(a)))
    return ("\n" + "\n".join(parts) + "\nUse these only to calibrate \"fit\"; never mention them.") if parts else ""


def review_job(ai, profile: str, *, title: str, company: str | None, location: str, description: str,
               decisions: str = "") -> dict | None:
    posting = (f"Title: {title}\nCompany: {company or 'unknown'}\nLocation: {location or 'unknown'}\n\n"
               f"{(description or '')[:6000]}")
    review = clean_review(ai.chat_json(REVIEW_SYSTEM.format(profile=(profile or DEFAULT_PROFILE) + decisions), posting, max_tokens=450))
    if review is not None and getattr(ai, "last_model", None):
        review["model"] = ai.last_model.rsplit("/", 1)[-1]
    return review


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


PREP_SYSTEM = """You prepare ONE candidate for a job interview. Be specific to the posting, never generic.
Candidate: {profile}
Write in English, plain text, no markdown symbols, at most 230 words, exactly these four parts:
LIKELY QUESTIONS: five questions this employer will probably ask, each followed by " -> " and a six-to-twelve word hint on how THIS candidate should answer from real experience.
WEAK POINTS: two gaps between the candidate and the posting, each with one sentence on how to address it honestly.
ASK THEM: three good questions for the candidate to ask (one about the team's tools or data, one about relocation or visa support when the job is abroad).
ONE LINE: a single sentence the candidate can use to introduce themselves for this job.
Never invent facts about the employer."""

APPROACH_SYSTEM = """You write a short unsolicited application (candidature spontanée) from ONE candidate to a firm that has not advertised a job.
Candidate: {profile}
Write in French when the firm or the project is in a French-speaking country, otherwise in English.
First line: "Subject: ..." (or "Objet : ..."). Then 110-150 words, first person, concrete: open with the specific reason for writing now
(given in the facts, such as a contract the firm just won), name 2-3 of the candidate's real skills that such work needs, say the candidate
can relocate or work remotely, and end by asking for a short call. No clichés, no invented facts, no placeholders such as [Name]."""


def write_prep(ai, profile: str, rec: dict, description: str = "", facts: list[str] | None = None) -> str | None:
    review = rec.get("ai") or {}
    lines = [f"Job title: {rec.get('title')}", f"Company: {rec.get('company') or 'unknown'}",
             f"Location: {rec.get('location_raw') or rec.get('country') or 'unknown'}"]
    requirements = review.get("requirements") or rec.get("matched_skills") or rec.get("skills") or []
    if requirements:
        lines.append("Key requirements: " + "; ".join(str(r) for r in requirements))
    if review.get("concerns"):
        lines.append("Known concern: " + review["concerns"])
    lines += list(facts or [])
    if description:
        lines.append("Posting text (excerpt): " + description[:2500])
    text = ai.chat(PREP_SYSTEM.format(profile=profile or DEFAULT_PROFILE), "\n".join(lines), max_tokens=600)
    return html.escape(text.strip()) if text else None


def write_approach(ai, profile: str, firm: str, facts: list[str]) -> str | None:
    text = ai.chat(APPROACH_SYSTEM.format(profile=profile or DEFAULT_PROFILE), "\n".join([f"Firm: {firm}"] + facts), max_tokens=420)
    return html.escape(text.strip()) if text else None
