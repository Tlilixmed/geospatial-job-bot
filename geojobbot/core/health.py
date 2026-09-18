"""Health alerts: tell the owner in Telegram when a source keeps failing, recovers, or a configured board is wrong.

Each condition is reported once: a source after FAIL_STREAK consecutive failed runs (and once more when it
recovers), an invalid configured board the first time it is seen. Flags live in the state document.
"""
from __future__ import annotations

from ..utils.text import truncate

FAIL_STREAK = 3


def health_messages(state: dict, report: dict) -> list[str]:
    """Return the lines to send after this run and update the 'already told' flags in state."""
    sources = state.setdefault("sources", {})
    lines: list[str] = []
    for result in report.get("sources") or []:
        record = sources.get(result["name"])
        if not isinstance(record, dict):
            continue
        status = result.get("status")
        streak = int(record.get("consecutive_failures") or 0)
        if status == "FAILED" and streak >= FAIL_STREAK and not record.get("alerted_down"):
            record["alerted_down"] = True
            lines.append(f"🔴 <b>{result['name']}</b> has failed {streak} runs in a row: "
                         f"{truncate(result.get('error') or 'no detail', 120)}")
        elif status in ("SUCCESS", "PARTIAL") and record.get("alerted_down"):
            record["alerted_down"] = False
            lines.append(f"🟢 <b>{result['name']}</b> is working again ({result.get('jobs', 0)} jobs this run).")

    told = state.setdefault("maintenance", {}).setdefault("invalid_notified", [])
    for ats, slugs in (report.get("invalid_configured") or {}).items():
        for slug in slugs or []:
            key = f"{ats}:{slug}"
            if key not in told:
                told.append(key)
                lines.append(f"🟠 Configured board <code>{key}</code> no longer exists; fix or remove it in sources.toml.")
    del told[:-200]
    return lines


def format_health(lines: list[str]) -> str | None:
    if not lines:
        return None
    return "🩺 <b>Bot health</b>\n" + "\n".join(lines[:15])
