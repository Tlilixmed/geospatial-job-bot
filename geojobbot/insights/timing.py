"""Timing facts about a posting: when it closes, and whether it keeps coming back.

Deadlines are read from the description ("closing date: 30 September 2026", "date limite de candidature : 30/09/2026",
"apply by Sept 30"). A High match that closes within three days and that the user has neither applied to nor hidden
gets one reminder; a posting past its deadline stops being listed. Reposts: the same title at the same employer seen
again at least three weeks after an earlier posting. Several reposts mean a role that is hard to fill (a better
starting point for sponsorship) or an evergreen advert; either way it is worth knowing before writing an application.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from ..utils.dates import parse_datetime
from ..utils.text import fold, normalize_company, normalize_title

REMIND_DAYS = 3
REPOST_GAP_DAYS = 21
MAX_AHEAD_DAYS = 200  # a "deadline" further away than this is something else (a start date, a contract end)

MONTHS = {"jan": 1, "january": 1, "janvier": 1, "janv": 1, "feb": 2, "february": 2, "fevrier": 2, "fevr": 2, "fev": 2,
          "mar": 3, "march": 3, "mars": 3, "apr": 4, "april": 4, "avril": 4, "avr": 4, "may": 5, "mai": 5,
          "jun": 6, "june": 6, "juin": 6, "jul": 7, "july": 7, "juillet": 7, "juil": 7, "aug": 8, "august": 8, "aout": 8,
          "sep": 9, "sept": 9, "september": 9, "septembre": 9, "oct": 10, "october": 10, "octobre": 10,
          "nov": 11, "november": 11, "novembre": 11, "dec": 12, "december": 12, "decembre": 12}
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
CUE_RE = re.compile(
    r"\b(closing date|closes? on|closes?|close date|application deadline|deadline(?: for applications?)?|apply (?:by|before|no later than)|"
    r"applications? (?:close|closes|must be (?:received|submitted) by|due|accepted until|will be accepted until|deadline)|"
    r"submit(?:ted)? (?:by|before|no later than)|posting end date|end date of posting|open until|"
    r"date limite(?: de (?:candidature|depot|reception)s?)?|date de cloture|cloture(?: des candidatures)?|au plus tard le|"
    r"avant le|jusqu au|candidatures? (?:jusqu au|avant le)|bewerbungsfrist|bewerbungsschluss)\b")
DATE_RES = (
    re.compile(r"\b(?P<y>20\d{2})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})\b"),
    re.compile(rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th|er)?(?: of)? (?P<mon>{_MONTH})\.?,? ?(?P<y>20\d{{2}})?\b"),
    re.compile(rf"\b(?P<mon>{_MONTH})\.? (?P<d>\d{{1,2}})(?:st|nd|rd|th)?,? ?(?P<y>20\d{{2}})?\b"),
    re.compile(r"\b(?P<d>\d{1,2})[/.](?P<m>\d{1,2})[/.](?P<y>20\d{2})\b"),
)


def _build(match: re.Match, today: date, day_first: bool = True) -> date | None:
    parts = match.groupdict()
    try:
        day = int(parts["d"])
        month = MONTHS[parts["mon"]] if parts.get("mon") else int(parts["m"])
        if not parts.get("mon") and (not day_first or month > 12) and day <= 12:
            day, month = month, day
        if parts.get("y"):
            return date(int(parts["y"]), month, day)
        guess = date(today.year, month, day)  # no year given: the next such day
        return guess if guess >= today - timedelta(days=30) else date(today.year + 1, month, day)
    except (ValueError, KeyError):
        return None


def find_deadline(text: str | None, now: datetime, country: str | None = None) -> date | None:
    """The application deadline stated in a description, or None. Only dates right after a deadline phrase count."""
    if not text:
        return None
    folded = fold(re.sub(r"<[^>]+>", " ", text))
    folded = re.sub(r"[^a-z0-9/.\-: ,]+", " ", folded)
    folded = re.sub(r"\s+", " ", folded)
    today = now.date()
    for cue in CUE_RE.finditer(folded):
        window = folded[cue.end(): cue.end() + 60]
        best = None
        for pattern in DATE_RES:
            found = pattern.search(window)
            if found and (best is None or found.start() < best[0]):
                built = _build(found, today, day_first=country != "United States")  # 09/30/2026 is a US habit
                if built:
                    best = (found.start(), built)
        if best and best[0] <= 25 and -60 <= (best[1] - today).days <= MAX_AHEAD_DAYS:
            return best[1]
    return None


def annotate_deadline(rec: dict, text: str | None, now: datetime) -> bool:
    found = find_deadline(text, now, rec.get("country"))
    if found is None:
        return False
    rec["deadline"] = found.isoformat()
    return True


def deadline_of(rec: dict) -> date | None:
    try:
        return date.fromisoformat(rec["deadline"]) if rec.get("deadline") else None
    except ValueError:
        return None


def deadline_passed(rec: dict, now: datetime) -> bool:
    closes = deadline_of(rec)
    return closes is not None and closes < now.date()


def deadline_badge(rec: dict, now: datetime | None = None) -> str | None:
    closes = deadline_of(rec)
    if closes is None:
        return None
    today = (now or datetime.now(timezone.utc)).date()
    days = (closes - today).days
    if days < 0:
        return None
    when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days" if days <= 10 else closes.strftime("%d %b")
    return f"⏳ closes {when}"


def due_reminders(state: dict, prefs: dict, now: datetime, is_open) -> list[dict]:
    """High matches closing within REMIND_DAYS that were not acted on and not yet reminded. `is_open(rec)` is the
    caller's notion of a job still worth showing (fresh, listed, not muted)."""
    skip = set(prefs.get("hidden") or []) | set(prefs.get("applied") or {})
    due = []
    for cid, rec in (state.get("jobs") or {}).items():
        closes = deadline_of(rec)
        if closes is None or rec.get("deadline_reminded") or rec.get("tier") != "high" or cid in skip:
            continue
        if 0 <= (closes - now.date()).days <= REMIND_DAYS and is_open(rec):
            due.append(rec)
    return sorted(due, key=lambda r: (r["deadline"], -int(r.get("score") or 0)))


def format_reminders(due: list[dict], now: datetime, code_of) -> str | None:
    if not due:
        return None
    from html import escape

    lines = ["⏳ <b>Closing soon — not applied yet</b>"]
    for rec in due[:8]:
        link = rec.get("apply_url") or rec.get("url")
        title = f'<a href="{escape(link)}">{escape(rec.get("title") or "")}</a>' if link else escape(rec.get("title") or "")
        lines += ["", f"• {title}" + (f" — {escape(rec['company'])}" if rec.get("company") else ""),
                  f"   {deadline_badge(rec, now)} · score {int(rec.get('score') or 0)} · <code>{code_of(rec)}</code>"]
    lines += ["", "/pitch code drafts the application · /applied code once sent · /hide code if it is not for you"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- reposts
def mark_reposts(jobs: dict) -> int:
    """Set rec["reposts"] = {"count", "first"} on accepted jobs that were posted before under another id."""
    groups: dict[tuple, list[tuple[datetime, str]]] = {}
    for cid, rec in jobs.items():
        company, title = normalize_company(rec.get("company")), normalize_title(rec.get("title"))
        first = parse_datetime(rec.get("first_seen"))
        if company and title and first is not None:
            groups.setdefault((company, title, fold(rec.get("country") or "")), []).append((first, cid))
    marked = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort()
        waves = [members[0][0]]  # postings at least REPOST_GAP_DAYS apart: one wave per genuine repost
        for first, cid in members[1:]:
            if first - waves[-1] >= timedelta(days=REPOST_GAP_DAYS):
                waves.append(first)
            earlier = [w for w in waves if first - w >= timedelta(days=REPOST_GAP_DAYS)]
            rec = jobs[cid]
            if earlier and rec.get("tier") in ("high", "possible"):
                rec["reposts"] = {"count": len(earlier), "first": earlier[0].date().isoformat()}
                marked += 1
    return marked


def repost_badge(rec: dict) -> str | None:
    info = rec.get("reposts") or {}
    if not info.get("count"):
        return None
    try:
        since = date.fromisoformat(info["first"]).strftime("%b %Y")
    except (ValueError, KeyError, TypeError):
        since = "earlier"
    times = "again" if info["count"] == 1 else f"{info['count']}× again"
    return f"♻️ posted {times} since {since}"
