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
    # French country names (keys are accent-folded)
    "tunisie": "Tunisia", "maroc": "Morocco", "algerie": "Algeria", "egypte": "Egypt", "libye": "Libya",
    "belgique": "Belgium", "suisse": "Switzerland", "allemagne": "Germany", "espagne": "Spain", "italie": "Italy",
    "royaume-uni": "United Kingdom", "etats-unis": "United States", "pays-bas": "Netherlands",
    "cote d'ivoire": "Ivory Coast", "ivory coast": "Ivory Coast", "cameroun": "Cameroon", "cameroon": "Cameroon",
    "mali": "Mali", "burkina faso": "Burkina Faso", "niger": "Niger", "benin": "Benin", "togo": "Togo",
    "gabon": "Gabon", "madagascar": "Madagascar", "mauritanie": "Mauritania", "mauritania": "Mauritania",
    "liban": "Lebanon", "lebanon": "Lebanon", "arabie saoudite": "Saudi Arabia",
    "emirats arabes unis": "United Arab Emirates",
}
# Cities/governorates that postings often list without a country (accent-folded keys).
CITY_COUNTRIES = {
    "tunis": "Tunisia", "ariana": "Tunisia", "ben arous": "Tunisia", "la manouba": "Tunisia", "manouba": "Tunisia",
    "sfax": "Tunisia", "sousse": "Tunisia", "monastir": "Tunisia", "nabeul": "Tunisia", "bizerte": "Tunisia",
    "gabes": "Tunisia", "kairouan": "Tunisia", "gafsa": "Tunisia", "medenine": "Tunisia", "tozeur": "Tunisia",
    "kebili": "Tunisia", "tataouine": "Tunisia", "beja": "Tunisia", "jendouba": "Tunisia", "le kef": "Tunisia",
    "siliana": "Tunisia", "zaghouan": "Tunisia", "mahdia": "Tunisia", "sidi bouzid": "Tunisia",
    "kasserine": "Tunisia", "hammamet": "Tunisia", "la marsa": "Tunisia", "lac": "Tunisia",
    "casablanca": "Morocco", "rabat": "Morocco", "marrakech": "Morocco", "tanger": "Morocco", "alger": "Algeria",
    "algiers": "Algeria", "oran": "Algeria", "dakar": "Senegal", "abidjan": "Ivory Coast",
    "fes": "Morocco", "agadir": "Morocco", "tangier": "Morocco",
    # Cities of countries whose ISO code is also a US state, a Canadian province or an Australian state:
    # "Berlin, DE" is Germany, "Dover, DE" is Delaware; "Riyadh, SA" is Saudi Arabia, "Adelaide, SA" is South Australia.
    "berlin": "Germany", "munich": "Germany", "munchen": "Germany", "hamburg": "Germany", "frankfurt": "Germany",
    "frankfurt am main": "Germany", "cologne": "Germany", "koln": "Germany", "stuttgart": "Germany", "dusseldorf": "Germany",
    "leipzig": "Germany", "dresden": "Germany", "bonn": "Germany", "karlsruhe": "Germany", "hannover": "Germany",
    "hanover": "Germany", "nuremberg": "Germany", "nurnberg": "Germany", "essen": "Germany", "dortmund": "Germany",
    "bremen": "Germany", "potsdam": "Germany", "darmstadt": "Germany", "heidelberg": "Germany", "freiburg": "Germany",
    "aachen": "Germany", "munster": "Germany", "mannheim": "Germany", "jena": "Germany", "oberpfaffenhofen": "Germany",
    "bangalore": "India", "bengaluru": "India", "mumbai": "India", "delhi": "India", "new delhi": "India",
    "hyderabad": "India", "chennai": "India", "pune": "India", "kolkata": "India", "noida": "India", "gurgaon": "India",
    "gurugram": "India", "ahmedabad": "India",
    "amsterdam": "Netherlands", "rotterdam": "Netherlands", "utrecht": "Netherlands", "the hague": "Netherlands",
    "den haag": "Netherlands", "eindhoven": "Netherlands", "delft": "Netherlands", "groningen": "Netherlands",
    "amersfoort": "Netherlands", "apeldoorn": "Netherlands", "zwolle": "Netherlands", "arnhem": "Netherlands",
    "nijmegen": "Netherlands", "enschede": "Netherlands", "wageningen": "Netherlands",
    "riyadh": "Saudi Arabia", "jeddah": "Saudi Arabia", "dammam": "Saudi Arabia", "khobar": "Saudi Arabia",
    "al khobar": "Saudi Arabia", "dhahran": "Saudi Arabia", "neom": "Saudi Arabia", "jubail": "Saudi Arabia",
    "mecca": "Saudi Arabia", "medina": "Saudi Arabia", "tabuk": "Saudi Arabia",
    "toronto": "Canada", "vancouver": "Canada", "calgary": "Canada", "ottawa": "Canada", "edmonton": "Canada",
    "winnipeg": "Canada", "halifax": "Canada", "saskatoon": "Canada", "regina": "Canada",
    "sydney": "Australia", "melbourne": "Australia", "brisbane": "Australia", "perth": "Australia", "adelaide": "Australia",
    "canberra": "Australia", "darwin": "Australia", "hobart": "Australia",
    "jakarta": "Indonesia", "bogota": "Colombia", "medellin": "Colombia", "lima": "Peru",
    "dubai": "United Arab Emirates", "abu dhabi": "United Arab Emirates", "sharjah": "United Arab Emirates",
    "doha": "Qatar", "kuwait city": "Kuwait", "muscat": "Oman", "manama": "Bahrain",
    "paris": "France", "lyon": "France", "marseille": "France", "toulouse": "France", "nantes": "France",
    "bordeaux": "France", "lille": "France", "montpellier": "France", "rennes": "France", "grenoble": "France",
    "strasbourg": "France", "nice": "France", "brussels": "Belgium", "bruxelles": "Belgium", "liege": "Belgium",
    "namur": "Belgium", "geneva": "Switzerland", "geneve": "Switzerland", "lausanne": "Switzerland",
    "zurich": "Switzerland", "bern": "Switzerland", "london": "United Kingdom", "manchester": "United Kingdom",
    "edinburgh": "United Kingdom", "glasgow": "United Kingdom", "bristol": "United Kingdom", "leeds": "United Kingdom",
    "dublin": "Ireland", "cork": "Ireland", "galway": "Ireland",
}
# Québec cities given without the province: the region matters (Francophone Mobility applies outside Québec only).
QUEBEC_CITIES = {"montreal", "quebec", "quebec city", "ville de quebec", "gatineau", "laval", "longueuil", "sherbrooke",
                 "trois-rivieres", "saguenay", "levis", "terrebonne", "brossard", "drummondville", "rimouski", "rouyn-noranda",
                 "val-d'or", "chicoutimi", "saint-hyacinthe", "granby", "victoriaville"}
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
# two-letter Australian codes collide with ISO countries (SA, NT, WA): used only when nothing says otherwise
AU_STATE_CODES = {"sa": "South Australia", "wa": "Western Australia", "nt": "Northern Territory"}

REMOTE_RE = re.compile(
    r"(\bfully\s+remote\b|100%\s*remote\b|\bremote\b|\bwork\s+from\s+home\b|\bwfh\b|\btelecommut\w*|\bhome[- ]based\b|\banywhere\b|"
    r"\bt[ée]l[ée]travail\b|\b[àa]\s+distance\b)",
    re.I,
)
HYBRID_RE = re.compile(r"\bhybrid(?:e|es)?\b", re.I)
ONSITE_RE = re.compile(r"\b(on[- ]?site|in[- ]office|office[- ]based|in[- ]person|pr[ée]sentiel|sur\s+site)\b", re.I)
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
    folded = [fold(tok) for tok in tokens]
    known_city_country = next((CITY_COUNTRIES[f] for f in folded if f in CITY_COUNTRIES), None)
    in_quebec = any(f in QUEBEC_CITIES for f in folded) and not any(
        f in CA_PROVINCES or f in CA_PROVINCE_NAMES for f in folded if f not in ("qc", "quebec"))
    city = region = country = None
    remaining: list[str] = []
    for tok, f in zip(tokens, folded):
        code = tok.upper() if tok.isupper() and len(tok) == 2 else None
        iso_country = ISO_CODES.get(code) if code and remaining else None
        state = None
        if remaining and tok.isupper():
            if f in CA_PROVINCES:
                state = (CA_PROVINCES[f], "Canada")
            elif f in AU_STATES:
                state = (AU_STATES[f], "Australia")
            elif f in AU_STATE_CODES and "Australia" in (known_city_country, country):  # "Perth, WA", never "Seattle, WA"
                state = (AU_STATE_CODES[f], "Australia")
            elif f in US_STATES:
                state = (US_STATES[f], "United States")
        if f in COUNTRIES and country is None:
            country = COUNTRIES[f]
        elif f in REGIONS and region is None:
            region = REGIONS[f]
        elif f in US_STATE_NAMES:
            region, country = US_STATE_NAMES[f], country or "United States"
        elif f in CA_PROVINCE_NAMES and not (f == "quebec" and not remaining):
            region, country = CA_PROVINCE_NAMES[f], country or "Canada"
        elif iso_country and state:
            # "Tunis, TN": Tunisia or Tennessee? The city decides; a code that names the city's own country is that country.
            if known_city_country == iso_country or (country == iso_country):
                country = iso_country
            elif known_city_country is None or known_city_country == state[1]:
                region, country = state[0], country or state[1]
            else:
                country = country or known_city_country
        elif iso_country:
            country = country or iso_country
        elif state:
            region, country = state[0], country or state[1]
        else:
            remaining.append(tok)
    if remaining:
        candidate = remaining[0]
        if len(fold(candidate)) >= 2 and fold(candidate) not in WORLDWIDE:
            city = candidate
    if country is None and known_city_country:
        country = known_city_country
    if in_quebec and (country in (None, "Canada")):
        region, country = region or "Quebec", "Canada"
    return city, region, country


# Workday and some other ATSs prefix the location with an ISO country code: "SA - Riyadh", "CA - MB, Winnipeg"
ISO_PREFIX = {"SA": "Saudi Arabia", "AE": "United Arab Emirates", "QA": "Qatar", "KW": "Kuwait", "OM": "Oman", "BH": "Bahrain",
              "CA": "Canada", "US": "United States", "GB": "United Kingdom", "UK": "United Kingdom", "IE": "Ireland",
              "AU": "Australia", "NZ": "New Zealand", "FR": "France", "DE": "Germany", "NL": "Netherlands", "BE": "Belgium",
              "CH": "Switzerland", "DK": "Denmark", "SE": "Sweden", "NO": "Norway", "ES": "Spain", "IT": "Italy", "PT": "Portugal",
              "TN": "Tunisia", "MA": "Morocco", "EG": "Egypt", "IN": "India", "SG": "Singapore", "ZA": "South Africa"}
ISO_CODES = dict(ISO_PREFIX, **{
    "AT": "Austria", "LU": "Luxembourg", "FI": "Finland", "IS": "Iceland", "PL": "Poland", "CZ": "Czechia", "RO": "Romania",
    "GR": "Greece", "HU": "Hungary", "EE": "Estonia", "LV": "Latvia", "LT": "Lithuania", "HR": "Croatia", "RS": "Serbia",
    "BG": "Bulgaria", "SK": "Slovakia", "SI": "Slovenia", "UA": "Ukraine", "TR": "Turkey", "DZ": "Algeria", "LY": "Libya",
    "NG": "Nigeria", "KE": "Kenya", "GH": "Ghana", "RW": "Rwanda", "ET": "Ethiopia", "SN": "Senegal", "TZ": "Tanzania",
    "UG": "Uganda", "ZM": "Zambia", "JO": "Jordan", "PK": "Pakistan", "MY": "Malaysia", "ID": "Indonesia", "PH": "Philippines",
    "VN": "Vietnam", "TH": "Thailand", "JP": "Japan", "KR": "South Korea", "CN": "China", "HK": "Hong Kong", "TW": "Taiwan",
    "BR": "Brazil", "MX": "Mexico", "CL": "Chile", "PE": "Peru", "CO": "Colombia", "AR": "Argentina", "CI": "Ivory Coast",
    "CM": "Cameroon", "LB": "Lebanon",
})
ISO_PREFIX_RE = re.compile(r"^([A-Z]{2}) +[-–] +(.+)$")


def parse_location(raw: str | None, remote_flag: bool | None = None, workplace_type=None) -> ParsedLocation:
    text = (raw or "").strip()
    prefixed = ISO_PREFIX_RE.match(text)
    if prefixed and prefixed.group(1) in ISO_PREFIX:
        rest = prefixed.group(2)
        region_first = re.match(r"^([A-Z]{2}), *(.+)$", rest)  # "MB, Winnipeg" -> "Winnipeg, MB"
        if region_first:
            rest = f"{region_first.group(2)}, {region_first.group(1)}"
        parsed = parse_location(f"{rest}, {ISO_PREFIX[prefixed.group(1)]}", remote_flag, workplace_type)
        parsed.raw = text
        return parsed
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
