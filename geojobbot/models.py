"""Core data models."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime

from .utils.dates import to_iso

# Authority of a source for "authoritative" fields during fusion.
SOURCE_PRIORITY = {
    "ats": 100,
    "employer_page": 80,
    "government": 75,
    "feed": 55,
    "aggregator": 40,
    "search": 20,
}
EXTRACTION_BONUS = {"api": 5, "jsonld": 3, "rdfa": 3, "embedded_json": 0, "html": -15}

TIER_HIGH = "high"
TIER_POSSIBLE = "possible"
TIER_REJECTED = "rejected"


@dataclass
class RawJob:
    """A single job observation from a single source, before fusion."""

    source_type: str
    source_name: str
    source_url: str
    title: str
    company: str | None = None
    url: str | None = None
    apply_url: str | None = None
    description: str = ""
    location_raw: str = ""
    remote_flag: bool | None = None
    workplace_type: str | None = None
    employment_type: str | None = None
    salary: str | None = None
    posted_at: datetime | None = None
    posted_at_reliable: bool = False
    native_id: str | None = None
    source_job_id: str | None = None
    board_key: str | None = None
    extraction_method: str = "api"
    from_rotation: bool = False  # discovered board checked on a rotating schedule
    geo_context: bool = False  # source is known to be geospatial (configured board, keyword search)

    @property
    def priority(self) -> int:
        return SOURCE_PRIORITY.get(self.source_type, 10) + EXTRACTION_BONUS.get(self.extraction_method, 0)

    def snapshot(self, description_limit: int = 20000) -> dict:
        data = asdict(self)
        data["posted_at"] = to_iso(self.posted_at)
        data["description"] = (self.description or "")[:description_limit]
        return data


@dataclass
class JobRecord:
    """Canonical persisted job (no full description stored)."""

    canonical_id: str
    title: str
    company: str | None = None
    url: str | None = None
    apply_url: str | None = None
    location_raw: str = ""
    city: str | None = None
    region: str | None = None
    country: str | None = None
    remote: bool | None = None
    remote_scope: str | None = None
    work_mode: str | None = None
    employment_type: str | None = None
    salary: str | None = None
    posted_at: str | None = None
    posted_at_reliable: bool = False
    first_seen: str | None = None
    last_seen: str | None = None
    sources: list = field(default_factory=list)
    field_priority: int = 0
    extraction_method: str | None = None
    description_length: int = 0
    description_hash: str | None = None
    content_fingerprint: str | None = None
    fuzzy_key: str | None = None
    aliases: list = field(default_factory=list)
    board_keys: list = field(default_factory=list)
    from_rotation: bool = False
    score: int = 0
    tier: str = TIER_REJECTED
    score_breakdown: dict = field(default_factory=dict)
    matched_skills: list = field(default_factory=list)
    matched_domains: list = field(default_factory=list)
    why_matched: list = field(default_factory=list)
    rejection_reasons: list = field(default_factory=list)
    alert_block_reason: str | None = None
    notified: bool = False
    notified_at: str | None = None
    notify_attempts: int = 0
    last_notify_error: str | None = None
    pending_update_alert: bool = False
    changes: list = field(default_factory=list)
    sponsor: list = field(default_factory=list)  # official visa-sponsor registers the employer appears on
    ai: dict = field(default_factory=dict)  # Workers AI second opinion: fit, summary, concerns, requirements, veto
    deadline: str | None = None  # application deadline read from the description (ISO date)
    deadline_reminded: bool = False
    rules: int = 0  # SCORER_VERSION the score was computed with (core/jobs.py)
    text_seen: str | None = None  # when the full posting text was last read (backends skip detail requests while it is recent)
    # annotations other modules put on the stored dict (learned, visa, reposts, watched…): carried through untouched,
    # so re-observing a job never silently drops what a later pipeline step wrote on it
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        extra = data.pop("extra") or {}
        return {**extra, **data}

    @classmethod
    def from_dict(cls, data: dict) -> "JobRecord":
        known = {f.name for f in fields(cls)} - {"extra"}
        record = cls(**{k: v for k, v in data.items() if k in known})
        record.extra = {k: v for k, v in data.items() if k not in known and k != "extra"}
        return record


@dataclass
class SourceResult:
    name: str
    phase: str
    status: str = "SKIPPED"  # SUCCESS | PARTIAL | FAILED | SKIPPED | DISABLED
    jobs: int = 0
    kept: int = 0
    prefiltered_out: int = 0
    error: str | None = None
    http_status: int | None = None
    duration_s: float = 0.0
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BackendOutput:
    jobs: list = field(default_factory=list)
    status: str = "SUCCESS"
    error: str | None = None
    http_status: int | None = None
    prefiltered_out: int = 0
    details: dict = field(default_factory=dict)
