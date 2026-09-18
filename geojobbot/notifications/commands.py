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
from ..ai.review import write_approach, write_pitch, write_prep
from ..core.descriptions import DescriptionStore
from ..core.jobs import APPLICATION_STATUSES, alert_block_reason, is_listed
from ..core.prefs import load_prefs, save_prefs
from ..insights import prospects, radar, signals, timing, visa, yields
from ..insights.learning import MIN_LABELS, build_model, describe_model, snapshot
from ..matching.profile import TECH_SKILLS
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
/sponsors [n] — matches from employers on official visa-sponsor registers (UK, Canada, NL)
/visa — is a work visa realistic? licence, legal salary minimum, occupation · /visa code · /visa france
/ai [n] — what the AI thinks of current matches: fit /10, summary, concerns
/pitch code — AI drafts a short application note for that job
/prep code — interview sheet: likely questions, weak points, what to ask (sent by itself when you record an interview)
/approach firm — AI drafts a speculative application to a firm that just won geospatial work · /approach lists them

<b>Track</b>
/applied code — mark as applied (no more alerts for it)
/applied — your applications and their status
/outcome code interview|offer|rejected|withdrawn|ghosted — record what happened
/hide code · /unhide code — dismiss or restore a job
/watch company (or a careers-page URL) · /unwatch name · /watch — employers to follow closely

<b>Tune</b>
/mute text · /unmute text · /muted — silence a company or title word
/threshold 70 55 — High and Possible cut-offs
/locations Canada, Tunisia · /locations reset
/interns on|off — include internships
/possible on|off — alert on Possible matches too (default: High only)
/pause · /resume — hold or release alerts

<b>Run</b>
/status — last run and current settings
/radar — skills the market asks for vs yours · /skills edits your list
/signals — firms winning geospatial contracts, consultancies, tenders
/sources — which sources actually deliver, and which only make noise
/prospects — employers proven to sponsor geomatics staff whose job boards I found
/learning — what I learned from your applications and hidden jobs
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
                 in_scraper_run: bool = False, ai=None, prefs: dict | None = None):
        self.settings = settings
        self.manager = manager
        self.notifier = notifier
        self.session = session or requests.Session()
        self.now = now or utcnow()
        self.in_scraper_run = in_scraper_run
        self.ai = ai
        self._state = state
        self.prefs = prefs if prefs is not None else load_prefs(manager)
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
            # obey the configured chat, or its owner writing from any other chat (a private chat id is a user id)
            owner = str(os.environ.get("TELEGRAM_OWNER_ID") or self.settings.telegram_chat_id)
            sender = message.get("from") or {}
            in_chat = str((message.get("chat") or {}).get("id")) == str(self.settings.telegram_chat_id)
            from_owner = not sender.get("is_bot") and str(sender.get("id")) == owner
            if not (in_chat or from_owner):
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
        counts = Counter(commands=1)
        for reply in replies:
            ok, error = self.notifier.send(reply)
            if not ok:
                counts["replies_failed"] += 1
                log.warning("reply not delivered: %s", error)
        if self._dirty:
            save_prefs(self.manager, self.prefs)
        return counts

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
                and alert_block_reason(rec, self.settings, self.now) is None and is_listed(rec, self.now)]
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
    NEEDS_CODE = {"why", "hide", "unhide", "pitch"}  # /outcome validates its own code
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
            "/pitch": self.cmd_pitch, "/draft": self.cmd_pitch, "/ai": self.cmd_ai, "/possible": self.cmd_possible,
            "/sponsors": self.cmd_sponsors, "/sponsor": self.cmd_sponsors, "/outcome": self.cmd_outcome,
            "/learning": self.cmd_learning, "/radar": self.cmd_radar, "/skills": self.cmd_skills,
            "/signals": self.cmd_signals, "/sources": self.cmd_sources, "/yield": self.cmd_sources,
            "/visa": self.cmd_visa, "/watch": self.cmd_watch, "/unwatch": self.cmd_unwatch,
            "/prospects": self.cmd_prospects, "/prep": self.cmd_prep, "/approach": self.cmd_approach,
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
        for hit in rec.get("sponsor") or []:
            note = f"{hit.get('icon', '🛂')} {_esc(hit['label'])}: {_esc(hit.get('name'))}"
            if hit.get("positions"):
                note += f" · {hit['positions']} foreign hires approved"
            if hit.get("occupations"):
                note += " · hired " + _esc(", ".join(hit["occupations"]))
            if hit.get("match") == "variant":
                note += " (name variant)"
            lines += ([""] if hit is (rec.get("sponsor") or [None])[0] else []) + [note]
        facts = [f for f in (timing.deadline_badge(rec, self.now), timing.repost_badge(rec)) if f]
        if rec.get("deadline") and timing.deadline_passed(rec, self.now):
            facts.append(f"⌛ the application deadline passed on {rec['deadline']}")
        if facts:
            lines += ["", _esc(" · ".join(facts))]
        route = visa.detail_lines(rec, self.now)
        if route:
            lines += [""] + route
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
        learned = rec.get("learned") or {}
        if learned.get("adj"):
            lines += ["", f"🧠 Learned from you: {learned['adj']:+d} points ({_esc(', '.join(learned.get('because') or []))})"]
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
        description = DescriptionStore.load(self.manager).get(rec["canonical_id"])
        note = write_pitch(ai, self.settings.candidate_profile, rec, description)
        if not note:
            return ["The AI service did not answer just now. Try again in a minute."]
        link = rec.get("apply_url") or rec.get("url")
        head = f"✍️ <b>Draft for {_esc(rec.get('title'))}</b>" + (f" — {_esc(rec['company'])}" if rec.get("company") else "")
        tail = f'\n\n<a href="{_esc(link)}">Apply</a> · edit before sending, it is a draft.' if link else ""
        return [f"{head}\n\n{note}{tail}"]

    def _known_facts(self, rec: dict) -> list[str]:
        """What the bot already established about a job, in plain text (shown to the user and given to the AI)."""
        facts = []
        if rec.get("salary"):
            facts.append(f"Posted salary: {rec['salary']}")
        for hit in (rec.get("sponsor") or [])[:2]:
            text = f"{hit.get('label')}: {hit.get('name')}"
            if hit.get("occupations"):
                text += " (already hired " + ", ".join(hit["occupations"]) + " from abroad)"
            facts.append(text)
        route = rec.get("visa") or {}
        if route.get("line"):
            facts.append(f"Visa route ({route.get('country')}): {route['line']}")
        closes, again = timing.deadline_badge(rec, self.now), timing.repost_badge(rec)
        if closes:
            facts.append(closes.replace("⏳ ", "Application "))
        if again:
            facts.append(again.replace("♻️ ", "This role was ") + ": ask why it is open again")
        return facts

    def cmd_prep(self, arg: str) -> list[str]:
        """/prep code: interview sheet for a job (sent by itself when /outcome records an interview)."""
        code = arg.split()[0] if arg.split() else ""
        rec = self._by_code(code)
        if rec is None:  # applied long ago: the job may have left the state, the application snapshot has not
            cid, info = self._applied_by_code(code)
            rec = dict(info, canonical_id=cid) if cid else None
        if rec is None:
            return ["Usage: /prep code — an interview sheet for that job. /applied lists your codes."]
        ai = self.ai if self.ai is not None else WorkersAI.from_settings(self.settings, budget=2)
        facts = self._known_facts(rec)
        head = f"🎤 <b>Interview sheet: {_esc(rec.get('title'))}</b>" + (f" — {_esc(rec['company'])}" if rec.get("company") else "")
        known = ("\n\n<b>What I know</b>\n" + "\n".join(f"• {_esc(f)}" for f in facts)) if facts else ""
        if ai is None:
            return [head + known + "\n\nThe question list needs Workers AI: add the CLOUDFLARE_AI_TOKEN secret (see README)."]
        sheet = write_prep(ai, self.settings.candidate_profile, rec, DescriptionStore.load(self.manager).get(rec["canonical_id"]), facts)
        if not sheet:
            return [head + known + "\n\nThe AI service did not answer just now. /prep " + _esc(code) + " tries again."]
        return [f"{head}{known}\n\n{sheet}\n\n<i>A draft to rehearse with, not a script. Good luck.</i>"[:4090]]

    def cmd_approach(self, arg: str) -> list[str]:
        """/approach firm: a speculative application to a firm with a reason to hire (a contract it just won…)."""
        name = arg.strip()
        awards = [s for s in (self.state.get("signals") or {}).get("items") or [] if s.get("kind") == "award" and s.get("winner")]
        if not name:
            if not awards:
                return ["Usage: /approach firm name — I draft a short unsolicited application. "
                        "/signals will list firms that just won geospatial contracts once some are found."]
            lines = ["✉️ <b>Firms with a reason to hire</b> (they just won geospatial work)"]
            for s in awards[:8]:
                lines.append(f"• <b>{_esc(s['winner'])}</b>" + (f" ({_esc(s['winner_country'])})" if s.get("winner_country") else "")
                             + f" — {_esc((s.get('title') or '')[:70])}, {_esc(s.get('country') or '?')}")
            lines += ["", "/approach firm name drafts the message · /watch firm name follows their job board"]
            return ["\n".join(lines)]
        ai = self.ai if self.ai is not None else WorkersAI.from_settings(self.settings, budget=2)
        if ai is None:
            return ["Drafting needs Workers AI: add the CLOUDFLARE_AI_TOKEN secret (see README)."]
        wanted = prospects.normalize_company(name)
        facts = []
        for s in awards:
            if wanted and wanted in prospects.normalize_company(s["winner"]):
                name = s["winner"]
                facts.append(f"Reason to write now: the firm just won the contract “{s.get('title')}” in {s.get('country') or 'an unnamed country'}"
                             + (f" ({s['value']})" if s.get("value") else "") + (f". The firm is based in {s['winner_country']}" if s.get("winner_country") else ""))
        book = (self.state.get("prospects") or {}).get(wanted) or {}
        if book.get("why") and not facts:
            facts.append(f"What is known about the firm: {book['why']}" + (f", based in {book['country']}" if book.get("country") else ""))
        if not facts:
            facts.append("No specific event is known: write about the firm's field of work in general, without inventing projects.")
        note = write_approach(ai, self.settings.candidate_profile, name, facts)
        if not note:
            return ["The AI service did not answer just now. Try again in a minute."]
        return [f"✉️ <b>Speculative application: {_esc(name)}</b>\n\n{note}\n\n"
                f"<i>A draft: check every fact, find a named person to send it to. /watch {_esc(name)} follows their job board.</i>"[:4090]]

    # ------------------------------------------------------------------ track
    def cmd_applied(self, arg: str) -> list[str]:
        if not arg:
            applied = self.prefs["applied"]
            if not applied:
                return ["No applications recorded yet. /applied code marks one."]
            rows = sorted(applied.items(), key=lambda kv: kv[1].get("at") or "", reverse=True)[:40]
            tally = Counter((a.get("status") or "applied") for a in applied.values())
            lines = ["<b>Your applications</b>",
                     "<i>" + " · ".join(f"{APPLICATION_STATUSES.get(s, '•')} {n} {s}" for s, n in tally.most_common()) + "</i>", ""]
            for cid, a in rows:
                status = a.get("status") or "applied"
                lines.append(f"{APPLICATION_STATUSES.get(status, '•')} {_esc(a.get('title'))}"
                             + (f" — {_esc(a['company'])}" if a.get("company") else "")
                             + f" · {status} · {(a.get('at') or '')[:10]} · <code>{job_code(cid)}</code>")
            lines += ["", "/outcome code interview|offer|rejected|withdrawn|ghosted updates one"]
            return ["\n".join(lines)]
        rec = self._by_code(arg)
        if rec is None:
            return ["I can't find that code. /jobs lists current codes."]
        self.prefs["applied"][rec["canonical_id"]] = {
            **snapshot(rec), "url": rec.get("apply_url") or rec.get("url"),
            "at": to_iso(self.now), "status": "applied", "history": [{"at": to_iso(self.now), "status": "applied"}]}
        self._touch()
        return [f"✅ Marked as applied: <b>{_esc(rec.get('title'))}</b>. Good luck! I'll check in with you in a week; "
                f"tell me how it goes with /outcome {job_code(rec['canonical_id'])} interview|rejected|offer."]

    def _applied_by_code(self, code: str) -> tuple[str, dict] | tuple[None, None]:
        code = code.strip().lower()
        for cid, info in self.prefs["applied"].items():
            if job_code(cid) == code or cid.lower() == code:
                return cid, info
        return None, None

    def cmd_outcome(self, arg: str) -> list[str]:
        """/outcome code interview|offer|rejected|withdrawn|ghosted|applied"""
        words = arg.split()
        status = next((w.lower() for w in words if w.lower() in APPLICATION_STATUSES), None)
        code = next((w for w in words if w.lower() not in APPLICATION_STATUSES), "")
        cid, info = self._applied_by_code(code)
        if cid is None:  # an outcome for a job never marked as applied: record the application too
            rec = self._by_code(code)
            if rec is not None and status:
                self.cmd_applied(code)
                cid, info = rec["canonical_id"], self.prefs["applied"][rec["canonical_id"]]
        if cid is None or status is None:
            return ["Usage: /outcome code interview|offer|rejected|withdrawn|ghosted — /applied lists your codes."]
        info["status"] = status
        info.setdefault("history", []).append({"at": to_iso(self.now), "status": status})
        self._touch()
        cheer = {"interview": "🎤 An interview! Well done.", "offer": "🎉 An offer! Congratulations.",
                 "rejected": "❌ Noted. Their loss; on to the next.", "withdrawn": "↩️ Noted as withdrawn.",
                 "ghosted": "👻 Noted as no reply.", "applied": "📨 Back to 'applied'."}[status]
        replies = [f"{cheer}\n<b>{_esc(info.get('title'))}</b>" + (f" — {_esc(info['company'])}" if info.get("company") else "")]
        if status == "interview":  # the moment an interview sheet is useful
            replies += self.cmd_prep(job_code(cid))
        return replies

    def cmd_hide(self, arg: str) -> list[str]:
        rec = self._by_code(arg)
        if rec is None:
            return ["Usage: /hide code"]
        if rec["canonical_id"] not in self.prefs["hidden"]:
            self.prefs["hidden"].append(rec["canonical_id"])
            self.prefs["hidden_info"][rec["canonical_id"]] = {**snapshot(rec), "at": to_iso(self.now)}
            self._touch()
        return [f"🙈 Hidden: {_esc(rec.get('title'))}. /unhide {job_code(rec['canonical_id'])} restores it."]

    def cmd_unhide(self, arg: str) -> list[str]:
        rec = self._by_code(arg)
        if rec is None or rec["canonical_id"] not in self.prefs["hidden"]:
            return ["That job isn't hidden."]
        self.prefs["hidden"].remove(rec["canonical_id"])
        self.prefs["hidden_info"].pop(rec["canonical_id"], None)
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

    def cmd_learning(self, arg: str) -> list[str]:
        """/learning — what I learned from your applications and hidden jobs · on | off | reset"""
        action = arg.strip().lower()
        if action in ("off", "on"):
            self.prefs["learning"] = action == "on"
            self._touch()
            return ["Learning is " + ("on: your applications and hidden jobs nudge the scores." if action == "on"
                                      else "off: scores are purely rule-based again from the next run.")]
        if action in ("reset", "forget"):
            self.prefs["learning_since"] = to_iso(self.now)
            self._touch()
            return ["🧹 Forgotten. I start learning again from your next actions."]
        if self.prefs.get("learning") is False:
            return ["Learning is off. /learning on switches it back on."]
        model = build_model(self.prefs, self.state.get("jobs", {}))
        labels = len(self.prefs["applied"]) + len(self.prefs["hidden"])
        if not model:
            return [f"🧠 Nothing learned yet: I need at least {MIN_LABELS} actions with something in common "
                    f"(you have {labels}). Every /applied and /hide teaches me."]
        liked, disliked = describe_model(model)
        lines = ["🧠 <b>What I learned from you</b>",
                 f"<i>from {len(self.prefs['applied'])} applications and {len(self.prefs['hidden'])} hidden jobs · "
                 f"a job moves by at most +6 / −8 points</i>"]
        if liked:
            lines += ["", "<b>You go for</b>"] + [f"• {_esc(x)}" for x in liked]
        if disliked:
            lines += ["", "<b>You skip</b>"] + [f"• {_esc(x)}" for x in disliked]
        lines += ["", "/why code shows a job's adjustment · /learning off · /learning reset"]
        return ["\n".join(lines)]

    def cmd_radar(self, arg: str) -> list[str]:
        have = radar.my_skills(self.settings, self.prefs)
        return [radar.format_radar(radar.compute(self.state.get("jobs", {}), have, self.now))]

    def cmd_skills(self, arg: str) -> list[str]:
        """/skills · /skills add PostGIS · /skills remove FME · /skills reset"""
        have = radar.my_skills(self.settings, self.prefs)
        action, _, name = arg.strip().partition(" ")
        name = name.strip()
        if action.lower() == "reset":
            self.prefs["my_skills"] = None
            self._touch()
            return ["Skills list reset to the one from your CV."]
        if action.lower() in ("add", "remove") and name:
            known = {fold(t.canonical): t.canonical for t in TECH_SKILLS}
            canonical = known.get(fold(name), name)
            have = [s for s in have if fold(s) != fold(canonical)]
            if action.lower() == "add":
                have.append(canonical)
            self.prefs["my_skills"] = have
            self._touch()
            return [f"{'Added' if action.lower() == 'add' else 'Removed'} {_esc(canonical)}. /radar uses the new list."]
        return ["<b>Skills I compare the market against</b>\n" + _esc(", ".join(have))
                + "\n\n/skills add PostGIS · /skills remove FME · /skills reset"]

    def cmd_signals(self, arg: str) -> list[str]:
        items = (self.state.get("signals") or {}).get("items") or []
        return [signals.format_signals(items, heading="Recent market signals", limit=self._number(arg, 10, 1, 20))
                or "No procurement signals stored yet. They are checked once a day during scraper runs."]

    def cmd_visa(self, arg: str) -> list[str]:
        """/visa: current matches by how open the visa route looks · /visa code · /visa country"""
        arg = arg.strip()
        if arg:
            rec = self._by_code(arg)
            if rec is not None:
                return ["\n".join(visa.detail_lines(rec, self.now))
                        or "No visa question for that job: it is in your home country, or I have no rules for its country."]
            country = visa.find_country(arg)
            if country:
                return [visa.country_card(country)]
            if not arg.isdigit():
                return ["Usage: /visa · /visa code · /visa country (uk, canada, france, germany, netherlands, ireland, australia, uae…)"]
        text = visa.format_list(self._current_matches(), lambda rec: job_code(rec.get("canonical_id")),
                                limit=self._number(arg, 12, 1, 25))
        return [text]

    def cmd_watch(self, arg: str) -> list[str]:
        """/watch · /watch Fugro · /watch https://firm.example/careers [Firm name]"""
        arg = arg.strip()
        watch = self.prefs["watch"]
        if not arg:
            if not watch:
                return ["You are not watching any employer. /watch Fugro — or a careers page: /watch https://firm.example/careers\n"
                        "I look for the employer's job board, read it every day, and alert you on Possible matches from it too."]
            lines = ["👀 <b>Employers you watch</b>"]
            book = self.state.get("prospects") or {}
            for item in watch:
                found = (book.get(prospects.normalize_company(item["name"])) or {}).get("boards") or []
                where = ", ".join(found) if found else (item.get("url") or "no readable job board found yet")
                lines.append(f"• <b>{_esc(item['name'])}</b> — {_esc(where)}")
            lines += ["", "/unwatch name removes one · /prospects shows every employer I went looking for"]
            return ["\n".join(lines)]
        url = next((w for w in arg.split() if w.lower().startswith(("http://", "https://"))), None)
        name = " ".join(w for w in arg.split() if w != url).strip()
        if url and not name:
            host = url.split("//", 1)[1].split("/", 1)[0].lower().removeprefix("www.").removeprefix("careers.").removeprefix("jobs.")
            name = host.split(".")[0].replace("-", " ").title()
        if len(prospects.normalize_company(name)) < 2 or len(name) > 80:
            return ["Usage: /watch company name — or /watch https://firm.example/careers Firm name"]
        if len(watch) >= 40 and not any(fold(w["name"]) == fold(name) for w in watch):
            return ["The watch list is full (40). /unwatch name frees a place."]
        watch[:] = [w for w in watch if fold(w["name"]) != fold(name)]
        watch.append({"name": name, "url": url, "at": to_iso(self.now)})
        self._touch()
        return [f"👀 Watching <b>{_esc(name)}</b>. " + ("I will read that careers page every run. " if url else
                "At the next run I look for its job board on the platforms I can read. ")
                + "Possible matches from a watched employer are alerted too. /watch lists them."]

    def cmd_unwatch(self, arg: str) -> list[str]:
        before = len(self.prefs["watch"])
        self.prefs["watch"][:] = [w for w in self.prefs["watch"] if fold(w["name"]) != fold(arg)]
        if not arg.strip() or len(self.prefs["watch"]) == before:
            return [f"“{_esc(arg)}” isn't on the watch list. /watch shows it."]
        self._touch()
        return [f"No longer watching {_esc(arg.strip())}."]

    def cmd_prospects(self, arg: str) -> list[str]:
        return [prospects.format_prospects(self.state, limit=self._number(arg, 15, 1, 30))]

    def cmd_sources(self, arg: str) -> list[str]:
        return [yields.format_yield(yields.compute(self.state, self.prefs, self.now))]

    def cmd_possible(self, arg: str) -> list[str]:
        if arg.lower() not in ("on", "off"):
            current = self.prefs["notify_possible"] if self.prefs["notify_possible"] is not None else self.settings.notify_possible
            return [f"Alerts currently include {'High and Possible' if current else 'High'} matches.\n"
                    "Usage: /possible on (alert on Possible too) · /possible off (High only; /jobs still lists everything)"]
        self.prefs["notify_possible"] = arg.lower() == "on"
        self._touch()
        return ["Alerts will include " + ("High and Possible matches." if arg.lower() == "on" else
                                          "High matches only. /jobs and /range still show the Possible ones.")]

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
                 "AI second opinion: " + ("{1} of {0} current matches reviewed · {2} vetoed".format(*self._ai_coverage())
                                          if self.settings.cloudflare_ai_token else "off (CLOUDFLARE_AI_TOKEN not set here)"),
                 f"Failed sources: {_esc(', '.join(failed)) or 'none'}",
                 f"Thresholds: High ≥ {self.prefs['high_threshold'] or self.settings.high_threshold}, "
                 f"Possible ≥ {self.prefs['medium_threshold'] or self.settings.medium_threshold}",
                 f"Alerts: {'paused' if self.prefs['paused'] else 'on'} · internships "
                 f"{'excluded' if (self.prefs['exclude_internships'] if self.prefs['exclude_internships'] is not None else self.settings.exclude_internships) else 'included'}",
                 f"Muted: {_esc(', '.join(self.prefs['muted'])) or 'nothing'} · applied: {len(self.prefs['applied'])} · "
                 f"hidden: {len(self.prefs['hidden'])}"]
        return ["\n".join(lines)]

    def cmd_sponsors(self, arg: str) -> list[str]:
        """Current matches whose employer is on an official sponsor register, or whose posting offers sponsorship."""
        limit = self._number(arg, 10, 1, 30)
        rows = []
        for rec in self._current_matches():
            offered = ("Visa sponsorship offered" in (rec.get("why_matched") or [])
                       or (rec.get("ai") or {}).get("sponsorship") == "offered")
            hits = rec.get("sponsor") or []
            if not (hits or offered):
                continue
            local = any(h.get("country") == rec.get("country") for h in hits)
            rows.append((0 if offered else 1 if local else 2, -int(rec.get("score") or 0), rec, hits, offered))
        if not rows:
            return ["No current match comes from an employer on the UK, Canada or Netherlands sponsor registers yet, and "
                    "none states that it sponsors. The registers are checked on every run."]
        rows.sort(key=lambda row: row[:2])
        lines = ["🛂 <b>Sponsorship evidence</b>",
                 f"<i>{len(rows)} current matches · posting says so first, then employers licensed in the job's own country</i>"]
        for index, (_, _, rec, hits, offered) in enumerate(rows[:limit], 1):
            link = rec.get("apply_url") or rec.get("url")
            title = f'<a href="{_esc(link)}">{_esc(rec.get("title"))}</a>' if link else f"<b>{_esc(rec.get('title'))}</b>"
            lines += ["", f"{index}. {title}" + (f" — {_esc(rec['company'])}" if rec.get("company") else ""),
                      f"   score {int(rec.get('score') or 0)} · {_esc(rec.get('country') or rec.get('location_raw') or 'location unknown')}"
                      f" · <code>{job_code(rec.get('canonical_id'))}</code>"]
            if offered:
                lines.append("   ✅ the posting offers visa sponsorship")
            for hit in hits[:3]:
                detail = f"   {hit.get('icon', '🛂')} {_esc(hit['label'])}: {_esc(hit.get('name'))}"
                if hit.get("positions"):
                    detail += f" · {hit['positions']} foreign hires approved"
                if hit.get("occupations"):
                    detail += " · hired " + _esc(", ".join(hit["occupations"]))
                if hit.get("match") == "variant":
                    detail += " (name variant: check)"
                lines.append(detail)
        text = "\n".join(lines)
        return [text if len(text) <= MAX_MESSAGE else text[: MAX_MESSAGE - 1] + "…"]

    def _ai_coverage(self) -> tuple[int, int, int]:
        """(current matches, of which reviewed by the AI, jobs the AI vetoed)."""
        current = self._current_matches()
        reviewed = sum(1 for rec in current if (rec.get("ai") or {}).get("summary") or (rec.get("ai") or {}).get("fit") is not None)
        vetoed = sum(1 for rec in self.state.get("jobs", {}).values() if "AI_NOT_RELEVANT" in (rec.get("rejection_reasons") or []))
        return len(current), reviewed, vetoed

    def cmd_ai(self, arg: str) -> list[str]:
        """The AI's second opinion on current matches, best fit first, in a layout built around it."""
        limit = self._number(arg, 8, 1, 25)
        total, reviewed_count, vetoed = self._ai_coverage()
        reviewed = [rec for rec in self._current_matches() if (rec.get("ai") or {}).get("fit") is not None]
        if not reviewed:
            if not self.settings.cloudflare_ai_token:
                return ["🤖 The AI second opinion is off: the CLOUDFLARE_AI_TOKEN secret is not reaching this workflow."]
            return [f"🤖 No current match has an AI review yet ({total} waiting). Reviews are added during scraper runs, "
                    f"up to {self.settings.ai_reviews_per_run} per run, best matches first. /run starts one now."]
        reviewed.sort(key=lambda r: (-int(r["ai"]["fit"]), -int(r.get("score") or 0)))
        lines = ["🤖 <b>AI second opinion</b>",
                 f"<i>{reviewed_count} of {total} current matches reviewed · {vetoed} vetoed as irrelevant · best fit first</i>"]
        for index, rec in enumerate(reviewed[:limit], 1):
            review = rec["ai"]
            link = rec.get("apply_url") or rec.get("url")
            title = f'<a href="{_esc(link)}">{_esc(rec.get("title"))}</a>' if link else f"<b>{_esc(rec.get('title'))}</b>"
            lines += ["", f"{index}. {title}" + (f" — {_esc(rec['company'])}" if rec.get("company") else ""),
                      f"   🎯 fit {review['fit']}/10 · score {int(rec.get('score') or 0)} · "
                      f"<code>{job_code(rec.get('canonical_id'))}</code>"]
            if review.get("summary"):
                lines.append(f"   💡 {_esc(review['summary'])}")
            if review.get("concerns"):
                lines.append(f"   ⚠️ {_esc(review['concerns'])}")
            extra = []
            if review.get("years") is not None:
                extra.append(f"{review['years']}+ yrs")
            if review.get("sponsorship") == "offered":
                extra.append("🛂 sponsorship offered")
            elif review.get("sponsorship") == "not_offered":
                extra.append("no sponsorship")
            if review.get("languages"):
                extra.append(", ".join(review["languages"][:3]))
            if extra:
                lines.append("   " + _esc(" · ".join(extra)))
        lines += ["", "/why code shows the full review · /pitch code drafts an application"]
        text = "\n".join(lines)
        return [text if len(text) <= MAX_MESSAGE else text[: MAX_MESSAGE - 1] + "…"]

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
