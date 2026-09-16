"""Shared machinery for ATS adapters.

Board validation semantics (never silently treat an invalid board as "zero jobs"):
  VALID             board exists (jobs may be zero)
  INVALID           ATS says the board does not exist (404/410)
  EMPTY_UNVERIFIED  ATS returned an empty list but cannot distinguish "no jobs" from "no such board"
  SCHEMA_MISMATCH   response did not have the documented structure
  ERROR             transient/network/blocked failure
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ...models import BackendOutput, RawJob
from ...utils.http import FetchError
from ..base import Backend, RunContext
from .detect import BoardRef

log = logging.getLogger(__name__)


@dataclass
class BoardResult:
    status: str
    jobs: list[RawJob] = field(default_factory=list)
    listed: int = 0
    prefiltered_out: int = 0
    error: str | None = None
    http_status: int | None = None
    company: str | None = None
    variant: str | None = None
    detail_errors: int = 0


class ATSAdapter:
    ats: str = "ats"

    def fetch_board(self, ctx: RunContext, ref: BoardRef, *, geo_context: bool, variant: str | None,
                    company_hint: str | None) -> BoardResult:
        raise NotImplementedError

    def fetch_job(self, ctx: RunContext, url: str) -> list[RawJob] | None:
        """Fetch a single job by public URL through the ATS API, if supported. None = unsupported."""
        return None

    @staticmethod
    def error_result(exc: FetchError) -> BoardResult:
        if exc.kind == "NOT_FOUND":
            return BoardResult("INVALID", error="board not found (404)", http_status=exc.status)
        return BoardResult("ERROR", error=str(exc), http_status=exc.status)


class ATSBackend(Backend):
    """Runs one ATS adapter over configured, hot and rotating boards."""

    phase = "extraction"
    source_type = "ats"

    def __init__(self, adapter: ATSAdapter, configured_slugs: list[str]):
        self.adapter = adapter
        self.name = adapter.ats
        self.configured_slugs = [s for s in configured_slugs if s]

    def run(self, ctx: RunContext) -> BackendOutput:
        registry = ctx.registry
        selection = registry.select(self.adapter.ats, self.configured_slugs, ctx.settings.rotation_boards_per_ats)
        out = BackendOutput()
        counts = {"VALID": 0, "INVALID": 0, "EMPTY_UNVERIFIED": 0, "SCHEMA_MISMATCH": 0, "ERROR": 0}
        invalid_configured, unverified_configured, errors = [], [], []
        reasons = {"config": 0, "hot": 0, "rotation": 0}
        skipped_for_time = 0
        last_http = None
        for ref, reason in selection:
            if ctx.out_of_time(reserve_s=180):
                skipped_for_time += 1
                continue
            entry = registry.entry(ref.key) or {}
            geo_context = reason == "config" or registry.is_geo_board(ref.key)
            try:
                result = self.adapter.fetch_board(ctx, ref, geo_context=geo_context, variant=entry.get("variant"),
                                                  company_hint=entry.get("company"))
            except FetchError as exc:
                result = ATSAdapter.error_result(exc)
            except Exception as exc:  # malformed payloads must never stop other boards
                log.exception("%s board %s crashed", self.name, ref.slug)
                ctx.record_parser_error(self.name)
                result = BoardResult("SCHEMA_MISMATCH", error=f"{type(exc).__name__}: {exc}"[:300])
            reasons[reason] += 1
            counts[result.status] = counts.get(result.status, 0) + 1
            registry.record(ref, result.status, job_count=result.listed, error=result.error,
                            variant=result.variant, company=result.company)
            for job in result.jobs:
                job.board_key = ref.key
                job.from_rotation = reason == "rotation"
                job.geo_context = job.geo_context or geo_context
            out.jobs.extend(result.jobs)
            out.prefiltered_out += result.prefiltered_out
            if result.http_status:
                last_http = result.http_status
            if reason == "config" and result.status == "INVALID":
                invalid_configured.append(ref.slug)
            if reason == "config" and result.status in ("EMPTY_UNVERIFIED", "SCHEMA_MISMATCH"):
                unverified_configured.append(f"{ref.slug} ({result.status})")
            if result.status in ("ERROR", "SCHEMA_MISMATCH"):
                errors.append(f"{ref.slug}: {result.error}")
            if result.jobs:
                ctx.snapshot(self.name, [j.snapshot() for j in result.jobs])
        out.details = {
            "boards_checked": sum(reasons.values()),
            "by_reason": reasons,
            "by_status": {k: v for k, v in counts.items() if v},
            "invalid_configured": invalid_configured,
            "unverified_configured": unverified_configured,
            "errors": errors[:20],
            "skipped_for_time": skipped_for_time,
        }
        out.http_status = last_http
        checked = sum(reasons.values())
        if checked == 0:
            out.status = "SKIPPED" if not selection else "PARTIAL"
            out.error = "no boards selected" if not selection else "time budget exhausted"
        elif counts.get("ERROR", 0) + counts.get("SCHEMA_MISMATCH", 0) == checked:
            out.status = "FAILED"
            out.error = errors[0] if errors else "all boards failed"
        elif counts.get("ERROR", 0) or counts.get("SCHEMA_MISMATCH", 0) or invalid_configured or skipped_for_time:
            out.status = "PARTIAL"
        return out
