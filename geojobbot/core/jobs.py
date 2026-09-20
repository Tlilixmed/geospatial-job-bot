"""Job lifecycle against persistent state.

* new jobs are created with first_seen/last_seen
* known jobs keep their canonical id; lower-authority sources only add provenance
* authoritative re-observations update fields, record changes and rescore
* alert eligibility: accepted tier, not yet notified, still live this run, fresh enough, retry budget left
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta

from ..matching.matcher import MatchResult, score_job
from ..models import TIER_HIGH, TIER_POSSIBLE, TIER_REJECTED, JobRecord
from ..utils.dates import age_hours, parse_datetime, to_iso
from ..insights.timing import deadline_passed
from ..utils.text import fold, normalize_company, normalize_title
from .fusion import FusedJob

# Bump when the matching rules change (profile.py, matcher.py, location.py): stored matches whose text is kept are
# scored again at the next run instead of waiting to be seen again, so a fixed rule also fixes yesterday's jobs.
SCORER_VERSION = 2

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
    rec.rules = SCORER_VERSION
    rec.score = result.score
    rec.tier = result.tier
    rec.score_breakdown = result.breakdown
    rec.matched_skills = [f"{h.canonical} ({h.qualifier})" if h.qualifier else h.canonical for h in result.skills]
    rec.matched_domains = result.domain_names
    rec.why_matched = result.why_matched
    rec.rejection_reasons = result.rejection_reasons


def rescore_stored(rec: dict, text: str, cfg) -> None:
    """Score a stored job again from its kept text. The adjustments recorded in score_breakdown go with the old score;
    the caller applies them again (register bonus, learning, AI lift, visa penalty, AI veto)."""
    from ..utils.location import parse_location

    place = parse_location(rec.get("location_raw"), None, None)  # the place is read again too: the gazetteer improves
    if place.country or place.city:
        rec.update(city=place.city, region=place.region, country=place.country)
    location = {**{k: rec.get(k) for k in ("city", "region", "country", "remote", "remote_scope", "work_mode")},
                "raw": rec.get("location_raw")}
    result = score_job(rec.get("title") or "", text, location, cfg)
    rec.update(rules=SCORER_VERSION, score=result.score, tier=result.tier, score_breakdown=dict(result.breakdown),
               matched_skills=[f"{h.canonical} ({h.qualifier})" if h.qualifier else h.canonical for h in result.skills],
               matched_domains=result.domain_names, why_matched=result.why_matched, rejection_reasons=result.rejection_reasons)
    for key in ("learned", "visa"):  # explanations of adjustments that no longer exist
        rec.pop(key, None)


AI_NOT_RELEVANT = "AI_NOT_RELEVANT"
AI_VETO_MAX_FIT = 2
AI_LIFT_MIN_FIT = 7      # a near miss the AI rates this high is raised to the Possible cut-off
AI_LIFT_EVIDENCE = "AI second opinion: strong fit"


def held_back_by_visa(rec: dict, settings) -> bool:
    """Rejected only because the visa penalty pushed the score under the cut-off. Such a job stays in play (its text
    is kept, the AI may read it): one fact - the posting does offer sponsorship - brings it back."""
    penalty = int((rec.get("score_breakdown") or {}).get("visa") or 0)
    return (penalty < 0 and rec.get("tier") == TIER_REJECTED and set(rec.get("rejection_reasons") or []) <= {"LOW_SCORE"}
            and int(rec.get("score") or 0) - penalty >= settings.medium_threshold)


# Verdicts on relevance, which a reading of the posting can overrule. Never: a negative title, the wrong place, a demand
# for existing work rights, or the AI's own veto.
SOFT_REJECTIONS = {"LOW_SCORE", "NO_RELEVANT_TITLE", "INSUFFICIENT_GEOSPATIAL_SIGNALS", "LOW_TECHNICAL_RELEVANCE"}
SECOND_CHANCE_BODY_POINTS = 14  # skills + domain + tasks found in the text: real geospatial content under an odd title


def near_miss(rec: dict, settings) -> bool:
    """Worth one AI reading: rejected on relevance alone, and either a few points under the cut-off or carrying real
    geospatial content under a title the rules cannot place ("Network Planner" with QGIS and fibre routes)."""
    reasons = set(rec.get("rejection_reasons") or [])
    if rec.get("tier") != TIER_REJECTED or not reasons or reasons - SOFT_REJECTIONS:
        return False
    breakdown = rec.get("score_breakdown") or {}
    body = sum(int(breakdown.get(k) or 0) for k in ("tech", "domain", "responsibilities"))
    margin = int(getattr(settings, "ai_second_chance_margin", 8))
    return int(rec.get("score") or 0) >= settings.medium_threshold - margin or body >= SECOND_CHANCE_BODY_POINTS


def apply_ai_lift(rec: dict, settings) -> bool:
    """Raise a near miss the AI found clearly relevant to the Possible cut-off: once per scoring, like the other
    adjustments (the key in score_breakdown disappears when the job is rescored, and the lift is applied again)."""
    review = rec.get("ai") or {}
    breakdown = rec.setdefault("score_breakdown", {})
    if (not review.get("lift") or "ai" in breakdown or rec.get("tier") != TIER_REJECTED
            or set(rec.get("rejection_reasons") or []) - SOFT_REJECTIONS):
        return False
    gap = max(0, settings.medium_threshold - int(rec.get("score") or 0))
    breakdown["ai"] = gap
    rec["score"] = int(rec.get("score") or 0) + gap
    rec["tier"], rec["rejection_reasons"] = TIER_POSSIBLE, []
    why = [w for w in rec.get("why_matched") or [] if not w.startswith(AI_LIFT_EVIDENCE)]
    rec["why_matched"] = why + [f"{AI_LIFT_EVIDENCE} ({review.get('fit')}/10)"]
    return True


def apply_ai_veto(rec) -> bool:
    """Keep a Possible match rejected once the AI review found it clearly irrelevant (fit <= 2).

    Works on a JobRecord or a stored dict. Rescoring on later runs would otherwise resurrect the job.
    High matches are never vetoed: the deterministic evidence outranks the model there.
    """
    get = rec.get if isinstance(rec, dict) else lambda k, d=None: getattr(rec, k, d)
    review = get("ai") or {}
    if not review.get("veto") or get("tier") != TIER_POSSIBLE:
        return False
    if get("notified"):
        return False  # the user was told about it (it was a High match then): it must not vanish from their lists
    reasons = list(dict.fromkeys(list(get("rejection_reasons") or []) + [AI_NOT_RELEVANT]))
    if isinstance(rec, dict):
        rec["tier"], rec["rejection_reasons"] = TIER_REJECTED, reasons
    else:
        rec.tier, rec.rejection_reasons = TIER_REJECTED, reasons
    return True


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
            rec.text_seen = to_iso(now) if len(fused.description or "") >= 300 else None
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
            # Seen again without its text (the detail budget went to newer postings): the job is still open, which is all
            # this observation says. Scoring it from the title alone would drop a High match to the title-only floor.
            worse_description = len(fused.description or "") < 300 <= rec.description_length
            if len(fused.description or "") >= 300:
                rec.text_seen = to_iso(now)
            if (fused.priority >= rec.field_priority and not worse_description) or better_description:
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
    if deadline_passed(rec, now):
        return "DEADLINE_PASSED"
    return None


LISTED_DAYS = 5            # sources polled every run
LISTED_DAYS_ROTATION = 21  # boards checked on a slow rotation
LISTED_DAYS_PAGES = 35     # pages and sitemaps: a cached page is not fetched again for up to 30 days
PAGE_METHODS = {"jsonld", "rdfa", "html", "embedded_json"}  # read from a fetched page, not from a feed or an API


def listed_days(rec: dict) -> int:
    """How long after it was last seen a posting still counts as open (the Worker reads this as `ttl` in the index)."""
    sources = rec.get("sources") or []
    from_pages = bool(sources) and all((s.get("method") or "") in PAGE_METHODS or s.get("source_type") == "employer_page" for s in sources)
    return LISTED_DAYS_PAGES if from_pages else LISTED_DAYS_ROTATION if rec.get("from_rotation") else LISTED_DAYS


def is_listed(rec: dict, now) -> bool:
    """Was the posting still seen recently? Used for lists and summaries shown after the alert went out."""
    seen = parse_datetime(rec.get("last_seen"))
    return seen is None or now - seen <= timedelta(days=listed_days(rec))


def is_muted(rec: dict, terms) -> bool:
    """Whole words of "title company": muting "US" must not silence "Industry" (cloudflare/worker.js mutedBy is the same rule)."""
    haystack = fold(f"{rec.get('title') or ''} {rec.get('company') or ''}")
    for term in terms or []:
        wanted = fold(term)
        if wanted and re.search(r"(?<![a-z0-9])" + re.escape(wanted) + r"(?![a-z0-9])", haystack):
            return True
    return False


# ---------------------------------------------------------------------------- applications (stored in prefs)
APPLICATION_STATUSES = {"applied": "📨", "interview": "🎤", "offer": "🎉", "rejected": "❌", "withdrawn": "↩️",
                        "ghosted": "👻"}
FOLLOW_UP_DAYS = (7, 21)


def due_follow_ups(prefs: dict, reminded: dict, now) -> list[tuple[str, dict, int]]:
    """Applications still at 'applied' that reached a follow-up age and were not yet reminded at that stage.

    `reminded` maps canonical id -> highest stage (in days) already sent; the scraper owns it (state.maintenance).
    """
    due = []
    for cid, info in (prefs.get("applied") or {}).items():
        if (info.get("status") or "applied") != "applied":
            continue
        when = parse_datetime(info.get("at"))
        if when is None:
            continue
        age = (now - when).days
        stage = max((d for d in FOLLOW_UP_DAYS if age >= d), default=None)
        if stage is not None and int(reminded.get(cid) or 0) < stage:
            due.append((cid, info, stage))
    return due


def watch_names(settings) -> list[str]:
    return [name for name in (normalize_company(w.get("name")) for w in getattr(settings, "watch_list", None) or []) if name]


def is_watched(rec: dict, settings, names: list[str] | None = None) -> bool:
    """Is the job's employer on the user's watch list (whole-word match on normalised names)?"""
    company = f" {normalize_company(rec.get('company'))} "
    if not company.strip():
        return False
    return any(f" {name} " in company for name in (watch_names(settings) if names is None else names))


def select_alerts(state: dict, seen_ids: set, settings, now) -> tuple[list[dict], Counter]:
    counts = Counter()
    candidates = []
    accepted = {TIER_HIGH, TIER_POSSIBLE} if settings.notify_possible else {TIER_HIGH}
    hidden = set(getattr(settings, "hidden_ids", None) or [])
    muted = [t for t in (getattr(settings, "muted_terms", None) or []) if t and t.strip()]
    # Not only this run's jobs: a match held back by the cap, by /pause or by a failed send may sit on a source that is
    # read once a week. Anything accepted, never notified, still listed and found within the alert age is a candidate.
    young = now - timedelta(hours=settings.max_job_age_hours)
    waiting = {cid for cid, rec in state["jobs"].items()
               if cid not in seen_ids and not rec.get("notified") and rec.get("tier") in (TIER_HIGH, TIER_POSSIBLE)
               and is_listed(rec, now) and (parse_datetime(rec.get("first_seen")) or now) >= young}
    counts["carried_over"] = len(waiting)
    names = watch_names(settings)
    for rec in state["jobs"].values():  # after /unwatch the mark must go from every job, not only from this run's
        if rec.get("watched") and not is_watched(rec, settings, names):
            rec["watched"] = False
    for cid in list(seen_ids) + sorted(waiting):
        rec = state["jobs"].get(cid)
        if not rec:
            continue
        rec["watched"] = is_watched(rec, settings, names)
        if rec.get("tier") not in accepted and not (rec["watched"] and rec.get("tier") == TIER_POSSIBLE):
            continue
        if cid in hidden:
            counts["hidden"] += 1
            continue
        if is_muted(rec, muted):
            counts["muted"] += 1
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
    if getattr(settings, "alerts_paused", False):  # jobs stay un-notified and go out after /resume
        counts["paused"] = len(candidates)
        return [], counts
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
