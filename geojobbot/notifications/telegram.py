"""Telegram Bot API notifications.

A job is marked notified only after Telegram confirms delivery (HTTP 200 and ok=true).
429 responses honour ``parameters.retry_after``. The bot token is never logged.
"""
from __future__ import annotations

import html
import logging
import time
from datetime import datetime, timezone
from typing import Callable

import requests

from ..models import TIER_HIGH
from ..utils.dates import parse_datetime
from ..insights.sponsors import sponsor_badge
from ..insights.visa import badge as visa_badge
from ..utils.location import ParsedLocation
from ..utils.text import job_code, normalize_company, normalize_title

log = logging.getLogger(__name__)
MAX_MESSAGE = 4096
DIGEST_SKILLS_SHOWN = 4


def _esc(value) -> str:
    return html.escape(str(value), quote=True) if value is not None else ""


def _location_of(rec: dict) -> ParsedLocation:
    return ParsedLocation(raw=rec.get("location_raw") or "", city=rec.get("city"), region=rec.get("region"),
                          country=rec.get("country"), remote=rec.get("remote"), remote_scope=rec.get("remote_scope"),
                          work_mode=rec.get("work_mode"))


def group_same_posting(records: list[dict]) -> list[list[dict]]:
    """Group records that are the same title at the same company (one posting per city is still one job to read).

    Order is preserved: a group sits where its first member was. Records without a company never group.
    """
    groups: dict[tuple, list[dict]] = {}
    ordered: list[list[dict]] = []
    for rec in records:
        company = normalize_company(rec.get("company"))
        key = (company, normalize_title(rec.get("title"))) if company else ("", id(rec))
        if key not in groups:
            groups[key] = []
            ordered.append(groups[key])
        groups[key].append(rec)
    return ordered


def _digest_entry(rec: dict, index: int, same: list[dict] | None = None) -> str:
    """One numbered entry of the digest list; `same` holds the other records of the same posting."""
    same = same or [rec]
    updated = bool(rec.get("notified") and rec.get("pending_update_alert"))
    head = f"{index}. <b>{_esc(rec.get('title'))}</b>"
    if rec.get("company"):
        head += f" — {_esc(rec['company'])}"
    if len(same) > 1:
        head += f" ×{len(same)}"
    head += f" · {int(rec.get('score') or 0)}/100"
    codes = [job_code(r.get("canonical_id")) for r in same[:3] if r.get("canonical_id")]
    if codes:
        head += " · " + " ".join(f"<code>{c}</code>" for c in codes)  # handles for /why, /applied, /hide
    if updated:
        head = "🔁 " + head
    places = list(dict.fromkeys(_location_of(r).display() for r in same))
    facts = [f"📍 {_esc(' | '.join(places[:3]))}" + (f" +{len(places) - 3}" if len(places) > 3 else "")]
    posted = parse_datetime(rec.get("posted_at"))
    if posted:
        facts.append(f"📅 {posted.strftime('%d %b')}")
    if rec.get("salary"):
        facts.append(f"💰 {_esc(rec['salary'])}")
    if "Visa sponsorship offered" in (rec.get("why_matched") or []):
        facts.append("🛂 sponsorship offered")
    badge = sponsor_badge(rec)
    if badge:
        facts.append(_esc(badge))
    if rec.get("watched"):
        facts.append("👀 watched employer")
    skills = [s.split(" (")[0] for s in rec.get("matched_skills") or []]
    tail = []
    if skills:
        tail.append(_esc(", ".join(dict.fromkeys(skills[:DIGEST_SKILLS_SHOWN]))))
    link = rec.get("apply_url") or rec.get("url")
    if link:
        tail.append(f'<a href="{_esc(link)}">Apply</a>')
    lines = [head, "   " + " · ".join(facts)]
    review = rec.get("ai") or {}
    if review.get("summary"):  # Workers AI second opinion (English, also for French/Arabic/German postings)
        lines.append(f"   💡 {_esc(review['summary'])}")
    if review.get("concerns"):
        lines.append(f"   ⚠️ {_esc(review['concerns'])}")
    route = visa_badge(rec)
    if route:
        lines.append(f"   {_esc(route)}")
    if tail:
        lines.append("   " + " · ".join(tail))
    return "\n".join(lines)


def format_digest(records: list[dict], *, now=None, part_limit: int = MAX_MESSAGE,
                  title: str | None = None) -> list[tuple[str, list[dict]]]:
    """Format records as one numbered list, split into as few Telegram messages as fit.

    Returns [(message_text, records_in_that_message)], so delivery can be tracked per message.
    High matches come first, then Possible ones; numbering runs across parts.
    """
    if not records:
        return []
    high = [r for r in records if r.get("tier") == TIER_HIGH]
    possible = [r for r in records if r.get("tier") != TIER_HIGH]
    stamp = (now or datetime.now(timezone.utc)).strftime("%d %b %Y %H:%M UTC")
    plural = "es" if len(records) != 1 else ""
    heading = title or f"Geospatial jobs — {len(records)} new match{plural}"
    header = (f"🗺️ <b>{_esc(heading)}</b>{{part}}\n"
              f"<i>{stamp} · {len(high)} high · {len(possible)} possible</i>")

    entries = []  # (section title on the first entry of a section, entry text, records covered, section)
    index = 0
    for section, tier_records in (("🔥 <b>High matches</b>", high), ("🟡 <b>Possible matches</b>", possible)):
        for position, same in enumerate(group_same_posting(tier_records)):
            index += 1
            entries.append((section if position == 0 else None, _digest_entry(same[0], index, same), same, section))

    budget = part_limit - len(header) - 16  # room for " (part 10/10)"
    parts: list[tuple[list[str], list[dict]]] = []
    blocks, recs, size = [], [], 0
    for section, text, same, current_section in entries:
        block = f"\n\n{section}\n\n{text}" if section else f"\n\n{text}"
        if blocks and size + len(block) > budget:
            parts.append((blocks, recs))
            blocks, recs, size = [], [], 0
            if section is None:  # continuation part: repeat the section title
                block = f"\n\n{current_section} (cont.)\n\n{text}"
        blocks.append(block)
        recs.extend(same)  # every record of a grouped posting is delivered (and marked) with its entry
        size += len(block)
    if blocks:
        parts.append((blocks, recs))

    out = []
    for number, (blocks, recs) in enumerate(parts, 1):
        part = f" (part {number}/{len(parts)})" if len(parts) > 1 else ""
        text = header.replace("{part}", part) + "".join(blocks)
        out.append((text if len(text) <= part_limit else text[: part_limit - 1] + "…", recs))
    return out


def format_job_message(rec: dict, *, update: bool = False) -> str:
    emoji = "🔥" if rec.get("tier") == TIER_HIGH else "🟡"
    label = "HIGH MATCH" if rec.get("tier") == TIER_HIGH else "POSSIBLE MATCH"
    if update:
        label = f"UPDATED — {label}"
    loc = ParsedLocation(raw=rec.get("location_raw") or "", city=rec.get("city"), region=rec.get("region"),
                         country=rec.get("country"), remote=rec.get("remote"), remote_scope=rec.get("remote_scope"),
                         work_mode=rec.get("work_mode"))
    lines = [f"{emoji} <b>{label} — {int(rec.get('score') or 0)}/100</b>", ""]
    lines.append(f"💼 <b>{_esc(rec.get('title'))}</b>")
    if rec.get("company"):
        lines.append(f"🏢 {_esc(rec['company'])}")
    lines.append(f"📍 {_esc(loc.display())}")
    meta = [x for x in (rec.get("employment_type"), (rec.get("work_mode") or "").capitalize() or None) if x]
    if meta:
        lines.append(f"🕒 {_esc(' · '.join(dict.fromkeys(meta)))}")
    if rec.get("salary"):
        lines.append(f"💰 {_esc(rec['salary'])}")
    posted = parse_datetime(rec.get("posted_at"))
    if posted:
        lines.append(f"📅 Posted {posted.strftime('%Y-%m-%d')}" + ("" if rec.get("posted_at_reliable") else " (unverified)"))
    skills = [s.split(" (")[0] for s in rec.get("matched_skills") or []]
    skill_line = list(dict.fromkeys(skills + (rec.get("matched_domains") or [])))[:8]
    if skill_line:
        lines += ["", "<b>Matched skills:</b>", _esc(", ".join(skill_line))]
    why = (rec.get("why_matched") or [])[:6]
    if why:
        lines += ["", "<b>Why it matched:</b>"] + [f"• {_esc(w)}" for w in why]
    sources = sorted({s.get("source_name") for s in rec.get("sources") or [] if s.get("source_name")})
    if sources:
        lines += ["", f"🔎 Sources: {_esc(', '.join(sources[:5]))}"]
    link = rec.get("apply_url") or rec.get("url")
    if link:
        lines += ["", f'🔗 <a href="{_esc(link)}">Apply</a>']
    text = "\n".join(lines)
    return text if len(text) <= MAX_MESSAGE else text[: MAX_MESSAGE - 1] + "…"


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, *, session: requests.Session | None = None,
                 delay_s: float = 1.2, sleep: Callable[[float], None] = time.sleep, max_retries: int = 2,
                 timeout: float = 20.0):
        self.token = token
        self.chat_id = chat_id
        self.session = session or requests.Session()
        self.delay_s = max(1.2, delay_s)
        self.sleep = sleep
        self.max_retries = max_retries
        self.timeout = timeout
        self._last_sent = 0.0

    def _redact(self, text: str) -> str:
        return text.replace(self.token, "***") if self.token else text

    def send(self, text: str) -> tuple[bool, str | None]:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        attempt = 0
        while True:
            wait = self.delay_s - (time.monotonic() - self._last_sent)
            if self._last_sent and wait > 0:
                self.sleep(wait)
            self._last_sent = time.monotonic()
            try:
                response = self.session.post(url, json=payload, timeout=self.timeout)
            except requests.exceptions.RequestException as exc:
                error = f"network error: {type(exc).__name__}"
                if attempt < self.max_retries:
                    attempt += 1
                    self.sleep(2 ** attempt)
                    continue
                return False, error
            try:
                body = response.json()
            except ValueError:
                body = {}
            if response.status_code == 200 and body.get("ok") is True:
                return True, None
            if response.status_code == 429 and attempt < self.max_retries:
                retry_after = int((body.get("parameters") or {}).get("retry_after") or 5)
                attempt += 1
                self.sleep(min(retry_after, 60))
                continue
            if response.status_code >= 500 and attempt < self.max_retries:
                attempt += 1
                self.sleep(2 ** attempt)
                continue
            description = body.get("description") or response.reason or "unknown error"
            return False, self._redact(f"HTTP {response.status_code}: {description}")
