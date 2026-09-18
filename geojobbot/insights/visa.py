"""Is a work visa realistic for this job? (/visa, a line in digests and /why)

The sponsor badge says the employer *can* sponsor. This puts the facts of one job next to the rules of the country's
main work-visa route: is the employer licensed (official registers), does the posted salary reach the legal minimum,
is the occupation of the right kind, and do the candidate's language or nationality open a lighter route (Francophone
Mobility in Canada, the France–Tunisia agreement). The rules live in config/visa_paths.toml with their sources and an
`as_of` date. The result is indicative: it orders and annotates, it never rejects a job.
"""
from __future__ import annotations

import logging
import re
import tomllib
from datetime import timedelta
from functools import lru_cache
from html import escape
from pathlib import Path

from ..utils.dates import parse_datetime
from ..utils.text import fold

log = logging.getLogger(__name__)

RULES_FILE = Path(__file__).resolve().parents[2] / "config" / "visa_paths.toml"
RANK = {"strong": 0, "open": 1, "hard": 2, "blocked": 3}
VERDICT_ICON = {"strong": "🟢", "open": "🟡", "hard": "🟠", "blocked": "⛔"}
VERDICT_TEXT = {"strong": "looks open", "open": "possible, facts missing", "hard": "hard from abroad", "blocked": "blocked"}
MARK = {True: "✓", False: "✗", None: "?"}

COUNTRY_CURRENCY = {
    "United Kingdom": "GBP", "Canada": "CAD", "Australia": "AUD", "New Zealand": "NZD", "United States": "USD",
    "Switzerland": "CHF", "United Arab Emirates": "AED", "Saudi Arabia": "SAR", "Qatar": "QAR", "Tunisia": "TND", "Denmark": "DKK", "Sweden": "SEK", "Norway": "NOK",
    "Germany": "EUR", "France": "EUR", "Netherlands": "EUR", "Ireland": "EUR", "Belgium": "EUR", "Austria": "EUR",
    "Spain": "EUR", "Italy": "EUR", "Luxembourg": "EUR", "Portugal": "EUR", "Finland": "EUR",
}
CURRENCY_SYMBOL = {"GBP": "£", "EUR": "€", "USD": "US$", "CAD": "CA$", "AUD": "A$", "DKK": "DKK "}
DOLLAR_COUNTRIES = {"CAD", "AUD", "NZD", "USD"}
PER_YEAR = {"hour": 2080, "day": 260, "week": 52, "month": 12, "year": 1}

NUMBER_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:[ ,.\u00a0\u202f]\d{3})+|\d+(?:[.,]\d+)?)\s*(k\b)?", re.I)
CODE_RE = re.compile(r"\b(GBP|EUR|USD|CAD|AUD|NZD|CHF|AED|SAR|QAR|TND|DKK|SEK|NOK)\b")
PERIODS = (
    ("hour", r"\b(hour|hourly|hr|heure|horaire|stunde)\b|/ ?h\b"),
    ("day", r"\b(day|daily|jour|journalier|tag)\b"),
    ("week", r"\b(week|weekly|semaine|woche)\b"),
    ("month", r"\b(month|monthly|mois|mensuel\w*|monat\w*|maand)\b|/ ?mo\b"),
    ("year", r"\b(year|yearly|annual\w*|annum|an|annee|annuel\w*|jahr\w*|jaar|pa)\b|/ ?yr\b"),
)

TECHNICIAN_RE = re.compile(r"\b(technician|technicien\w*|technologist|technologue|operator|operateur\w*|drafter|draughts\w*|"
                           r"dessinat\w+|assistant\w*|clerk|aide|trainee)\b")
SPATIAL_RE = re.compile(r"\b(survey\w*|geometre\w*|topograph\w*|arpenteu\w*|hydrograph\w*|geodes\w*|cartograph\w*|gis|sig|"
                        r"geospatial|geomati\w+|spatial|remote sensing|teledetection|photogramm\w*|lidar|mapping)\b")
BILATERAL_SURE_RE = re.compile(r"\b(geometre\w*|topograph\w*|survey\w*|arpenteu\w*|dessinat\w+|projeteu\w*|charge\w* d etudes?)\b")
BILATERAL_MAYBE_RE = re.compile(r"\b(informaticien\w*|developpeu\w*|developer|software|ingenieur\w*|engineer|sig|gis|geomati\w+|"
                                r"cartograph\w*|data)\b")


@lru_cache(maxsize=4)
def load_rules(path: str | None = None) -> dict:
    """{"as_of": str, "paths": {country: [rule, ...]}}; an unreadable file disables the feature, never the run."""
    try:
        with open(path or RULES_FILE, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("visa rules not loaded: %s", type(exc).__name__)
        return {"as_of": None, "paths": {}}
    paths: dict[str, list[dict]] = {}
    for rule in data.get("path") or []:
        if rule.get("country") and rule.get("name"):
            paths.setdefault(rule["country"], []).append(rule)
    return {"as_of": data.get("as_of"), "paths": paths}


# ---------------------------------------------------------------------------- salary
def _amount(raw: str, kilo: bool) -> float | None:
    text = raw.replace("\u00a0", " ").replace("\u202f", " ")
    if re.fullmatch(r"\d{1,3}(?:[ ,.]\d{3})+", text):
        value = float(re.sub(r"[ ,.]", "", text))
    else:
        try:
            value = float(text.replace(",", "."))
        except ValueError:
            return None
    return value * 1000 if kilo else value


def parse_salary(text: str | None, country: str | None) -> dict | None:
    """{"currency", "low", "high", "period"} from the free-text salary of a posting, or None when it is unusable."""
    if not text:
        return None
    local = COUNTRY_CURRENCY.get(country or "")
    code = CODE_RE.search(text.upper())
    if code:
        currency = code.group(1)
    elif "£" in text:
        currency = "GBP"
    elif "€" in text:
        currency = "EUR"
    elif "$" in text:
        currency = local if local in DOLLAR_COUNTRIES else "USD"
    else:
        currency = local
    folded = fold(text)
    period = next((name for name, pattern in PERIODS if re.search(pattern, folded)), None)
    amounts = [a for a in (_amount(m.group(1), bool(m.group(2))) for m in NUMBER_RE.finditer(text)) if a and a >= 8]
    if not amounts or currency is None:
        return None
    low, high = min(amounts[:2]), max(amounts[:2])
    if period is None:  # no unit given: judge by magnitude
        period = "hour" if high < 250 else "month" if high < 15000 else "year"
    yearly = high * PER_YEAR[period]
    if not 4000 <= yearly <= 1_500_000:
        return None
    return {"currency": currency, "low": low, "high": high, "period": period}


def _in_period(amount: float, period: str, rule: dict) -> float:
    """Convert a posted amount to the rule's period (the Dutch monthly norm excludes the 8% holiday allowance)."""
    target = rule.get("period") or "year"
    if period == target:
        return amount
    yearly = amount * PER_YEAR[period]
    if target == "month":
        return yearly / float(rule.get("year_to_month") or (12.96 if rule.get("country") == "Netherlands" else 12))
    return yearly / PER_YEAR[target]


def _money(amount: float, currency: str) -> str:
    return f"{CURRENCY_SYMBOL.get(currency, currency + ' ')}{amount:,.0f}"


def _salary_check(rec: dict, rule: dict) -> dict | None:
    minimum = rule.get("salary_min")
    if not minimum:
        return None
    currency, per = rule.get("currency") or "", rule.get("period") or "year"
    need = f"{_money(minimum, currency)}/{per}"
    reduced = rule.get("salary_low")
    posted = parse_salary(rec.get("salary"), rec.get("country"))
    if posted is None or posted["currency"] != currency:
        text = f"salary not stated (minimum {need}"
        if reduced:
            text += f", {_money(reduced, currency)} for {rule.get('salary_low_label') or 'reduced cases'}"
        return {"k": "salary", "ok": None, "text": text + ")"}
    low, high = _in_period(posted["low"], posted["period"], rule), _in_period(posted["high"], posted["period"], rule)
    shown = _money(high, currency) if abs(high - low) < 1 else f"{_money(low, currency)}–{_money(high, currency)}"
    if low >= minimum:
        return {"k": "salary", "ok": True, "text": f"salary {shown} ≥ {need}"}
    if high < (reduced or minimum):
        return {"k": "salary", "ok": False, "text": f"salary {shown} is below the minimum {_money(reduced or minimum, currency)}/{per}"}
    if high < minimum:
        return {"k": "salary", "ok": None, "text": f"salary {shown} only reaches the reduced minimum {_money(reduced, currency)} "
                                                  f"({rule.get('salary_low_label') or 'reduced cases'}); standard is {need}"}
    return {"k": "salary", "ok": None, "text": f"salary range {shown} straddles the minimum {need}"}


# ---------------------------------------------------------------------------- the other checks
def _offered(rec: dict) -> bool | None:
    """True: the posting offers sponsorship. False: it says it does not. None: silent."""
    if "Visa sponsorship offered" in (rec.get("why_matched") or []):
        return True
    reading = (rec.get("ai") or {}).get("sponsorship")
    return True if reading == "offered" else False if reading == "not_offered" else None


def _sponsor_check(rec: dict, rule: dict) -> dict:
    kind = rule.get("sponsor") or "employer"
    offered = _offered(rec)
    if kind == "default":
        return {"k": "sponsor", "ok": True, "text": "employer-sponsored by default"}
    hit = next((h for h in rec.get("sponsor") or [] if h.get("country") == rule.get("country")), None)
    if hit:
        text = f"{hit.get('label')}: {hit.get('name')}"
        if hit.get("geo"):
            text += " (already hired " + ", ".join(hit.get("occupations") or ["geomatics staff"])[:60] + ")"
        if hit.get("match") == "variant":
            text += " (name variant: check)"
        return {"k": "sponsor", "ok": True, "text": text}
    if offered:
        return {"k": "sponsor", "ok": True, "text": "the posting offers sponsorship"}
    if offered is False:
        return {"k": "sponsor", "ok": False, "text": "the posting says it does not sponsor"}
    if rule.get("no_lmia"):
        return {"k": "sponsor", "ok": None, "text": "no LMIA needed, but the employer must agree to file the offer"}
    if kind.startswith("register:"):
        return {"k": "sponsor", "ok": None, "text": "employer not found on the official register under this name"}
    return {"k": "sponsor", "ok": None, "text": "the posting does not mention sponsorship"}


def _occupation_check(rec: dict, rule: dict) -> dict | None:
    kind = rule.get("occupation") or "any"
    title = fold(rec.get("title") or "")
    if kind == "graduate":
        if TECHNICIAN_RE.search(title):
            return {"k": "occupation", "ok": None, "text": "technician-level title: the route is for graduate-level occupations"}
        return {"k": "occupation", "ok": True, "text": "graduate-level occupation"}
    if kind == "spatial":
        if SPATIAL_RE.search(title) and not TECHNICIAN_RE.search(title):
            return {"k": "occupation", "ok": True, "text": "surveying and spatial science occupations are listed"}
        return {"k": "occupation", "ok": None, "text": "check the occupation list for this title"}
    if kind == "bilateral":
        if BILATERAL_SURE_RE.search(title):
            return {"k": "occupation", "ok": True, "text": "métier on the agreement's list (no labour-market test)"}
        if BILATERAL_MAYBE_RE.search(title):
            return {"k": "occupation", "ok": None, "text": "may fit “Chargé d'études techniques” or “Informaticien d'étude” on the agreement's list"}
        return None  # the route does not apply
    return None


def _applies(rec: dict, rule: dict, settings) -> bool:
    language = rule.get("requires_language")
    if language and fold(language) not in {fold(x) for x in getattr(settings, "my_languages", None) or []}:
        return False
    home = rule.get("requires_home")
    if home and fold(home) not in {fold(x) for x in getattr(settings, "home_countries", None) or []}:
        return False
    regions = {fold(r) for r in rule.get("exclude_regions") or []}
    if regions:
        place = {fold(rec.get("region") or ""), fold(rec.get("city") or "")} | set(fold(rec.get("location_raw") or "").replace(",", " ").split())
        if regions & place:
            return False
    return True


def _assess_rule(rec: dict, rule: dict) -> dict | None:
    occupation = _occupation_check(rec, rule)
    if rule.get("occupation") == "bilateral" and occupation is None:
        return None
    checks = [c for c in (_sponsor_check(rec, rule), _salary_check(rec, rule), occupation) if c]
    if rule.get("requires_language"):
        checks.append({"k": "language", "ok": True, "text": f"you speak {rule['requires_language']}"})
    if rule.get("hard"):
        verdict = "hard"
    elif any(c["ok"] is False for c in checks):
        verdict = "blocked"
    elif all(c["ok"] for c in checks):
        verdict = "strong"
    else:
        verdict = "open"
    return {"path": rule["name"], "country": rule["country"], "verdict": verdict, "checks": checks,
            "note": rule.get("note"), "url": rule.get("url"), "no_lmia": bool(rule.get("no_lmia"))}


def assess(rec: dict, settings, rules: dict | None = None) -> dict | None:
    """Best route for one stored job plus the alternatives, or None when no visa question arises or no rule exists."""
    rules = rules if rules is not None else load_rules()
    country = rec.get("country")
    if not country or fold(country) in {fold(x) for x in getattr(settings, "home_countries", None) or []}:
        return None
    candidates = [a for a in (_assess_rule(rec, rule) for rule in rules["paths"].get(country, []) if _applies(rec, rule, settings)) if a]
    if not candidates:
        return None
    candidates.sort(key=lambda a: RANK[a["verdict"]])
    best = dict(candidates[0])
    best["others"] = [{"path": a["path"], "verdict": a["verdict"], "line": line(a)} for a in candidates[1:3]]
    best["line"] = line(best)
    best["as_of"] = rules.get("as_of")
    return best


def annotate(rec: dict, settings, rules: dict | None = None) -> bool:
    """Store the assessment on the job (recomputed every run: sponsor hits and AI readings arrive later)."""
    result = assess(rec, settings, rules)
    _apply_penalty(rec, result, settings)
    if result is None:
        rec.pop("visa", None)
        return False
    rec["visa"] = result
    return True


def _apply_penalty(rec: dict, result: dict | None, settings) -> None:
    """A route that is hard or blocked, with no sign that the employer sponsors, costs VISA_PENALTY points.

    Kept in score_breakdown["visa"] so it is applied once, shown in /why, and lifted again when the facts change
    (a register hit or an AI reading that sponsorship is offered). It never adds a rejection reason.
    """
    from .sponsors import retier  # local import: sponsors does not know about visa routes

    breakdown = rec.setdefault("score_breakdown", {})
    wanted = 0
    size = int(getattr(settings, "visa_penalty", 0) or 0)
    if result is not None and size and result["verdict"] in ("hard", "blocked"):
        sponsor = next((c for c in result.get("checks") or [] if c["k"] == "sponsor"), None)
        if not (sponsor and sponsor["ok"] is True and result["verdict"] == "hard"):
            wanted = -size
    current = int(breakdown.get("visa") or 0)
    if wanted == current or set(rec.get("rejection_reasons") or []) - {"LOW_SCORE"}:
        return
    rec["score"] = max(0, min(100, int(rec.get("score") or 0) + wanted - current))
    if wanted:
        breakdown["visa"] = wanted
    else:
        breakdown.pop("visa", None)
    retier(rec, settings)


# ---------------------------------------------------------------------------- text
def line(assessment: dict) -> str:
    """One plain-text line: route, then each check with its mark."""
    parts = [f"{c['k']} {MARK[c['ok']]}" for c in assessment.get("checks") or []]
    return f"{assessment['path']}: " + " · ".join(parts)


def badge(rec: dict) -> str | None:
    """Digest line, only when it says something the sponsor badge does not."""
    visa = rec.get("visa") or {}
    verdict = visa.get("verdict")
    if verdict == "strong":
        return f"🟢 Visa route looks open — {visa['line']}"
    if verdict == "blocked":
        failed = next((c["text"] for c in visa.get("checks") or [] if c["ok"] is False), "a requirement is not met")
        return f"⛔ Visa: {failed} ({visa['path']})"
    if verdict == "hard":
        return f"🟠 Visa: {visa['path']} is hard from abroad"
    if visa.get("no_lmia"):
        return "🍁 No LMIA needed for French speakers (Francophone Mobility)"
    return None


def detail_lines(rec: dict, now=None) -> list[str]:
    """Block for /why: every check in words, the note, the source and the date of the rules (HTML)."""
    visa = rec.get("visa")
    if not visa:
        return []
    lines = [f"{VERDICT_ICON.get(visa['verdict'], '🛂')} <b>Visa route: {escape(visa['path'])}</b> — {VERDICT_TEXT.get(visa['verdict'], '')}"]
    lines += [f"   {MARK[c['ok']]} {escape(c['text'])}" for c in visa.get("checks") or []]
    penalty = int((rec.get("score_breakdown") or {}).get("visa") or 0)
    if penalty:
        lines.append(f"   score {penalty} points: this route is {VERDICT_TEXT.get(visa['verdict'], visa['verdict'])} and nothing says the employer sponsors")
    if visa.get("note"):
        lines.append(f"   <i>{escape(visa['note'])}</i>")
    for other in visa.get("others") or []:
        lines.append(f"   also: {escape(other['line'])} ({VERDICT_TEXT.get(other['verdict'], other['verdict'])})")
    tail = f"rules as of {visa.get('as_of') or '?'}"
    checked = parse_datetime(visa.get("as_of"))
    if now is not None and checked is not None and now - checked > timedelta(days=365):
        tail += " — over a year old, verify"
    if visa.get("url"):
        tail = f'<a href="{escape(visa["url"])}">official source</a> · ' + tail
    lines.append(f"   {tail} · indicative, not legal advice")
    return lines


def format_list(rows: list[dict], code_of, limit: int = 12) -> str:
    """/visa: current matches ordered by how open the route looks."""
    rated = [r for r in rows if r.get("visa")]
    if not rated:
        return ("🛂 No current match is in a country I have visa rules for yet.\n"
                "/visa france (or uk, canada, germany, netherlands, ireland, australia…) shows a country's route.")
    rated.sort(key=lambda r: (RANK[r["visa"]["verdict"]], -int(r.get("score") or 0)))
    counts = {v: sum(1 for r in rated if r["visa"]["verdict"] == v) for v in RANK}
    lines = ["🛂 <b>Visa routes of current matches</b>",
             "<i>" + " · ".join(f"{VERDICT_ICON[v]} {counts[v]} {VERDICT_TEXT[v]}" for v in RANK if counts[v]) + "</i>"]
    for i, rec in enumerate(rated[:limit], 1):
        visa = rec["visa"]
        link = rec.get("apply_url") or rec.get("url")
        title = f'<a href="{escape(link)}">{escape(rec.get("title") or "")}</a>' if link else f"<b>{escape(rec.get('title') or '')}</b>"
        lines += ["", f"{i}. {title}" + (f" — {escape(rec['company'])}" if rec.get("company") else ""),
                  f"   {VERDICT_ICON[visa['verdict']]} {escape(visa['country'])} · score {int(rec.get('score') or 0)} · "
                  f"<code>{code_of(rec)}</code>"]
        lines += [f"   {MARK[c['ok']]} {escape(c['text'])}" for c in visa.get("checks") or []]
    lines += ["", "/why code shows the rule, its source and the alternatives · /visa country explains a route",
              "<i>Indicative, not legal advice.</i>"]
    text = "\n".join(lines)
    return text if len(text) <= 4096 else text[:4095] + "…"


COUNTRY_ALIASES = {"uk": "United Kingdom", "britain": "United Kingdom", "england": "United Kingdom", "usa": "United States",
                   "america": "United States", "uae": "United Arab Emirates", "emirates": "United Arab Emirates",
                   "dubai": "United Arab Emirates", "saudi": "Saudi Arabia", "ksa": "Saudi Arabia", "holland": "Netherlands",
                   "pays-bas": "Netherlands", "allemagne": "Germany", "royaume-uni": "United Kingdom", "irlande": "Ireland",
                   "australie": "Australia", "danemark": "Denmark", "suisse": "Switzerland", "etats-unis": "United States"}


def find_country(text: str, rules: dict | None = None) -> str | None:
    rules = rules if rules is not None else load_rules()
    wanted = fold(text)
    if not wanted:
        return None
    if wanted in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[wanted]
    exact = next((c for c in rules["paths"] if fold(c) == wanted), None)
    if exact or len(wanted) < 5:  # "can i get a visa" must not mean Canada
        return exact
    starts = [c for c in rules["paths"] if fold(c).startswith(wanted)]
    return starts[0] if len(starts) == 1 else None


def country_card(country: str, rules: dict | None = None) -> str:
    rules = rules if rules is not None else load_rules()
    lines = [f"🛂 <b>Work-visa routes: {escape(country)}</b>", f"<i>rules as of {rules.get('as_of') or '?'} · indicative, not legal advice</i>"]
    for rule in rules["paths"].get(country, []):
        lines += ["", f"<b>{escape(rule['name'])}</b>" + (" — hard from abroad" if rule.get("hard") else "")]
        if rule.get("salary_min"):
            currency, per = rule.get("currency") or "", rule.get("period") or "year"
            pay = f"   minimum pay {_money(rule['salary_min'], currency)}/{per}"
            if rule.get("salary_low"):
                pay += f" · {_money(rule['salary_low'], currency)} for {rule.get('salary_low_label')}"
            lines.append(escape(pay))
        sponsor = rule.get("sponsor") or "employer"
        lines.append("   employer: " + ("must hold a sponsor licence (I check the official register)" if sponsor.startswith("register:")
                                        else "sponsors as a matter of course" if sponsor == "default" else "any employer can sponsor"))
        if rule.get("requires_language"):
            lines.append(f"   for {escape(rule['requires_language'])} speakers")
        if rule.get("requires_home"):
            lines.append(f"   for nationals of {escape(rule['requires_home'])}")
        if rule.get("note"):
            lines.append(f"   <i>{escape(rule['note'])}</i>")
        if rule.get("url"):
            lines.append(f'   <a href="{escape(rule["url"])}">official source</a>')
    return "\n".join(lines)


def cards(rules: dict | None = None) -> dict:
    """{folded country name or alias: card} so the Worker can answer /visa <country> without Python."""
    rules = rules if rules is not None else load_rules()
    out = {fold(country): country_card(country, rules) for country in rules["paths"]}
    for alias, country in COUNTRY_ALIASES.items():
        if country in rules["paths"]:
            out[alias] = out[fold(country)]
    return out
