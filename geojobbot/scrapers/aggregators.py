"""Keyless aggregator APIs.

workable   jobs.workable.com: Workable's public job search across all its customers, with full text and dates. Finds
           employers nobody could list by hand (Gulf and Canadian consultancies among them). No key.
freehire   freehire.dev: an open-source search engine over jobs read from company ATS boards and partner feeds (about four
           million open postings), with full descriptions. No key. Queried by title with geospatial terms, newest first,
           a few terms per run on rotation. Its own AI fields (visa_sponsorship, relocation) are deliberately not trusted:
           the posting text goes through this bot's own wording rules like every other source.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from ..models import BackendOutput, RawJob
from ..utils.dates import parse_datetime
from ..utils.http import FetchError
from ..utils.location import ISO_PREFIX
from ..utils.text import html_to_text
from .base import Backend, RunContext
from .feeds import days_window, rotating_batch

log = logging.getLogger(__name__)


class WorkableSearchBackend(Backend):
    name = "workable_search"
    phase = "extraction"
    source_type = "ats"
    min_interval_hours = 6
    api = "https://jobs.workable.com/api/v1/jobs"
    terms = ["GIS", "geospatial", "geomatics", "surveyor", "cartographer", "lidar", "photogrammetry", "remote sensing", "SIG", "géomatique"]
    queries_per_run = 4

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        errors, ok, seen = [], 0, set()
        batch = rotating_batch(ctx, self.name, [("all", term) for term in self.terms], self.queries_per_run)
        for term in batch.get("all", []):
            if ctx.out_of_time(200):
                break
            try:
                data = ctx.client.get_json(self.api, params={"query": term, "day_range": 30})
            except FetchError as exc:
                errors.append(f"{term}: {exc.kind}")
                if exc.kind in ("BLOCKED", "RATE_LIMITED"):
                    break
                continue
            items = data.get("jobs") if isinstance(data, dict) else None
            if not isinstance(items, list):
                errors.append(f"{term}: unexpected structure")
                continue
            ok += 1
            for item in items:
                if not isinstance(item, dict) or not item.get("id") or item["id"] in seen:
                    continue
                seen.add(item["id"])
                try:
                    job = self.to_raw(item)
                except Exception:
                    ctx.record_parser_error(self.name)
                    continue
                if job.title and ctx.prefilter(job.title, False):  # the search reads the whole text: the title must qualify
                    out.jobs.append(job)
                else:
                    out.prefiltered_out += 1
        out.details = {"queries_ok": ok, "errors": errors[:10]}
        if out.jobs:
            ctx.snapshot(self.name, [j.snapshot() for j in out.jobs])
        if ok == 0 and errors:
            out.status, out.error = "FAILED", errors[0]
        elif errors:
            out.status = "PARTIAL"
        return out

    @staticmethod
    def to_raw(item: dict) -> RawJob:
        place = item.get("location") or {}
        location = ", ".join(p for p in (place.get("city"), place.get("subregion"), place.get("countryName")) if p)
        parts = [item.get("description"), item.get("requirementsSection"), item.get("benefitsSection")]
        posted = parse_datetime(item.get("created"))
        url = item.get("url")
        workplace = item.get("workplace") or None
        return RawJob(
            source_type="ats", source_name="workable_search", source_url="https://jobs.workable.com/api/v1/jobs",
            title=html_to_text(item.get("title")), company=(item.get("company") or {}).get("title") or None, url=url, apply_url=url,
            description="\n\n".join(html_to_text(p) for p in parts if p), location_raw=location,
            remote_flag=True if workplace == "remote" else None, workplace_type=workplace, employment_type=item.get("employmentType") or None,
            posted_at=posted, posted_at_reliable=posted is not None, source_job_id=f"workable_search:{item['id']}", extraction_method="api",
            geo_context=False,
        )


class FreehireBackend(Backend):
    name = "freehire"
    phase = "extraction"
    source_type = "aggregator"
    min_interval_hours = 4
    api = "https://freehire.dev/api/v1/jobs/search"
    terms = ["gis", "geospatial", "geomatics", "lidar", "remote sensing", "cartographer", "surveyor", "photogrammetry",
             "SIG", "géomatique", "topographe", "cartographe"]
    queries_per_run = 4
    page_size = 100

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        errors, ok, seen, too_old = [], 0, set(), 0
        oldest = ctx.now - timedelta(days=days_window(ctx.settings))
        batch = rotating_batch(ctx, self.name, [("all", term) for term in self.terms], self.queries_per_run)
        for term in batch.get("all", []):
            if ctx.out_of_time(200):
                break
            try:
                data = ctx.client.get(self.api, params={"q": term, "q_fields": "title", "sort": "posted_at", "limit": self.page_size},
                                      respect_robots=False, detect_challenge=False).json()
            except FetchError as exc:
                errors.append(f"{term}: {exc.kind}")
                if exc.kind in ("BLOCKED", "RATE_LIMITED"):
                    break
                continue
            except ValueError:
                errors.append(f"{term}: non-JSON response")
                continue
            items = data.get("data") if isinstance(data, dict) else None
            if not isinstance(items, list):
                errors.append(f"{term}: unexpected structure")
                continue
            ignored = [p.get("param") for p in ((data.get("meta") or {}).get("ignored_params") or [])]
            if "q" in ignored:  # the API drops filters it does not know instead of refusing: never take that for a result
                errors.append(f"{term}: the search parameter was ignored")
                continue
            dropped = sorted(set(ignored) & {"q_fields", "sort", "limit"})
            if dropped:  # still results for the term, but no longer the newest hundred by title: say so (PARTIAL)
                errors.append(f"{term}: ignored by the API: {', '.join(dropped)}")
            ok += 1
            for item in items:
                if not isinstance(item, dict) or item.get("closed_at"):
                    continue
                key = item.get("public_slug") or item.get("url")
                if not key or key in seen:
                    continue
                seen.add(key)
                posted = parse_datetime(item.get("posted_at") or item.get("created_at"))
                if posted is not None and posted < oldest:
                    too_old += 1
                    continue
                try:
                    job = self.to_raw(item, posted)
                except Exception:
                    ctx.record_parser_error(self.name)
                    continue
                if job.title and ctx.prefilter(job.title, True):
                    out.jobs.append(job)
                else:
                    out.prefiltered_out += 1
        out.details = {"queries_ok": ok, "older_than_window": too_old, "errors": errors[:10]}
        if out.jobs:
            ctx.snapshot(self.name, [j.snapshot() for j in out.jobs])
        if ok == 0:
            out.status, out.error = ("FAILED", errors[0]) if errors else ("SKIPPED", "no query ran")
        elif errors:
            out.status = "PARTIAL"
        return out

    @staticmethod
    def to_raw(item: dict, posted) -> RawJob:
        enrichment = item.get("enrichment") or {}
        location = (item.get("location") or "").strip()
        countries = [ISO_PREFIX.get(str(c).upper()) for c in item.get("countries") or []]
        country = next((c for c in countries if c), None)
        if country and country.lower() not in location.lower():
            location = f"{location}, {country}".strip(", ")
        salary = None
        if enrichment.get("salary_min") or enrichment.get("salary_max"):
            salary = (f"{enrichment.get('salary_currency') or ''} {enrichment.get('salary_min') or ''}–{enrichment.get('salary_max') or ''} "
                      f"{enrichment.get('salary_period') or ''}").strip()
        url = (item.get("url") or "").split("?utm_")[0] or f"https://freehire.dev/jobs/{item.get('public_slug')}"
        return RawJob(
            source_type="aggregator", source_name=f"freehire:{item.get('source') or 'unknown'}", source_url="https://freehire.dev/api/v1/jobs/search",
            title=html_to_text(item.get("title")), company=item.get("company") or None, url=url, apply_url=url,
            description=html_to_text(item.get("description")), location_raw=location,
            remote_flag=True if item.get("work_mode") == "remote" else None, workplace_type=item.get("work_mode") or None,
            employment_type=(enrichment.get("employment_type") or "").replace("_", " ") or None, salary=salary, posted_at=posted,
            posted_at_reliable=posted is not None, source_job_id=f"freehire:{item.get('public_slug')}", extraction_method="api",
            geo_context=True,
        )
