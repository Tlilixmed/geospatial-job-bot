"""Authoritative persistent state stored as one gzipped JSON document.

Layout (keys are relative to STATE_PREFIX):
  state/state.json.gz                     authoritative state (jobs, boards, sources, cursors)
  state/backups/<timestamp>-<run>.json.gz previous versions (last N kept)
  state/conflicts/<run>.json.gz           state that could not be saved because another writer won
  runs/<YYYY-MM-DD>/<run>.json.gz         run report + match diagnostics
  runs/latest.json                        latest run summary (small, uncompressed)
  raw/<YYYY-MM-DD>/<run>/<source>.jsonl.gz raw normalised snapshots (retention: RAW_RETENTION_DAYS)

Integrity strategy:
* A single PUT is atomic in R2, so the state object is always a complete document.
* The serialised document is round-trip verified before upload.
* Before the first overwrite in a run the previous version is copied to state/backups/.
* Optimistic concurrency: the ETag seen at load must still match before writing, otherwise the
  write is diverted to state/conflicts/ and the run fails loudly instead of clobbering data.
* A missing state object with existing backups is treated as suspicious and the newest valid
  backup is restored rather than silently starting from empty.
"""
from __future__ import annotations

import gzip
import json
import logging
import re
from datetime import timedelta

from ..utils.dates import parse_datetime, utcnow
from .base import ObjectStore, StorageError

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
REQUIRED_KEYS = ("schema_version", "jobs", "boards", "sources", "page_cache", "cursors")


class StateCorruptError(Exception):
    pass


class ConcurrentModificationError(Exception):
    pass


def empty_state() -> dict:
    now = utcnow().isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": now,
        "updated_at": now,
        "last_run_id": None,
        "jobs": {},
        "boards": {},
        "sources": {},
        "page_cache": {},
        "cursors": {},
        "maintenance": {},
        "stats": {"runs": 0},
    }


def encode_state(state: dict) -> bytes:
    raw = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    return gzip.compress(raw, compresslevel=6, mtime=0)


def decode_state(data: bytes) -> dict:
    try:
        state = json.loads(gzip.decompress(data).decode("utf-8"))
    except (OSError, EOFError, ValueError, UnicodeDecodeError) as exc:
        raise StateCorruptError(f"cannot decode state: {type(exc).__name__}") from exc
    validate_state(state)
    return state


def validate_state(state) -> None:
    if not isinstance(state, dict):
        raise StateCorruptError("state is not an object")
    missing = [k for k in REQUIRED_KEYS if k not in state]
    if missing:
        raise StateCorruptError(f"state missing keys: {missing}")
    if state.get("schema_version") != SCHEMA_VERSION:
        raise StateCorruptError(f"unsupported schema_version {state.get('schema_version')}")
    for key in ("jobs", "boards", "sources", "page_cache", "cursors"):
        if not isinstance(state[key], dict):
            raise StateCorruptError(f"state.{key} is not an object")


class StateManager:
    def __init__(self, store: ObjectStore, prefix: str = "", *, backups_keep: int = 20, allow_reset: bool = False):
        self.store = store
        self.prefix = (prefix.strip("/") + "/") if prefix.strip("/") else ""
        self.backups_keep = backups_keep
        self.allow_reset = allow_reset
        self.etag: str | None = None
        self.loaded = False
        self.first_run = False
        self.restored_from_backup: str | None = None
        self._backed_up_this_run = False

    def key(self, suffix: str) -> str:
        return f"{self.prefix}{suffix}"

    @property
    def state_key(self) -> str:
        return self.key("state/state.json.gz")

    # ------------------------------------------------------------------ load
    def _backup_keys(self) -> list[str]:
        return sorted(self.store.list_keys(self.key("state/backups/")), reverse=True)

    def load(self) -> dict:
        """Load state. Raises StorageError if storage is unreachable (never silently starts empty)."""
        data = self.store.get_bytes(self.state_key)
        if data is None:
            # Diagnose before deciding: a HEAD that succeeds where GET reported "missing" is a storage/client
            # problem, not a first run, and must never be answered by starting empty.
            head = self.store.head(self.state_key)
            siblings = self.store.list_keys(self.key("state/"))
            log.warning("GET %s returned nothing; HEAD=%s; %d objects under %s: %s", self.state_key, head,
                        len(siblings), self.key("state/"), siblings[:10])
            if head is not None:
                raise StateCorruptError(
                    f"GET {self.state_key} reported the object missing but HEAD finds it ({head}); "
                    f"store={self.store.name}. This is a storage or client fault, not a first run"
                )
            backups = self._backup_keys()
            if backups:
                log.warning("state object missing but %d backups exist; restoring newest valid backup", len(backups))
                state = self._load_from_backups(backups)
                self.etag = None
                self.loaded = True
                return state
            previous_runs = self.store.list_keys(self.key("runs/"))
            if previous_runs and not self.allow_reset:
                # Earlier runs wrote reports here, so starting empty would re-alert every job they saw.
                raise StateCorruptError(
                    f"{self.state_key} is missing although {len(previous_runs)} objects exist under "
                    f"{self.key('runs/')} (store={self.store.name}, objects under {self.key('state/')}: "
                    f"{siblings[:10]}); refusing to start fresh. Check the bucket, restore a backup, or set "
                    f"ALLOW_STATE_RESET=true to start over deliberately"
                )
            log.info("no existing state found at %s (store=%s): starting fresh (first run)", self.state_key,
                     self.store.name)
            self.first_run = True
            self.loaded = True
            self.etag = None
            return empty_state()
        head = self.store.head(self.state_key)
        try:
            state = decode_state(data)
        except StateCorruptError as exc:
            log.error("state is corrupt (%s); trying backups", exc)
            state = self._load_from_backups(self._backup_keys())
        self.etag = head["etag"] if head else None
        self.loaded = True
        return state

    def _load_from_backups(self, backups: list[str]) -> dict:
        for key in backups:
            data = self.store.get_bytes(key)
            if data is None:
                continue
            try:
                state = decode_state(data)
            except StateCorruptError:
                log.error("backup %s is corrupt", key)
                continue
            self.restored_from_backup = key
            return state
        if self.allow_reset:
            log.error("no valid state or backup; ALLOW_STATE_RESET=true so starting empty")
            self.first_run = True
            return empty_state()
        raise StateCorruptError("state and all backups are unreadable; set ALLOW_STATE_RESET=true to start over")

    def peek(self) -> dict:
        """Read-only view of the state for commands: no ETag tracking, no backups, empty when absent."""
        data = self.store.get_bytes(self.state_key)
        return decode_state(data) if data else empty_state()

    # ------------------------------------------------------------------ save
    def save(self, state: dict, run_id: str) -> str | None:
        validate_state(state)
        state["updated_at"] = utcnow().isoformat()
        payload = encode_state(state)
        decode_state(payload)  # round-trip verification before touching storage

        current = self.store.head(self.state_key)
        current_etag = current["etag"] if current else None
        if current_etag != self.etag and not (current_etag is None and self.etag is None):
            conflict_key = self.key(f"state/conflicts/{run_id}.json.gz")
            self.store.put_bytes(conflict_key, payload, "application/gzip")
            raise ConcurrentModificationError(
                f"state changed since load (expected etag {self.etag}, found {current_etag}); "
                f"this run's state was written to {conflict_key}"
            )
        if current is not None and not self._backed_up_this_run:
            stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
            self.store.copy(self.state_key, self.key(f"state/backups/{stamp}-{run_id}.json.gz"))
            self._backed_up_this_run = True
            self._prune_backups()
        etag = self.store.put_bytes(self.state_key, payload, "application/gzip")
        head = self.store.head(self.state_key)  # read back: the object must be there for the next runner
        if head is None:
            raise StateCorruptError(f"{self.state_key} could not be read back after writing it (store={self.store.name})")
        if head.get("size") not in (None, 0, len(payload)):
            raise StateCorruptError(f"{self.state_key} read back with {head['size']} bytes, wrote {len(payload)}")
        self.etag = etag or head["etag"]
        log.info("state saved: %s (%d bytes, %d jobs)", self.state_key, len(payload), len(state.get("jobs", {})))
        return self.etag

    def _prune_backups(self) -> None:
        try:
            backups = self._backup_keys()
            stale = backups[self.backups_keep:]
            if stale:
                self.store.delete_keys(stale)
        except StorageError as exc:
            log.warning("backup pruning failed: %s", exc)

    # ------------------------------------------------------------------ artifacts
    def write_json(self, suffix: str, obj, *, compress: bool) -> str:
        key = self.key(suffix)
        body = json.dumps(obj, ensure_ascii=False, indent=None if compress else 2, default=str).encode("utf-8")
        if compress:
            self.store.put_bytes(key, gzip.compress(body, mtime=0), "application/gzip")
        else:
            self.store.put_bytes(key, body, "application/json")
        return key

    def write_jsonl_gz(self, suffix: str, rows: list[dict]) -> str:
        key = self.key(suffix)
        body = "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows).encode("utf-8")
        self.store.put_bytes(key, gzip.compress(body, mtime=0), "application/gzip")
        return key

    def read_json(self, suffix: str):
        data = self.store.get_bytes(self.key(suffix))
        if data is None:
            return None
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        return json.loads(data.decode("utf-8"))

    def cleanup_retention(self, raw_days: int, run_days: int, now=None) -> dict:
        """Delete dated objects older than the retention windows."""
        now = now or utcnow()
        result = {}
        for folder, days in (("raw/", raw_days), ("runs/", run_days)):
            cutoff = (now - timedelta(days=days)).date()
            keys = self.store.list_keys(self.key(folder))
            old = []
            for key in keys:
                match = re.search(r"/(\d{4}-\d{2}-\d{2})/", key[len(self.prefix):])
                if match:
                    parsed = parse_datetime(match.group(1))
                    if parsed and parsed.date() < cutoff:
                        old.append(key)
            result[folder] = self.store.delete_keys(old) if old else 0
        return result
