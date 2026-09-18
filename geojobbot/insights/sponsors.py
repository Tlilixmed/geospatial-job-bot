"""Official registers of employers allowed to sponsor foreign workers.

Postings rarely say whether an employer sponsors visas; governments publish who can:

  UK  Home Office "Register of licensed sponsors: workers"   CSV, republished daily, ~140k organisations
  CA  ESDC "Employers who were issued a positive LMIA"         XLSX per quarter, with the occupation (NOC) hired
  NL  IND "Public register recognised sponsors"                HTML table, ~13k organisations

The registers are downloaded at most once a week, reduced to normalised names and stored in R2
(reference/sponsors.json.gz). Every accepted job's employer is looked up by exact normalised name, then by
"core" name (country/legal tokens removed, e.g. "Esri (UK) Ltd" ~ "Esri"). A match in the job's own country
adds a small, visible score bonus; any match is shown so the user can judge it.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import timedelta

from ..utils.dates import parse_datetime, to_iso
from ..utils.http import FetchError
from ..utils.text import fold, normalize_company

log = logging.getLogger(__name__)

STORE_SUFFIX = "reference/sponsors.json.gz"
REFRESH_DAYS = 7
SPONSOR_BONUS = 4
MAX_DOWNLOAD = 80 * 1024 * 1024

UK_PAGE = "https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers"
UK_ROUTES = ("skilled worker", "global business mobility: senior or specialist worker")
CA_PAGE = "https://open.canada.ca/data/en/dataset/90fed587-1364-4f33-a9ee-208181dc0b97"
CA_QUARTERS = 4
NL_PAGE = ("https://ind.nl/en/public-register-recognised-sponsors/"
           "public-register-regular-labour-and-highly-skilled-migrants")

# NOC 2021 codes close to the candidate's field: a positive LMIA for one of these is strong evidence
GEO_NOC = {"21203": "land surveyors", "22213": "land survey technologists", "22214": "geomatics technicians",
           "21102": "geoscientists", "22101": "geological technologists", "21223": "database analysts",
           "21232": "software developers"}
GEO_NOC_STRONG = {"21203", "22213", "22214", "21102", "22101"}

REGISTERS = {
    "uk": {"country": "United Kingdom", "label": "UK licensed sponsor", "icon": "🛂"},
    "ca": {"country": "Canada", "label": "Canada LMIA employer", "icon": "🍁"},
    "nl": {"country": "Netherlands", "label": "NL recognised sponsor", "icon": "🛂"},
}
# tokens that distinguish a national subsidiary or legal form, not the company
CORE_DROP = {"uk", "gb", "great", "britain", "england", "scotland", "europe", "european", "emea", "international",
             "intl", "global", "worldwide", "canada", "canadian", "netherlands", "nederland", "holland", "benelux",
             "the", "of", "and"}
MIN_CORE_CHARS = 4


def core_name(normalised: str) -> str:
    return " ".join(tok for tok in normalised.split() if tok not in CORE_DROP)


def _names(raw: str) -> list[str]:
    """A register line can hold two names: 'Jane Doe T/A Doe Mapping'."""
    parts = re.split(r"\s+t/a\s+|\s+trading as\s+", raw or "", flags=re.I)
    return [p.strip() for p in parts if p and p.strip()]


# ---------------------------------------------------------------------------- parsers
def parse_uk_csv(body: bytes) -> dict:
    rows = csv.reader(io.StringIO(body.decode("utf-8-sig", "replace")))
    header = next(rows, [])
    try:
        name_i, route_i = header.index("Organisation Name"), header.index("Route")
    except ValueError:
        raise ValueError(f"unexpected UK register header {header[:5]}") from None
    out: dict[str, dict] = {}
    for row in rows:
        if len(row) <= max(name_i, route_i) or fold(row[route_i]).strip() not in UK_ROUTES:
            continue
        for name in _names(row[name_i]):
            norm = normalize_company(name)
            if len(norm) >= 3 and norm not in out:
                out[norm] = {"n": name.strip()[:80]}
    return out


def read_xlsx_rows(body: bytes) -> list[list[str]]:
    """First worksheet of an .xlsx as rows of strings (standard library only)."""
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    archive = zipfile.ZipFile(io.BytesIO(body))
    shared = []
    if "xl/sharedStrings.xml" in archive.namelist():
        for si in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall(f"{ns}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{ns}t")))
    sheet_name = sorted(n for n in archive.namelist() if n.startswith("xl/worksheets/sheet"))[0]
    rows = []
    for row in ET.fromstring(archive.read(sheet_name)).iter(f"{ns}row"):
        cells = []
        for cell in row.findall(f"{ns}c"):
            value = cell.find(f"{ns}v")
            text = value.text if value is not None and value.text is not None else ""
            if cell.get("t") == "s" and text:
                text = shared[int(text)]
            elif cell.get("t") == "inlineStr":
                text = "".join(t.text or "" for t in cell.iter(f"{ns}t"))
            cells.append(text)
        rows.append(cells)
    return rows


def parse_ca_rows(rows: list[list[str]], into: dict) -> int:
    header_i = next((i for i, r in enumerate(rows[:10]) if any(fold(c).strip() == "employer" for c in r)), None)
    if header_i is None:
        raise ValueError("no 'Employer' column in the LMIA file")
    header = [fold(c).strip() for c in rows[header_i]]
    emp_i = header.index("employer")
    occ_i = header.index("occupation") if "occupation" in header else None
    pos_i = header.index("approved positions") if "approved positions" in header else None
    added = 0
    for row in rows[header_i + 1:]:
        if len(row) <= emp_i or not row[emp_i].strip():
            continue
        norm = normalize_company(row[emp_i])
        if len(norm) < 3:
            continue
        entry = into.setdefault(norm, {"n": row[emp_i].strip()[:80], "p": 0})
        added += 1
        try:
            entry["p"] += int(float(row[pos_i])) if pos_i is not None and len(row) > pos_i and row[pos_i] else 0
        except ValueError:
            pass
        code = (row[occ_i] if occ_i is not None and len(row) > occ_i else "")[:5]
        if code in GEO_NOC:
            entry.setdefault("o", [])
            if GEO_NOC[code] not in entry["o"]:
                entry["o"].append(GEO_NOC[code])
            if code in GEO_NOC_STRONG:
                entry["g"] = 1
    return added


def parse_nl_html(html_text: str) -> dict:
    out = {}
    for name in re.findall(r"<tr[^>]*>\s*<t[hd][^>]*>\s*([^<]{2,160}?)\s*</t[hd]>\s*<td[^>]*>\s*\d{8}", html_text):
        clean = re.sub(r'"+', "", name.replace("&amp;", "&")).strip()
        norm = normalize_company(clean)
        if len(norm) >= 3 and norm not in out:
            out[norm] = {"n": clean[:80]}
    return out


# ---------------------------------------------------------------------------- registry
class SponsorRegistry:
    def __init__(self, data: dict | None = None):
        self.data = data if isinstance(data, dict) else {}
        self.data.setdefault("registers", {})
        self._cores: dict[str, dict[str, str]] = {}

    # -- storage
    @classmethod
    def load(cls, manager) -> "SponsorRegistry":
        try:
            return cls(manager.read_json(STORE_SUFFIX))
        except Exception as exc:  # unreadable reference data must never stop a run
            log.warning("sponsor registers unreadable: %s", type(exc).__name__)
            return cls()

    def save(self, manager) -> None:
        manager.write_json(STORE_SUFFIX, self.data, compress=True)

    def stale(self, now) -> bool:
        checked = parse_datetime(self.data.get("checked_at"))
        return checked is None or now - checked > timedelta(days=REFRESH_DAYS)

    def counts(self) -> dict:
        return {code: len(names) for code, names in self.data["registers"].items()}

    # -- refresh
    def refresh(self, ctx) -> dict:
        """Download every register; a failing one keeps its previous content. Returns per-register status."""
        status = {}
        for code, fetch in (("uk", self._fetch_uk), ("ca", self._fetch_ca), ("nl", self._fetch_nl)):
            if ctx.out_of_time(240):
                status[code] = "skipped (time budget)"
                continue
            try:
                names = fetch(ctx)
                if len(names) < 100:
                    raise ValueError(f"only {len(names)} names parsed")
                self.data["registers"][code] = names
                status[code] = len(names)
            except (FetchError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
                status[code] = f"failed: {type(exc).__name__}: {str(exc)[:80]}"
                log.warning("sponsor register %s not refreshed: %s", code, status[code])
        self.data["checked_at"] = to_iso(ctx.now)
        self._cores.clear()
        return status

    @staticmethod
    def _fetch_uk(ctx) -> dict:
        page = ctx.client.get(UK_PAGE).text
        match = re.search(r'https://assets\.publishing\.service\.gov\.uk/[^"\s]+\.csv', page)
        if not match:
            raise ValueError("CSV link not found on the gov.uk page")
        return parse_uk_csv(ctx.client.get(match.group(0), max_bytes=MAX_DOWNLOAD, detect_challenge=False).content)

    @staticmethod
    def _fetch_ca(ctx) -> dict:
        page = ctx.client.get(CA_PAGE).text
        links = set(re.findall(r'https?://open\.canada\.ca/data/dataset/[^"\s]+?tfwp_\d{4}q\d_pos_en\.xlsx', page))
        ordered = sorted(links, key=lambda url: re.search(r"tfwp_(\d{4}q\d)", url).group(1), reverse=True)
        if not ordered:
            raise ValueError("no quarterly LMIA files found")
        names: dict = {}
        for url in ordered[:CA_QUARTERS]:
            body = ctx.client.get(url, max_bytes=MAX_DOWNLOAD, detect_challenge=False).content
            parse_ca_rows(read_xlsx_rows(body), names)
        return names

    @staticmethod
    def _fetch_nl(ctx) -> dict:
        return parse_nl_html(ctx.client.get(NL_PAGE, max_bytes=MAX_DOWNLOAD).text)

    # -- lookup
    def _core_index(self, code: str) -> dict[str, str]:
        if code not in self._cores:
            index: dict[str, str] = {}
            for norm in self.data["registers"].get(code, {}):
                core = core_name(norm)
                if len(core) >= MIN_CORE_CHARS:
                    index.setdefault(core, norm)
            self._cores[code] = index
        return self._cores[code]

    def lookup(self, company: str | None) -> list[dict]:
        """Registers this employer appears on: [{register, label, icon, country, name, match, ...}]."""
        norm = normalize_company(company)
        if len(norm) < 3:
            return []
        core = core_name(norm)
        found = []
        for code, meta in REGISTERS.items():
            names = self.data["registers"].get(code) or {}
            key, kind = (norm, "exact") if norm in names else (None, None)
            if key is None and len(core) >= MIN_CORE_CHARS:
                key = self._core_index(code).get(core)
                kind = "variant" if key else None
            if key is None:
                continue
            entry = names[key]
            hit = {"register": code, "label": meta["label"], "icon": meta["icon"], "country": meta["country"],
                   "name": entry.get("n"), "match": kind}
            if entry.get("p"):
                hit["positions"] = entry["p"]
            if entry.get("o"):
                hit["occupations"] = entry["o"][:3]
            if entry.get("g"):
                hit["geo"] = True
            found.append(hit)
        return found


def sponsor_badge(rec: dict) -> str | None:
    """Short text for digests: the register of the job's own country first, otherwise any register."""
    hits = rec.get("sponsor") or []
    if not hits:
        return None
    country = rec.get("country")
    hit = next((h for h in hits if h.get("country") == country), hits[0])
    text = f"{hit.get('icon', '🛂')} {hit['label']}"
    if hit.get("geo"):
        text += " (hired " + ", ".join(hit.get("occupations") or ["geomatics staff"])[:40] + ")"
    if hit.get("country") != country and country:
        text += " — not this country"
    return text


def annotate_record(rec: dict, registry: SponsorRegistry, settings) -> int:
    """Attach register hits to a stored job and apply the bonus once. Returns the bonus applied now."""
    hits = registry.lookup(rec.get("company"))
    rec["sponsor"] = hits
    breakdown = rec.setdefault("score_breakdown", {})
    if breakdown.get("sponsor"):  # still carrying the bonus from an earlier run (the job was not rescored)
        return 0
    country = rec.get("country")
    local = [h for h in hits if h.get("country") == country or (country is None and rec.get("remote"))]
    if not local or set(rec.get("rejection_reasons") or []) - {"LOW_SCORE"}:
        return 0
    bonus = SPONSOR_BONUS + (2 if any(h.get("geo") for h in local) else 0)
    breakdown["sponsor"] = bonus
    rec["score"] = min(100, int(rec.get("score") or 0) + bonus)
    note = f"{local[0]['label']} ({local[0].get('name')})"
    if note not in (rec.get("why_matched") or []):
        rec.setdefault("why_matched", []).append(note)
    retier(rec, settings)
    return bonus


def retier(rec: dict, settings) -> None:
    """Re-derive the tier after a score adjustment; hard rejections are never lifted."""
    reasons = [r for r in rec.get("rejection_reasons") or [] if r != "LOW_SCORE"]
    if reasons:
        return
    score = int(rec.get("score") or 0)
    if score >= settings.high_threshold:
        rec["tier"], rec["rejection_reasons"] = "high", []
    elif score >= settings.medium_threshold:
        rec["tier"], rec["rejection_reasons"] = "possible", []
    else:
        rec["tier"], rec["rejection_reasons"] = "rejected", ["LOW_SCORE"]
