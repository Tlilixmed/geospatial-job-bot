"""Date parsing helpers. All datetimes are timezone-aware UTC."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_datetime(value) -> datetime | None:
    """Parse ISO strings, RFC 2822 strings, plain dates and epoch seconds/milliseconds."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e12:  # milliseconds
            seconds /= 1000.0
        if seconds <= 0:
            return None
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{10}(\d{3})?", text):
        return parse_datetime(int(text))
    candidate = re.sub(r"\s+UTC$", "+00:00", text)
    candidate = candidate.replace("Z", "+00:00") if candidate.endswith("Z") else candidate
    candidate = re.sub(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})", r"\1T\2", candidate)
    candidate = re.sub(r"(\.\d{6})\d+", r"\1", candidate)
    try:
        parsed = datetime.fromisoformat(candidate)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def age_hours(value: datetime | None, now: datetime | None = None) -> float | None:
    if value is None:
        return None
    now = now or utcnow()
    return (now - value).total_seconds() / 3600.0


def days_ago(days: float, now: datetime | None = None) -> datetime:
    return (now or utcnow()) - timedelta(days=days)
