"""Runtime preferences changed from Telegram.

Stored in their own object (state/prefs.json) so the command poller and the scraper never compete for
the state document: the scraper only reads preferences, the command processor only writes them.
"""
from __future__ import annotations

from ..utils.dates import to_iso, utcnow

PREFS_SUFFIX = "state/prefs.json"


def default_prefs() -> dict:
    return {
        "paused": False,
        "muted": [],                 # substrings matched against "title company"
        "hidden": [],                # canonical ids the user dismissed
        "applied": {},               # canonical id -> {"title", "company", "at"}
        "high_threshold": None,      # None = use the environment/default value
        "medium_threshold": None,
        "preferred_locations": None,
        "exclude_internships": None,
        "notify_possible": None,
        "hidden_info": {},           # canonical id -> snapshot of a hidden job (what learning needs once the job is pruned)
        "learning": None,            # False switches learning off
        "learning_since": None,      # actions before this moment are ignored (/learning reset)
        "my_skills": None,           # None = MY_SKILLS / the built-in list from the CV
        "watch": [],                 # employers to follow closely: [{"name", "url", "at"}]
    }


def load_prefs(manager) -> dict:
    prefs = default_prefs()
    stored = manager.read_json(PREFS_SUFFIX)
    if isinstance(stored, dict):
        prefs.update({k: v for k, v in stored.items() if k in prefs})
    return prefs


def save_prefs(manager, prefs: dict) -> None:
    manager.write_json(PREFS_SUFFIX, {**prefs, "updated_at": to_iso(utcnow())}, compress=False)


def apply_prefs(settings, prefs: dict) -> None:
    """Overlay stored preferences on the settings of this process."""
    if prefs.get("high_threshold") is not None:
        settings.high_threshold = int(prefs["high_threshold"])
    if prefs.get("medium_threshold") is not None:
        settings.medium_threshold = int(prefs["medium_threshold"])
    settings.medium_threshold = min(settings.medium_threshold, settings.high_threshold)
    if prefs.get("preferred_locations") is not None:
        settings.preferred_locations = list(prefs["preferred_locations"])
    if prefs.get("exclude_internships") is not None:
        settings.exclude_internships = bool(prefs["exclude_internships"])
    if prefs.get("notify_possible") is not None:
        settings.notify_possible = bool(prefs["notify_possible"])
    settings.muted_terms = list(prefs.get("muted") or [])
    settings.hidden_ids = list(prefs.get("hidden") or []) + list((prefs.get("applied") or {}).keys())
    settings.alerts_paused = bool(prefs.get("paused"))
    settings.watch_list = [w for w in prefs.get("watch") or [] if isinstance(w, dict) and w.get("name")]
