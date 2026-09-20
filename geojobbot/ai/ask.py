"""/ask: free questions about the current matches, answered from the bot's own data and nothing else.

"which of these are in French-speaking countries and pay over 50k?", "compare a3f9c and b2c1d", "which ones close this
week?", "où sont les postes de topographe ?". The model sees one compact line per current match (the facts the bot
established: score, place, salary in euros, visa route, sponsor evidence, deadline, the AI summary) and is told to
answer only from those lines and to cite job codes, so every claim can be checked with /why code. It cannot change
anything: it has no tools, and its answer is text.
"""
from __future__ import annotations

import html
import re

from ..utils.text import job_code
from .review import DEFAULT_PROFILE

MAX_JOBS = 45
ASK_SYSTEM = """You answer ONE question from a job seeker about the list of job matches below. Candidate: {profile}
Rules: use ONLY the facts in the list; if the list does not contain the answer, say so plainly. Cite jobs by their code in
square brackets, e.g. [a3f9c]. Answer in the language of the question (English or French). At most 170 words, plain text,
no markdown symbols, no invented employers, salaries, dates or visa facts. When comparing or ranking, give the reason in a
few words per job. Today is {today}.

MATCHES (one per line: code | score | tier | title | company | place | facts):
{lines}"""


def job_line(rec: dict) -> str:
    facts = []
    if rec.get("salary"):
        pair = rec.get("salary_eur")
        facts.append(f"salary {rec['salary']}" + (f" (about EUR {pair[0]}-{pair[1]} a year)" if pair else ""))
    visa = rec.get("visa") or {}
    if visa.get("verdict"):
        facts.append(f"visa route {visa.get('path')}: {visa['verdict']}")
    if "Visa sponsorship offered" in (rec.get("why_matched") or []):
        facts.append("posting offers sponsorship")
    for hit in (rec.get("sponsor") or [])[:1]:
        facts.append(f"employer on register: {hit.get('label')}")
    if rec.get("deadline"):
        facts.append(f"closes {rec['deadline']}")
    if rec.get("posted_at"):
        facts.append(f"posted {str(rec['posted_at'])[:10]}")
    if rec.get("remote"):
        facts.append("remote")
    skills = [str(s).split(" (")[0] for s in (rec.get("matched_skills") or [])[:6]]
    if skills:
        facts.append("skills: " + ", ".join(skills))
    review = rec.get("ai") or {}
    if review.get("fit") is not None:
        facts.append(f"AI fit {review['fit']}/10")
    if review.get("years") is not None:
        facts.append(f"{review['years']}+ years required")
    if review.get("languages"):
        facts.append("languages required: " + ", ".join(review["languages"]))
    if review.get("summary"):
        facts.append(str(review["summary"])[:160])
    if review.get("concerns"):
        facts.append("concern: " + str(review["concerns"])[:80])
    place = ", ".join(p for p in (rec.get("city"), rec.get("country")) if p) or (rec.get("location_raw") or "unknown place")
    clean = lambda v: re.sub(r"\s+", " ", re.sub(r"[|\n\r]+", " ", str(v or ""))).strip()  # noqa: E731
    return " | ".join([job_code(rec.get("canonical_id")), str(int(rec.get("score") or 0)), str(rec.get("tier")), clean(rec.get("title"))[:80],
                       clean(rec.get("company"))[:40] or "?", clean(place)[:50], clean("; ".join(facts))[:520]])


def answer(ai, profile: str, question: str, records: list[dict], today: str) -> str | None:
    """HTML-safe answer, with every cited code that really exists turned into a tappable <code> handle."""
    rows = records[:MAX_JOBS]
    if not rows:
        return None
    system = ASK_SYSTEM.format(profile=profile or DEFAULT_PROFILE, today=today, lines="\n".join(job_line(r) for r in rows))
    text = ai.chat(system, question.strip()[:400], max_tokens=420)
    if not text:
        return None
    known = {job_code(r.get("canonical_id")) for r in rows}
    safe = html.escape(text.strip())
    safe = re.sub(r"\[([0-9a-f]{5})\]", lambda m: f"<code>{m.group(1)}</code>" if m.group(1) in known else m.group(0), safe)
    invented = [c for c in re.findall(r"\[([0-9a-f]{5})\]", text) if c not in known]
    if invented:  # a code that is not in the list is the clearest sign of a made-up answer: say so rather than hide it
        safe += "\n\n⚠️ The answer cites codes I do not have (" + ", ".join(sorted(set(invented))) + "): treat it with care."
    return safe
