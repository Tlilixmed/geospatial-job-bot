"""Plugin architecture for discovery/extraction backends.

Every backend is independent: it receives a RunContext, returns a BackendOutput and never
touches another backend's data. Exceptions are caught by the pipeline and recorded.
"""
from __future__ import annotations

import heapq
import logging
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..matching.matcher import title_prefilter
from ..models import BackendOutput
from ..utils.urls import canonicalize_url

log = logging.getLogger(__name__)


@dataclass(order=True)
class QueuedPage:
    sort_key: tuple
    url: str = field(compare=False)
    origin: str = field(compare=False)
    source_type: str = field(compare=False, default="employer_page")
    geo_context: bool = field(compare=False, default=False)
    lastmod: str | None = field(compare=False, default=None)
    company_hint: str | None = field(compare=False, default=None)


class PageQueue:
    """Thread-safe priority queue of public job-page URLs awaiting generic extraction."""

    def __init__(self):
        self._heap: list[QueuedPage] = []
        self._seen: set[str] = set()
        self._lock = threading.Lock()
        self._counter = 0

    def add(self, url: str, *, origin: str, priority: int = 50, source_type: str = "employer_page",
            geo_context: bool = False, lastmod: str | None = None, company_hint: str | None = None) -> bool:
        canon = canonicalize_url(url)
        if not canon:
            return False
        with self._lock:
            if canon in self._seen:
                return False
            self._seen.add(canon)
            self._counter += 1
            heapq.heappush(self._heap, QueuedPage((-priority, self._counter), url, origin, source_type,
                                                  geo_context, lastmod, company_hint))
            return True

    def pop_all(self) -> list[QueuedPage]:
        with self._lock:
            items = [heapq.heappop(self._heap) for _ in range(len(self._heap))]
            return items

    def __len__(self) -> int:
        with self._lock:
            return len(self._heap)


TEXT_FRESH_DAYS = 7  # after that the text is read again: postings get edited (a deadline moves, "no sponsorship" appears)


class RunContext:
    def __init__(self, settings, client, state: dict, *, run_id: str, now, registry=None, match_config=None,
                 deadline: float | None = None):
        self.settings = settings
        self.client = client
        self.state = state
        self.run_id = run_id
        self.now = now
        self.registry = registry
        self.match_config = match_config
        self.deadline = deadline if deadline is not None else time.monotonic() + settings.run_time_budget_s
        self.pages = PageQueue()
        self.lock = threading.RLock()
        self.parser_errors: Counter = Counter()
        self.snapshots: dict[str, list[dict]] = defaultdict(list)
        self._text_known: set[str] | None = None

    def knows_text(self, job_id: str | None) -> bool:
        """Was this posting's full text read within TEXT_FRESH_DAYS? Then a backend need not spend a detail request on it:
        it reports the job by its title (which keeps it listed, and never overwrites the stored text) and gives the
        request to a posting nobody has read yet."""
        if not job_id:
            return False
        with self.lock:
            if self._text_known is None:
                from ..utils.dates import parse_datetime

                known: set[str] = set()
                for cid, rec in (self.state.get("jobs") or {}).items():
                    seen = parse_datetime(rec.get("text_seen"))
                    if seen is not None and (self.now - seen).days < TEXT_FRESH_DAYS and int(rec.get("description_length") or 0) >= 300:
                        known.add(cid.lower())
                        known.update(str(a).lower() for a in rec.get("aliases") or [])
                self._text_known = known
            return job_id.lower() in self._text_known

    def time_left(self) -> float:
        return self.deadline - time.monotonic()

    def out_of_time(self, reserve_s: float = 60.0) -> bool:
        return self.time_left() < reserve_s

    def prefilter(self, title: str, geo_context: bool) -> bool:
        extra = tuple(self.settings.negative_titles()) if self.settings else ()
        return title_prefilter(title, geo_context, extra)

    def cursor(self, name: str) -> dict:
        with self.lock:
            return self.state.setdefault("cursors", {}).setdefault(name, {})

    def record_parser_error(self, source: str) -> None:
        with self.lock:
            self.parser_errors[source] += 1

    def snapshot(self, source: str, rows: list[dict]) -> None:
        if not self.settings.store_raw_snapshots:
            return
        with self.lock:
            self.snapshots[source].extend(rows)


class Backend:
    name: str = "backend"
    phase: str = "extraction"  # "discovery" runs before "extraction"
    source_type: str = "feed"
    min_interval_hours: float = 0.0
    quota_bound: bool = False  # a paid-by-the-call API: the interval also counts from a failed attempt, not only from a success

    def enabled(self, ctx: RunContext) -> tuple[bool, str | None]:
        if self.name in ctx.settings.disabled_backends:
            return False, "disabled via DISABLED_BACKENDS"
        return True, None

    def run(self, ctx: RunContext) -> BackendOutput:  # pragma: no cover - interface
        raise NotImplementedError
