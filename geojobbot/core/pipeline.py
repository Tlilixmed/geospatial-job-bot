"""End-to-end run orchestration.

load state (R2) -> discovery backends (parallel) -> extraction backends (parallel) -> fuse ->
score/merge -> checkpoint save -> alerts -> final save -> run report, snapshots, retention.

Failure policy:
* one backend failing never affects the others (each is isolated and recorded)
* state load failure aborts the run BEFORE scraping/alerting (prevents duplicate alerts)
* checkpoint save failure aborts BEFORE alerting
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from ..ai.client import WorkersAI
from ..ai.review import review_job
from ..insights import radar, signals, timing, visa, yields
from ..insights.learning import apply_learning, build_model
from ..insights.prospects import ProspectBackend
from ..insights.sponsors import SponsorRegistry, annotate_record
from ..models import SourceResult
from ..notifications.commands import HELP, CommandProcessor
from ..notifications.telegram import TelegramNotifier, format_digest, format_job_message
from ..notifications.weekly import format_follow_ups, format_weekly
from ..scrapers.ats.base import ATSBackend
from ..scrapers.ats.more_ats import all_adapters
from ..scrapers.official import BundesagenturBackend, FranceTravailBackend
from ..scrapers.base import Backend, RunContext
from ..scrapers.feeds import (AdzunaBackend, ArbeitnowBackend, HimalayasBackend, JobicyBackend, JobSpyBackend,
                              JoobleBackend, JSearchBackend, ReliefWebBackend, RemoteOKBackend, RemotiveBackend,
                              RssFeedBackend, UsaJobsBackend)
from ..scrapers.pages import CareerSitesBackend, GenericPagesBackend
from ..scrapers.search import CommonCrawlBackend, DuckDuckGoBackend, SearxngBackend
from ..storage.base import LocalStore, ObjectStore, StorageError
from ..storage.r2 import R2Store
from ..storage.state import ConcurrentModificationError, StateCorruptError, StateManager
from ..utils.dates import parse_datetime, to_iso, utcnow
from ..utils.http import HttpClient
from ..utils.robots import RobotsCache
from ..utils.text import fold, job_code
from .boards import BoardRegistry
from .descriptions import DescriptionStore
from .fusion import fuse
from .health import format_health, health_messages
from .index import SUFFIX as INDEX_SUFFIX
from .index import build_index
from .jobs import (AI_VETO_MAX_FIT, alert_block_reason, apply_ai_veto, due_follow_ups, is_listed, mark_failed, mark_notified, process_fused, prune_state,
                   select_alerts)
from .prefs import apply_prefs, load_prefs
from .report import build_markdown, build_summary, diagnostics_rows

log = logging.getLogger(__name__)

API_HOST_DELAYS = {
    "boards-api.greenhouse.io": 0.5, "api.lever.co": 0.5, "api.eu.lever.co": 0.5, "api.ashbyhq.com": 0.5,
    "api.smartrecruiters.com": 0.7, "apply.workable.com": 1.0, "index.commoncrawl.org": 5.0,
    "html.duckduckgo.com": 6.0, "remotive.com": 3.0, "himalayas.app": 2.0,
}


class ConfigError(Exception):
    pass


def build_store(settings) -> ObjectStore:
    if settings.r2_configured:
        return R2Store(settings.r2_bucket_name, endpoint_url=settings.r2_endpoint_url,
                       access_key_id=settings.r2_access_key_id, secret_access_key=settings.r2_secret_access_key)
    if settings.require_r2:
        raise ConfigError("REQUIRE_R2=true but R2_ENDPOINT_URL/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY/R2_BUCKET_NAME are not all set")
    return LocalStore(settings.local_state_dir)


def build_backends(settings, sponsor_data=None) -> list[Backend]:
    """`sponsor_data`: callable returning the sponsor registers' data, for the employer prospector."""
    sources = settings.sources or {}
    ats_cfg = sources.get("ats", {}) or {}
    adapters = all_adapters()
    backends: list[Backend] = []
    for name, adapter in adapters.items():
        slugs = list(ats_cfg.get(name, []) or [])
        if name == "workday":
            from ..scrapers.ats.detect import parse_workday_url
            slugs = [ref.slug for ref in (parse_workday_url(u) for u in slugs) if ref]
        backends.append(ATSBackend(adapter, slugs))
    watched_pages = [{"name": w["name"], "url": w["url"], "geospatial": True}
                     for w in getattr(settings, "watch_list", None) or [] if w.get("url")]
    backends += [
        ProspectBackend(adapters, sponsor_data, lambda: settings.watch_list),
        CareerSitesBackend((sources.get("career_sites", []) or []) + watched_pages),
        SearxngBackend((sources.get("search", {}) or {}).get("extra_queries", [])),
        DuckDuckGoBackend((sources.get("search", {}) or {}).get("extra_queries", [])),
        CommonCrawlBackend(),
        GenericPagesBackend(adapters),
        RemotiveBackend(), JobicyBackend(), HimalayasBackend(), ArbeitnowBackend(), RemoteOKBackend(),
        RssFeedBackend(sources.get("rss_feeds", []) or []),
        UsaJobsBackend(),
        AdzunaBackend(),
        JoobleBackend(),
        JSearchBackend(),
        ReliefWebBackend(),
        BundesagenturBackend(),
        FranceTravailBackend(),
        JobSpyBackend(),
    ]
    return backends


class SecretRedactingFilter(logging.Filter):
    def __init__(self, secrets_list):
        super().__init__()
        self.secrets = [s for s in secrets_list if s and len(s) >= 6]

    def filter(self, record):
        if self.secrets:
            message = record.getMessage()
            for secret in self.secrets:
                message = message.replace(secret, "***")
            record.msg, record.args = message, ()
        return True


class Pipeline:
    def __init__(self, settings, *, store: ObjectStore | None = None, http_session=None, notifier=None,
                 backends: list[Backend] | None = None, now=None, sleep=time.sleep, ai=None, sponsors=None):
        self.settings = settings
        self.store = store
        self.http_session = http_session
        self.notifier = notifier
        self.backends = backends
        self.ai = ai
        self.sponsors = sponsors
        self.descriptions: DescriptionStore | None = None
        self._registry_cache: SponsorRegistry | None = None
        self.now = now or utcnow()
        self.sleep = sleep
        suffix = os.environ.get("GITHUB_RUN_ID") or secrets.token_hex(3)
        self.run_id = f"{self.now.strftime('%Y%m%dT%H%M%SZ')}-{suffix}"

    # ------------------------------------------------------------------ backends
    def _run_backend(self, backend: Backend, ctx: RunContext) -> tuple[SourceResult, list]:
        result = SourceResult(name=backend.name, phase=backend.phase)
        sources_state = ctx.state.setdefault("sources", {})
        with ctx.lock:
            record = sources_state.setdefault(backend.name, {})
        try:
            ok, reason = backend.enabled(ctx)
        except Exception as exc:
            ok, reason = False, f"enabled() failed: {type(exc).__name__}"
        if not ok:
            result.status, result.error = "DISABLED", reason
            return result, []
        if backend.min_interval_hours:
            last_success = parse_datetime(record.get("last_success"))
            if last_success and self.now - last_success < timedelta(hours=backend.min_interval_hours):
                result.status = "SKIPPED"
                result.error = f"ran {to_iso(last_success)}; min interval {backend.min_interval_hours}h"
                return result, []
        started = time.monotonic()
        jobs = []
        try:
            output = backend.run(ctx)
            jobs = output.jobs
            result.status, result.error = output.status, output.error
            result.http_status, result.details = output.http_status, output.details
            result.prefiltered_out = output.prefiltered_out
        except Exception as exc:  # isolation boundary
            log.exception("backend %s crashed", backend.name)
            result.status, result.error = "FAILED", f"{type(exc).__name__}: {str(exc)[:300]}"
            result.http_status = getattr(exc, "status", None)
        result.duration_s = round(time.monotonic() - started, 2)
        result.jobs = len(jobs)
        with ctx.lock:
            record["last_attempt"] = to_iso(self.now)
            record["last_status"] = result.status
            record["last_http_status"] = result.http_status
            record["last_error"] = result.error
            record["last_job_count"] = result.jobs
            if result.status in ("SUCCESS", "PARTIAL"):
                record["last_success"] = to_iso(self.now)
                record["consecutive_failures"] = 0
            elif result.status == "FAILED":
                record["last_failure"] = to_iso(self.now)
                record["consecutive_failures"] = int(record.get("consecutive_failures") or 0) + 1
        log.info("source %-22s %-8s jobs=%d %s", backend.name, result.status, result.jobs, result.error or "")
        return result, jobs

    def _run_phase(self, phase: str, backends: list[Backend], ctx: RunContext):
        selected = [b for b in backends if b.phase == phase]
        results, jobs = [], []
        with ThreadPoolExecutor(max_workers=self.settings.source_concurrency, thread_name_prefix=phase) as pool:
            for result, found in pool.map(lambda b: self._run_backend(b, ctx), selected):
                results.append(result)
                jobs.extend(found)
        return results, jobs

    # ------------------------------------------------------------------ main
    def run(self) -> tuple[int, dict]:
        settings = self.settings
        started = time.monotonic()
        report = {"run_id": self.run_id, "started_at": to_iso(self.now), "sources": [], "counts": Counter(),
                  "code": (os.environ.get("GITHUB_SHA") or "")[:7] or "local",
                  "storage": {"backend": None, "loaded": False, "saved": False, "snapshots": 0},
                  "invalid_configured": {}, "unverified_configured": {}}
        exit_code = 0
        try:
            store = self.store or build_store(settings)
        except Exception as exc:  # ConfigError or client construction failure
            return self._fatal(report, f"storage configuration: {exc}", started)
        report["storage"]["backend"] = store.name
        manager = StateManager(store, settings.state_prefix, backups_keep=settings.state_backups_keep,
                               allow_reset=settings.allow_state_reset)
        try:
            state = manager.load()
        except (StorageError, StateCorruptError) as exc:
            return self._fatal(report, f"state load failed: {exc}", started)
        report["storage"].update(loaded=True, first_run=manager.first_run,
                                 restored_from_backup=manager.restored_from_backup)
        report["commands"] = self._process_commands(manager, state)
        writes_allowed = not settings.dry_run or settings.dry_run_write_state

        client = HttpClient(settings.user_agent, default_delay=settings.default_host_delay, host_delays=API_HOST_DELAYS,
                            timeout=settings.http_timeout, session=self.http_session, sleep=self.sleep)
        client.robots = RobotsCache(client, settings.robots_agent_token)
        registry = BoardRegistry(state, self.now, hot_days=settings.hot_board_days)
        ctx = RunContext(settings, client, state, run_id=self.run_id, now=self.now, registry=registry,
                         match_config=settings.match_config())
        backends = self.backends if self.backends is not None else build_backends(
            settings, sponsor_data=lambda: self._sponsor_registry(manager).data if settings.sponsor_registers else None)

        raws = []
        for phase in ("discovery", "extraction"):
            results, found = self._run_phase(phase, backends, ctx)
            report["sources"].extend(r.to_dict() for r in results)
            raws.extend(found)
        counts = report["counts"]
        counts["raw"] = len(raws)
        yields.record_run(state, raws, self.now)
        counts["prefiltered_out"] = sum(r.get("prefiltered_out", 0) for r in report["sources"])
        for r in report["sources"]:
            details = r.get("details") or {}
            if details.get("invalid_configured"):
                report["invalid_configured"][r["name"]] = details["invalid_configured"]
            if details.get("unverified_configured"):
                report["unverified_configured"][r["name"]] = details["unverified_configured"]

        fused = fuse(raws, state["jobs"], self.now)
        counts["unique"] = len(fused)
        outcome = process_fused(fused, state, settings, self.now)
        counts.update(outcome.counts)
        self.descriptions = self._keep_descriptions(manager, outcome, state)
        counts.update(self._sponsors(manager, ctx, outcome, state, writes_allowed, report))
        counts.update(self._learning(manager, outcome, state))
        counts.update(self._ai_review(outcome, state))
        counts.update(self._visa(state, outcome.seen_ids))
        counts.update(self._timing(outcome, state))

        relevant_by_board = Counter()
        for fj, result, _ in outcome.evaluated:
            if result.tier != "rejected":
                for key in fj.board_keys:
                    relevant_by_board[key] += 1
        for key, n in relevant_by_board.items():
            registry.mark_relevant(key, n)
        counts.update(prune_state(state, settings, self.now))
        state["last_run_id"] = self.run_id
        state.setdefault("stats", {})["runs"] = int(state.get("stats", {}).get("runs", 0)) + 1

        if writes_allowed:
            try:
                manager.save(state, self.run_id)
            except (StorageError, ConcurrentModificationError, StateCorruptError) as exc:
                return self._fatal(report, f"checkpoint save failed, alerts not sent: {exc}", started, ctx)
            if self.descriptions is not None:
                try:
                    self.descriptions.prune(state["jobs"])
                    self.descriptions.save(manager)
                except StorageError as exc:
                    log.warning("descriptions not saved: %s", exc)

        selected, alert_counts = select_alerts(state, outcome.seen_ids, settings, self.now)
        counts.update(alert_counts)
        notifier = self.notifier
        if notifier is None and settings.telegram_configured and not settings.dry_run:
            notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id,
                                        delay_s=settings.telegram_delay_s, sleep=self.sleep)
        for message, batch in self._alert_messages(selected):
            if settings.dry_run:
                print("\n--- DRY RUN ALERT ---\n" + message)
                counts["alerts_pending"] += len(batch)
                continue
            if notifier is None:
                counts["alerts_pending"] += len(batch)
                continue
            ok, error = notifier.send(message)
            if ok:
                for rec in batch:
                    mark_notified(rec, utcnow())
                counts["alerts_sent"] += len(batch)
                counts["messages_sent"] += 1
            else:
                for rec in batch:
                    mark_failed(rec, error)
                counts["alerts_failed"] += len(batch)
                log.warning("telegram delivery failed for %s: %s", ", ".join(r["canonical_id"] for r in batch), error)
        if notifier is None and selected and not settings.dry_run:
            log.warning("Telegram is not configured: %d alerts left pending", len(selected))
        counts["weekly_summary_sent"] = int(self._weekly_summary(manager, state, notifier))
        counts["follow_ups_sent"] = self._follow_ups(manager, state, notifier)
        counts["deadline_reminders_sent"] = self._deadline_reminders(manager, state, notifier)
        counts.update(self._insights(manager, ctx, state, notifier))
        health = format_health(health_messages(state, report))
        if health and notifier is not None and not settings.dry_run:
            counts["health_alerts_sent"] = int(notifier.send(health)[0])

        counts["jobs_in_state"] = len(state["jobs"])
        counts["boards_known"] = len(state["boards"])
        report["new_boards"] = dict(registry.new_this_run)
        report["http"] = client.stats.to_dict()
        report["parser_errors"] = dict(ctx.parser_errors)

        if writes_allowed:
            try:
                manager.save(state, self.run_id)
                report["storage"]["saved"] = True
            except (StorageError, ConcurrentModificationError, StateCorruptError) as exc:
                report["fatal"] = f"final state save failed (alerts sent this run may repeat): {exc}"
                exit_code = 1
            self._write_artifacts(manager, report, outcome, ctx, state)

        enabled = [r for r in report["sources"] if r["status"] not in ("DISABLED", "SKIPPED")]
        if enabled and all(r["status"] == "FAILED" for r in enabled):
            report["fatal"] = report.get("fatal") or "every enabled source failed (network problem?)"
            exit_code = exit_code or 2
        report["duration_s"] = time.monotonic() - started
        self._emit(report)
        return exit_code, report

    def _process_commands(self, manager: StateManager, state: dict) -> dict:
        """Answer pending Telegram commands, then overlay the stored preferences on this run's settings.

        Never fatal: a Telegram or storage hiccup here must not stop the scrape.
        """
        settings = self.settings
        handled: dict = {}
        if settings.telegram_configured and not settings.dry_run and self.notifier is None:
            try:
                notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id,
                                            delay_s=settings.telegram_delay_s, sleep=self.sleep)
                handled = dict(CommandProcessor(settings, manager, notifier, state=state, now=self.now,
                                                in_scraper_run=True).run())
            except Exception as exc:  # e.g. HTTP 409 when a webhook (Cloudflare Worker) owns the updates
                log.info("telegram polling skipped: %s", type(exc).__name__)
                handled["polling"] = f"skipped ({type(exc).__name__})"
        try:
            apply_prefs(settings, load_prefs(manager))
        except Exception as exc:
            log.warning("stored preferences not applied: %s", type(exc).__name__)
            handled["prefs"] = f"not applied ({type(exc).__name__})"
        return handled

    def _keep_descriptions(self, manager: StateManager, outcome, state: dict) -> DescriptionStore | None:
        """Remember the text of accepted jobs (for the AI backlog and /pitch); optional, never fatal."""
        try:
            store = DescriptionStore.load(manager)
            for fused, result, rec in outcome.evaluated:
                if result.tier in ("high", "possible"):
                    store.put(rec.canonical_id, fused.description)
            return store
        except Exception as exc:
            log.warning("descriptions not kept: %s", type(exc).__name__)
            return None

    def _sponsor_registry(self, manager: StateManager) -> SponsorRegistry:
        """Loaded once per run: the prospector reads it during discovery, the sponsor lookup after scoring."""
        if self._registry_cache is None:
            self._registry_cache = SponsorRegistry.load(manager)
        return self._registry_cache

    def _sponsors(self, manager: StateManager, ctx: RunContext, outcome, state: dict, writes_allowed: bool,
                  report: dict) -> Counter:
        """Look every accepted employer up in the official sponsor registers (refreshed weekly, never fatal)."""
        counts = Counter()
        if not self.settings.sponsor_registers and self.sponsors is None:
            return counts
        try:
            registry = self.sponsors if self.sponsors is not None else self._sponsor_registry(manager)
            if self.sponsors is None and registry.stale(self.now) and writes_allowed and not ctx.out_of_time(420):
                report["sponsor_registers"] = registry.refresh(ctx)
                registry.save(manager)
            for _, _, rec in outcome.evaluated:
                stored = state["jobs"][rec.canonical_id]
                if set(stored.get("rejection_reasons") or []) - {"LOW_SCORE"}:
                    continue
                before = stored.get("tier")
                annotate_record(stored, registry, self.settings)
                counts["sponsor_matches"] += bool(stored.get("sponsor"))
                if stored.get("tier") != before:
                    counts[f"tier_{before}"] -= 1
                    counts[f"tier_{stored['tier']}"] += 1
        except Exception as exc:
            log.warning("sponsor registers skipped: %s", type(exc).__name__)
            counts["sponsor_errors"] += 1
        return counts

    def _ai_review(self, outcome, state: dict) -> Counter:
        """Workers AI second opinion for accepted jobs that have a description and no review yet.

        Best matches first, capped per run (AI_REVIEWS_PER_RUN), stored on the job so it happens once.
        Never fatal and never required: without a token this is a no-op.
        """
        counts = Counter()
        settings = self.settings
        if settings.dry_run and not settings.dry_run_write_state:
            return counts
        ai = self.ai if self.ai is not None else WorkersAI.from_settings(settings)
        if ai is None:
            return counts
        # this run's jobs first, then the backlog of stored matches whose text we kept (descriptions store)
        work = {}
        for fused, result, rec in outcome.evaluated:
            if len(fused.description or "") >= 300:
                work[rec.canonical_id] = fused.description
        if self.descriptions is not None:
            for cid, text in self.descriptions.data.items():
                work.setdefault(cid, text)
        pending = []
        for cid, text in work.items():
            stored = state["jobs"].get(cid)
            if (stored and stored.get("tier") in ("high", "possible") and not stored.get("ai")
                    and alert_block_reason(stored, settings, self.now) is None):
                pending.append((stored, text))
        pending.sort(key=lambda pair: -int(pair[0].get("score") or 0))
        for stored, text in pending:
            if ai.budget <= 0 or ai.failures >= 3:
                counts["ai_deferred"] += 1
                continue
            try:
                place = ", ".join(p for p in (stored.get("city"), stored.get("country")) if p) or stored.get("location_raw")
                review = review_job(ai, settings.candidate_profile, title=stored.get("title") or "",
                                    company=stored.get("company"), location=place or "", description=text)
            except Exception as exc:  # a model hiccup must never cost us the run
                log.warning("ai review skipped for %s: %s", stored.get("canonical_id"), type(exc).__name__)
                review = None
            if review is None:
                counts["ai_failed"] += 1
                continue
            review["veto"] = bool(settings.ai_veto_possible and review["fit"] <= AI_VETO_MAX_FIT)
            review["at"] = to_iso(self.now)
            stored["ai"] = review
            counts["ai_reviewed"] += 1
            if apply_ai_veto(stored):
                counts["ai_vetoed"] += 1
                counts["tier_possible"] -= 1
                counts["tier_rejected"] += 1
        return counts

    def _timing(self, outcome, state: dict) -> Counter:
        """Application deadlines read from descriptions, and postings that keep coming back."""
        counts = Counter()
        try:
            texts = dict(self.descriptions.data) if self.descriptions is not None else {}
            texts.update({rec.canonical_id: fused.description for fused, _, rec in outcome.evaluated if fused.description})
            for cid, text in texts.items():
                stored = state["jobs"].get(cid)
                if stored and stored.get("tier") in ("high", "possible") and timing.annotate_deadline(stored, text, self.now):
                    counts["deadlines_found"] += 1
            counts["reposts_marked"] = timing.mark_reposts(state["jobs"])
        except Exception as exc:
            log.warning("timing facts skipped: %s", type(exc).__name__)
        return counts

    def _deadline_reminders(self, manager: StateManager, state: dict, notifier) -> int:
        """One reminder for High matches that close within three days and were neither applied to nor hidden."""
        if notifier is None or self.settings.dry_run:
            return 0
        try:
            prefs = load_prefs(manager)
            muted = [fold(t) for t in prefs.get("muted") or [] if t.strip()]

            def is_open(rec):
                label = fold(f"{rec.get('title') or ''} {rec.get('company') or ''}")
                return (alert_block_reason(rec, self.settings, self.now) is None and is_listed(rec, self.now)
                        and not any(term in label for term in muted))

            due = timing.due_reminders(state, prefs, self.now, is_open)
            text = timing.format_reminders(due, self.now, lambda rec: job_code(rec.get("canonical_id")))
            if text and notifier.send(text)[0]:
                for rec in due:
                    rec["deadline_reminded"] = True
                return len(due)
        except Exception as exc:
            log.warning("deadline reminders skipped: %s", type(exc).__name__)
        return 0

    def _visa(self, state: dict, seen_ids: set) -> Counter:
        """Compare every accepted job with its country's work-visa route (pure computation, never fatal)."""
        counts = Counter()
        if not self.settings.visa_paths:
            return counts
        try:
            rules = visa.load_rules()
            for cid, rec in state["jobs"].items():
                before = rec.get("tier")
                # jobs the penalty pushed under the cut-off are revisited too, so it can be lifted when facts change
                if before not in ("high", "possible") and not (rec.get("score_breakdown") or {}).get("visa"):
                    continue
                if visa.annotate(rec, self.settings, rules):
                    counts[f"visa_{rec['visa']['verdict']}"] += 1
                if rec.get("tier") != before:
                    counts["visa_retiered"] += 1
                if rec.get("tier") != before and cid in seen_ids:  # the run's tier counts cover this run's jobs only
                    counts[f"tier_{before}"] -= 1
                    counts[f"tier_{rec['tier']}"] += 1
        except Exception as exc:
            log.warning("visa routes skipped: %s", type(exc).__name__)
        return counts

    def _learning(self, manager: StateManager, outcome, state: dict) -> Counter:
        """Nudge scores by what the user applied to and hid (transparent, small, optional)."""
        counts = Counter()
        try:
            model = build_model(load_prefs(manager), state["jobs"])
            if not model:
                return counts
            for _, _, rec in outcome.evaluated:
                stored = state["jobs"][rec.canonical_id]
                before = stored.get("tier")
                if apply_learning(stored, model, self.settings):
                    counts["learned_adjustments"] += 1
                if stored.get("tier") != before:
                    counts[f"tier_{before}"] -= 1
                    counts[f"tier_{stored['tier']}"] += 1
        except Exception as exc:
            log.warning("learning skipped: %s", type(exc).__name__)
        return counts

    def _insights(self, manager: StateManager, ctx: RunContext, state: dict, notifier) -> Counter:
        """Procurement signals (daily check, sent only when new) and the monthly skills radar."""
        counts = Counter()
        if notifier is None or self.settings.dry_run:
            return counts
        if self.settings.market_signals:
            try:
                text = signals.format_signals(signals.check(ctx, state))
                if text:
                    counts["market_signals_sent"] = int(notifier.send(text)[0])
            except Exception as exc:
                log.warning("market signals skipped: %s", type(exc).__name__)
        if self.settings.monthly_radar:
            try:
                maintenance = state.setdefault("maintenance", {})
                last = parse_datetime(maintenance.get("last_radar"))
                if last is None or self.now - last >= timedelta(days=30):
                    prefs = load_prefs(manager)
                    data = radar.compute(state["jobs"], radar.my_skills(self.settings, prefs), self.now)
                    if data["jobs"] >= radar.MIN_JOBS and notifier.send(radar.format_radar(data))[0]:
                        maintenance["last_radar"] = to_iso(self.now)
                        counts["radar_sent"] = 1
            except Exception as exc:
                log.warning("skills radar skipped: %s", type(exc).__name__)
        return counts

    def _follow_ups(self, manager: StateManager, state: dict, notifier) -> int:
        """Remind about applications with no recorded outcome after 7 and 21 days (once per stage)."""
        if notifier is None or self.settings.dry_run:
            return 0
        try:
            reminded = state.setdefault("maintenance", {}).setdefault("follow_ups", {})
            due = due_follow_ups(load_prefs(manager), reminded, self.now)
            text = format_follow_ups(due, state["jobs"], self.now)
            if text and notifier.send(text)[0]:
                for cid, _, stage in due:
                    reminded[cid] = stage
                return len(due)
        except Exception as exc:
            log.warning("follow-up reminders skipped: %s", type(exc).__name__)
        return 0

    def _weekly_summary(self, manager: StateManager, state: dict, notifier) -> bool:
        """Send the weekly summary once every 7 days (tracked in state.maintenance)."""
        if not self.settings.weekly_summary or notifier is None or self.settings.dry_run:
            return False
        maintenance = state.setdefault("maintenance", {})
        last = parse_datetime(maintenance.get("last_weekly"))
        if last is not None and self.now - last < timedelta(days=7):
            return False
        try:
            text = format_weekly(state, load_prefs(manager), self.settings, self.now)
        except Exception as exc:
            log.warning("weekly summary skipped: %s", type(exc).__name__)
            return False
        if text is None:
            return False
        ok, _ = notifier.send(text)
        if ok:
            maintenance["last_weekly"] = to_iso(self.now)
        return ok

    def _alert_messages(self, selected: list[dict]) -> list[tuple[str, list[dict]]]:
        """Messages to send this run, each with the records it covers (marked notified only on delivery)."""
        if self.settings.alert_format == "individual":
            return [(format_job_message(rec, update=bool(rec.get("notified") and rec.get("pending_update_alert"))), [rec])
                    for rec in selected]
        return format_digest(selected, now=self.now)

    def _write_artifacts(self, manager: StateManager, report: dict, outcome, ctx: RunContext, state: dict) -> None:
        day = self.now.strftime("%Y-%m-%d")
        try:
            if self.settings.store_raw_snapshots:
                for source, rows in ctx.snapshots.items():
                    if rows:
                        safe = source.replace(":", "_").replace("/", "_")
                        manager.write_jsonl_gz(f"raw/{day}/{self.run_id}/{safe}.jsonl.gz", rows)
                        report["storage"]["snapshots"] += 1
            full = dict(report, counts=dict(report["counts"]), diagnostics=diagnostics_rows(outcome.evaluated))
            manager.write_json(f"runs/{day}/{self.run_id}.json.gz", full, compress=True)
            manager.write_json("runs/latest.json", dict(report, counts=dict(report["counts"])), compress=False)
            # read model for the Cloudflare Worker's instant replies
            manager.write_json(INDEX_SUFFIX, build_index(state, self.settings, report, self.now, HELP,
                                                         views=self._views(manager, state)), compress=False)
            maintenance = state.setdefault("maintenance", {})
            last = parse_datetime(maintenance.get("last_cleanup"))
            if last is None or self.now - last > timedelta(hours=24):
                deleted = manager.cleanup_retention(self.settings.raw_retention_days, self.settings.run_retention_days,
                                                    self.now)
                maintenance["last_cleanup"] = to_iso(self.now)
                maintenance["last_cleanup_deleted"] = deleted
                report["counts"]["retention_deleted"] = sum(deleted.values())
                manager.save(state, self.run_id)
        except (StorageError, ConcurrentModificationError) as exc:
            log.warning("writing run artifacts failed: %s", exc)
            report["artifact_error"] = str(exc)

    def _views(self, manager: StateManager, state: dict) -> dict:
        """Replies formatted here so the Worker can send them instantly ({command: html})."""
        views = {}
        try:
            prefs = load_prefs(manager)
            views["sources"] = yields.format_yield(yields.compute(state, prefs, self.now))
            processor = CommandProcessor(self.settings, manager, None, state=state, now=self.now, prefs=prefs)
            views["visa"] = processor.cmd_visa("")[0]
            views["prospects"] = processor.cmd_prospects("")[0]
        except Exception as exc:
            log.warning("views not built: %s", type(exc).__name__)
        return views

    def _fatal(self, report: dict, message: str, started: float, ctx: RunContext | None = None):
        log.error(message)
        report["fatal"] = message
        report["duration_s"] = time.monotonic() - started
        if ctx is not None:
            report["http"] = ctx.client.stats.to_dict()
        self._emit(report)
        return 1, report

    @staticmethod
    def _emit(report: dict) -> None:
        print(build_summary(report))
        path = os.environ.get("GITHUB_STEP_SUMMARY")
        if path:
            try:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(build_markdown(report) + "\n")
            except OSError:
                pass
