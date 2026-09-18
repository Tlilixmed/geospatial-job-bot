"""Two-way Telegram: poll getUpdates, execute commands sent from the configured chat, reply.

There is no server: the scraper calls this at the start of every run and .github/workflows/commands.yml
calls it on a short schedule. Telegram keeps undelivered updates for 24 hours, and confirming them with
an offset means two pollers never answer the same message. Messages from any other chat are ignored.
"""
from __future__ import annotations

import logging
import os
from collections import Counter

import requests

from ..ai.client import WorkersAI
from ..ai.review import write_pitch
from ..core.jobs import alert_block_reason
from ..core.prefs import load_prefs, save_prefs
from ..models import TIER_HIGH, TIER_POSSIBLE
from ..utils.dates import parse_datetime, to_iso, utcnow
from ..utils.text import fold, job_code
from .intents import interpret
from .telegram import MAX_MESSAGE, _esc, format_digest
from .weekly import format_weekly

log = logging.getLogger(__name__)

HELP = """🗺️ <b>Geospatial job bot — commands</b>

<b>Find</b>
/jobs [n] — best current matches (default 10)
/high [n] — High matches only
/search words — search stored jobs (plain text works too)
/why code — why a job matched, score breakdown and the AI second opinion
/pitch code — AI drafts a short application note for that job

<b>Track</b>
/applied code — mark as applied (no more alerts for it)
/applied — list what you applied to
/hide code · /unhide code — dismiss or restore a job

<b>Tune</b>
/mute text · /unmute text · /muted — silence a company or title word
/threshold 70 55 — High and Possible cut-offs
/locations Canada, Tunisia · /locations reset
/interns on|off — include internships
/pause · /resume — hold or release alerts

<b>Run</b>
/status — last run and current settings
/weekly — applications and open matches summary
/run — start a scraper run now

/range 60 70 — jobs whose score is in a range

The <code>code</code> is the 5-character tag shown next to each job.

<b>Or just talk to me</b> (English or French, typos are fine):
“i want the top 5 matching offers” · “jobs between 60 and 70” · “jobs above 80” · “lidar jobs in montreal”
“why a3f9c” · “write a cover letter for a3f9c” · “i applied to a3f9c” · “not interested in a3f9c” · “stop showing leidos”
“set threshold to 75” · “no internships” · “pause alerts” · “resume” · “run now” · “what can you do”
I reply with how I understood you, e.g. ↪ /jobs 5."""


class CommandProcessor:
    def __init__(self, settings, manager, notifier, *, state: dict | None = None, session=None, now=None,
                 in_scraper_run: bool = False, ai=None):
        self.settings = settings
        self.manager = manager
        self.notifier = notifier
        self.session = session or requests.Session()
        self.now = now or utcnow()
        self.in_scraper_run = in_scraper_run
        self.ai = ai
        self._state = state
        self.prefs = load_prefs(manager)
        self._dirty = False

    # ------------------------------------------------------------------ telegram plumbing
    def _api(self, method: str, **params):
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/{method}"
        response = self.session.post(url, json=params, timeout=25)
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code != 200 or not body.get("ok"):
            raise RuntimeError(f"telegram {method}: HTTP {response.status_code} {body.get('description') or ''}".strip())
        return body.get("result")

    def run(self) -> Counter:
        counts = Counter()
        updates = self._api("getUpdates", timeout=0, allowed_updates=["message"]) or []
        last = None
        for update in updates:
            last = update.get("update_id", last)
            message = update.get("message") or {}
            text = (message.get("text") or "").strip()
            if str((message.get("chat") or {}).get("id")) != str(self.settings.telegram_chat_id):
                counts["ignored_other_chat"] += 1
                continue
            if not text:
                continue
            try:
                replies = self.handle(text)
            except Exception as exc:  # one bad command must not block the rest
                log.exception("command failed")
                replies = [f"⚠️ That command failed ({type(exc).__name__}). /help lists what I understand."]
            for reply in replies:
                ok, error = self.notifier.send(reply)
                if not ok:
                    log.warning("reply not delivered: %s", error)
            counts["commands"] += 1
        if self._dirty:
            save_prefs(self.manager, self.prefs)
            counts["prefs_saved"] += 1
        if last is not None:  # confirm, so no poller sees these updates again
            self._api("getUpdates", offset=int(last) + 1, limit=1, timeout=0)
        return counts

    INBOX_SUFFIX = "inbox/"

    def run_inbox(self) -> Counter:
        """Execute the messages the Cloudflare Worker left in R2 (inbox/<update id>.json), oldest first.

        On a public repository workflow inputs are world-readable, so the Worker hands messages over
        through the private bucket instead. Each object is deleted once handled.
        """
        import json

        counts = Counter()
        store = self.manager.store
        for key in sorted(store.list_keys(self.manager.key(self.INBOX_SUFFIX))):
            data = store.get_bytes(key)
            store.delete_keys([key])
            try:
                item = json.loads(data.decode("utf-8")) if data else {}
            except (ValueError, UnicodeDecodeError):
                counts["unreadable"] += 1
                continue
            text = str(item.get("text") or "").strip()
            if text:
                counts += self.run_text(text, str(item.get("hint") or ""))
        return counts

    def run_text(self, text: str, hint: str = "") -> Counter:
        """Execute one message handed over directly (the Cloudflare Worker webhook path, where Telegram
        no longer serves getUpdates). The caller has already verified the chat. `hint` is the Worker's
        optional AI reading of a free-text message."""
        try:
            replies = self.handle(text.strip(), hint)
        except Exception as exc:
            log.exception("command failed")
            replies = [f"⚠️ That command failed ({type(exc).__name__}). /help lists what I understand."]
        for reply in replies:
            self.notifier.send(reply)
        if self._dirty:
            save_prefs(self.manager, self.prefs)
        return Counter(commands=1)

    # ------------------------------------------------------------------ data helpers
    @property
    def state(self) -> dict:
        if self._state is None:
            self._state = self.manager.peek()
        return self._state

    def _touch(self) -> None:
        self._dirty = True

    def _excluded_ids(self) -> set:
        return set(self.prefs["hidden"]) | set(self.prefs["applied"])

    def _is_muted(self, rec: dict) -> bool:
        haystack = fold(f"{rec.get('title') or ''} {rec.get('company') or ''}")
        return any(fold(term) in haystack for term in self.prefs["muted"] if term.strip())

    def _current_matches(self, only_high: bool = False) -> list[dict]:
        tiers = {TIER_HIGH} if only_high else {TIER_HIGH, TIER_POSSIBLE}
        excluded = self._excluded_ids()
        rows = [rec for cid, rec in self.state.get("jobs", {}).items()
                if rec.get("tier") in tiers and cid not in excluded and not self._is_muted(rec)
                and alert_block_reason(rec, self.settings, self.now) is None]
        rows.sort(key=lambda r: (r.get("tier") != TIER_HIGH, -int(r.get("score") or 0),
                                 -(parse_datetime(r.get("posted_at")) or self.now).timestamp()))
        return rows

    def _by_code(self, code: str) -> dict | None:
        code = code.strip().lower().strip("<>[]#")
        if not code:
            return None
        for cid, rec in self.state.get("jobs", {}).items():
            if job_code(cid) == code or cid.lower() == code:
                return rec
        return None

    @staticmethod
    def _number(arg: str, default: int, low: int, high: int) -> int:
        try:
            return max(low, min(high, int(arg.split()[0])))
        except (ValueError, IndexError):
            return default

    # ------------------------------------------------------------------ dispatch
    NEEDS_CODE = {"why", "hide", "unhide", "pitch"}
    FALLBACK_INTENTS = {"search", "help"}  # what the rules answer when they did not recognise an instruction

    def _accept_hint(self, hint: str) -> tuple[str, str] | None:
        """Validate the Worker's AI reading: a known command, a sane argument, an existing job code."""
        hint = (hint or "").strip()
        if not hint.startswith("/") or len(hint) > 120 or "\n" in hint:
            return None
        command, _, arg = hint.partition(" ")
        command, arg = command[1:].split("@")[0].lower(), arg.strip()
        if "/" + command not in self._handlers() or command in ("start", "top"):
            return None
        if command in self.NEEDS_CODE or (command == "applied" and arg):
            if self._by_code(arg) is None:
                return None
        return command, arg

    def handle(self, text: str, hint: str = "") -> list[str]:
        if text.startswith("/"):
            command, _, arg = text.partition(" ")
            return self._dispatch(command.split("@")[0].lower(), arg.strip())
        # plain language: the deterministic rules decide; when they only fall back to a search, a validated
        # AI reading (if the Worker supplied one) may replace it. Either way the reply says what was understood.
        command, arg = interpret(text, lambda token: self._by_code(token) is not None)
        source = ""
        if command in self.FALLBACK_INTENTS:
            accepted = self._accept_hint(hint)
            if accepted and accepted != (command, arg):
                (command, arg), source = accepted, " · AI"
        replies = self._dispatch("/" + command, arg)
        echo = f"↪ <i>/{command}{' ' + _esc(arg) if arg else ''}{source}</i>"
        if replies and len(replies[0]) + len(echo) + 2 <= MAX_MESSAGE:
            return ["\n\n".join((echo, replies[0]))] + replies[1:]
        return [echo] + replies

    def _dispatch(self, command: str, arg: str) -> list[str]:
        handler = self._handlers().get(command)
        if handler is None:
            return [f"I don't know <code>{_esc(command)}</code>. Send /help for the list."]
        return handler(arg)

    def _handlers(self) -> dict:
        return {
            "/start": self.cmd_help, "/help": self.cmd_help, "/jobs": self.cmd_jobs, "/top": self.cmd_jobs,
            "/high": self.cmd_high, "/search": self.cmd_search, "/why": self.cmd_why, "/applied": self.cmd_applied,
            "/hide": self.cmd_hide, "/unhide": self.cmd_unhide, "/mute": self.cmd_mute, "/unmute": self.cmd_unmute,
            "/muted": self.cmd_muted, "/threshold": self.cmd_threshold, "/locations": self.cmd_locations,
            "/interns": self.cmd_interns, "/pause": self.cmd_pause, "/resume": self.cmd_resume,
            "/status": self.cmd_status, "/run": self.cmd_run, "/weekly": self.cmd_weekly, "/range": self.cmd_range,
            "/pitch": self.cmd_pitch, "/draft": self.cmd_pitch,
        }

    # ------------------------------------------------------------------ find
    def cmd_help(self, arg: str) -> list[str]:
        return [HELP]

    def _listing(self, rows: list[dict], title: str, empty: str) -> list[str]:
        if not rows:
            return [empty]
        return [text for text, _ in format_digest(rows, now=self.now, title=title)]

    def cmd_jobs(self, arg: str, only_high: bool = False) -> list[str]:
        limit = self._number(arg, 10, 1, 40)
        rows = self._current_matches(only_high)
        label = "High matches" if only_high else "current matches"
        return self._listing(rows[:limit], f"Top {min(limit, len(rows))} of {len(rows)} {label}",
                             "Nothing relevant and fresh is stored right now. /status shows the last run.")

    def cmd_range(self, arg: str) -> list[str]:
        """Fresh jobs whose score lies in [low, high], including ones below the Possible cut-off that were
        rejected for their score alone."""
        numbers = [int(x) for x in arg.replace("-", " ").replace(",", " ").split() if x.isdigit()]
        if not numbers or not all(0 <= n <= 100 for n in numbers):
            return ["Usage: /range 60 70 — or just say “jobs between 60 and 70”, “jobs above 80”."]
        low, high = (min(numbers[:2]), max(numbers[:2])) if len(numbers) > 1 else (numbers[0], 100)
        excluded = self._excluded_ids()
        rows = []
        for cid, rec in self.state.get("jobs", {}).items():
            score = int(rec.get("score") or 0)
            only_low_score = set(rec.get("rejection_reasons") or []) <= {"LOW_SCORE"}
            if (low <= score <= high and cid not in excluded and not self._is_muted(rec)
                    and (rec.get("tier") in (TIER_HIGH, TIER_POSSIBLE) or only_low_score)
                    and alert_block_reason(rec, self.settings, self.now) is None):
                rows.append(rec)
        rows.sort(key=lambda r: -int(r.get("score") or 0))
        return self._listing(rows[:30], f"{len(rows)} job{'s' if len(rows) != 1 else ''} scoring {low}–{high}",
                             f"No fresh job scores between {low} and {high}.")

    def cmd_high(self, arg: str) -> list[str]:
        return self.cmd_jobs(arg, only_high=True)

    def cmd_search(self, arg: str) -> list[str]:
        words = [fold(w) for w in arg.split() if w.strip()]
        if not words:
            return ["Usage: /search lidar toronto"]
        rows = []
        for rec in self.state.get("jobs", {}).values():
            if rec.get("tier") not in (TIER_HIGH, TIER_POSSIBLE):
                continue
            haystack = fold(" ".join(str(x) for x in (rec.get("title"), rec.get("company"), rec.get("location_raw"),
                                                        rec.get("country"), " ".join(rec.get("matched_skills") or []),
                                                        " ".join(rec.get("matched_domains") or []))))
            if all(word in haystack for word in words):
                rows.append(rec)
        rows.sort(key=lambda r: -int(r.get("score") or 0))
        return self._listing(rows[:15], f"{len(rows)} stored match{'es' if len(rows) != 1 else ''} for “{arg}”",
                             f"No stored match for “{_esc(arg)}”.")

    def cmd_why(self, arg: str) -> list[str]:
        rec = self._by_code(arg)
        if rec is None:
            return ["Usage: /why code — the 5-character tag next to a job."]
        breakdown = rec.get("score_breakdown") or {}
        lines = [f"<b>{_esc(rec.get('title'))}</b>" + (f" — {_esc(rec['company'])}" if rec.get("company") else ""),
                 f"Score {int(rec.get('score') or 0)}/100 · {_esc(rec.get('tier'))}",
                 "Title {title} · skills {tech} · domain {domain} · tasks {responsibilities} · location {location}".format(
                     **{k: breakdown.get(k, 0) for k in ("title", "tech", "domain", "responsibilities", "location")})]
        if rec.get("why_matched"):
            lines += ["", "<b>Evidence</b>"] + [f"• {_esc(w)}" for w in rec["why_matched"][:12]]
        review = rec.get("ai") or {}
        if review:
            lines += ["", f"<b>AI second opinion</b> · fit {review.get('fit', '?')}/10"]
            if review.get("summary"):
                lines.append(f"💡 {_esc(review['summary'])}")
            if review.get("concerns"):
                lines.append(f"⚠️ {_esc(review['concerns'])}")
            facts = []
            if review.get("years") is not None:
                facts.append(f"{review['years']}+ years")
            if review.get("sponsorship") and review["sponsorship"] != "unknown":
                facts.append("sponsorship " + review["sponsorship"].replace("_", " "))
            if review.get("languages"):
                facts.append("languages: " + ", ".join(review["languages"]))
            if facts:
                lines.append(_esc(" · ".join(facts)))
            if review.get("requirements"):
                lines += [f"• {_esc(r)}" for r in review["requirements"]]
        if rec.get("rejection_reasons"):
            lines += ["", "Rejected: " + _esc(", ".join(rec["rejection_reasons"]))]
        sources = sorted({s.get("source_name") for s in rec.get("sources") or [] if s.get("source_name")})
        if sources:
            lines += ["", "Sources: " + _esc(", ".join(sources[:6]))]
        link = rec.get("apply_url") or rec.get("url")
        if link:
            lines += ["", f'<a href="{_esc(link)}">Open posting</a>']
        return ["\n".join(lines)]

    def cmd_pitch(self, arg: str) -> list[str]:
        """Draft a short tailored application note with Workers AI."""
        rec = self._by_code(arg)
        if rec is None:
            return ["Usage: /pitch code — I draft a short application note for that job."]
        ai = self.ai if self.ai is not None else WorkersAI.from_settings(self.settings, budget=2)
        if ai is None:
            return ["Drafting needs Workers AI: add the CLOUDFLARE_AI_TOKEN secret (see README)."]
        note = write_pitch(ai, self.settings.candidate_profile, rec)
        if not note:
            return ["The AI service did not answer just now. Try again in a minute."]
        link = rec.get("apply_url") or rec.get("url")
        head = f"✍️ <b>Draft for {_esc(rec.get('title'))}</b>" + (f" — {_esc(rec['company'])}" if rec.get("company") else "")
        tail = f'\n\n<a href="{_esc(link)}">Apply</a> · edit before sending, it is a draft.' if link else ""
        return [f"{head}\n\n{note}{tail}"]

    # ------------------------------------------------------------------ track
    def cmd_applied(self, arg: str) -> list[str]:
        if not arg:
            applied = self.prefs["applied"]
            if not applied:
                return ["No applications recorded yet. /applied code marks one."]
            rows = sorted(applied.values(), key=lambda a: a.get("at") or "", reverse=True)[:40]
            return ["<b>Applied</b>\n" + "\n".join(
                f"• {_esc(a.get('title'))}" + (f" — {_esc(a['company'])}" if a.get("company") else "")
                + f" ({(a.get('at') or '')[:10]})" for a in rows)]
        rec = self._by_code(arg)
        if rec is None:
            return ["I can't find that code. /jobs lists current codes."]
        self.prefs["applied"][rec["canonical_id"]] = {"title": rec.get("title"), "company": rec.get("company"),
                                                      "at": to_iso(self.now)}
        self._touch()
        return [f"✅ Marked as applied: <b>{_esc(rec.get('title'))}</b>. Good luck!"]

    def cmd_hide(self, arg: str) -> list[str]:
        rec = self._by_code(arg)
        if rec is None:
            return ["Usage: /hide code"]
        if rec["canonical_id"] not in self.prefs["hidden"]:
            self.prefs["hidden"].append(rec["canonical_id"])
            self._touch()
        return [f"🙈 Hidden: {_esc(rec.get('title'))}. /unhide {job_code(rec['canonical_id'])} restores it."]

    def cmd_unhide(self, arg: str) -> list[str]:
        rec = self._by_code(arg)
        if rec is None or rec["canonical_id"] not in self.prefs["hidden"]:
            return ["That job isn't hidden."]
        self.prefs["hidden"].remove(rec["canonical_id"])
        self._touch()
        return [f"Restored: {_esc(rec.get('title'))}"]

    # ------------------------------------------------------------------ tune
    def cmd_mute(self, arg: str) -> list[str]:
        if not arg:
            return ["Usage: /mute leidos — silences jobs whose title or company contains the text."]
        if fold(arg) not in [fold(t) for t in self.prefs["muted"]]:
            self.prefs["muted"].append(arg)
            self._touch()
        return [f"🔇 Muted “{_esc(arg)}”. /muted shows the list."]

    def cmd_unmute(self, arg: str) -> list[str]:
        keep = [t for t in self.prefs["muted"] if fold(t) != fold(arg)]
        if len(keep) == len(self.prefs["muted"]):
            return [f"“{_esc(arg)}” wasn't muted."]
        self.prefs["muted"] = keep
        self._touch()
        return [f"🔊 Unmuted “{_esc(arg)}”."]

    def cmd_muted(self, arg: str) -> list[str]:
        return ["Muted: " + (_esc(", ".join(self.prefs["muted"])) or "nothing")]

    def cmd_threshold(self, arg: str) -> list[str]:
        numbers = [int(x) for x in arg.replace(",", " ").split() if x.isdigit()]
        if not numbers or not all(0 < n <= 100 for n in numbers):
            return [f"Usage: /threshold 70 55 (now High ≥ {self.settings.high_threshold}, "
                    f"Possible ≥ {self.settings.medium_threshold})"]
        high = numbers[0]
        medium = min(numbers[1] if len(numbers) > 1 else self.settings.medium_threshold, high)
        self.prefs["high_threshold"], self.prefs["medium_threshold"] = high, medium
        self._touch()
        return [f"Thresholds set: High ≥ {high}, Possible ≥ {medium}. They apply from the next run."]

    def cmd_locations(self, arg: str) -> list[str]:
        if not arg:
            return ["Preferred locations: " + (_esc(", ".join(self.settings.preferred_locations)) or "none")
                    + "\nUsage: /locations Canada, Tunisia · /locations reset"]
        self.prefs["preferred_locations"] = None if arg.lower() == "reset" else [p.strip() for p in arg.split(",") if p.strip()]
        self._touch()
        return ["Preferred locations " + ("reset to the default." if arg.lower() == "reset"
                                          else "set to " + _esc(", ".join(self.prefs["preferred_locations"])) + ".")]

    def cmd_interns(self, arg: str) -> list[str]:
        if arg.lower() not in ("on", "off"):
            return ["Usage: /interns on (include internships) · /interns off (exclude them)"]
        self.prefs["exclude_internships"] = arg.lower() == "off"
        self._touch()
        return ["Internships will be " + ("excluded." if arg.lower() == "off" else "included.") + " Applies from the next run."]

    def cmd_pause(self, arg: str) -> list[str]:
        self.prefs["paused"] = True
        self._touch()
        return ["⏸ Alerts paused. Jobs keep being collected; /resume releases them."]

    def cmd_resume(self, arg: str) -> list[str]:
        self.prefs["paused"] = False
        self._touch()
        return ["▶️ Alerts resumed."]

    # ------------------------------------------------------------------ run
    def cmd_status(self, arg: str) -> list[str]:
        report = self.manager.read_json("runs/latest.json") or {}
        counts = report.get("counts") or {}
        failed = [s["name"] for s in report.get("sources") or [] if s.get("status") == "FAILED"]
        jobs = self.state.get("jobs", {})
        lines = ["<b>Status</b>",
                 f"Last run: {_esc(report.get('started_at') or 'never')} · code {_esc(report.get('code') or '?')}",
                 f"That run: {counts.get('tier_high', 0)} high · {counts.get('tier_possible', 0)} possible · "
                 f"{counts.get('alerts_sent', 0)} alerted · {counts.get('new', 0)} new",
                 f"Stored jobs: {len(jobs)} · fresh matches now: {len(self._current_matches())}",
                 f"Failed sources: {_esc(', '.join(failed)) or 'none'}",
                 f"Thresholds: High ≥ {self.prefs['high_threshold'] or self.settings.high_threshold}, "
                 f"Possible ≥ {self.prefs['medium_threshold'] or self.settings.medium_threshold}",
                 f"Alerts: {'paused' if self.prefs['paused'] else 'on'} · internships "
                 f"{'excluded' if (self.prefs['exclude_internships'] if self.prefs['exclude_internships'] is not None else self.settings.exclude_internships) else 'included'}",
                 f"Muted: {_esc(', '.join(self.prefs['muted'])) or 'nothing'} · applied: {len(self.prefs['applied'])} · "
                 f"hidden: {len(self.prefs['hidden'])}"]
        return ["\n".join(lines)]

    def cmd_weekly(self, arg: str) -> list[str]:
        return [format_weekly(self.state, self.prefs, self.settings, self.now)
                or "Nothing to summarise yet: no alerts, applications or open High matches."]

    def cmd_run(self, arg: str) -> list[str]:
        if self.in_scraper_run:
            return ["▶️ A run is in progress right now; any new matches follow this message."]
        repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
        if not (repo and token):
            return ["/run works only from GitHub Actions (the commands workflow)."]
        response = self.session.post(
            f"https://api.github.com/repos/{repo}/actions/workflows/scraper.yml/dispatches", json={"ref": "main"},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}, timeout=20)
        if response.status_code in (201, 204):
            return ["🚀 Scraper run started. Matches arrive in a few minutes."]
        return [f"Could not start a run (HTTP {response.status_code}). The commands workflow needs 'actions: write'."]
