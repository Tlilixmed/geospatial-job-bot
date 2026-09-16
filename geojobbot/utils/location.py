"""Location and work-mode parsing.

Rules:
* bare "Remote" -> remote=True, remote_scope=None (worldwide is never inferred)
* explicit "Anywhere"/"Worldwide"/"Global" -> remote_scope="Worldwide"
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .text import fold

WORLDWIDE = {"worldwide", "global", "anywhere", "anywhere in the world", "international", "world", "globally"}
REGIONS = {
    "emea": "EMEA", "europe": "Europe", "eu": "Europe", "european union": "Europe", "apac": "APAC",
    "asia pacific": "APAC", "asia-pacific": "APAC", "latam": "LATAM", "latin america": "LATAM",
    "americas": "Americas", "north america": "North America", "south america": "South America",
    "africa": "Africa", "middle east": "Middle East", "mena": "MENA", "asia": "Asia", "oceania": "Oceania",
    "nordics": "Nordics", "dach": "DACH",
}
COUNTRIES = {
    "usa": "United States", "us": "United States", "u.s.": "United States", "u.s.a.": "United States",
    "united states": "United States", "united states of america": "United States",
    "canada": "Canada", "uk": "United Kingdom", "u.k.": "United Kingdom", "united kingdom": "United Kingdom",
    "great britain": "United Kingdom", "england": "United Kingdom", "scotland": "United Kingdom",
    "wales": "United Kingdom", "northern ireland": "United Kingdom",
    "ireland": "Ireland", "australia": "Australia", "new zealand": "New Zealand", "germany": "Germany",
    "deutschland": "Germany", "france": "France", "netherlands": "Netherlands", "the netherlands": "Netherlands",
    "belgium": "Belgium", "luxembourg": "Luxembourg", "spain": "Spain", "portugal": "Portugal", "italy": "Italy",
    "switzerland": "Switzerland", "austria": "Austria", "sweden": "Sweden", "norway": "Norway",
    "denmark": "Denmark", "finland": "Finland", "iceland": "Iceland", "poland": "Poland", "czechia": "Czechia",
    "czech republic": "Czechia", "romania": "Romania", "greece": "Greece", "hungary": "Hungary",
    "estonia": "Estonia", "latvia": "Latvia", "lithuania": "Lithuania", "croatia": "Croatia", "serbia": "Serbia",
    "bulgaria": "Bulgaria", "slovakia": "Slovakia", "slovenia": "Slovenia", "ukraine": "Ukraine", "turkey": "Turkey",
    "tunisia": "Tunisia", "morocco": "Morocco", "algeria": "Algeria", "egypt": "Egypt", "libya": "Libya",
    "south africa": "South Africa", "nigeria": "Nigeria", "kenya": "Kenya", "ghana": "Ghana", "rwanda": "Rwanda",
    "ethiopia": "Ethiopia", "senegal": "Senegal", "tanzania": "Tanzania", "uganda": "Uganda", "zambia": "Zambia",
    "namibia": "Namibia", "botswana": "Botswana", "uae": "United Arab Emirates",
    "united arab emirates": "United Arab Emirates", "saudi arabia": "Saudi Arabia", "qatar": "Qatar",
    "oman": "Oman", "kuwait": "Kuwait", "bahrain": "Bahrain", "jordan": "Jordan", "israel": "Israel",
    "india": "India", "pakistan": "Pakistan", "singapore": "Singapore", "malaysia": "Malaysia",
    "indonesia": "Indonesia", "philippines": "Philippines", "vietnam": "Vietnam", "thailand": "Thailand",
    "japan": "Japan", "south korea": "South Korea", "korea": "South Korea", "china": "China",
    "hong kong": "Hong Kong", "taiwan": "Taiwan", "brazil": "Brazil", "mexico": "Mexico", "chile": "Chile",
    "peru": "Peru", "colombia": "Colombia", "argentina": "Argentina", "ecuador": "Ecuador", "bolivia": "Bolivia",
    "uruguay": "Uruguay", "costa rica": "Costa Rica", "papua new guinea": "Papua New Guinea",
    "mongolia": "Mongolia", "kazakhstan": "Kazakhstan",
}
US_STATES = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas", "ca": "California", "co": "Colorado",
    "ct": "Connecticut", "de": "Delaware", "fl": "Florida", "ga": "Georgia", "hi": "Hawaii", "id": "Idaho",
    "il": "Illinois", "in": "Indiana", "ia": "Iowa", "ks": "Kansas", "ky": "Kentucky", "la": "Louisiana",
    "me": "Maine", "md": "Maryland", "ma": "Massachusetts", "mi": "Michigan", "mn": "Minnesota",
    "ms": "Mississippi", "mo": "Missouri", "mt": "Montana", "ne": "Nebraska", "nv": "Nevada",
    "nh": "New Hampshire", "nj": "New Jersey", "nm": "New Mexico", "ny": "New York", "nc": "North Carolina",
    "nd": "North Dakota", "oh": "Ohio", "ok": "Oklahoma", "or": "Oregon", "pa": "Pennsylvania",
    "ri": "Rhode Island", "sc": "South Carolina", "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas",
    "ut": "Utah", "vt": "Vermont", "va": "Virginia", "wa": "Washington", "wv": "West Virginia",
    "wi": "Wisconsin", "wy": "Wyoming", "dc": "District of Columbia",
}
US_STATE_NAMES = {v.lower(): v for v in US_STATES.values()}
CA_PROVINCES = {
    "bc": "British Columbia", "ab": "Alberta", "sk": "Saskatchewan", "mb": "Manitoba", "on": "Ontario",
    "qc": "Quebec", "nb": "New Brunswick", "ns": "Nova Scotia", "pe": "Prince Edward Island",
    "nl": "Newfoundland and Labrador", "yt": "Yukon", "nt": "Northwest Territories", "nu": "Nunavut",
}
CA_PROVINCE_NAMES = {v.lower(): v for v in CA_PROVINCES.values()}
AU_STATES = {"nsw": "New South Wales", "vic": "Victoria", "qld": "Queensland", "tas": "Tasmania",
             "act": "Australian Capital Territory"}

REMOTE_RE = re.compile(
    r"(\bfully\s+remote\b|100%\s*remote\b|\bremote\b|\bwork\s+from\s+home\b|\bwfh\b|\btelecommut\w*|\bhome[- ]based\b|\banywhere\b)",
    re.I,
)
HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
ONSITE_RE = re.compile(r"\b(on[- ]?site|in[- ]office|office[- ]based|in[- ]person)\b", re.I)
FULLY_REMOTE_TEXT_RE = re.compile(r"\b(fully|100%|100 %|completely)\s+remote\b", re.I)


@dataclass
class ParsedLocation:
    raw: str = ""
    city: str | None = None
    region: str | None = None
    country: str | None = None
    remote: bool | None = None
    remote_scope: str | None = None
    work_mode: str | None = None  # remote | hybrid | onsite

    def to_dict(self) -> dict:
        return asdict(self)

    def display(self) -> str:
        place = ", ".join(p for p in (self.city, None if self.city else self.region, self.country) if p)
        if self.remote:
            scope = self.remote_scope or place
            return f"Remote — {scope}" if scope else "Remote"
        if place and self.work_mode == "hybrid":
            return f"{place} — Hybrid"
        if place:
            return place
        return self.raw or "Location not specified"


def normalize_work_mode(value) -> str | None:
    if value is None:
        return None
    text = fold(str(value)).replace("_", " ")
    if not text or text in {"unspecified", "unknown", "none"}:
        return None
    if "hybrid" in text:
        return "hybrid"
    if any(t in text for t in ("remote", "telecommute", "work from home", "wfh")):
        return "remote"
    if any(t in text for t in ("onsite", "on site", "on-site", "office", "in person")):
        return "onsite"
    return None


def scope_from_text(text: str | None) -> str | None:
    t = fold(text).strip(" -–—:|,()[]/.")
    t = re.sub(r"\b(only|based|eligible|within|in|from|residents?|position|job|role|timezones?)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–—:|,()[]/.")
    if not t:
        return None
    if t in WORLDWIDE:
        return "Worldwide"
    if t in REGIONS:
        return REGIONS[t]
    if t in COUNTRIES:
        return COUNTRIES[t]
    if t in US_STATE_NAMES:
        return "United States"
    if t in CA_PROVINCE_NAMES:
        return "Canada"
    return None


def _parse_place(text: str) -> tuple[str | None, str | None, str | None]:
    tokens = [tok.strip(" .()[]") for tok in re.split(r"[,|(]| - | – | — ", text) if tok.strip(" .()[]")]
    city = region = country = None
    remaining: list[str] = []
    for tok in tokens:
        f = fold(tok)
        if f in COUNTRIES and country is None:
            country = COUNTRIES[f]
        elif f in REGIONS and region is None:
            region = REGIONS[f]
        elif f in US_STATE_NAMES:
            region, country = US_STATE_NAMES[f], country or "United States"
        elif f in CA_PROVINCE_NAMES:
            region, country = CA_PROVINCE_NAMES[f], country or "Canada"
        elif remaining and tok.isupper() and f in CA_PROVINCES:
            region, country = CA_PROVINCES[f], country or "Canada"
        elif remaining and tok.isupper() and f in AU_STATES:
            region, country = AU_STATES[f], country or "Australia"
        elif remaining and tok.isupper() and f in US_STATES:
            region, country = US_STATES[f], country or "United States"
        else:
            remaining.append(tok)
    if remaining:
        candidate = remaining[0]
        if len(fold(candidate)) >= 2 and fold(candidate) not in WORLDWIDE:
            city = candidate
    return city, region, country


def parse_location(raw: str | None, remote_flag: bool | None = None, workplace_type=None) -> ParsedLocation:
    text = (raw or "").strip()
    first = re.split(r";| or | / |\n", text)[0].strip() if text else ""
    loc = ParsedLocation(raw=text)

    mode = normalize_work_mode(workplace_type)
    if mode is None and text:
        if HYBRID_RE.search(text):
            mode = "hybrid"
        elif REMOTE_RE.search(text):
            mode = "remote"
        elif ONSITE_RE.search(text):
            mode = "onsite"
    if remote_flag is True and mode is None:
        mode = "remote"
    loc.work_mode = mode
    if remote_flag is not None:
        loc.remote = bool(remote_flag) or mode == "remote"
    else:
        loc.remote = True if mode == "remote" else (False if mode in ("hybrid", "onsite") else None)

    if not first:
        return loc
    remainder = HYBRID_RE.sub(" ", first)
    remainder = ONSITE_RE.sub(" ", remainder)
    if re.search(r"\banywhere\b", first, re.I):
        loc.remote_scope = "Worldwide" if loc.remote else None
    remainder = REMOTE_RE.sub(" ", remainder)
    remainder = re.sub(r"\(\s*\)|\[\s*\]", " ", remainder)
    remainder = re.sub(r"\s+", " ", remainder).strip(" -–—:|,()/")
    if not remainder:
        return loc

    whole_scope = scope_from_text(remainder)
    if loc.remote and loc.remote_scope is None and whole_scope:
        loc.remote_scope = whole_scope
    folded = fold(remainder)
    if folded in WORLDWIDE or folded in REGIONS:
        return loc
    city, region, country = _parse_place(remainder)
    loc.city, loc.region, loc.country = city, region, country
    if loc.remote and loc.remote_scope is None:
        loc.remote_scope = country or (region if region in REGIONS.values() else None)
    return loc
