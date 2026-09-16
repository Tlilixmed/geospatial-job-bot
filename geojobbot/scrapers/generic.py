"""Universal job-page extraction for unknown public pages.

Order of attempts:
  1. JSON-LD JobPosting (schema.org) - supports @graph, lists, nested objects, missing fields
  2. embedded JSON (__NEXT_DATA__, application/json scripts) - single job object with title+description
  3. semantic metadata + structured HTML (h1, OpenGraph, itemprop, description containers)
Site-specific adapters can be registered in SITE_ADAPTERS for hosts that need them.
Malformed markup never raises: every stage is guarded and simply yields nothing.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Callable

from bs4 import BeautifulSoup

from ..models import RawJob
from ..utils.dates import parse_datetime
from ..utils.text import clean_whitespace, html_to_text
from ..utils.urls import absolutize, host_of, is_http_url, looks_like_job_url
from .ats.detect import detect

log = logging.getLogger(__name__)

MAX_JSON_NODES = 20000
JOB_SIGNAL_RE = re.compile(
    r"\b(responsibilit\w+|qualifications?|requirements?|what you.ll do|about the role|job description|"
    r"apply now|apply for this job|submit application|essential duties|minimum qualifications)\b",
    re.I,
)


@dataclass
class ExtractionResult:
    jobs: list[RawJob] = field(default_factory=list)
    method: str | None = None
    expired: int = 0
    links: list[tuple[str, str]] = field(default_factory=list)  # (url, anchor text)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------- JSON-LD
def _types(obj: dict) -> list[str]:
    value = obj.get("@type") or obj.get("type") or []
    values = value if isinstance(value, list) else [value]
    return [str(v).split(":")[-1].split("/")[-1].lower() for v in values]


def _walk_jsonld(node, out: list, depth: int = 0, budget: list | None = None):
    budget = budget if budget is not None else [MAX_JSON_NODES]
    if depth > 12 or budget[0] <= 0:
        return
    budget[0] -= 1
    if isinstance(node, list):
        for item in node:
            _walk_jsonld(item, out, depth + 1, budget)
    elif isinstance(node, dict):
        if "jobposting" in _types(node):
            out.append(node)
            return
        for key in ("@graph", "mainEntity", "itemListElement", "item", "hasPart", "about"):
            if key in node:
                _walk_jsonld(node[key], out, depth + 1, budget)


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("name") or value.get("value") or value.get("@value") or "")
    if isinstance(value, list):
        return ", ".join(_text(v) for v in value if _text(v))
    return str(value)


def _address_text(address) -> str:
    if isinstance(address, str):
        return address
    if not isinstance(address, dict):
        return ""
    parts = [_text(address.get(k)) for k in ("addressLocality", "addressRegion", "addressCountry")]
    joined = ", ".join(p for p in parts if p)
    return joined or _text(address.get("streetAddress"))


def _location_from_jsonld(posting: dict) -> str:
    locations = posting.get("jobLocation")
    items = locations if isinstance(locations, list) else [locations] if locations else []
    texts = []
    for item in items:
        if isinstance(item, dict):
            texts.append(_address_text(item.get("address")) or _text(item.get("name")))
        elif isinstance(item, str):
            texts.append(item)
    return "; ".join(t for t in texts if t)


def _remote_scope_from_jsonld(posting: dict) -> str:
    req = posting.get("applicantLocationRequirements")
    items = req if isinstance(req, list) else [req] if req else []
    return ", ".join(_text(i) for i in items if _text(i))


def _salary_from_jsonld(posting: dict) -> str | None:
    salary = posting.get("baseSalary") or posting.get("estimatedSalary")
    if isinstance(salary, list):
        salary = salary[0] if salary else None
    if not isinstance(salary, dict):
        return str(salary) if isinstance(salary, (str, int, float)) and salary else None
    currency = salary.get("currency") or ""
    value = salary.get("value")
    unit = ""
    if isinstance(value, dict):
        unit = value.get("unitText") or ""
        lo, hi, single = value.get("minValue"), value.get("maxValue"), value.get("value")
        amount = f"{lo}–{hi}" if lo is not None and hi is not None else str(single if single is not None else lo or hi or "")
    else:
        amount = str(value or "")
    text = f"{currency} {amount} {unit}".strip()
    return text or None


def parse_jsonld_postings(soup: BeautifulSoup, page_url: str, now=None) -> tuple[list[RawJob], int, list[str]]:
    postings: list[dict] = []
    errors: list[str] = []
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw, strict=False)
        except ValueError:
            try:  # common breakage: trailing commas / HTML comments
                cleaned = re.sub(r",\s*([}\]])", r"\1", re.sub(r"<!--|-->", "", raw))
                data = json.loads(cleaned, strict=False)
            except ValueError:
                errors.append("invalid JSON-LD block")
                continue
        _walk_jsonld(data, postings)
    jobs, expired = [], 0
    for posting in postings:
        title = clean_whitespace(html_to_text(_text(posting.get("title") or posting.get("name"))))
        if not title:
            continue
        valid_through = parse_datetime(_text(posting.get("validThrough")))
        if valid_through and now and valid_through < now:
            expired += 1
            continue
        org = posting.get("hiringOrganization")
        company = _text(org) if org else None
        url = _text(posting.get("url"))
        url = absolutize(page_url, url) if url else page_url
        location_type = _text(posting.get("jobLocationType")).upper()
        remote = True if "TELECOMMUTE" in location_type else None
        location_raw = _location_from_jsonld(posting)
        scope = _remote_scope_from_jsonld(posting)
        if remote and scope:
            location_raw = f"Remote - {scope}" + (f"; {location_raw}" if location_raw else "")
        elif remote and not location_raw:
            location_raw = "Remote"
        identifier = posting.get("identifier")
        source_job_id = _text(identifier) if identifier else None
        date_posted = parse_datetime(_text(posting.get("datePosted")))
        employment = posting.get("employmentType")
        employment = ", ".join(employment) if isinstance(employment, list) else (employment or None)
        native, _ = detect(url)
        if not native:
            native, _ = detect(page_url)
        jobs.append(RawJob(
            source_type="employer_page", source_name="generic_jsonld", source_url=page_url, title=title,
            company=company or None, url=url, apply_url=url, description=html_to_text(_text(posting.get("description"))),
            location_raw=location_raw, remote_flag=remote, employment_type=str(employment) if employment else None,
            salary=_salary_from_jsonld(posting), posted_at=date_posted, posted_at_reliable=date_posted is not None,
            native_id=native, source_job_id=source_job_id or None, extraction_method="jsonld",
        ))
    return jobs, expired, errors


# ---------------------------------------------------------------------------- embedded JSON
_TITLE_KEYS = ("title", "jobTitle", "job_title", "positionTitle", "postingTitle")
_DESC_KEYS = ("description", "jobDescription", "descriptionHtml", "job_description", "descriptionPlain", "content")


def _find_job_objects(node, found: list, budget: list, depth: int = 0):
    if depth > 15 or budget[0] <= 0 or len(found) > 20:
        return
    budget[0] -= 1
    if isinstance(node, dict):
        title = next((node[k] for k in _TITLE_KEYS if isinstance(node.get(k), str) and node.get(k).strip()), None)
        desc = next((node[k] for k in _DESC_KEYS if isinstance(node.get(k), str) and len(node.get(k)) > 200), None)
        if title and desc and len(title) < 200:
            found.append(node)
            return
        for value in node.values():
            _find_job_objects(value, found, budget, depth + 1)
    elif isinstance(node, list):
        for value in node[:500]:
            _find_job_objects(value, found, budget, depth + 1)


def parse_embedded_json(soup: BeautifulSoup, page_url: str, page_title: str | None) -> list[RawJob]:
    blobs = []
    for script in soup.find_all("script"):
        stype = (script.get("type") or "").lower()
        sid = (script.get("id") or "").lower()
        if sid == "__next_data__" or stype == "application/json":
            text = script.string or ""
            if text.strip():
                try:
                    blobs.append(json.loads(text))
                except ValueError:
                    continue
    found: list[dict] = []
    for blob in blobs:
        _find_job_objects(blob, found, [MAX_JSON_NODES])
    if not found:
        return []
    chosen = found[0] if len(found) == 1 else None
    if chosen is None and page_title:
        wanted = page_title.strip().lower()
        chosen = next((f for f in found if any(isinstance(f.get(k), str) and f[k].strip().lower() == wanted
                                               for k in _TITLE_KEYS)), None)
    if chosen is None:
        return []
    title = next(chosen[k] for k in _TITLE_KEYS if isinstance(chosen.get(k), str) and chosen[k].strip())
    desc = next(chosen[k] for k in _DESC_KEYS if isinstance(chosen.get(k), str) and len(chosen[k]) > 200)
    location = chosen.get("location") or chosen.get("locationName") or chosen.get("city") or ""
    if isinstance(location, dict):
        location = _address_text(location) or _text(location)
    elif isinstance(location, list):
        location = "; ".join(_text(x) for x in location)
    company = chosen.get("companyName") or chosen.get("company") or chosen.get("hiringOrganization")
    posted = None
    for key in ("datePosted", "postedAt", "publishedAt", "published_at", "createdAt", "created_at"):
        posted = parse_datetime(chosen.get(key)) if chosen.get(key) else None
        if posted:
            break
    native, _ = detect(page_url)
    return [RawJob(
        source_type="employer_page", source_name="generic_embedded_json", source_url=page_url,
        title=clean_whitespace(title), company=_text(company) or None, url=page_url, apply_url=page_url,
        description=html_to_text(desc), location_raw=str(location or ""), posted_at=posted,
        posted_at_reliable=False, native_id=native, extraction_method="embedded_json",
    )]


# ---------------------------------------------------------------------------- HTML fallback
def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def parse_html_fallback(soup: BeautifulSoup, page_url: str) -> list[RawJob]:
    h1 = soup.find("h1")
    title = clean_whitespace(h1.get_text(" ")) if h1 else ""
    og_title = _meta(soup, "og:title", "twitter:title")
    if not title and og_title:
        title = re.split(r"\s+[|–—-]\s+", og_title)[0].strip()
    if not title or len(title) > 160:
        return []
    container = None
    for selector in ('[itemprop="description"]', '[class*="job-description"]', '[class*="jobDescription"]',
                     '[id*="job-description"]', '[class*="posting"]', '[class*="description"]', "article", "main"):
        try:
            candidates = soup.select(selector)
        except Exception:
            continue
        if candidates:
            container = max(candidates, key=lambda c: len(c.get_text(" ")))
            if len(container.get_text(" ").strip()) >= 300:
                break
    text = html_to_text(str(container)) if container else ""
    body_text = soup.get_text(" ")
    if len(text) < 300 or not JOB_SIGNAL_RE.search(body_text) or not looks_like_job_url(page_url):
        return []
    location = ""
    loc_tag = soup.select_one('[itemprop="jobLocation"], [class*="location"], [data-qa*="location"]')
    if loc_tag:
        location = clean_whitespace(loc_tag.get_text(" "))[:160]
    company = _meta(soup, "og:site_name")
    native, _ = detect(page_url)
    return [RawJob(
        source_type="employer_page", source_name="generic_html", source_url=page_url, title=title,
        company=company, url=page_url, apply_url=page_url, description=text, location_raw=location,
        native_id=native, extraction_method="html",
    )]


# ---------------------------------------------------------------------------- site adapters
SiteAdapter = Callable[[BeautifulSoup, str], list[RawJob]]
SITE_ADAPTERS: list[tuple[re.Pattern, SiteAdapter]] = []


def register_site_adapter(host_pattern: str, func: SiteAdapter) -> None:
    SITE_ADAPTERS.append((re.compile(host_pattern, re.I), func))


# ---------------------------------------------------------------------------- entry point
def extract_links(soup: BeautifulSoup, page_url: str, limit: int = 400) -> list[tuple[str, str]]:
    links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        url = absolutize(page_url, a.get("href"))
        if not url or url in seen:
            continue
        seen.add(url)
        links.append((url, clean_whitespace(a.get_text(" "))[:200]))
        if len(links) >= limit:
            break
    return links


def extract_jobs(html: str | bytes, page_url: str, now=None) -> ExtractionResult:
    result = ExtractionResult()
    if isinstance(html, bytes):
        html = html.decode("utf-8", "replace")
    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception as exc:  # pragma: no cover - html.parser is extremely tolerant
        result.errors.append(f"unparseable HTML: {type(exc).__name__}")
        return result
    try:
        result.links = extract_links(soup, page_url)
    except Exception as exc:
        result.errors.append(f"link extraction: {type(exc).__name__}")

    host = host_of(page_url)
    for pattern, adapter in SITE_ADAPTERS:
        if pattern.search(host):
            try:
                jobs = adapter(soup, page_url)
            except Exception as exc:
                result.errors.append(f"site adapter: {type(exc).__name__}")
                jobs = []
            if jobs:
                result.jobs, result.method = jobs, "site_adapter"
                return result

    try:
        jobs, expired, errors = parse_jsonld_postings(soup, page_url, now)
        result.expired = expired
        result.errors.extend(errors)
        if jobs:
            result.jobs, result.method = jobs, "jsonld"
            return result
    except Exception as exc:
        result.errors.append(f"jsonld: {type(exc).__name__}")

    page_title = None
    h1 = soup.find("h1")
    if h1:
        page_title = clean_whitespace(h1.get_text(" "))
    try:
        jobs = parse_embedded_json(soup, page_url, page_title)
        if jobs:
            result.jobs, result.method = jobs, "embedded_json"
            return result
    except Exception as exc:
        result.errors.append(f"embedded_json: {type(exc).__name__}")

    try:
        jobs = parse_html_fallback(soup, page_url)
        if jobs:
            result.jobs, result.method = jobs, "html"
    except Exception as exc:
        result.errors.append(f"html: {type(exc).__name__}")
    return result


def is_probable_job_link(url: str, anchor: str, custom_pattern: re.Pattern | None = None) -> bool:
    if not is_http_url(url):
        return False
    if custom_pattern is not None:
        return bool(custom_pattern.search(url))
    native, board = detect(url)
    if native:
        return True
    return looks_like_job_url(url) and len(anchor) >= 4
