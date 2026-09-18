"""Full descriptions of accepted jobs, kept beside the state document (state/descriptions.json.gz).

The state stores only metadata to stay small. Keeping the text of the jobs that matter lets the AI review a
backlog, lets /pitch write from the real posting, and would allow rescoring when the profile changes.
Only High/Possible jobs are kept, truncated, and entries disappear with their job.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

SUFFIX = "state/descriptions.json.gz"
MAX_CHARS = 6000
MIN_CHARS = 300
MAX_ENTRIES = 2500


class DescriptionStore:
    def __init__(self, data: dict | None = None):
        self.data: dict[str, str] = data if isinstance(data, dict) else {}
        self.dirty = False

    @classmethod
    def load(cls, manager) -> "DescriptionStore":
        try:
            return cls(manager.read_json(SUFFIX))
        except Exception as exc:  # optional data: never stop a run or a command over it
            log.warning("descriptions unreadable: %s", type(exc).__name__)
            return cls()

    def save(self, manager) -> bool:
        if not self.dirty:
            return False
        manager.write_json(SUFFIX, self.data, compress=True)
        self.dirty = False
        return True

    def get(self, canonical_id: str) -> str:
        return self.data.get(canonical_id) or ""

    def put(self, canonical_id: str, text: str | None) -> None:
        text = (text or "").strip()[:MAX_CHARS]
        if len(text) >= MIN_CHARS and len(text) > len(self.data.get(canonical_id) or ""):
            self.data[canonical_id] = text
            self.dirty = True

    def prune(self, jobs: dict) -> int:
        """Drop texts whose job is gone or no longer accepted; cap the total, oldest-seen first."""
        keep = {cid for cid, rec in jobs.items() if rec.get("tier") in ("high", "possible")}
        removed = [cid for cid in self.data if cid not in keep]
        if len(self.data) - len(removed) > MAX_ENTRIES:
            ordered = sorted((cid for cid in self.data if cid in keep), key=lambda c: jobs[c].get("last_seen") or "")
            removed += ordered[: len(self.data) - len(removed) - MAX_ENTRIES]
        for cid in removed:
            del self.data[cid]
        self.dirty = self.dirty or bool(removed)
        return len(removed)
