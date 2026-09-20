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
# Cues that announce a closing date on their own, and weak ones that do so only in the right company: "closes" needs a
# subject ("this vacancy closes…", not "our office closes on 24 December"), and the French "avant le" / "jusqu'au" need
# an application verb before them ("candidatures avant le…", not "poste à pourvoir avant le…", "CDD jusqu'au…").
CUE_RE = re.compile(
    r"\b(closing date|close date|application deadline|deadline(?: for applications?)?|apply (?:by|before|no later than)|"
    r"applications? (?:close|closes|must be (?:received|submitted) by|due|accepted until|will be accepted until|deadline)|"
    r"submit(?:ted)? (?:your )?(?:application|cv|resume|candidature)s? (?:by|before|no later than)|posting end date|end date of posting|"
    r"date limite(?: de (?:candidature|depot|reception)s?)?|date de cloture|cloture des candidatures|bewerbungsfrist|bewerbungsschluss)\b")
WEAK_CUE_RE = re.compile(r"\b(closes? on|closes?|avant le|jusqu au|au plus tard le|open until)\b")
CLOSES_SUBJECT_RE = re.compile(r"\b(vacancy|advert\w*|posting|position|role|job|applications?|recruitment|competition|offre|poste)\b[^.;!?]{0,40}$")
FR_APPLY_RE = re.compile(r"\b(candidat\w*|postul\w*|cv|dossier|envoy\w*|adress\w*|transm\w*|depos\w*)\b[^.;!?]{0,40}$")
FR_NOT_APPLY_RE = re.compile(r"\b(pourvoir|demarrage|debut|prise de poste|cdd|cdi|contrat|mission|remplacement)\b[^.;!?]{0,40}$")
# Between the cue and the date there must be no sentence end and no other label ("Closing date: ongoing. Posted 01/09/2026")
GAP_STOP_RE = re.compile(r"[.;!?]\s|\b(?:posted|published|publication|publie\w*|start\w*|debut|demarrage|from|since|depuis)\b")
DATE_RES = (
    re.compile(r"\b(?P<y>20\d{2})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})\b"),
    re.compile(rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th|er)?(?: of)? (?P<mon>{_MONTH})\.?,? ?(?P<y>20\d{{2}})?\b"),
    re.compile(rf"\b(?P<mon>{_MONTH})\.? (?P<d>\d{{1,2}})(?:st|nd|rd|th)?,? ?(?P<y>20\d{{2}})?\b"),
    re.compile(r"\b(?P<d>\d{1,2})[-/.](?P<m>\d{1,2})[-/.](?P<y>20\d{2})\b"),
)
MONTH_FIRST = {"United States"}
DAY_FIRST = {"United Kingdom", "Ireland", "France", "Germany", "Netherlands", "Belgium", "Switzerland", "Luxembourg", "Spain", "Italy",
             "Portugal", "Austria", "Denmark", "Sweden", "Norway", "Finland", "Australia", "New Zealand", "Tunisia", "Morocco", "Algeria",
             "Egypt", "United Arab Emirates", "Saudi Arabia", "Qatar", "Kuwait", "Oman", "Bahrain", "India", "South Africa", "Senegal"}


def _build(match: re.Match, today: date, country: str | None) -> date | None:
    parts = match.groupdict()
    try:
        day = int(parts["d"])
        if parts.get("mon"):
            month = MONTHS[parts["mon"]]
        else:
            month = int(parts["m"])
            if match.re is DATE_RES[0]:
                pass  # ISO order: nothing to guess
            elif month > 12 >= day:
                day, month = month, day
            elif day <= 12 and month <= 12 and day != month and country not in DAY_FIRST:
                if country in MONTH_FIRST:
                    day, month = month, day
                else:  # "10/08/2026" in Canada or for a remote role: read both ways, never hide a job on a guess
                    year = int(parts["y"])
                    a, b = date(year, month, day), date(year, day, month)
                    if a >= today and b >= today:
                        return min(a, b)
                    if a < today and b < today:
                        return max(a, b)
                    return None
        if parts.get("y"):
            return date(int(parts["y"]), month, day)
        guess = date(today.year, month, day)  # no year given: the next such day
        return guess if guess >= today - timedelta(days=30) else date(today.year + 1, month, day)
    except (ValueError, KeyError):
        return None


def _cues(folded: str):
    for cue in CUE_RE.finditer(folded):
        yield cue
    for cue in WEAK_CUE_RE.finditer(folded):
        segment = re.split(r"[.;!?] ", folded[max(0, cue.start() - 70):cue.start()])[-1]  # the cue's own sentence so far
        word = cue.group(1)
        if word.startswith("close") or word == "open until":
            if segment.strip(" :-") and not CLOSES_SUBJECT_RE.search(segment):
                continue  # "our office closes on 24 December"; a bare label "Closes: 4 Oct" has nothing before it
        elif not FR_APPLY_RE.search(segment) or FR_NOT_APPLY_RE.search(segment):
            continue
        yield cue


def find_deadline(text: str | None, now: datetime, country: str | None = None) -> date | None:
    """The application deadline stated in a description, or None. Only dates right after a deadline phrase count."""
    if not text:
        return None
    flat = re.sub(r"<[^>]+>", " . ", re.sub(r"[\n\r\u2022]+", " . ", text))  # a block or line end is a sentence end
    folded = fold(flat)
    folded = re.sub(r"[^a-z0-9/.\-:;!? ,]+", " ", folded)
    folded = re.sub(r"(?: \.)+ ", " . ", re.sub(r"\s+", " ", folded))
    today = now.date()
    for cue in sorted(_cues(folded), key=lambda m: m.start()):
        window = folded[cue.end(): cue.end() + 60]
        window = re.sub(r"^\s*[:\-]?\s*(?:\.\s+)?", "", window)  # "Closing Date:" followed by a line break and the date
        best = None
        for pattern in DATE_RES:
            found = pattern.search(window)
            if found and (best is None or found.start() < best[0]):
                best = (found.start(), found)
        if best is None or best[0] > 25 or GAP_STOP_RE.search(window[:best[0]]):
            continue
        built = _build(best[1], today, country)
        if built and -60 <= (built - today).days <= MAX_AHEAD_DAYS:
            return built
    return None


def annotate_deadline(rec: dict, text: str | None, now: datetime) -> bool:
    """Set, replace or clear the stored deadline from the posting text; the AI review's reading fills in when the rules
    find nothing. Without a text the stored value is left alone (a feed that carries no description proves nothing)."""
    if not text or len(text) < 200:
        return bool(rec.get("deadline"))
    found = find_deadline(text, now, rec.get("country"))
    if found is None:
        try:
            ai_date = date.fromisoformat(str((rec.get("ai") or {}).get("deadline") or ""))
            if -60 <= (ai_date - now.date()).days <= MAX_AHEAD_DAYS:
                found = ai_date
        except ValueError:
            pass
    if found is None:
        rec.pop("deadline", None)  # a date read by an older, laxer rule, or removed from the posting: never keep hiding the job
        rec["deadline_reminded"] = False
        return False
    if rec.get("deadline") != found.isoformat():
        rec["deadline_reminded"] = False
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
