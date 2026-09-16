"""Additional public ATS endpoints.

  SmartRecruiters GET https://api.smartrecruiters.com/v1/companies/{id}/postings[/{postingId}]
  Workable        GET https://apply.workable.com/api/v1/widget/accounts/{subdomain}?details=true
  Recruitee       GET https://{company}.recruitee.com/api/offers/
  Personio        GET https://{company}.jobs.personio.de/xml
  Workday         POST https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
                  GET  https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{externalPath}
The Workday endpoints power the public career site itself; robots.txt is still honoured for them.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ...models import RawJob
from ...utils.dates import parse_datetime
from ...utils.http import FetchError
from ...utils.text import html_to_text, prettify_slug
from ..base import RunContext
from .base import ATSAdapter, BoardResult
from .detect import BoardRef, detect

WORKDAY_SEARCH_TERMS = ["GIS", "geospatial", "mapping", "LiDAR", "cartographer", "surveying", "remote sensing",
                        "photogrammetry", "geomatics", "spatial"]


class SmartRecruitersAdapter(ATSAdapter):
    ats = "smartrecruiters"
    base = "https://api.smartrecruiters.com/v1/companies"

    def _detail(self, ctx, slug: str, posting_id: str) -> dict | None:
        try:
            data = ctx.client.get_json(f"{self.base}/{slug}/postings/{posting_id}", respect_robots=False)
            return data if isinstance(data, dict) else None
        except FetchError:
            return None

    def _to_raw(self, item: dict, detail: dict | None, slug: str, geo_context: bool) -> RawJob:
        loc = item.get("location") or {}
        location_raw = loc.get("fullLocation") or ", ".join(
            x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
        description = ""
        apply_url = None
        if detail:
            sections = ((detail.get("jobAd") or {}).get("sections") or {})
            description = "\n\n".join(html_to_text((sections.get(k) or {}).get("text"))
                                      for k in ("jobDescription", "qualifications", "additionalInformation"))
            apply_url = detail.get("applyUrl")
        posting_id = str(item.get("id"))
        company = (item.get("company") or {}).get("name") or prettify_slug(slug)
        url = (detail or {}).get("postingUrl") or f"https://jobs.smartrecruiters.com/{slug}/{posting_id}"
        released = parse_datetime(item.get("releasedDate"))
        return RawJob(
            source_type="ats", source_name="smartrecruiters", source_url=f"{self.base}/{slug}/postings/{posting_id}",
            title=(item.get("name") or "").strip(), company=company, url=url, apply_url=apply_url or url,
            description=description.strip(), location_raw=location_raw,
            remote_flag=loc.get("remote") if isinstance(loc.get("remote"), bool) else None,
            employment_type=(item.get("typeOfEmployment") or {}).get("label"),
            posted_at=released, posted_at_reliable=released is not None,
            native_id=f"smartrecruiters:{posting_id}", source_job_id=posting_id, extraction_method="api",
            geo_context=geo_context,
        )

    def fetch_board(self, ctx: RunContext, ref: BoardRef, *, geo_context: bool, variant=None, company_hint=None):
        items, offset, total = [], 0, None
        try:
            for _ in range(10):
                page = ctx.client.get_json(f"{self.base}/{ref.slug}/postings",
                                           params={"limit": 100, "offset": offset}, respect_robots=False)
                if not isinstance(page, dict) or not isinstance(page.get("content"), list):
                    return BoardResult("SCHEMA_MISMATCH", error="missing 'content' list")
                total = int(page.get("totalFound") or 0)
                items.extend(page["content"])
                offset += 100
                if offset >= total or not page["content"]:
                    break
        except FetchError as exc:
            return self.error_result(exc)
        if not items:
            return BoardResult("EMPTY_UNVERIFIED", error="SmartRecruiters returns 200/empty for unknown companies")
        result = BoardResult("VALID", listed=len(items))
        budget = ctx.settings.max_detail_fetches_per_board
        fetched = 0
        for item in items:
            title = (item.get("name") or "").strip()
            if not item.get("id") or not ctx.prefilter(title, geo_context):
                result.prefiltered_out += 1
                continue
            detail = None
            if fetched < budget and not ctx.out_of_time(120):
                detail = self._detail(ctx, ref.slug, str(item["id"]))
                fetched += 1
            raw = self._to_raw(item, detail, ref.slug, geo_context)
            result.company = result.company or raw.company
            result.jobs.append(raw)
        return result

    def fetch_job(self, ctx: RunContext, url: str):
        native, board = detect(url)
        if not board or board.ats != "smartrecruiters" or not native:
            return None
        posting_id = native.split(":")[1]
        detail = self._detail(ctx, board.slug, posting_id)
        if not detail:
            return []
        item = {"id": posting_id, "name": detail.get("name"), "location": detail.get("location"),
                "company": detail.get("company"), "releasedDate": detail.get("releasedDate"),
                "typeOfEmployment": detail.get("typeOfEmployment")}
        return [self._to_raw(item, detail, board.slug, False)]


class WorkableAdapter(ATSAdapter):
    ats = "workable"
    base = "https://apply.workable.com/api/v1/widget/accounts"

    def fetch_board(self, ctx: RunContext, ref: BoardRef, *, geo_context: bool, variant=None, company_hint=None):
        try:
            payload = ctx.client.get_json(f"{self.base}/{ref.slug}", params={"details": "true"}, respect_robots=False)
        except FetchError as exc:
            return self.error_result(exc)
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            return BoardResult("SCHEMA_MISMATCH", error="missing 'jobs' list")
        company = payload.get("name") or company_hint or prettify_slug(ref.slug)
        result = BoardResult("VALID", listed=len(payload["jobs"]), company=company)
        for item in payload["jobs"]:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            if not ctx.prefilter(title, geo_context):
                result.prefiltered_out += 1
                continue
            shortcode = str(item.get("shortcode") or item.get("code") or "").upper()
            location_raw = ", ".join(x for x in (item.get("city"), item.get("state"), item.get("country")) if x)
            if not location_raw:
                locs = [", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
                        for loc in item.get("locations") or [] if isinstance(loc, dict) and not loc.get("hidden")]
                location_raw = "; ".join(x for x in locs if x)
            published = parse_datetime(item.get("published_on") or item.get("created_at"))
            url = item.get("url") or item.get("shortlink") or (
                f"https://apply.workable.com/{ref.slug}/j/{shortcode}/" if shortcode else None)
            result.jobs.append(RawJob(
                source_type="ats", source_name="workable", source_url=f"{self.base}/{ref.slug}",
                title=title, company=company, url=url, apply_url=item.get("application_url") or url,
                description=html_to_text(item.get("description")), location_raw=location_raw,
                remote_flag=item.get("telecommuting") if isinstance(item.get("telecommuting"), bool) else None,
                employment_type=item.get("employment_type"), posted_at=published,
                posted_at_reliable=published is not None,
                native_id=f"workable:{shortcode}" if shortcode else None, source_job_id=shortcode or None,
                extraction_method="api", geo_context=geo_context,
            ))
        return result


class RecruiteeAdapter(ATSAdapter):
    ats = "recruitee"

    def fetch_board(self, ctx: RunContext, ref: BoardRef, *, geo_context: bool, variant=None, company_hint=None):
        api = f"https://{ref.slug}.recruitee.com/api/offers/"
        try:
            payload = ctx.client.get_json(api, respect_robots=False)
        except FetchError as exc:
            return self.error_result(exc)
        if not isinstance(payload, dict) or not isinstance(payload.get("offers"), list):
            return BoardResult("SCHEMA_MISMATCH", error="missing 'offers' list")
        result = BoardResult("VALID", listed=len(payload["offers"]))
        for item in payload["offers"]:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            if not ctx.prefilter(title, geo_context):
                result.prefiltered_out += 1
                continue
            company = item.get("company_name") or company_hint or prettify_slug(ref.slug)
            result.company = result.company or company
            offer_slug = str(item.get("slug") or item.get("id"))
            url = item.get("careers_url") or f"https://{ref.slug}.recruitee.com/o/{offer_slug}"
            location_raw = item.get("location") or ", ".join(x for x in (item.get("city"), item.get("country")) if x)
            work_mode = "remote" if item.get("remote") else ("hybrid" if item.get("hybrid") else None)
            published = parse_datetime(item.get("published_at") or item.get("created_at"))
            result.jobs.append(RawJob(
                source_type="ats", source_name="recruitee", source_url=api, title=title, company=company,
                url=url, apply_url=item.get("careers_apply_url") or url,
                description=html_to_text(f"{item.get('description') or ''}\n{item.get('requirements') or ''}"),
                location_raw=location_raw or "", workplace_type=work_mode,
                remote_flag=item.get("remote") if isinstance(item.get("remote"), bool) else None,
                employment_type=item.get("employment_type_code"), posted_at=published,
                posted_at_reliable=published is not None,
                native_id=f"recruitee:{ref.slug.lower()}:{offer_slug.lower()}", source_job_id=offer_slug,
                extraction_method="api", geo_context=geo_context,
            ))
        return result


class PersonioAdapter(ATSAdapter):
    ats = "personio"

    def fetch_board(self, ctx: RunContext, ref: BoardRef, *, geo_context: bool, variant=None, company_hint=None):
        api = f"https://{ref.slug}.jobs.personio.de/xml"
        try:
            response = ctx.client.get(api, respect_robots=False, detect_challenge=False)
        except FetchError as exc:
            return self.error_result(exc)
        body = response.content or b""
        if b"<workzag-jobs" not in body[:2000] and b"<position" not in body:
            # Personio redirects unknown companies to an HTML page instead of returning 404
            if b"<html" in body[:2000].lower():
                return BoardResult("INVALID", error="no Personio XML feed (HTML returned)", http_status=response.status_code)
            return BoardResult("EMPTY_UNVERIFIED", error="empty Personio feed")
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            return BoardResult("SCHEMA_MISMATCH", error=f"XML parse error: {exc}")
        positions = root.findall(".//position")
        company = company_hint or prettify_slug(ref.slug)
        result = BoardResult("VALID", listed=len(positions), company=company)
        for pos in positions:
            title = (pos.findtext("name") or "").strip()
            if not ctx.prefilter(title, geo_context):
                result.prefiltered_out += 1
                continue
            job_id = (pos.findtext("id") or "").strip()
            parts = []
            for desc in pos.findall(".//jobDescription"):
                parts.append(f"{desc.findtext('name') or ''}\n{html_to_text(desc.findtext('value'))}")
            created = parse_datetime(pos.findtext("createdAt"))
            url = f"https://{ref.slug}.jobs.personio.de/job/{job_id}" if job_id else None
            result.jobs.append(RawJob(
                source_type="ats", source_name="personio", source_url=api, title=title,
                company=(pos.findtext("subcompany") or "").strip() or company, url=url, apply_url=url,
                description="\n\n".join(parts), location_raw=(pos.findtext("office") or "").strip(),
                employment_type=(pos.findtext("employmentType") or "").strip() or None,
                posted_at=created, posted_at_reliable=created is not None,
                native_id=f"personio:{ref.slug.lower()}:{job_id}" if job_id else None, source_job_id=job_id or None,
                extraction_method="api", geo_context=geo_context,
            ))
        return result


class WorkdayAdapter(ATSAdapter):
    ats = "workday"

    @staticmethod
    def _parts(ref: BoardRef) -> tuple[str, str, str]:
        tenant, wd, site = ref.slug.split("/", 2)
        return tenant, wd, site

    def _detail(self, ctx, ref: BoardRef, external_path: str, geo_context: bool, fallback: dict) -> RawJob:
        tenant, wd, site = self._parts(ref)
        origin = f"https://{tenant}.{wd}.myworkdayjobs.com"
        api = f"{origin}/wday/cxs/{tenant}/{site}{external_path}"
        public_url = f"{origin}/{site}{external_path}"
        info, company = {}, None
        try:
            data = ctx.client.get_json(api)
            if isinstance(data, dict):
                info = data.get("jobPostingInfo") or {}
                company = (data.get("hiringOrganization") or {}).get("name")
        except FetchError:
            info = {}
        start = parse_datetime(info.get("startDate"))
        req_id = info.get("jobReqId") or next(iter(fallback.get("bulletFields") or []), None)
        return RawJob(
            source_type="ats", source_name="workday", source_url=api,
            title=(info.get("title") or fallback.get("title") or "").strip(),
            company=company or prettify_slug(tenant), url=info.get("externalUrl") or public_url,
            apply_url=info.get("externalUrl") or public_url, description=html_to_text(info.get("jobDescription")),
            location_raw=info.get("location") or fallback.get("locationsText") or "",
            workplace_type=info.get("remoteType"), employment_type=info.get("timeType"),
            posted_at=start, posted_at_reliable=start is not None,
            native_id=f"workday:{tenant.lower()}:{str(req_id).upper()}" if req_id else None,
            source_job_id=str(req_id) if req_id else None, extraction_method="api", geo_context=geo_context,
        )

    def fetch_board(self, ctx: RunContext, ref: BoardRef, *, geo_context: bool, variant=None, company_hint=None):
        try:
            tenant, wd, site = self._parts(ref)
        except ValueError:
            return BoardResult("INVALID", error="workday slug must be tenant/wdN/site")
        api = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
        postings: dict[str, dict] = {}
        listed_total = 0
        for term in WORKDAY_SEARCH_TERMS:
            if ctx.out_of_time(150):
                break
            try:
                payload = ctx.client.post(api, json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": term},
                                          headers={"Accept": "application/json"}, detect_challenge=False).json()
            except FetchError as exc:
                if exc.kind in ("NOT_FOUND", "HTTP_ERROR") and not postings:
                    return BoardResult("INVALID", error=f"Workday site not found ({exc.status})", http_status=exc.status)
                return self.error_result(exc) if not postings else BoardResult(
                    "VALID", error=str(exc), listed=listed_total)
            except ValueError:
                return BoardResult("SCHEMA_MISMATCH", error="non-JSON Workday response")
            if not isinstance(payload, dict) or "jobPostings" not in payload:
                return BoardResult("SCHEMA_MISMATCH", error="missing 'jobPostings'")
            listed_total = max(listed_total, int(payload.get("total") or 0))
            for item in payload.get("jobPostings") or []:
                path = item.get("externalPath")
                if path:
                    postings.setdefault(path, item)
        result = BoardResult("VALID", listed=listed_total, company=company_hint)
        budget = ctx.settings.max_detail_fetches_per_board
        fetched = 0
        for path, item in postings.items():
            title = (item.get("title") or "").strip()
            if not ctx.prefilter(title, geo_context):
                result.prefiltered_out += 1
                continue
            if fetched >= budget or ctx.out_of_time(120):
                break
            fetched += 1
            raw = self._detail(ctx, ref, path, geo_context, item)
            if raw.title:
                result.jobs.append(raw)
                result.company = result.company or raw.company
        return result

    def fetch_job(self, ctx: RunContext, url: str):
        native, board = detect(url)
        if not board or board.ats != "workday" or "/job/" not in url:
            return None
        m = re.search(r"(/job/.+)$", url.split("?")[0])
        if not m:
            return None
        raw = self._detail(ctx, board, m.group(1), False, {})
        return [raw] if raw.title else []


def all_adapters() -> dict[str, ATSAdapter]:
    from .core_ats import AshbyAdapter, GreenhouseAdapter, LeverAdapter

    adapters = [GreenhouseAdapter(), LeverAdapter(), AshbyAdapter(), SmartRecruitersAdapter(), WorkableAdapter(),
                RecruiteeAdapter(), PersonioAdapter(), WorkdayAdapter()]
    return {a.ats: a for a in adapters}
