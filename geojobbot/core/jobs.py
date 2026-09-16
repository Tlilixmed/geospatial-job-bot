"""Job lifecycle against persistent state.

* new jobs are created with first_seen/last_seen
* known jobs keep their canonical id; lower-authority sources only add provenance
* authoritative re-observations update fields, record changes and rescore
* alert eligibility: accepted tier, not yet notified, still live this run, fresh enough, retry budget left
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta

from ..matching.matcher import MatchResult, score_job
from ..models import TIER_HIGH, TIER_POSSIBLE, TIER_REJECTED, JobRecord
from ..utils.dates import age_hours, parse_datetime, to_iso
from ..utils.text import normalize_title
from .fusion import FusedJob

CHANGE_FIELDS = ("title", "location_raw", "salary", "remote", "employment_type", "description_hash")
ALERT_CHANGE_FIELDS = {"title", "location_raw", "salary", "remote"}
MAX_CHANGES_KEPT = 10
MAX_SOURCES_KEPT = 12
MAX_PAGE_CACHE = 30000


@dataclass
class ProcessOutcome:
    counts: Counter = field(default_factory=Counter)
    evaluated: list[tuple[FusedJob, MatchResult, JobRecord]] = field(default_factory=list)
    seen_ids: set = field(default_factory=set)


def _apply(rec: JobRecord, fused: FusedJob, result: MatchResult) -> None:
    loc = fused.location
    rec.title = fused.title
    rec.company = fused.company or rec.company
    rec.url = fused.url or rec.url
    rec.apply_url = fused.apply_url or rec.apply_url
    rec.location_raw = loc.raw
    rec.city, rec.region, rec.country = loc.city, loc.region, loc.country
    rec.remote, rec.remote_scope, rec.work_mode = loc.remote, loc.remote_scope, loc.work_mode
    rec.employment_type = fused.employment_type
    rec.salary = fused.salary
    if fused.posted_at is not None and (fused.posted_at_reliable or not rec.posted_at_reliable):
        rec.posted_at = to_iso(fused.posted_at)
        rec.posted_at_reliable = fused.posted_at_reliable
    rec.field_priority = fused.priority
    rec.extraction_method = fused.extraction_method
    rec.description_length = len(fused.description or "")
    rec.description_hash = fused.description_hash
    rec.content_fingerprint = fused.content_fingerprint
    rec.fuzzy_key = fused.fuzzy_key or rec.fuzzy_key
    rec.score = result.score
    rec.tier = result.tier
    rec.score_breakdown = result.breakdown
    rec.matched_skills = [f"{h.canonical} ({h.qualifier})" if h.qualifier else h.canonical for h in result.skills]
    rec.matched_domains = result.domain_names
    rec.why_matched = result.why_matched
    rec.rejection_reasons = result.rejection_reasons


def _merge_sources(rec: JobRecord, fused: FusedJob, now) -> None:
    existing = {(s.get("source_name"), s.get("job_url") or s.get("source_url")): s for s in rec.sources}
    for src in fused.sources(now):
        key = (src["source_name"], src.get("job_url") or src.get("source_url"))
        if key in existing:
            existing[key]["last_seen"] = to_iso(now)
        else:
            existing[key] = src
    ordered = sorted(existing.values(), key=lambda s: s.get("last_seen") or "", reverse=True)
    rec.sources = ordered[:MAX_SOURCES_KEPT]


def process_fused(fused_jobs: list[FusedJob], state: dict, settings, now) -> ProcessOutcome:
    jobs = state.setdefault("jobs", {})
    cfg = settings.match_config()
    outcome = ProcessOutcome()
    for fused in fused_jobs:
        result = score_job(fused.title, fused.description, fused.location.to_dict(), cfg)
        outcome.counts[f"tier_{result.tier}"] += 1
        existing = jobs.get(fused.canonical_id)
        if existing is None:
            rec = JobRecord(canonical_id=fused.canonical_id, title=fused.title, first_seen=to_iso(now),
                            last_seen=to_iso(now), from_rotation=fused.from_rotation)
            _apply(rec, fused, result)
            _merge_sources(rec, fused, now)
            rec.aliases = fused.aliases
            rec.board_keys = sorted(fused.board_keys)
            outcome.counts["new"] += 1
        else:
            rec = JobRecord.from_dict(existing)
            rec.last_seen = to_iso(now)
            rec.from_rotation = rec.from_rotation and fused.from_rotation
            _merge_sources(rec, fused, now)
            rec.aliases = list(dict.fromkeys((rec.aliases or []) + fused.aliases))[:40]
            rec.board_keys = sorted(set(rec.board_keys or []) | fused.board_keys)
            outcome.counts["already_seen"] += 1
            better_description = rec.description_length < 300 <= len(fused.description or "")
            if fused.priority >= rec.field_priority or better_description:
                before = {
                    "title": normalize_title(rec.title), "location_raw": rec.location_raw, "salary": rec.salary,
                    "remote": rec.remote, "employment_type": rec.employment_type,
                    "description_hash": rec.description_hash,
                }
                previous_fingerprint = rec.content_fingerprint
                _apply(rec, fused, result)
                after = {
                    "title": normalize_title(rec.title), "location_raw": rec.location_raw, "salary": rec.salary,
                    "remote": rec.remote, "employment_type": rec.employment_type,
                    "description_hash": rec.description_hash,
                }
                changed = [f for f in CHANGE_FIELDS if before[f] != after[f]]
                if previous_fingerprint and previous_fingerprint != rec.content_fingerprint and changed:
                    outcome.counts["updated"] += 1
                    rec.changes = (rec.changes or []) + [{"at": to_iso(now), "fields": changed}]
                    rec.changes = rec.changes[-MAX_CHANGES_KEPT:]
                    if (settings.alert_on_changes and rec.notified and ALERT_CHANGE_FIELDS & set(changed)
                            and rec.tier in (TIER_HIGH, TIER_POSSIBLE)):
                        rec.pending_update_alert = True
        jobs[rec.canonical_id] = rec.to_dict()
        outcome.seen_ids.add(rec.canonical_id)
        outcome.evaluated.append((fused, result, rec))
    return outcome


def alert_block_reason(rec: dict, settings, now) -> str | None:
    posted = parse_datetime(rec.get("posted_at"))
    if rec.get("posted_at_reliable") and posted is not None:
        limit = settings.rotation_max_job_age_hours if rec.get("from_rotation") else settings.max_job_age_hours
        hours = age_hours(posted, now)
        if hours is not None and hours > limit:
            return "STALE_POSTING"
    return None


def select_alerts(state: dict, seen_ids: set, settings, now) -> tuple[list[dict], Counter]:
    counts = Counter()
    candidates = []
    accepted = {TIER_HIGH, TIER_POSSIBLE} if settings.notify_possible else {TIER_HIGH}
    for cid in seen_ids:
        rec = state["jobs"].get(cid)
        if not rec or rec.get("tier") not in accepted:
            continue
        if rec.get("notified") and not rec.get("pending_update_alert"):
            counts["already_notified"] += 1
            continue
        if int(rec.get("notify_attempts") or 0) >= settings.max_notify_attempts:
            counts["attempts_exhausted"] += 1
            continue
        reason = alert_block_reason(rec, settings, now)
        rec["alert_block_reason"] = reason
        if reason:
            counts["blocked_stale"] += 1
            continue
        candidates.append(rec)
    candidates.sort(key=lambda r: (r.get("tier") != TIER_HIGH, -int(r.get("score") or 0),
                                   -(parse_datetime(r.get("posted_at")) or now).timestamp()))
    selected = candidates[: settings.max_alerts_per_run]
    counts["deferred_by_cap"] = max(0, len(candidates) - len(selected))
    return selected, counts


def mark_notified(rec: dict, now) -> None:
    rec["notified"] = True
    rec["notified_at"] = to_iso(now)
    rec["pending_update_alert"] = False
    rec["last_notify_error"] = None


def mark_failed(rec: dict, error: str) -> None:
    rec["notify_attempts"] = int(rec.get("notify_attempts") or 0) + 1
    rec["last_notify_error"] = (error or "")[:300]


def prune_state(state: dict, settings, now) -> Counter:
    counts = Counter()
    jobs = state.get("jobs", {})
    for cid in list(jobs):
        rec = jobs[cid]
        last = parse_datetime(rec.get("last_seen"))
        if last is None:
            continue
        rejected = rec.get("tier") == TIER_REJECTED and not rec.get("notified")
        limit = settings.rejected_retention_days if rejected else settings.job_retention_days
        if now - last > timedelta(days=limit):
            del jobs[cid]
            counts["jobs_pruned"] += 1
    cache = state.get("page_cache", {})
    for url in list(cache):
        fetched = parse_datetime(cache[url].get("fetched_at"))
        if fetched is None or now - fetched > timedelta(days=30):
            del cache[url]
            counts["page_cache_pruned"] += 1
    if len(cache) > MAX_PAGE_CACHE:
        for url in sorted(cache, key=lambda u: cache[u].get("fetched_at") or "")[: len(cache) - MAX_PAGE_CACHE]:
            del cache[url]
            counts["page_cache_pruned"] += 1
    return counts
