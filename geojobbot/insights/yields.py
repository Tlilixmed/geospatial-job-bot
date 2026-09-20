"""Which sources earn their keep (/sources, a line in the weekly summary).

Two questions per source over the last four weeks: how much did it deliver, and how much of that would have been
missed without it? "Found" counts stored jobs first seen in the window that the scorer accepted; "only here" the
ones no other source carried; "applied" the ones the user went for. Raw volume is accumulated per run (rejected
jobs are mostly not stored), so noise shows as a large raw count next to nothing found.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
from html import escape

from ..utils.dates import parse_datetime

WINDOW_DAYS = 28
KEEP_DAYS = 35
GENERIC = {"generic_jsonld", "generic_embedded_json", "generic_html"}
QUOTA_SOURCES = {"jsearch", "adzuna", "jooble"}  # paid for in API calls: the ones worth pruning


def group(source_name: str | None) -> str:
    """Collapse per-publisher and per-page names to the thing the user can switch on or off."""
    name = escape((source_name or "unknown").strip() or "unknown", quote=False)
    if name in GENERIC:
        return "career pages"
    if name.startswith("jsearch:"):
        return "jsearch"
    if name.startswith("freehire:"):
        return "freehire"
    return name


def record_run(state: dict, raws, now) -> None:
    """Add this run's raw volume per source to today's bucket."""
    days = state.setdefault("yield", {}).setdefault("days", {})
    bucket = days.setdefault(now.strftime("%Y-%m-%d"), {})
    for name, n in Counter(group(raw.source_name) for raw in raws).items():
        bucket[name] = int(bucket.get(name) or 0) + n
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    for day in [d for d in days if d < cutoff]:
        del days[day]


def compute(state: dict, prefs: dict | None, now, window_days: int = WINDOW_DAYS) -> dict:
    since = now - timedelta(days=window_days)
    rows: dict[str, Counter] = {}
    cutoff = since.strftime("%Y-%m-%d")
    for day, bucket in ((state.get("yield") or {}).get("days") or {}).items():
        if day >= cutoff:
            for name, n in bucket.items():
                rows.setdefault(name, Counter())["raw"] += int(n or 0)
    applied = set((prefs or {}).get("applied") or {})
    accepted_total = 0
    for cid, rec in (state.get("jobs") or {}).items():
        first = parse_datetime(rec.get("first_seen"))
        if first is None or first < since or rec.get("tier") not in ("high", "possible"):
            continue
        groups = {group(s.get("source_name")) for s in rec.get("sources") or []} or {"unknown"}
        accepted_total += 1
        for name in groups:
            row = rows.setdefault(name, Counter())
            row["found"] += 1
            row["high"] += rec.get("tier") == "high"
            row["only"] += len(groups) == 1
            row["applied"] += cid in applied
    table = [{"source": name, "raw": c["raw"], "found": c["found"], "high": c["high"], "only": c["only"],
              "applied": c["applied"]} for name, c in rows.items()]
    table.sort(key=lambda r: (-r["only"], -r["found"], -r["raw"], r["source"]))
    first_day = min(((state.get("yield") or {}).get("days") or {}), default=None)
    return {"window_days": window_days, "since": first_day, "accepted": accepted_total, "rows": table}


def verdicts(data: dict) -> list[str]:
    """Plain-language conclusions; cautious until a source has had a fair volume."""
    notes = []
    dead = [r for r in data["rows"] if r["raw"] >= 150 and r["found"] == 0]
    if dead:
        notes.append("💤 Noise so far: " + ", ".join(f"{r['source']} ({r['raw']:,} raw, nothing accepted)" for r in dead[:5]))
    redundant = [r for r in data["rows"] if r["found"] >= 5 and r["only"] == 0]
    if redundant:
        notes.append("♻️ Redundant so far (everything also came from elsewhere): " + ", ".join(r["source"] for r in redundant[:5]))
    costly = [r for r in data["rows"] if r["source"] in QUOTA_SOURCES and (r["only"] == 0 and r["raw"] >= 50)]
    if costly:
        notes.append("💸 Uses an API quota without adding anything unique: " + ", ".join(r["source"] for r in costly))
    return notes


def format_yield(data: dict, limit: int = 14) -> str:
    rows = [r for r in data["rows"] if r["raw"] or r["found"]]
    if not rows:
        return "📊 No source statistics yet: they build up from the next scraper run."
    lines = ["📊 <b>Source yield</b>", f"<i>last {data['window_days']} days · {data['accepted']} accepted jobs"
             + (f" · counting since {data['since']}" if data.get("since") else "") + "</i>", ""]
    for r in rows[:limit]:
        line = f"• <b>{r['source']}</b>: {r['raw']:,} raw → {r['found']} found"
        if r["found"]:
            line += f" ({r['high']} high) · <b>{r['only']}</b> only here"
        if r["applied"]:
            line += f" · {r['applied']} applied"
        lines.append(line)
    if len(rows) > limit:
        lines.append(f"… and {len(rows) - limit} smaller sources")
    notes = verdicts(data)
    if notes:
        lines += [""] + notes
    lines += ["", "<i>“only here” is what you would have missed without that source.</i>"]
    return "\n".join(lines)


def weekly_lines(data: dict) -> list[str]:
    """Two or three lines for the weekly summary."""
    best = [r for r in data["rows"] if r["only"]][:3]
    lines = []
    if best:
        lines.append("📊 Best sources: " + ", ".join(f"{r['source']} ({r['only']} only here)" for r in best))
    lines += verdicts(data)[:2]
    return lines
