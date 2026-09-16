"""Telegram Bot API notifications.

A job is marked notified only after Telegram confirms delivery (HTTP 200 and ok=true).
429 responses honour ``parameters.retry_after``. The bot token is never logged.
"""
from __future__ import annotations

import html
import logging
import time
from typing import Callable

import requests

from ..models import TIER_HIGH
from ..utils.dates import parse_datetime
from ..utils.location import ParsedLocation

log = logging.getLogger(__name__)
MAX_MESSAGE = 4096


def _esc(value) -> str:
    return html.escape(str(value), quote=True) if value is not None else ""


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
