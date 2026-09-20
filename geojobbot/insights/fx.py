"""Salaries in one currency, so a CAD, SAR or GBP offer can be compared at a glance.

Rates come once a day from the keyless fawazahmed0 currency API (150+ currencies, including SAR, AED, QAR and TND,
which the ECB-based services lack). The posted salary is parsed by the same parser the visa check uses, put on a
yearly basis and converted to euros. It is an orientation figure: gross, before tax, and blind to the cost of living.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from ..utils.dates import parse_datetime, to_iso
from ..utils.http import FetchError
from .visa import PER_YEAR, parse_salary

log = logging.getLogger(__name__)

RATES_URL = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/eur.json"
KEEP = ("usd", "gbp", "cad", "aud", "nzd", "chf", "dkk", "sek", "nok", "sar", "aed", "qar", "kwd", "omr", "bhd", "tnd", "mad", "egp",
        "inr", "sgd", "zar", "pln")


def refresh(ctx, state: dict) -> bool:
    """Fetch the rates at most once a day. Returns True when fresh rates are available."""
    store = state.setdefault("fx", {})
    checked = parse_datetime(store.get("checked_at"))
    if checked is not None and ctx.now - checked < timedelta(hours=20) and store.get("eur"):
        return True
    try:
        data = ctx.client.get(RATES_URL, respect_robots=False, detect_challenge=False).json()
        rates = {k: float(data["eur"][k]) for k in KEEP if data["eur"].get(k)}
    except (FetchError, ValueError, KeyError, TypeError) as exc:
        log.info("exchange rates not refreshed: %s", getattr(exc, "kind", type(exc).__name__))
        return bool(store.get("eur"))
    if len(rates) < 5:
        return bool(store.get("eur"))
    store.update(eur=rates, date=data.get("date"), checked_at=to_iso(ctx.now))
    return True


def yearly_eur(salary: str | None, country: str | None, rates: dict | None) -> tuple[int, int] | None:
    """(low, high) gross euros a year for a posted salary, or None when it cannot be read or is already in euros."""
    parsed = parse_salary(salary, country)
    if not parsed or not rates:
        return None
    currency = parsed["currency"].lower()
    if currency == "eur" and parsed["period"] == "year":
        return None
    rate = 1.0 if currency == "eur" else rates.get(currency)
    if not rate:
        return None
    factor = PER_YEAR[parsed["period"]] / rate
    return round(parsed["low"] * factor), round(parsed["high"] * factor)


def note(rec: dict) -> str | None:
    pair = rec.get("salary_eur")
    if not pair:
        return None
    low, high = pair
    shown = f"€{low / 1000:.0f}k" if abs(high - low) < 500 else f"€{low / 1000:.0f}–{high / 1000:.0f}k"
    return f"≈ {shown}/yr"


def annotate(rec: dict, rates: dict | None) -> bool:
    pair = yearly_eur(rec.get("salary"), rec.get("country"), rates)
    if pair is None:
        rec.pop("salary_eur", None)
        return False
    rec["salary_eur"] = list(pair)
    return True
