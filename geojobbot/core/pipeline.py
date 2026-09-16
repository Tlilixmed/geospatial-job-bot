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

from ..models import SourceResult
from ..notifications.telegram import TelegramNotifier, format_digest, format_job_message
from ..scrapers.ats.base import ATSBackend
from ..scrapers.ats.more_ats import all_adapters
from ..scrapers.base import Backend, RunContext
from ..scrapers.feeds import (AdzunaBackend, ArbeitnowBackend, HimalayasBackend, JobicyBackend, JobSpyBackend,
                              JoobleBackend, RemoteOKBackend, RemotiveBackend, RssFeedBackend, UsaJobsBackend)
from ..scrapers.pages import CareerSitesBackend, GenericPagesBackend
from ..scrapers.search import CommonCrawlBackend, DuckDuckGoBackend, SearxngBackend
from ..storage.base import LocalStore, ObjectStore, StorageError
from ..storage.r2 import R2Store
from ..storage.state import ConcurrentModificationError, StateCorruptError, StateManager
from ..utils.dates import parse_datetime, to_iso, utcnow
from ..utils.http import HttpClient
from ..utils.robots import RobotsCache
from .boards import BoardRegistry
from .fusion import fuse
from .jobs import mark_failed, mark_notified, process_fused, prune_state, select_alerts
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


def build_backends(settings) -> list[Backend]:
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
    backends += [
        CareerSitesBackend(sources.get("career_sites", []) or []),
        SearxngBackend((sources.get("search", {}) or {}).get("extra_queries", [])),
        DuckDuckGoBackend((sources.get("search", {}) or {}).get("extra_queries", [])),
        CommonCrawlBackend(),
        GenericPagesBackend(adapters),
        RemotiveBackend(), JobicyBackend(), HimalayasBackend(), ArbeitnowBackend(), RemoteOKBackend(),
        RssFeedBackend(sources.get("rss_feeds", []) or []),
        UsaJobsBackend(),
        AdzunaBackend(),
        JoobleBackend(),
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
                 backends: list[Backend] | None = None, now=None, sleep=time.sleep):
        self.settings = settings
        self.store = store
        self.http_session = http_session
        self.notifier = notifier
        self.backends = backends
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
        writes_allowed = not settings.dry_run or settings.dry_run_write_state

        client = HttpClient(settings.user_agent, default_delay=settings.default_host_delay, host_delays=API_HOST_DELAYS,
                            timeout=settings.http_timeout, session=self.http_session, sleep=self.sleep)
        client.robots = RobotsCache(client, settings.robots_agent_token)
        registry = BoardRegistry(state, self.now, hot_days=settings.hot_board_days)
        ctx = RunContext(settings, client, state, run_id=self.run_id, now=self.now, registry=registry,
                         match_config=settings.match_config())
        backends = self.backends if self.backends is not None else build_backends(settings)

        raws = []
        for phase in ("discovery", "extraction"):
            results, found = self._run_phase(phase, backends, ctx)
            report["sources"].extend(r.to_dict() for r in results)
            raws.extend(found)
        counts = report["counts"]
        counts["raw"] = len(raws)
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
