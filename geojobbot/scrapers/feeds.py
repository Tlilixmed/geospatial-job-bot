"""Legitimate public job feeds.

  Remotive   https://remotive.com/api/remote-jobs?search=...   (asks for low request frequency)
  Jobicy     https://jobicy.com/api/v2/remote-jobs?tag=...
  Himalayas  https://himalayas.app/jobs/api/search?q=...        (attribution to himalayas.app requested)
  Arbeitnow  https://www.arbeitnow.com/api/job-board-api?page=N
  RemoteOK   https://remoteok.com/api                           (attribution requested)
  RSS/Atom   any feed configured in sources.toml [[rss_feeds]]
  USAJOBS    https://data.usajobs.gov/api/search (free API key + email, optional)
  JobSpy     python-jobspy library (optional, supplementary)

Each feed runs at most once per ``min_interval_hours`` (tracked in R2 state) to respect the
providers' rate expectations. A non-empty response that parses to zero jobs is SCHEMA_MISMATCH,
never a silent "no jobs".
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timezone

from ..models import BackendOutput, RawJob
from ..utils.dates import parse_datetime
from ..utils.http import FetchError
from ..utils.text import html_to_text
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


class RssFeedBackend(Backend):
    """Generic RSS/Atom feeds listed in sources.toml [[rss_feeds]] (name, url, source_type, geospatial)."""

    name = "rss_feeds"
    phase = "extraction"
    source_type = "feed"

    def __init__(self, feeds: list[dict]):
        self.feeds = [f for f in feeds if isinstance(f, dict) and f.get("url")]

    def enabled(self, ctx):
        ok, reason = super().enabled(ctx)
        if ok and not self.feeds:
            return False, "no rss_feeds configured"
        return ok, reason

    @staticmethod
    def parse(body: bytes, feed: dict) -> list[RawJob]:
        root = ET.fromstring(body)
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
            desc = find("description", "{http://www.w3.org/2005/Atom}summary", "{http://www.w3.org/2005/Atom}content")
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

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        errors, ok = [], 0
        for feed in self.feeds:
            try:
                body = ctx.client.get(feed["url"], detect_challenge=False).content
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
                if ctx.prefilter(job.title, job.geo_context):
                    out.jobs.append(job)
                else:
                    out.prefiltered_out += 1
        out.details = {"feeds": len(self.feeds), "ok": ok, "errors": errors}
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
                                           params={"Keyword": keyword, "ResultsPerPage": 100, "DatePosted": 7})
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

    def run(self, ctx: RunContext) -> BackendOutput:
        from jobspy import scrape_jobs

        out = BackendOutput()
        cursor = ctx.cursor(self.name)
        start = int(cursor.get("index", 0))
        n = min(ctx.settings.jobspy_terms_per_run, len(self.terms))
        errors, ok = [], 0
        for offset in range(n):
            term = self.terms[(start + offset) % len(self.terms)]
            cursor["index"] = (start + offset + 1) % len(self.terms)
            for location in ctx.settings.jobspy_locations:
                if ctx.out_of_time(300):
                    break
                try:
                    frame = scrape_jobs(site_name=ctx.settings.jobspy_sites, search_term=term, location=location,
                                        results_wanted=ctx.settings.jobspy_results_wanted, hours_old=72,
                                        country_indeed=ctx.settings.jobspy_country_indeed, verbose=0)
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
        out.details = {"searches_ok": ok, "errors": errors[:10], "sites": ctx.settings.jobspy_sites}
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
