"""Legitimate public job feeds.

  Remotive   https://remotive.com/api/remote-jobs?search=...   (asks for low request frequency)
  Jobicy     https://jobicy.com/api/v2/remote-jobs?tag=...
  Himalayas  https://himalayas.app/jobs/api/search?q=...        (attribution to himalayas.app requested)
  Arbeitnow  https://www.arbeitnow.com/api/job-board-api?page=N
  RemoteOK   https://remoteok.com/api                           (attribution requested)
  RSS/Atom   any feed configured in sources.toml [[rss_feeds]]
  USAJOBS    https://data.usajobs.gov/api/search (free API key + email, optional)
  Adzuna     https://api.adzuna.com/v1/api/jobs/{country}/search/1 (free app id + key, optional)
  Jooble     https://jooble.org/api/{key} (free API key, optional; covers Tunisia and Canada among others)
  JSearch    https://jsearch.p.rapidapi.com/search (RapidAPI key, free 200 req/month; Google for Jobs index,
             which carries LinkedIn, Indeed and Glassdoor postings with full descriptions)
  JobSpy     python-jobspy library (optional, supplementary: Indeed, LinkedIn, Glassdoor, Bayt, Google)

Each feed runs at most once per ``min_interval_hours`` (tracked in R2 state) to respect the
providers' rate expectations. A non-empty response that parses to zero jobs is SCHEMA_MISMATCH,
never a silent "no jobs".
"""
from __future__ import annotations

import html.entities as html_entities
import logging
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timezone

from ..models import BackendOutput, RawJob
from ..utils.dates import parse_datetime
from ..utils.http import FetchError
from ..utils.text import html_to_text
from ..utils.urls import is_aggregator
from .ats.detect import detect
from .base import Backend, RunContext

log = logging.getLogger(__name__)


class JsonFeedBackend(Backend):
    phase = "extraction"
    source_type = "feed"
    queries: list = [None]
    geo_query = True  # queries are geospatial keywords

    def request(self, ctx: RunContext, query):
        raise NotImplementedError

    def items(self, payload) -> list | None:
        raise NotImplementedError

    def to_raw(self, item: dict) -> RawJob | None:
        raise NotImplementedError

    def enabled(self, ctx):
        ok, reason = super().enabled(ctx)
        if ok and self.name not in ctx.settings.feeds_enabled:
            return False, "not listed in FEEDS_ENABLED"
        return ok, reason

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        stats = Counter()
        errors = []
        seen = set()
        for query in self.queries:
            if ctx.out_of_time(200):
                break
            try:
                payload = self.request(ctx, query)
            except FetchError as exc:
                stats["requests_failed"] += 1
                errors.append(f"{query}: {exc}")
                out.http_status = exc.status
                if exc.kind in ("RATE_LIMITED", "BLOCKED", "ROBOTS_DISALLOWED"):
                    break
                continue
            items = self.items(payload)
            if items is None:
                stats["schema_mismatch"] += 1
                errors.append(f"{query}: unexpected response structure")
                continue
            stats["requests_ok"] += 1
            stats["listed"] += len(items)
            parsed = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    job = self.to_raw(item)
                except Exception as exc:  # one malformed item must not stop the feed
                    ctx.record_parser_error(self.name)
                    stats["item_errors"] += 1
                    log.debug("%s item error: %s", self.name, exc)
                    continue
                if job is None or not job.title:
                    continue
                parsed += 1
                key = job.native_id or job.source_job_id or job.url
                if key in seen:
                    continue
                seen.add(key)
                job.geo_context = self.geo_query
                if ctx.prefilter(job.title, job.geo_context):
                    out.jobs.append(job)
                else:
                    out.prefiltered_out += 1
            if items and parsed == 0:
                stats["schema_mismatch"] += 1
                errors.append(f"{query}: {len(items)} items but none parseable")
        out.details = {**dict(stats), "errors": errors[:10]}
        if out.jobs:
            ctx.snapshot(self.name, [j.snapshot() for j in out.jobs])
        if stats["requests_ok"] == 0:
            out.status = "FAILED"
            out.error = errors[0] if errors else "no successful requests"
        elif stats["schema_mismatch"] or stats["requests_failed"]:
            out.status = "PARTIAL"
            out.error = errors[0] if errors else None
        return out


def _link_native(url: str | None) -> str | None:
    native, _ = detect(url) if url else (None, None)
    return native


def days_window(settings) -> int:
    """The alert freshness window in whole days, used as each source's own 'posted within' filter."""
    return max(1, -(-int(settings.max_job_age_hours) // 24))


class RemotiveBackend(JsonFeedBackend):
    name = "remotive"
    min_interval_hours = 12
    queries = ["gis", "geospatial"]

    def request(self, ctx, query):
        return ctx.client.get_json("https://remotive.com/api/remote-jobs", params={"search": query, "limit": 100},
                                   respect_robots=False)

    def items(self, payload):
        return payload.get("jobs") if isinstance(payload, dict) and isinstance(payload.get("jobs"), list) else None

    def to_raw(self, item):
        posted = parse_datetime(item.get("publication_date"))
        url = item.get("url")
        return RawJob(
            source_type="feed", source_name="remotive", source_url="https://remotive.com/api/remote-jobs",
            title=(item.get("title") or "").strip(), company=item.get("company_name"), url=url, apply_url=url,
            description=html_to_text(item.get("description")),
            location_raw=f"Remote - {item['candidate_required_location']}" if item.get("candidate_required_location") else "Remote",
            remote_flag=True, employment_type=item.get("job_type"), salary=item.get("salary") or None,
            posted_at=posted, posted_at_reliable=posted is not None, native_id=_link_native(url),
            source_job_id=f"remotive:{item.get('id')}" if item.get("id") else None, extraction_method="api",
        )


class JobicyBackend(JsonFeedBackend):
    name = "jobicy"
    min_interval_hours = 6
    queries = ["gis", "geospatial"]

    def request(self, ctx, query):
        return ctx.client.get_json("https://jobicy.com/api/v2/remote-jobs", params={"count": 50, "tag": query},
                                   respect_robots=False)

    def items(self, payload):
        if isinstance(payload, dict):
            jobs = payload.get("jobs")
            if isinstance(jobs, list):
                return jobs
            if payload.get("success") is False or payload.get("jobCount") == 0:
                return []
        return None

    def to_raw(self, item):
        posted = parse_datetime(item.get("pubDate"))
        url = item.get("url")
        salary = None
        if item.get("annualSalaryMin") or item.get("annualSalaryMax"):
            salary = f"{item.get('salaryCurrency') or ''} {item.get('annualSalaryMin') or ''}–{item.get('annualSalaryMax') or ''}".strip()
        job_type = item.get("jobType")
        return RawJob(
            source_type="feed", source_name="jobicy", source_url="https://jobicy.com/api/v2/remote-jobs",
            title=html_to_text(item.get("jobTitle")), company=item.get("companyName"), url=url, apply_url=url,
            description=html_to_text(item.get("jobDescription") or item.get("jobExcerpt")),
            location_raw=f"Remote - {item['jobGeo']}" if item.get("jobGeo") else "Remote", remote_flag=True,
            employment_type=", ".join(job_type) if isinstance(job_type, list) else job_type, salary=salary,
            posted_at=posted, posted_at_reliable=posted is not None, native_id=_link_native(url),
            source_job_id=f"jobicy:{item.get('id')}" if item.get("id") else None, extraction_method="api",
        )


class HimalayasBackend(JsonFeedBackend):
    name = "himalayas"
    min_interval_hours = 4
    queries = ["gis", "geospatial", "lidar", "cartographer", "remote sensing", "photogrammetry"]

    def request(self, ctx, query):
        return ctx.client.get_json("https://himalayas.app/jobs/api/search", params={"q": query}, respect_robots=False)

    def items(self, payload):
        return payload.get("jobs") if isinstance(payload, dict) and isinstance(payload.get("jobs"), list) else None

    def to_raw(self, item):
        restrictions = []
        for r in item.get("locationRestrictions") or []:
            restrictions.append(r.get("name") if isinstance(r, dict) else str(r))
        scope = ", ".join(x for x in restrictions if x)
        posted = parse_datetime(item.get("pubDate") or item.get("publishedAt"))
        url = item.get("applicationLink") or item.get("guid")
        salary = None
        if item.get("minSalary") or item.get("maxSalary"):
            salary = f"{item.get('currency') or ''} {item.get('minSalary') or ''}–{item.get('maxSalary') or ''}".strip()
        return RawJob(
            source_type="feed", source_name="himalayas", source_url="https://himalayas.app/jobs/api/search",
            title=(item.get("title") or "").strip(), company=item.get("companyName"), url=url, apply_url=url,
            description=html_to_text(item.get("description") or item.get("excerpt")),
            location_raw=f"Remote - {scope}" if scope else "Remote", remote_flag=True,
            employment_type=item.get("employmentType"), salary=salary, posted_at=posted,
            posted_at_reliable=posted is not None, native_id=_link_native(url),
            source_job_id=f"himalayas:{item.get('guid') or url}", extraction_method="api",
        )


class ArbeitnowBackend(JsonFeedBackend):
    name = "arbeitnow"
    min_interval_hours = 8
    queries = [1, 2, 3]
    geo_query = False

    def request(self, ctx, query):
        return ctx.client.get_json("https://www.arbeitnow.com/api/job-board-api", params={"page": query},
                                   respect_robots=False)

    def items(self, payload):
        return payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), list) else None

    def to_raw(self, item):
        posted = parse_datetime(item.get("created_at"))
        url = item.get("url")
        types = item.get("job_types")
        return RawJob(
            source_type="feed", source_name="arbeitnow", source_url="https://www.arbeitnow.com/api/job-board-api",
            title=(item.get("title") or "").strip(), company=item.get("company_name"), url=url, apply_url=url,
            description=html_to_text(item.get("description")), location_raw=item.get("location") or "",
            remote_flag=item.get("remote") if isinstance(item.get("remote"), bool) else None,
            employment_type=", ".join(types) if isinstance(types, list) else types, posted_at=posted,
            posted_at_reliable=posted is not None, native_id=_link_native(url),
            source_job_id=f"arbeitnow:{item.get('slug')}" if item.get("slug") else None, extraction_method="api",
        )


class RemoteOKBackend(JsonFeedBackend):
    name = "remoteok"
    min_interval_hours = 12
    queries = ["all"]
    geo_query = False

    def request(self, ctx, query):
        return ctx.client.get_json("https://remoteok.com/api", respect_robots=False)

    def items(self, payload):
        if not isinstance(payload, list):
            return None
        return [x for x in payload if isinstance(x, dict) and x.get("position")]  # first element is a legal notice

    def to_raw(self, item):
        posted = parse_datetime(item.get("epoch") or item.get("date"))
        url = item.get("url")
        salary = None
        if item.get("salary_min") or item.get("salary_max"):
            salary = f"USD {item.get('salary_min') or ''}–{item.get('salary_max') or ''}"
        return RawJob(
            source_type="feed", source_name="remoteok", source_url="https://remoteok.com/api",
            title=(item.get("position") or "").strip(), company=item.get("company"), url=url,
            apply_url=item.get("apply_url") or url, description=html_to_text(item.get("description")),
            location_raw=f"Remote - {item['location']}" if item.get("location") else "Remote", remote_flag=True,
            salary=salary, posted_at=posted, posted_at_reliable=posted is not None, native_id=_link_native(url),
            source_job_id=f"remoteok:{item.get('id')}" if item.get("id") else None, extraction_method="api",
        )


_XML_BUILTIN_ENTITIES = {b"amp", b"lt", b"gt", b"quot", b"apos"}
_NAMED_ENTITY_RE = re.compile(rb"&([A-Za-z][A-Za-z0-9]*);")


def repair_xml_entities(body: bytes) -> bytes:
    """Replace HTML named entities XML does not define (&raquo;, &nbsp;, &eacute;…) with numeric references.

    WordPress and other CMS feeds emit them in titles; expat rejects the whole document otherwise.
    Entity names are ASCII, so the byte-level substitution is safe for any ASCII-compatible encoding.
    """
    def substitute(match):
        name = match.group(1)
        if name in _XML_BUILTIN_ENTITIES:
            return match.group(0)
        code = html_entities.name2codepoint.get(name.decode("ascii"))
        return f"&#{code};".encode("ascii") if code else match.group(0)
    return _NAMED_ENTITY_RE.sub(substitute, body)


class RssFeedBackend(Backend):
    """Generic RSS/Atom feeds listed in sources.toml [[rss_feeds]] (name, url, source_type, geospatial).

    Runs in the discovery phase: items that carry only an excerpt have their posting page queued, so the
    generic extractor reads the full text (JSON-LD or HTML) in the same run and fusion merges the two.
    """

    name = "rss_feeds"
    phase = "discovery"
    source_type = "feed"
    FULL_DESCRIPTION_CHARS = 300  # below this the feed item is treated as an excerpt

    def __init__(self, feeds: list[dict]):
        self.feeds = [f for f in feeds if isinstance(f, dict) and f.get("url")]

    def enabled(self, ctx):
        ok, reason = super().enabled(ctx)
        if ok and not self.feeds:
            return False, "no rss_feeds configured"
        return ok, reason

    @staticmethod
    def parse(body: bytes, feed: dict) -> list[RawJob]:
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            root = ET.fromstring(repair_xml_entities(body))
        jobs = []
        entries = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        for entry in entries:
            def find(*names):
                for name in names:
                    el = entry.find(name)
                    if el is not None:
                        return (el.text or el.get("href") or "").strip()
                return ""
            title = html_to_text(find("title", "{http://www.w3.org/2005/Atom}title"))
            link = find("link", "{http://www.w3.org/2005/Atom}link")
            # WordPress feeds (job boards built on WP Job Manager) carry the full posting in content:encoded
            desc = find("{http://purl.org/rss/1.0/modules/content/}encoded", "description",
                        "{http://www.w3.org/2005/Atom}summary", "{http://www.w3.org/2005/Atom}content")
            posted = parse_datetime(find("pubDate", "{http://www.w3.org/2005/Atom}updated",
                                         "{http://www.w3.org/2005/Atom}published"))
            if not title:
                continue
            jobs.append(RawJob(
                source_type=feed.get("source_type", "feed"), source_name=f"rss:{feed.get('name', 'feed')}",
                source_url=feed["url"], title=title, company=feed.get("company"), url=link or None,
                apply_url=link or None, description=html_to_text(desc), posted_at=posted,
                posted_at_reliable=posted is not None, native_id=_link_native(link),
                source_job_id=find("guid", "{http://www.w3.org/2005/Atom}id") or None, extraction_method="api",
                geo_context=bool(feed.get("geospatial", False)),
            ))
        return jobs

    @staticmethod
    def is_html_not_feed(body: bytes, content_type: str) -> bool:
        """WordPress answers a search feed with no results with an ordinary HTML page: an empty result, not an error."""
        return "html" in (content_type or "").lower() and not re.search(rb"<(rss|feed|rdf:RDF)\b", body[:4000])

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        errors, ok, queued, empty_html = [], 0, 0, []
        for feed in self.feeds:
            try:
                response = ctx.client.get(feed["url"], detect_challenge=False)
                body = response.content
                if self.is_html_not_feed(body, response.headers.get("Content-Type", "")):
                    empty_html.append(feed.get("name"))
                    ok += 1
                    continue
                jobs = self.parse(body, feed)
                ok += 1
            except FetchError as exc:
                errors.append(f"{feed.get('name')}: {exc.kind}")
                continue
            except ET.ParseError as exc:
                ctx.record_parser_error(self.name)
                errors.append(f"{feed.get('name')}: XML parse error {exc}")
                continue
            for job in jobs:
                if not ctx.prefilter(job.title, job.geo_context):
                    out.prefiltered_out += 1
                    continue
                out.jobs.append(job)
                if job.url and len(job.description) < self.FULL_DESCRIPTION_CHARS and not is_aggregator(job.url):
                    if ctx.pages.add(job.url, origin="rss", priority=80, source_type=job.source_type,
                                     geo_context=job.geo_context):
                        queued += 1
        out.details = {"feeds": len(self.feeds), "ok": ok, "pages_queued": queued, "empty_html": empty_html,
                       "errors": errors}
        if ok == 0:
            out.status, out.error = "FAILED", errors[0] if errors else "no feeds parsed"
        elif errors:
            out.status = "PARTIAL"
        return out


class UsaJobsBackend(Backend):
    name = "usajobs"
    phase = "extraction"
    source_type = "government"
    min_interval_hours = 4
    keywords = ["GIS", "geospatial", "cartographer", "photogrammetry", "remote sensing", "geodesist", "LiDAR",
                "surveying technician"]

    def enabled(self, ctx):
        ok, reason = super().enabled(ctx)
        if ok and not (ctx.settings.usajobs_api_key and ctx.settings.usajobs_email):
            return False, "USAJOBS_API_KEY / USAJOBS_EMAIL not set"
        return ok, reason

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        headers = {"Host": "data.usajobs.gov", "User-Agent": ctx.settings.usajobs_email,
                   "Authorization-Key": ctx.settings.usajobs_api_key}
        errors, ok, seen = [], 0, set()
        for keyword in self.keywords:
            try:
                data = ctx.client.get_json("https://data.usajobs.gov/api/search", headers=headers, respect_robots=False,
                                           params={"Keyword": keyword, "ResultsPerPage": 100,
                                                   "DatePosted": min(60, days_window(ctx.settings))})
            except FetchError as exc:
                errors.append(f"{keyword}: {exc.kind}")
                if exc.kind in ("AUTH_REQUIRED", "BLOCKED", "RATE_LIMITED"):
                    break
                continue
            items = (((data or {}).get("SearchResult") or {}).get("SearchResultItems")) if isinstance(data, dict) else None
            if not isinstance(items, list):
                errors.append(f"{keyword}: unexpected structure")
                continue
            ok += 1
            for wrapper in items:
                desc = (wrapper or {}).get("MatchedObjectDescriptor") or {}
                pid = desc.get("PositionID") or desc.get("PositionURI")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                title = (desc.get("PositionTitle") or "").strip()
                if not ctx.prefilter(title, True):
                    out.prefiltered_out += 1
                    continue
                details = ((desc.get("UserArea") or {}).get("Details") or {})
                posted = parse_datetime(desc.get("PublicationStartDate"))
                apply = (desc.get("ApplyURI") or [None])[0]
                schedule = ", ".join(s.get("Name", "") for s in desc.get("PositionSchedule") or [] if isinstance(s, dict))
                pay = desc.get("PositionRemuneration") or []
                salary = None
                if pay and isinstance(pay[0], dict):
                    salary = f"USD {pay[0].get('MinimumRange')}–{pay[0].get('MaximumRange')} {pay[0].get('Description') or ''}".strip()
                out.jobs.append(RawJob(
                    source_type="government", source_name="usajobs", source_url="https://data.usajobs.gov/api/search",
                    title=title, company=desc.get("OrganizationName") or desc.get("DepartmentName"),
                    url=desc.get("PositionURI"), apply_url=apply or desc.get("PositionURI"),
                    description="\n\n".join(x for x in (details.get("JobSummary"), desc.get("QualificationSummary"),
                                                        " ".join(details.get("MajorDuties") or [])) if x),
                    location_raw=desc.get("PositionLocationDisplay") or "", employment_type=schedule or None,
                    salary=salary, posted_at=posted, posted_at_reliable=posted is not None,
                    source_job_id=f"usajobs:{pid}", extraction_method="api", geo_context=True,
                    workplace_type="remote" if "anywhere" in (desc.get("PositionLocationDisplay") or "").lower() else None,
                ))
        out.details = {"queries_ok": ok, "errors": errors}
        if ok == 0:
            out.status, out.error = "FAILED", errors[0] if errors else "no successful requests"
        elif errors:
            out.status = "PARTIAL"
        return out


class JobSpyBackend(Backend):
    """Supplementary discovery via python-jobspy. Degrades to SKIPPED when the library is missing."""

    name = "jobspy"
    phase = "extraction"
    source_type = "aggregator"
    min_interval_hours = 4
    terms = ["GIS Analyst", "GIS Technician", "Geospatial Analyst", "GIS Specialist", "LiDAR", "Cartographer",
             "Photogrammetry", "Remote Sensing Specialist", "Geomatics", "Survey Technician", "Utility GIS",
             "CAD/GIS Technician"]

    def enabled(self, ctx):
        ok, reason = super().enabled(ctx)
        if not ok:
            return ok, reason
        if not ctx.settings.jobspy_enabled:
            return False, "JOBSPY_ENABLED=false"
        try:
            import jobspy  # noqa: F401
        except Exception:
            return False, "python-jobspy not installed (optional)"
        return True, None

    # Sites that only exist per country: skipped for locations whose country is "worldwide".
    COUNTRY_SITES = {"indeed", "glassdoor"}

    @staticmethod
    def location_specs(settings) -> list[tuple[str, str]]:
        """Parse JOBSPY_LOCATIONS entries "Location" / "Location@country" into (location, indeed_country)."""
        specs = []
        for item in settings.jobspy_locations:
            location, _, country = item.partition("@")
            location = location.strip()
            if location:
                specs.append((location, (country.strip() or settings.jobspy_country_indeed).lower()))
        return specs or [("Remote", settings.jobspy_country_indeed.lower())]

    @staticmethod
    def patch_country_parsing() -> None:
        """python-jobspy aborts a whole LinkedIn search when a result's country is missing from its
        enum (Tunisia, for example). Fall back to WORLDWIDE so the row is kept instead."""
        try:
            from jobspy.model import Country
        except Exception:
            return
        original = getattr(Country.from_string, "__func__", None)
        if original is None or getattr(original, "_geojobbot_tolerant", False):
            return

        def tolerant(cls, value):
            try:
                return original(cls, value)
            except ValueError:
                return cls.WORLDWIDE

        tolerant._geojobbot_tolerant = True
        Country.from_string = classmethod(tolerant)

    def run(self, ctx: RunContext) -> BackendOutput:
        from jobspy import scrape_jobs

        self.patch_country_parsing()
        out = BackendOutput()
        cursor = ctx.cursor(self.name)
        start = int(cursor.get("index", 0))
        n = min(ctx.settings.jobspy_terms_per_run, len(self.terms))
        all_sites = [s.strip().lower() for s in ctx.settings.jobspy_sites if s.strip()]
        specs = self.location_specs(ctx.settings)
        errors, ok = [], 0
        for offset in range(n):
            term = self.terms[(start + offset) % len(self.terms)]
            cursor["index"] = (start + offset + 1) % len(self.terms)
            for location, country in specs:
                if ctx.out_of_time(300):
                    break
                sites = [s for s in all_sites if not (country == "worldwide" and s in self.COUNTRY_SITES)]
                if not sites:
                    continue
                kwargs = dict(site_name=sites, search_term=term, location=location,
                              results_wanted=ctx.settings.jobspy_results_wanted,
                              hours_old=int(ctx.settings.max_job_age_hours), country_indeed=country, verbose=0)
                if "linkedin" in sites and ctx.settings.jobspy_linkedin_fetch_description:
                    kwargs["linkedin_fetch_description"] = True
                try:
                    frame = scrape_jobs(**kwargs)
                except Exception as exc:  # the library raises many types; never propagate
                    message = str(exc)[:200]
                    kind = "BLOCKED" if re.search(r"429|403|captcha|blocked", message, re.I) else type(exc).__name__
                    errors.append(f"{term}/{location}: {kind}")
                    continue
                ok += 1
                for row in frame.to_dict("records") if frame is not None else []:
                    job = self._row_to_raw(row)
                    if job and ctx.prefilter(job.title, True):
                        out.jobs.append(job)
                    elif job:
                        out.prefiltered_out += 1
        out.details = {"searches_ok": ok, "errors": errors[:10], "sites": all_sites,
                       "locations": [f"{loc}@{country}" for loc, country in specs]}
        if out.jobs:
            ctx.snapshot(self.name, [j.snapshot() for j in out.jobs])
        if ok == 0:
            out.status, out.error = "FAILED", errors[0] if errors else "no searches completed"
        elif errors:
            out.status = "PARTIAL"
        return out

    @staticmethod
    def _clean(value):
        if value is None:
            return None
        try:
            if value != value:  # NaN
                return None
        except Exception:
            pass
        return value

    def _row_to_raw(self, row: dict) -> RawJob | None:
        c = self._clean
        title = c(row.get("title"))
        if not title:
            return None
        direct = c(row.get("job_url_direct"))
        url = c(row.get("job_url"))
        raw_date = c(row.get("date_posted"))
        if isinstance(raw_date, date) and not isinstance(raw_date, datetime):
            raw_date = datetime(raw_date.year, raw_date.month, raw_date.day, tzinfo=timezone.utc)
        posted = parse_datetime(raw_date if isinstance(raw_date, datetime) else (str(raw_date) if raw_date else None))
        salary = None
        if c(row.get("min_amount")) or c(row.get("max_amount")):
            salary = f"{c(row.get('currency')) or ''} {c(row.get('min_amount')) or ''}–{c(row.get('max_amount')) or ''} {c(row.get('interval')) or ''}".strip()
        native = _link_native(direct) or _link_native(url)
        remote = c(row.get("is_remote"))
        return RawJob(
            source_type="aggregator", source_name=f"jobspy:{c(row.get('site')) or 'unknown'}",
            source_url=url or "", title=str(title).strip(), company=c(row.get("company")), url=direct or url,
            apply_url=direct or url, description=str(c(row.get("description")) or ""),
            location_raw=str(c(row.get("location")) or ""), remote_flag=bool(remote) if remote is not None else None,
            employment_type=str(c(row.get("job_type")) or "") or None, salary=salary, posted_at=posted,
            posted_at_reliable=posted is not None, native_id=native,
            source_job_id=f"jobspy:{c(row.get('id'))}" if c(row.get("id")) else None, extraction_method="api",
            geo_context=True,
        )


class AdzunaBackend(Backend):
    """Adzuna search API (https://developer.adzuna.com, free app id + key). Aggregator: descriptions are
    short snippets, so scoring leans on the title, and the link goes through Adzuna to the employer."""

    name = "adzuna"
    phase = "extraction"
    source_type = "aggregator"
    min_interval_hours = 4
    terms = ["GIS", "geospatial", "geomatics", "LiDAR", "cartographer", "remote sensing", "photogrammetry",
             "surveying technician"]
    api = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"

    def enabled(self, ctx):
        ok, reason = super().enabled(ctx)
        if ok and not (ctx.settings.adzuna_app_id and ctx.settings.adzuna_app_key):
            return False, "ADZUNA_APP_ID / ADZUNA_APP_KEY not set"
        return ok, reason

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        errors, ok, seen = [], 0, set()
        stop = False
        for country in ctx.settings.adzuna_countries:
            for term in self.terms:
                if stop or ctx.out_of_time(200):
                    break
                url = self.api.format(country=country)
                params = {"app_id": ctx.settings.adzuna_app_id, "app_key": ctx.settings.adzuna_app_key, "what": term,
                          "results_per_page": 50, "max_days_old": days_window(ctx.settings), "sort_by": "date",
                          "content-type": "application/json"}
                try:
                    data = ctx.client.get_json(url, params=params, respect_robots=False)
                except FetchError as exc:
                    errors.append(f"{country}/{term}: {exc.kind}")  # never the URL: it carries the key
                    stop = exc.kind in ("AUTH_REQUIRED", "BLOCKED", "RATE_LIMITED")
                    continue
                results = data.get("results") if isinstance(data, dict) else None
                if not isinstance(results, list):
                    errors.append(f"{country}/{term}: unexpected structure")
                    continue
                ok += 1
                for item in results:
                    if not isinstance(item, dict):
                        continue
                    try:
                        job = self.to_raw(item, url)
                    except Exception:
                        ctx.record_parser_error(self.name)
                        continue
                    key = job.source_job_id or job.url
                    if not job.title or key in seen:
                        continue
                    seen.add(key)
                    if ctx.prefilter(job.title, True):
                        out.jobs.append(job)
                    else:
                        out.prefiltered_out += 1
        out.details = {"queries_ok": ok, "errors": errors[:10], "countries": ctx.settings.adzuna_countries}
        if out.jobs:
            ctx.snapshot(self.name, [j.snapshot() for j in out.jobs])
        if ok == 0:
            out.status, out.error = "FAILED", errors[0] if errors else "no successful requests"
        elif errors:
            out.status = "PARTIAL"
        return out

    @staticmethod
    def to_raw(item: dict, source_url: str) -> RawJob:
        posted = parse_datetime(item.get("created"))
        url = item.get("redirect_url")
        salary = None
        if item.get("salary_min") or item.get("salary_max"):
            salary = f"{item.get('salary_min') or ''}–{item.get('salary_max') or ''}".strip("–")
        contract = " ".join(x for x in (item.get("contract_time"), item.get("contract_type")) if x)
        return RawJob(
            source_type="aggregator", source_name="adzuna", source_url=source_url.split("?")[0],
            title=html_to_text(item.get("title")), company=(item.get("company") or {}).get("display_name"),
            url=url, apply_url=url, description=html_to_text(item.get("description")),
            location_raw=(item.get("location") or {}).get("display_name") or "",
            employment_type=contract or None, salary=salary, posted_at=posted, posted_at_reliable=posted is not None,
            native_id=_link_native(url), source_job_id=f"adzuna:{item['id']}" if item.get("id") else None,
            extraction_method="api", geo_context=True,
        )


class JoobleBackend(Backend):
    """Jooble job search API (https://jooble.org/api/about, free key). One POST per term and location;
    useful for countries the other feeds don't cover (Tunisia, Maghreb, Canada in French)."""

    name = "jooble"
    phase = "extraction"
    source_type = "aggregator"
    min_interval_hours = 4
    terms = ["GIS", "geospatial", "geomatics", "LiDAR", "cartographer", "SIG", "géomatique", "topographe",
             "télédétection"]

    def enabled(self, ctx):
        ok, reason = super().enabled(ctx)
        if ok and not ctx.settings.jooble_api_key:
            return False, "JOOBLE_API_KEY not set"
        return ok, reason

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        api = f"https://jooble.org/api/{ctx.settings.jooble_api_key}"
        locations = ctx.settings.jooble_locations or ctx.settings.preferred_locations or [""]
        errors, ok, seen = [], 0, set()
        stop = False
        for location in locations:
            for term in self.terms:
                if stop or ctx.out_of_time(200):
                    break
                try:
                    response = ctx.client.post(api, json={"keywords": term, "location": location, "page": 1},
                                               headers={"Accept": "application/json"}, respect_robots=False,
                                               detect_challenge=False)
                    data = response.json()
                except FetchError as exc:
                    errors.append(f"{location or 'any'}/{term}: {exc.kind}")  # never the URL: it carries the key
                    stop = exc.kind in ("AUTH_REQUIRED", "BLOCKED", "RATE_LIMITED")
                    continue
                except ValueError:
                    errors.append(f"{location or 'any'}/{term}: non-JSON response")
                    continue
                jobs = data.get("jobs") if isinstance(data, dict) else None
                if not isinstance(jobs, list):
                    errors.append(f"{location or 'any'}/{term}: unexpected structure")
                    continue
                ok += 1
                for item in jobs:
                    if not isinstance(item, dict):
                        continue
                    try:
                        job = self.to_raw(item)
                    except Exception:
                        ctx.record_parser_error(self.name)
                        continue
                    key = job.source_job_id or job.url
                    if not job.title or key in seen:
                        continue
                    seen.add(key)
                    if ctx.prefilter(job.title, True):
                        out.jobs.append(job)
                    else:
                        out.prefiltered_out += 1
        out.details = {"queries_ok": ok, "errors": errors[:10], "locations": locations}
        if out.jobs:
            ctx.snapshot(self.name, [j.snapshot() for j in out.jobs])
        if ok == 0:
            out.status, out.error = "FAILED", errors[0] if errors else "no successful requests"
        elif errors:
            out.status = "PARTIAL"
        return out

    @staticmethod
    def to_raw(item: dict) -> RawJob:
        posted = parse_datetime(item.get("updated"))
        url = item.get("link")
        return RawJob(
            source_type="aggregator", source_name="jooble", source_url="https://jooble.org/api",
            title=html_to_text(item.get("title")), company=item.get("company") or None, url=url, apply_url=url,
            description=html_to_text(item.get("snippet")), location_raw=item.get("location") or "",
            employment_type=item.get("type") or None, salary=item.get("salary") or None, posted_at=posted,
            posted_at_reliable=posted is not None, native_id=_link_native(url),
            source_job_id=f"jooble:{item['id']}" if item.get("id") else None, extraction_method="api",
            geo_context=True,
        )


class JSearchBackend(Backend):
    """JSearch on RapidAPI: Google for Jobs results (LinkedIn, Indeed, Glassdoor and employer sites) with full
    descriptions. The free tier is 200 requests a month, so the configured "query@country" list rotates and
    only JSEARCH_REQUESTS_PER_RUN queries run per execution (one per 4-hourly run = ~180/month)."""

    name = "jsearch"
    phase = "extraction"
    source_type = "aggregator"
    api = "https://jsearch.p.rapidapi.com/search"
    host = "jsearch.p.rapidapi.com"

    def enabled(self, ctx):
        ok, reason = super().enabled(ctx)
        if ok and not ctx.settings.jsearch_api_key:
            return False, "JSEARCH_API_KEY not set"
        if ok and not (ctx.settings.jsearch_queries and ctx.settings.jsearch_requests_per_run):
            return False, "no JSearch queries or JSEARCH_REQUESTS_PER_RUN=0"
        return ok, reason

    @staticmethod
    def date_posted(settings) -> str:
        days = days_window(settings)
        return "today" if days <= 1 else "3days" if days <= 3 else "week" if days <= 7 else "month"

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        queries = ctx.settings.jsearch_queries
        cursor = ctx.cursor(self.name)
        start = int(cursor.get("index", 0)) % len(queries)
        n = min(ctx.settings.jsearch_requests_per_run, len(queries))
        headers = {"X-RapidAPI-Key": ctx.settings.jsearch_api_key, "X-RapidAPI-Host": self.host}
        errors, ok, seen = [], 0, set()
        for offset in range(n):
            if ctx.out_of_time(200):
                break
            spec = queries[(start + offset) % len(queries)]
            cursor["index"] = (start + offset + 1) % len(queries)
            query, _, country = spec.partition("@")
            params = {"query": query.strip(), "page": 1, "num_pages": 1, "date_posted": self.date_posted(ctx.settings)}
            if country.strip():
                params["country"] = country.strip().lower()
            try:
                data = ctx.client.get_json(self.api, params=params, headers=headers, respect_robots=False)
            except FetchError as exc:
                errors.append(f"{spec}: {exc.kind}")
                if exc.kind in ("AUTH_REQUIRED", "BLOCKED", "RATE_LIMITED"):
                    break  # 429 here means the monthly quota is gone: stop spending requests
                continue
            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list):
                errors.append(f"{spec}: unexpected structure")
                continue
            ok += 1
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    job = self.to_raw(item)
                except Exception:
                    ctx.record_parser_error(self.name)
                    continue
                key = job.source_job_id or job.url
                if not job.title or key in seen:
                    continue
                seen.add(key)
                if ctx.prefilter(job.title, True):
                    out.jobs.append(job)
                else:
                    out.prefiltered_out += 1
        out.details = {"queries_ok": ok, "errors": errors[:10], "next_index": cursor.get("index")}
        if out.jobs:
            ctx.snapshot(self.name, [j.snapshot() for j in out.jobs])
        if ok == 0:
            out.status, out.error = "FAILED", errors[0] if errors else "no successful requests"
        elif errors:
            out.status = "PARTIAL"
        return out

    @staticmethod
    def to_raw(item: dict) -> RawJob:
        posted = parse_datetime(item.get("job_posted_at_datetime_utc"))
        if posted is None and item.get("job_posted_at_timestamp"):
            posted = datetime.fromtimestamp(int(item["job_posted_at_timestamp"]), tz=timezone.utc)
        url = item.get("job_apply_link") or item.get("job_google_link")
        location = ", ".join(str(x) for x in (item.get("job_city"), item.get("job_state"), item.get("job_country")) if x)
        salary = None
        if item.get("job_min_salary") or item.get("job_max_salary"):
            salary = (f"{item.get('job_salary_currency') or ''} {item.get('job_min_salary') or ''}–"
                      f"{item.get('job_max_salary') or ''} {item.get('job_salary_period') or ''}").strip()
        remote = item.get("job_is_remote")
        return RawJob(
            source_type="aggregator", source_name=f"jsearch:{item.get('job_publisher') or 'google'}",
            source_url="https://jsearch.p.rapidapi.com/search", title=html_to_text(item.get("job_title")),
            company=item.get("employer_name") or None, url=url, apply_url=url,
            description=html_to_text(item.get("job_description")), location_raw=location,
            remote_flag=bool(remote) if remote is not None else None,
            employment_type=item.get("job_employment_type") or None, salary=salary, posted_at=posted,
            posted_at_reliable=posted is not None, native_id=_link_native(url),
            source_job_id=f"jsearch:{item['job_id']}" if item.get("job_id") else None, extraction_method="api",
            geo_context=True,
        )
