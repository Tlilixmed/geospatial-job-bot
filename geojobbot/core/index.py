"""Compact read model for the Cloudflare Worker (state/index.json).

The Worker answers most Telegram messages itself, in under a second, from this file and state/prefs.json;
only /run, /pitch, /weekly, /radar, /skills, /learning and free-text instructions that change settings still
start the Python commands workflow. Everything the Worker shows is computed here, so there is one brain:
codes, scores, tiers, AI notes, sponsor hits, the run status and even the help text come from Python.
"""
from __future__ import annotations

from ..insights import fx, radar, timing, visa
from ..utils.dates import to_iso
from ..utils.location import ParsedLocation
from ..utils.text import job_code
from .jobs import is_listed, listed_days

SUFFIX = "state/index.json"
SCHEMA = 1
MAX_JOBS = 900


def _entry(cid: str, rec: dict, skills: list[str] | None = None) -> dict:
    loc = ParsedLocation(raw=rec.get("location_raw") or "", city=rec.get("city"), region=rec.get("region"),
                         country=rec.get("country"), remote=rec.get("remote"), remote_scope=rec.get("remote_scope"),
                         work_mode=rec.get("work_mode"))
    why = rec.get("why_matched") or []
    entry = {
        "id": cid, "code": job_code(cid), "t": rec.get("title"), "c": rec.get("company"), "loc": loc.display(),
        "country": rec.get("country"), "s": int(rec.get("score") or 0), "tier": rec.get("tier"),
        "p": rec.get("posted_at"), "rel": bool(rec.get("posted_at_reliable")), "seen": rec.get("last_seen"),
        "rot": bool(rec.get("from_rotation")), "ttl": listed_days(rec), "url": rec.get("apply_url") or rec.get("url"),
        "sal": rec.get("salary"), "sk": [str(s) for s in (rec.get("matched_skills") or [])[:8]],
        "dom": list((rec.get("matched_domains") or [])[:8]), "why": why[:12], "bd": rec.get("score_breakdown") or {},
        "rej": rec.get("rejection_reasons") or [], "offered": "Visa sponsorship offered" in why,
        "src": sorted({s.get("source_name") for s in rec.get("sources") or [] if s.get("source_name")})[:5],
    }
    review = rec.get("ai") or {}
    if review.get("fit") is not None:
        entry["ai"] = {k: review.get(k) for k in ("fit", "summary", "concerns", "years", "sponsorship", "languages",
                                                  "requirements")}
        entry["ai"].update({k: review[k] for k in ("model", "lift", "restricted") if review.get(k)})
    if rec.get("sponsor"):
        entry["sp"] = [{k: h.get(k) for k in ("label", "icon", "country", "name", "match", "positions", "occupations", "geo")}
                       for h in rec["sponsor"][:3]]
    if fx.note(rec):
        entry["sal"] = f"{rec.get('salary')} ({fx.note(rec)})"
    if rec.get("deadline"):
        entry["dl"] = rec["deadline"]
    if skills is not None and rec.get("tier") in ("high", "possible"):
        line = radar.gap_line(rec, skills)
        if line:
            entry["gap"] = line
    if timing.repost_badge(rec):
        entry["rp"] = timing.repost_badge(rec)
    if rec.get("watched"):
        entry["w"] = True
    if rec.get("visa"):
        entry["visa"] = {"v": rec["visa"].get("verdict"), "b": visa.badge(rec)}
        if rec.get("tier") in ("high", "possible"):
            entry["visa"]["d"] = visa.detail_lines(rec)
    if (rec.get("learned") or {}).get("adj"):
        entry["learned"] = rec["learned"]
    return entry


def build_index(state: dict, settings, report: dict, now, help_text: str, views: dict | None = None,
                skills: list[str] | None = None) -> dict:
    """`views` are replies Python formatted in advance ({command: html}); the Worker sends them as they are."""
    rows = []
    for cid, rec in state.get("jobs", {}).items():
        if not is_listed(rec, now):
            continue  # gone from its source: it would only push a fresh job past MAX_JOBS; /why on an old code goes to Python
        accepted = rec.get("tier") in ("high", "possible")
        near_miss = set(rec.get("rejection_reasons") or []) <= {"LOW_SCORE"} and int(rec.get("score") or 0) >= 35
        if accepted or near_miss:
            rows.append(_entry(cid, rec, skills))
    rows.sort(key=lambda e: (e["tier"] != "high", -e["s"]))
    counts = report.get("counts") or {}
    return {
        "schema": SCHEMA, "generated_at": to_iso(now), "help": help_text, "views": views or {},
        "settings": {"high": settings.high_threshold, "medium": settings.medium_threshold,
                     "max_age_h": settings.max_job_age_hours, "rotation_max_age_h": settings.rotation_max_job_age_hours,
                     "notify_possible": settings.notify_possible, "exclude_internships": settings.exclude_internships,
                     "ai": bool(settings.cloudflare_ai_token)},
        "run": {"started_at": report.get("started_at"), "code": report.get("code"),
                "high": counts.get("tier_high", 0), "possible": counts.get("tier_possible", 0),
                "alerted": counts.get("alerts_sent", 0), "new": counts.get("new", 0),
                "failed": [s["name"] for s in report.get("sources") or [] if s.get("status") == "FAILED"],
                "jobs_in_state": len(state.get("jobs", {}))},
        "signals": ((state.get("signals") or {}).get("items") or [])[:20],
        "visa_cards": visa.cards() if getattr(settings, "visa_paths", False) else {},
        "jobs": rows[:MAX_JOBS],
    }
