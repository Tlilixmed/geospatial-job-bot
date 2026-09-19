"""Skills radar: what the jobs that match you ask for, against what you have.

The bot reads hundreds of relevant postings a month. Counting the canonical skills the matcher found in them
turns that into guidance no job board gives: which of your skills are in demand, and which skills you lack keep
appearing (and how often as a hard requirement). Deterministic: it only counts what the scorer already extracted.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta

from ..utils.dates import parse_datetime
from ..utils.text import fold

WINDOW_DAYS = 45
MIN_JOBS = 8
# canonical names from matching/profile.py that the CV supports; override with MY_SKILLS or /skills
DEFAULT_SKILLS = ["ArcGIS Pro", "ArcGIS", "QGIS", "Python", "ArcPy", "AutoCAD", "MicroStation", "FME", "TerraScan",
                  "Smallworld", "Agisoft Metashape", "CloudCompare/LAStools", "Spatial databases"]


def my_skills(settings, prefs: dict) -> list[str]:
    return list(prefs.get("my_skills") or getattr(settings, "my_skills", None) or DEFAULT_SKILLS)


def compute(jobs: dict, have: list[str], now, *, tiers=("high", "possible")) -> dict:
    cutoff = now - timedelta(days=WINDOW_DAYS)
    rows = [r for r in jobs.values() if r.get("tier") in tiers and (parse_datetime(r.get("last_seen")) or now) >= cutoff]
    mentioned, required, domains = Counter(), Counter(), Counter()
    for rec in rows:
        for skill in rec.get("matched_skills") or []:
            name, _, qualifier = str(skill).partition(" (")
            mentioned[name] += 1
            if qualifier.startswith("required"):
                required[name] += 1
        for domain in rec.get("matched_domains") or []:
            domains[domain] += 1
    owned = {fold(s) for s in have}
    total = len(rows)

    def share(name: str, counter: Counter) -> int:
        return round(100 * counter[name] / total) if total else 0

    ranked = [{"skill": n, "share": share(n, mentioned), "required": share(n, required), "have": fold(n) in owned}
              for n, _ in mentioned.most_common()]
    return {"jobs": total, "window_days": WINDOW_DAYS,
            "strengths": [r for r in ranked if r["have"]][:8],
            "gaps": [r for r in ranked if not r["have"] and r["share"] >= 5][:8],
            "domains": [(n, round(100 * c / total)) for n, c in domains.most_common(6)] if total else []}


def format_radar(data: dict) -> str:
    if data["jobs"] < MIN_JOBS:
        return (f"📡 <b>Skills radar</b>\nOnly {data['jobs']} matching jobs in the last {data['window_days']} days; "
                f"I need at least {MIN_JOBS} before the percentages mean anything.")
    lines = ["📡 <b>Skills radar</b>",
             f"<i>what {data['jobs']} matching jobs from the last {data['window_days']} days ask for</i>"]
    if data["gaps"]:
        lines += ["", "<b>Gaps worth closing</b> (not in your skills)"]
        for row in data["gaps"]:
            lines.append(f"• {row['skill']} — {row['share']}% of matches"
                         + (f", required in {row['required']}%" if row["required"] else ""))
    if data["strengths"]:
        lines += ["", "<b>Your skills in demand</b>"]
        for row in data["strengths"]:
            lines.append(f"• {row['skill']} — {row['share']}%" + (f", required in {row['required']}%" if row["required"] else ""))
    if data["domains"]:
        lines += ["", "<b>Domains</b>: " + " · ".join(f"{name} {pct}%" for name, pct in data["domains"])]
    lines += ["", "/skills shows or edits the list I compare against."]
    return "\n".join(lines)


def gap(rec: dict, have: list[str]) -> dict:
    """Per job: which of its skills you have and which are not on your list (the AI's requirements count too)."""
    owned = {fold(s) for s in have}
    asked: list[str] = []
    for skill in rec.get("matched_skills") or []:
        name = str(skill).partition(" (")[0].strip()
        if name and name not in asked:
            asked.append(name)
    for req in ((rec.get("ai") or {}).get("requirements") or []):
        for name in DEFAULT_SKILLS + list(have):  # a requirement sentence that names a known skill
            if fold(name) in fold(str(req)) and name not in asked:
                asked.append(name)
    return {"have": [n for n in asked if fold(n) in owned], "lack": [n for n in asked if fold(n) not in owned]}


def gap_line(rec: dict, have: list[str]) -> str | None:
    data = gap(rec, have)
    if not data["have"] and not data["lack"]:
        return None
    parts = []
    if data["have"]:
        parts.append("✅ you have " + ", ".join(data["have"][:6]))
    if data["lack"]:
        parts.append("❌ not on your list: " + ", ".join(data["lack"][:6]))
    return " · ".join(parts)
