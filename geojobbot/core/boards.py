"""Registry of ATS boards (configured + discovered) persisted in state["boards"].

Scheduling per ATS each run:
  1. configured boards (always)
  2. "hot" discovered boards: produced a relevant job within HOT_BOARD_DAYS, or discovered this
     run from a high-signal origin (search result, career page) - always
  3. rotation: never-checked boards first, then least-recently-checked, within a per-ATS budget
Boards returning 404 are marked INVALID and rechecked rarely; repeated errors back off.
"""
from __future__ import annotations

import threading
from collections import Counter
from datetime import timedelta

from ..scrapers.ats.detect import BoardRef
from ..utils.dates import parse_datetime, to_iso

HIGH_SIGNAL_ORIGINS = {"search", "career_page", "generic_page", "config"}
ORIGIN_RANK = {"config": 0, "career_page": 1, "prospect": 1, "search": 2, "generic_page": 3, "commoncrawl": 4}
PROSPECT_RECHECK_HOURS = 20  # boards of employers proven to sponsor (insights/prospects.py): daily, not on the slow rotation
INVALID_RECHECK_DAYS = 60
MAX_BOARDS_PER_ATS = 30000


class BoardRegistry:
    def __init__(self, state: dict, now, *, hot_days: int = 30):
        self.boards: dict = state.setdefault("boards", {})
        self._per_ats = Counter(key.split(":", 1)[0] for key in self.boards)  # kept current: counting on every call was O(n²)
        self.now = now
        self.hot_days = hot_days
        self._lock = threading.Lock()
        self.new_this_run: dict[str, int] = {}
        self.hot_this_run: set[str] = set()

    def register(self, ref: BoardRef, origin: str) -> bool:
        """Add a board if unknown. Returns True when newly added."""
        key = ref.key
        with self._lock:
            entry = self.boards.get(key)
            if entry is None:
                if origin == "commoncrawl" and self._per_ats[ref.ats] >= MAX_BOARDS_PER_ATS:
                    return False
                self._per_ats[ref.ats] += 1
                self.boards[key] = {
                    "ats": ref.ats, "slug": ref.slug, "origin": origin, "status": "UNKNOWN",
                    "first_discovered": to_iso(self.now), "last_checked": None, "last_job_count": None,
                    "last_relevant_at": None, "relevant_total": 0, "consecutive_failures": 0,
                    "variant": None, "last_error": None,
                }
                self.new_this_run[ref.ats] = self.new_this_run.get(ref.ats, 0) + 1
                if origin in HIGH_SIGNAL_ORIGINS:
                    self.hot_this_run.add(key)
                return True
            if ORIGIN_RANK.get(origin, 9) < ORIGIN_RANK.get(entry.get("origin"), 9):
                entry["origin"] = origin
            if origin in HIGH_SIGNAL_ORIGINS and entry.get("status") != "INVALID":
                self.hot_this_run.add(key)
            return False

    def entry(self, key: str) -> dict | None:
        return self.boards.get(key)

    def is_geo_board(self, key: str) -> bool:
        entry = self.boards.get(key) or {}
        return entry.get("origin") in {"config", "career_page", "prospect"} or bool(entry.get("relevant_total"))

    def _due(self, entry: dict) -> bool:
        last = parse_datetime(entry.get("last_checked"))
        if last is None:
            return True
        status = entry.get("status")
        if status == "INVALID":
            return self.now - last >= timedelta(days=INVALID_RECHECK_DAYS)
        failures = int(entry.get("consecutive_failures") or 0)
        if failures >= 3:
            return self.now - last >= timedelta(days=min(2 ** (failures - 2), 30))
        return True

    def select(self, ats: str, configured_slugs: list[str], rotation_budget: int) -> list[tuple[BoardRef, str]]:
        """Return [(board, reason)] where reason is config | hot | rotation."""
        selected: dict[str, tuple[BoardRef, str]] = {}
        for slug in configured_slugs:
            ref = BoardRef(ats, slug)
            self.register(ref, "config")
            selected[ref.key] = (ref, "config")
        hot_cutoff = self.now - timedelta(days=self.hot_days)
        rotation: list[tuple[int, str, str]] = []
        with self._lock:
            items = [(k, e) for k, e in self.boards.items() if e.get("ats") == ats and k not in selected]
        for key, entry in items:
            ref = BoardRef(ats, entry["slug"])
            last_rel = parse_datetime(entry.get("last_relevant_at"))
            if key in self.hot_this_run or (last_rel and last_rel >= hot_cutoff and entry.get("status") != "INVALID"):
                if self._due(entry) or key in self.hot_this_run:
                    selected[key] = (ref, "hot")
                continue
            if not self._due(entry):
                continue
            if entry.get("origin") == "prospect" and entry.get("status") != "INVALID":
                checked = parse_datetime(entry.get("last_checked"))
                if checked is None or self.now - checked >= timedelta(hours=PROSPECT_RECHECK_HOURS):
                    selected[key] = (ref, "hot")
                continue
            last = entry.get("last_checked") or ""
            rank = ORIGIN_RANK.get(entry.get("origin"), 9)
            rotation.append((0 if not last else 1, last, f"{rank:02d}{key}"))
        rotation.sort()
        budget = max(0, rotation_budget)
        for _, _, ranked_key in rotation[:budget]:
            key = ranked_key[2:]
            entry = self.boards[key]
            selected[key] = (BoardRef(ats, entry["slug"]), "rotation")
        return list(selected.values())

    def record(self, ref: BoardRef, status: str, *, job_count: int | None = None, error: str | None = None,
               variant: str | None = None, company: str | None = None) -> None:
        with self._lock:
            entry = self.boards.setdefault(ref.key, {"ats": ref.ats, "slug": ref.slug, "origin": "unknown",
                                                     "relevant_total": 0, "consecutive_failures": 0})
            entry["last_checked"] = to_iso(self.now)
            entry["status"] = status
            entry["last_error"] = error
            if job_count is not None:
                entry["last_job_count"] = job_count
            if variant:
                entry["variant"] = variant
            if company:
                entry["company"] = company
            if status in ("VALID", "EMPTY_UNVERIFIED", "INVALID"):
                entry["consecutive_failures"] = 0
            else:
                entry["consecutive_failures"] = int(entry.get("consecutive_failures") or 0) + 1

    def mark_relevant(self, board_key: str, count: int) -> None:
        with self._lock:
            entry = self.boards.get(board_key)
            if entry and count > 0:
                entry["relevant_total"] = int(entry.get("relevant_total") or 0) + count
                entry["last_relevant_at"] = to_iso(self.now)
