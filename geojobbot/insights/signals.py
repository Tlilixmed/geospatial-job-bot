"""Market signals from public procurement: follow the money, not only the vacancies.

World Bank procurement notices are open (search.worldbank.org/api/v2/procnotices). Two kinds matter:

* contract awards for geospatial work: a firm that has just won a cadastre, LiDAR survey or GIS contract will
  need people within weeks, often before anything is advertised;
* individual-consultant selections: short GIS / mapping consultancies the candidate can apply to directly,
  many in French-speaking Africa and the MENA region.

Checked once a day, relevance-filtered with the same geospatial vocabulary as the job matcher, deduplicated by
notice id, and sent as one short Telegram message only when something new turns up.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import timedelta

from ..utils.dates import parse_datetime, to_iso
from ..utils.http import FetchError
from ..utils.text import fold

log = logging.getLogger(__name__)

API = "https://search.worldbank.org/api/v2/procnotices"
DETAIL = "https://projects.worldbank.org/en/projects-operations/procurement-detail/{id}"
TERMS = ["GIS", "geospatial", "cadastre", "LiDAR", "remote sensing", "cartographie", "SIG", "topographie",
         "land administration", "photogrammetry"]
TERMS_PER_CHECK = 5
CHECK_EVERY_HOURS = 20
MAX_AGE_DAYS = 21
KEEP_ITEMS = 80

GEO_RE = re.compile(
    r"\b(gis|sig|geo-?spatial|geomati\w*|cadastr\w*|lidar|photogramm\w*|orthophoto\w*|remote sensing|teledetection|"
    r"cartograph\w*|topograph\w*|mapping|geodata|geodetic|geodes\w*|land (?:administration|information|registration|records)|"
    r"spatial data|satellite imagery|aerial (?:survey|photograph\w*|imagery)|drone survey|bathymetr\w*|hydrograph\w*|"
    r"foncier\w*|systeme d.information geographique|prises? de vues? aerienne\w*|base de donnees geographique)\b")
NOISE_RE = re.compile(
    r"\b(interior design|furniture|vehicles?|insurance|assurance|office supplies|stationery|audit\w*|printing|catering|"
    r"cleaning|security services|fuel|air ?conditioning|generator|rehabilitation of|construction of|travaux de construction)\b")
AWARD_RE = re.compile(r"Awarded (?:Firm|Bidder|Consultant)\(?s?\)?:\s*(.+?)\s*(?:\(\d+\))?\s*Country:\s*([A-Za-z ,'.\-]+?)\s+"
                      r"(?:Final|Signed|Evaluated|Bid|Price)", re.S)
PRICE_RE = re.compile(r"Signed Contract Price\s+([A-Z]{3})\s*([\d,.]+)")


def _plain(markup: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(markup or ""))).strip()


def classify(item: dict) -> dict | None:
    """Turn one API notice into a signal, or None when it is not geospatial work worth a look."""
    title = (item.get("bid_description") or "").strip()
    folded = fold(title)
    if not GEO_RE.search(folded) or NOISE_RE.search(folded):
        return None
    notice_type = item.get("notice_type") or ""
    method = item.get("procurement_method_name") or ""
    if notice_type == "Contract Award":
        kind = "award"
    elif "individual consultant" in method.lower():
        kind = "consultancy"
    elif notice_type in ("Request for Expression of Interest", "Invitation for Bids", "General Procurement Notice",
                         "Invitation for Prequalification", "Request for Bids", "Request for Proposals"):
        kind = "tender"
    else:
        return None
    signal = {"id": item.get("id"), "kind": kind, "title": title[:160], "country": item.get("project_ctry_name"),
              "project": (item.get("project_name") or "")[:90], "date": item.get("submission_date") or item.get("noticedate"),
              "url": DETAIL.format(id=item.get("id")), "method": method}
    if kind == "award":
        text = _plain(item.get("notice_text"))
        match = AWARD_RE.search(text)
        if match:
            signal["winner"] = match.group(1).strip()[:80]
            signal["winner_country"] = match.group(2).strip()[:40]
        price = PRICE_RE.search(text)
        if price:
            signal["value"] = f"{price.group(1)} {price.group(2)}"
    return signal


def check(ctx, state: dict) -> list[dict]:
    """Fetch new relevant notices (at most once per CHECK_EVERY_HOURS). Returns the new signals, newest first."""
    store = state.setdefault("signals", {})
    last = parse_datetime(store.get("checked_at"))
    if last and ctx.now - last < timedelta(hours=CHECK_EVERY_HOURS):
        return []
    seen = set(store.get("seen") or [])
    start = int(store.get("term_index") or 0) % len(TERMS)
    fresh: dict[str, dict] = {}
    ok = 0
    for offset in range(TERMS_PER_CHECK):
        term = TERMS[(start + offset) % len(TERMS)]
        if ctx.out_of_time(180):
            break
        try:
            data = ctx.client.get_json(API, params={"format": "json", "qterm": term, "rows": 40,
                                                    "srt": "submission_date", "order": "desc"})
        except FetchError as exc:
            log.info("procurement signals: %s for %r", exc.kind, term)
            continue
        ok += 1
        notices = data.get("procnotices") if isinstance(data, dict) else None
        notices = list(notices.values()) if isinstance(notices, dict) else (notices or [])
        for item in notices:
            if not isinstance(item, dict) or not item.get("id") or item["id"] in seen or item["id"] in fresh:
                continue
            when = parse_datetime(item.get("submission_date"))
            if when is None or ctx.now - when > timedelta(days=MAX_AGE_DAYS):
                continue
            signal = classify(item)
            if signal:
                fresh[item["id"]] = signal
    if not ok:
        return []
    store["checked_at"] = to_iso(ctx.now)
    store["term_index"] = (start + TERMS_PER_CHECK) % len(TERMS)
    new = sorted(fresh.values(), key=lambda s: s.get("date") or "", reverse=True)
    store["seen"] = (list(seen) + [s["id"] for s in new])[-3000:]
    store["items"] = (new + list(store.get("items") or []))[:KEEP_ITEMS]
    return new


def format_signals(signals: list[dict], *, heading: str = "Market signals", limit: int = 9) -> str | None:
    if not signals:
        return None
    order = {"consultancy": 0, "award": 1, "tender": 2}
    rows = sorted(signals, key=lambda s: (order.get(s["kind"], 9), -(parse_datetime(s.get("date")).timestamp()
                                                                   if parse_datetime(s.get("date")) else 0)))[:limit]
    labels = {"consultancy": "🧑‍💼 <b>Individual consultancies you could apply to</b>",
              "award": "🏆 <b>Firms that just won geospatial contracts</b> (likely to need people)",
              "tender": "📄 <b>Geospatial projects being tendered</b>"}
    lines = [f"🛰 <b>{heading}</b>", "<i>World Bank-financed procurement, geospatial work only</i>"]
    current = None
    for s in rows:
        if s["kind"] != current:
            current = s["kind"]
            lines += ["", labels[current]]
        esc = html.escape
        line = f'• <a href="{esc(s["url"], quote=True)}">{esc(s["title"])}</a> — {esc(s.get("country") or "?")}'
        if s.get("winner"):
            line += f"\n   won by <b>{esc(s['winner'])}</b>" + (f" ({esc(s['winner_country'])})" if s.get("winner_country") else "")
            if s.get("value"):
                line += f" · {esc(s['value'])}"
        if s.get("date"):
            line += f"\n   {str(s['date'])[:10]}" + (f" · {esc(s['project'])}" if s.get("project") else "")
        lines.append(line)
    text = "\n".join(lines)
    return text if len(text) <= 4096 else text[:4095] + "…"
