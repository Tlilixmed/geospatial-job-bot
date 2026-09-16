"""Greenhouse, Lever and Ashby public job-board APIs.

Endpoints (public, unauthenticated, intended for programmatic job-board embedding):
  Greenhouse  GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs          (list)
              GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{id}     (detail)
              GET https://boards-api.greenhouse.io/v1/boards/{token}                (board name)
  Lever       GET https://api.lever.co/v0/postings/{slug}?mode=json  (EU: api.eu.lever.co)
  Ashby       GET https://api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true
"""
from __future__ import annotations

import re

from ...models import RawJob
from ...utils.dates import parse_datetime
from ...utils.http import FetchError
from ...utils.text import html_to_text, prettify_slug
from ..base import RunContext
from .base import ATSAdapter, BoardResult
from .detect import UUID_RE, BoardRef, detect


class GreenhouseAdapter(ATSAdapter):
    ats = "greenhouse"
    base = "https://boards-api.greenhouse.io/v1/boards"

    def _detail_to_raw(self, data: dict, slug: str, company: str, geo_context: bool) -> RawJob:
        location = (data.get("location") or {}).get("name") or ""
        offices = [o.get("name") for o in data.get("offices") or [] if isinstance(o, dict) and o.get("name")]
        if not location and offices:
            location = "; ".join(offices)
        first_published = parse_datetime(data.get("first_published"))
        posted = first_published or parse_datetime(data.get("updated_at"))
        job_id = str(data.get("id"))
        url = data.get("absolute_url") or f"https://job-boards.greenhouse.io/{slug}/jobs/{job_id}"
        return RawJob(
            source_type="ats", source_name="greenhouse", source_url=f"{self.base}/{slug}/jobs/{job_id}",
            title=(data.get("title") or "").strip(), company=company, url=url, apply_url=url,
            description=html_to_text(data.get("content")), location_raw=location,
            posted_at=posted, posted_at_reliable=first_published is not None,
            native_id=f"greenhouse:{job_id}", source_job_id=job_id, extraction_method="api",
            geo_context=geo_context,
        )

    def fetch_board(self, ctx: RunContext, ref: BoardRef, *, geo_context: bool, variant=None, company_hint=None):
        client = ctx.client
        try:
            payload = client.get_json(f"{self.base}/{ref.slug}/jobs", respect_robots=False)
        except FetchError as exc:
            return self.error_result(exc)
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            return BoardResult("SCHEMA_MISMATCH", error="missing 'jobs' list")
        listed = payload["jobs"]
        result = BoardResult("VALID", listed=len(listed))
        candidates = []
        for item in listed:
            title = (item.get("title") or "").strip() if isinstance(item, dict) else ""
            if title and item.get("id") is not None and ctx.prefilter(title, geo_context):
                candidates.append(item)
            else:
                result.prefiltered_out += 1
        if not candidates:
            return result
        company = company_hint
        if not company:
            try:
                meta = client.get_json(f"{self.base}/{ref.slug}", respect_robots=False)
                company = (meta or {}).get("name") if isinstance(meta, dict) else None
            except FetchError:
                company = None
        company = company or prettify_slug(ref.slug)
        result.company = company
        budget = ctx.settings.max_detail_fetches_per_board
        for index, item in enumerate(candidates):
            if index < budget and not ctx.out_of_time(120):
                try:
                    detail = client.get_json(f"{self.base}/{ref.slug}/jobs/{item['id']}", respect_robots=False)
                    if isinstance(detail, dict) and detail.get("title"):
                        result.jobs.append(self._detail_to_raw(detail, ref.slug, company, geo_context))
                        continue
                except FetchError:
                    result.detail_errors += 1
            # title-only fallback (detail budget exhausted or detail failed)
            result.jobs.append(self._detail_to_raw(dict(item, content=""), ref.slug, company, geo_context))
        return result

    def fetch_job(self, ctx: RunContext, url: str):
        native, board = detect(url)
        if not board or board.ats != "greenhouse" or not native:
            return None
        job_id = native.split(":")[1]
        detail = ctx.client.get_json(f"{self.base}/{board.slug}/jobs/{job_id}", respect_robots=False)
        if not isinstance(detail, dict) or not detail.get("title"):
            return []
        return [self._detail_to_raw(detail, board.slug, prettify_slug(board.slug), False)]


class LeverAdapter(ATSAdapter):
    ats = "lever"
    hosts = {"global": "https://api.lever.co", "eu": "https://api.eu.lever.co"}

    def _to_raw(self, item: dict, slug: str, api_base: str, company: str, geo_context: bool) -> RawJob:
        cats = item.get("categories") or {}
        parts = [item.get("descriptionPlain") or html_to_text(item.get("description"))]
        for block in item.get("lists") or []:
            if isinstance(block, dict):
                parts.append(f"{block.get('text') or ''}\n{html_to_text(block.get('content'))}")
        parts.append(item.get("additionalPlain") or html_to_text(item.get("additional")))
        salary = None
        rng = item.get("salaryRange") or {}
        if isinstance(rng, dict) and (rng.get("min") or rng.get("max")):
            salary = f"{rng.get('currency') or ''} {rng.get('min') or ''}–{rng.get('max') or ''} {rng.get('interval') or ''}".strip()
        all_locations = cats.get("allLocations") or []
        location = cats.get("location") or "; ".join(x for x in all_locations if isinstance(x, str))
        created = parse_datetime(item.get("createdAt"))
        job_id = str(item.get("id"))
        return RawJob(
            source_type="ats", source_name="lever", source_url=f"{api_base}/v0/postings/{slug}/{job_id}",
            title=(item.get("text") or "").strip(), company=company, url=item.get("hostedUrl"),
            apply_url=item.get("applyUrl") or item.get("hostedUrl"),
            description="\n\n".join(p for p in parts if p), location_raw=location or "",
            workplace_type=item.get("workplaceType"), employment_type=cats.get("commitment"), salary=salary,
            posted_at=created, posted_at_reliable=created is not None, native_id=f"lever:{job_id.lower()}",
            source_job_id=job_id, extraction_method="api", geo_context=geo_context,
        )

    def fetch_board(self, ctx: RunContext, ref: BoardRef, *, geo_context: bool, variant=None, company_hint=None):
        order = ["eu", "global"] if variant == "eu" else ["global", "eu"]
        payload = None
        used = None
        last_error: FetchError | None = None
        for region in order:
            try:
                payload = ctx.client.get_json(f"{self.hosts[region]}/v0/postings/{ref.slug}",
                                              params={"mode": "json"}, respect_robots=False)
                used = region
                break
            except FetchError as exc:
                last_error = exc
                if exc.kind != "NOT_FOUND":
                    return self.error_result(exc)
        if payload is None:
            return self.error_result(last_error) if last_error else BoardResult("ERROR", error="no response")
        if not isinstance(payload, list):
            return BoardResult("SCHEMA_MISMATCH", error="expected a JSON list", variant=used)
        company = company_hint or prettify_slug(ref.slug)
        result = BoardResult("VALID", listed=len(payload), variant=used, company=company)
        for item in payload:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            title = (item.get("text") or "").strip()
            if not ctx.prefilter(title, geo_context):
                result.prefiltered_out += 1
                continue
            result.jobs.append(self._to_raw(item, ref.slug, self.hosts[used], company, geo_context))
        return result

    def fetch_job(self, ctx: RunContext, url: str):
        native, board = detect(url)
        if not board or board.ats != "lever" or not native:
            return None
        job_id = native.split(":")[1]
        base = self.hosts["eu"] if ".eu." in url else self.hosts["global"]
        item = ctx.client.get_json(f"{base}/v0/postings/{board.slug}/{job_id}", respect_robots=False)
        if not isinstance(item, dict) or not item.get("id"):
            return []
        return [self._to_raw(item, board.slug, base, prettify_slug(board.slug), False)]


class AshbyAdapter(ATSAdapter):
    ats = "ashby"
    base = "https://api.ashbyhq.com/posting-api/job-board"

    def fetch_board(self, ctx: RunContext, ref: BoardRef, *, geo_context: bool, variant=None, company_hint=None):
        try:
            payload = ctx.client.get_json(f"{self.base}/{ref.slug}", params={"includeCompensation": "true"},
                                          respect_robots=False)
        except FetchError as exc:
            return self.error_result(exc)
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            return BoardResult("SCHEMA_MISMATCH", error="missing 'jobs' list")
        jobs = payload["jobs"]
        company = company_hint or prettify_slug(ref.slug)
        if not jobs:
            return BoardResult("EMPTY_UNVERIFIED", error="Ashby returned no jobs (board may not exist)",
                               company=company)
        result = BoardResult("VALID", listed=len(jobs), company=company)
        for item in jobs:
            if not isinstance(item, dict) or item.get("isListed") is False:
                continue
            title = (item.get("title") or "").strip()
            if not ctx.prefilter(title, geo_context):
                result.prefiltered_out += 1
                continue
            job_url = item.get("jobUrl") or ""
            m = re.search(UUID_RE, job_url)
            job_id = str(item.get("id") or (m.group(0) if m else "")).lower()
            locations = [item.get("location") or ""]
            for sec in item.get("secondaryLocations") or []:
                if isinstance(sec, dict) and sec.get("location"):
                    locations.append(sec["location"])
            address = ((item.get("address") or {}).get("postalAddress") or {})
            location_raw = "; ".join(x for x in locations if x)
            if not location_raw and address:
                location_raw = ", ".join(x for x in (address.get("addressLocality"), address.get("addressRegion"),
                                                     address.get("addressCountry")) if x)
            comp = item.get("compensation") or {}
            salary = None
            if isinstance(comp, dict):
                salary = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
            published = parse_datetime(item.get("publishedAt"))
            result.jobs.append(RawJob(
                source_type="ats", source_name="ashby", source_url=f"{self.base}/{ref.slug}",
                title=title, company=company, url=job_url or None, apply_url=item.get("applyUrl") or job_url or None,
                description=item.get("descriptionPlain") or html_to_text(item.get("descriptionHtml")),
                location_raw=location_raw, remote_flag=item.get("isRemote") if isinstance(item.get("isRemote"), bool) else None,
                workplace_type=item.get("workplaceType"), employment_type=item.get("employmentType"), salary=salary,
                posted_at=published, posted_at_reliable=published is not None,
                native_id=f"ashby:{job_id}" if job_id else None, source_job_id=job_id or None,
                extraction_method="api", geo_context=geo_context,
            ))
        return result
