"""Oracle Recruiting Cloud ("Candidate Experience") public REST endpoints, the ones its career sites call.

  List    GET https://{pod}.fa.{dc}.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions
              ?onlyData=true&expand=requisitionList.secondaryLocations
              &finder=findReqs;siteNumber={site},limit={L},offset={O}[,keyword="{term}"],sortBy=POSTING_DATES_DESC
  Detail  GET https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails
              ?expand=all&onlyData=true&finder=ById;Id="{Id}",siteNumber={site}
  Site    GET https://{host}/hcmUI/CandidateExperience/en/sites/{site}   (public career-site page)

Verified live 2026-10-10 (WSP emit.ca3/CX_2001, Michael Baker ebxs.us2/CX_2):
- `limit` is capped at 200 without an error; the total is items[0].TotalJobsCount; the top-level `hasMore` is always
  false, so paging stops on the total. Without `expand=requisitionList...` the rows are missing.
- The API answers any siteNumber (a made-up one returns the tenant's default site), so it cannot say whether a site
  exists. The public site page can: it redirects an unknown site to another one (CX_9999 -> CX_2001). Many tenants
  publish their site under a code (cbct.em2 gallifordtrycareers, hdjm.ca2 Alta): that page is served in place and names
  the site number behind it in `data-sitenumber` (CX_1, CX), which is the one the list API wants (siteNumber=Alta
  lists the default site's 92 postings, CX Alta's own 25). The detail API ignores siteNumber. The page also carries
  the employer's name (`og:site_name`, "FAB |" on one tenant); the rows' LegalEmployer is usually null.
- Postings written in French come back in French without any language parameter; `Accept-Language: fr-CA` only
  swaps in translated titles of bilingual postings, with the same ids, so the default language is kept.
- robots.txt is 404 on the pods; responses are no-store without an ETag.
"""
from __future__ import annotations

import html as html_lib
import re
from urllib.parse import quote

from ...models import RawJob
from ...utils.dates import parse_datetime
from ...utils.http import FetchError
from ...utils.text import html_to_text, prettify_slug
from ..base import RunContext
from .base import ATSAdapter, BoardResult
from .detect import ORACLE_SITE, BoardRef, detect
from .more_ats import WORKDAY_SEARCH_TERMS

ORACLE_PAGE = 200            # the server serves at most 200 rows, whatever limit is asked
ORACLE_FULL_SITE_MAX = 600   # a site this small is read in full (3 calls); larger ones are searched by keyword
ORACLE_TERM_PAGES = 2        # 1 on a board without geospatial context: only geospatial titles pass there anyway
ORACLE_SEARCH_TERMS = [*WORKDAY_SEARCH_TERMS, "SIG"]  # SIG: Québec postings are written in French
API = "hcmRestApi/resources/latest"
SITE_NUMBER_RE = re.compile(rf'data-sitenumber="({ORACLE_SITE})"')
SITE_NAME_RE = re.compile(r'<meta\s+property="og:site_name"\s+content="([^"]+)"', re.I)
LANDED_SITE_RE = re.compile(rf"/sites/({ORACLE_SITE})(?=/|$|[?#])")


def _host(ref: BoardRef) -> tuple[str, str, str]:
    """BoardRef slug "emit.ca3/CX_2001" -> (host, tenant, site)."""
    tenant, site = ref.slug.split("/", 1)
    pod, dc = tenant.split(".")
    if not pod or not dc or not re.fullmatch(ORACLE_SITE, site):
        raise ValueError(ref.slug)
    return f"{pod}.fa.{dc}.oraclecloud.com", tenant.lower(), site


def _location(item: dict) -> str:
    places = [item.get("PrimaryLocation") or ""]
    places += [loc.get("Name") or "" for loc in item.get("secondaryLocations") or [] if isinstance(loc, dict)]
    return "; ".join(dict.fromkeys(p for p in places if p))


def _site_name(page: str) -> str | None:
    """The employer's name from og:site_name, without the separators some tenants leave in it ("FAB |")."""
    found = SITE_NAME_RE.search(page)
    name = html_lib.unescape(found.group(1)).strip(" \t|·•:–—-") if found else ""
    return name if sum(c.isalpha() for c in name) >= 2 else None


def _first(sources: tuple[dict, ...], *keys: str) -> str | None:
    """The first non-empty field, list row before detail ("" for WorkplaceType means unset, as on WSP)."""
    return next((str(src[k]).strip() for src in sources for k in keys if str(src.get(k) or "").strip()), None)


class OracleAdapter(ATSAdapter):
    ats = "oracle"

    @staticmethod
    def _list_url(host: str, site: str, offset: int, term: str | None) -> str:
        # the finder's ";" and "," must stay literal, so the query string is built by hand; only the keyword is encoded
        finder = f"findReqs;siteNumber={site},limit={ORACLE_PAGE},offset={offset}"
        if term:
            finder += f",keyword=%22{quote(term)}%22"
        return (f"https://{host}/{API}/recruitingCEJobRequisitions?onlyData=true"
                f"&expand=requisitionList.secondaryLocations&finder={finder},sortBy=POSTING_DATES_DESC")

    @staticmethod
    def _detail_url(host: str, site: str, job_id: str) -> str:
        return (f"https://{host}/{API}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
                f"&finder=ById;Id=%22{quote(job_id, safe='')}%22,siteNumber={site}")

    @staticmethod
    def _site(ctx: RunContext, host: str, site: str) -> tuple[bool | None, str | None, str | None]:
        """(does the career site exist, the site number the list API wants, the employer's name); None when the page
        could not tell. An unknown site redirects to another one; a site code is served in place under its number."""
        try:
            response = ctx.client.get(f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}")
        except FetchError as exc:
            return (False if exc.kind == "NOT_FOUND" else None), None, None
        page = response.text
        served = SITE_NUMBER_RE.search(page)
        if not served:
            return None, None, _site_name(page)
        landed = LANDED_SITE_RE.search(response.url or "")
        # site names are not case-sensitive (cx_2001 redirects to CX_2001)
        exists = site.lower() in {served.group(1).lower(), landed.group(1).lower() if landed else ""}
        return exists, served.group(1), _site_name(page)

    @staticmethod
    def _page(ctx: RunContext, url: str) -> tuple[int, list[dict]] | None:
        """(TotalJobsCount, rows), or None when the answer does not have the documented shape."""
        payload = ctx.client.get_json(url, respect_robots=False)
        items = payload.get("items") if isinstance(payload, dict) else None
        first = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else None
        if first is None or not isinstance(first.get("requisitionList"), list) or "TotalJobsCount" not in first:
            return None
        return int(first.get("TotalJobsCount") or 0), [r for r in first["requisitionList"] if isinstance(r, dict) and r.get("Id")]

    def _to_raw(self, host: str, tenant: str, site: str, row: dict, detail: dict | None, company: str | None,
                geo_context: bool) -> RawJob:
        job_id = str(row.get("Id"))
        public_url = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{job_id}"
        info = detail or {}
        # the job's own text only: CorporateDescriptionStr / OrganizationDescriptionStr are the employer's boilerplate,
        # which would lend every posting the same geospatial terms
        description = "\n\n".join(html_to_text(info.get(k)) for k in
                                  ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")
                                  if info.get(k))
        posted = parse_datetime(info.get("ExternalPostedStartDate")) or parse_datetime(row.get("PostedDate"))
        return RawJob(
            source_type="ats", source_name="oracle",
            source_url=self._detail_url(host, site, job_id) if detail else public_url,
            title=(row.get("Title") or info.get("Title") or "").strip(),
            company=company or _first((row, info), "LegalEmployer", "BusinessUnit") or prettify_slug(tenant.split(".")[0]),
            url=public_url, apply_url=public_url, description=description,
            location_raw=_location(row) or _location(info), workplace_type=_first((row, info), "WorkplaceType"),
            employment_type=_first((row, info), "JobSchedule", "JobType", "ContractType"),
            posted_at=posted, posted_at_reliable=posted is not None,
            native_id=f"oracle:{tenant}:{job_id}", source_job_id=job_id, extraction_method="api", geo_context=geo_context,
        )

    def _detail(self, ctx: RunContext, host: str, site: str, job_id: str) -> dict | None:
        payload = ctx.client.get_json(self._detail_url(host, site, job_id), respect_robots=False)
        items = payload.get("items") if isinstance(payload, dict) else None
        return items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else None

    def _expired(self, ctx: RunContext, detail: dict) -> bool:
        ends = parse_datetime(detail.get("ExternalPostedEndDate"))  # null on many postings: never required
        return ends is not None and ends < ctx.now

    def fetch_board(self, ctx: RunContext, ref: BoardRef, *, geo_context: bool, variant=None, company_hint=None):
        try:
            host, tenant, site = _host(ref)
        except ValueError:
            return BoardResult("INVALID", error="oracle slug must be pod.dc/site, e.g. emit.ca3/CX_2001")
        exists, number, site_name = self._site(ctx, host, site)
        if exists is False:
            return BoardResult("INVALID", error=f"career site {site} not found (the API would answer with another site)")
        number = number or site  # the API's site; public links keep the URL's own (a code such as gallifordtrycareers)
        company = company_hint or site_name
        rows: dict[str, dict] = {}
        try:
            probe = self._page(ctx, self._list_url(host, number, 0, None))
        except FetchError as exc:
            return self.error_result(exc)
        if probe is None:
            return BoardResult("SCHEMA_MISMATCH", error="missing items[0].requisitionList / TotalJobsCount")
        total, first = probe
        for row in first:
            rows.setdefault(str(row["Id"]), row)
        error = None
        whole = total <= ORACLE_FULL_SITE_MAX
        hits_by_term: set[str] = set()
        # a small site is read whole; a large one by keyword, on top of its newest 200 postings from the probe
        # a rotation board keeps geospatial titles only, which the newest page of hits holds: half the calls
        term_pages = ORACLE_TERM_PAGES if geo_context else 1
        searches = [(None, range(ORACLE_PAGE, total, ORACLE_PAGE))] if whole else [
            (term, range(0, ORACLE_PAGE * term_pages, ORACLE_PAGE)) for term in ORACLE_SEARCH_TERMS]
        for term, offsets in searches:
            if error or ctx.out_of_time(150):
                break
            for offset in offsets:
                try:
                    page = self._page(ctx, self._list_url(host, number, offset, term))
                except FetchError as exc:
                    error = str(exc)
                    break
                if page is None:
                    error = "missing items[0].requisitionList on a later page"
                    break
                hits, found = page
                for row in found:
                    rows.setdefault(str(row["Id"]), row)
                    if term:
                        hits_by_term.add(str(row["Id"]))
                if not found or offset + ORACLE_PAGE >= hits or ctx.out_of_time(150):
                    break
        if total == 0 and exists:
            return BoardResult("VALID", company=company)
        if not exists:  # the page could not confirm the site; the API alone answers for any site number
            status = "EMPTY_UNVERIFIED"
            error = error or f"site {site} not confirmed by its career page"
        else:
            status = "VALID"
        result = BoardResult(status, listed=total, company=company, error=error)
        budget = ctx.settings.max_detail_fetches_per_board
        fetched = 0
        for job_id, row in rows.items():
            title = (row.get("Title") or "").strip()
            # the probe's newest postings of a large site are not search hits: WSP's would let every "Engineer" through
            context = geo_context and (whole or job_id in hits_by_term)
            if not ctx.prefilter(title, context):
                result.prefiltered_out += 1
                continue
            native = f"oracle:{tenant}:{job_id}"
            if ctx.knows_text(native):
                result.jobs.append(self._to_raw(host, tenant, site, row, None, company, context))  # still open; text on file
                continue
            if fetched >= budget or ctx.out_of_time(120):
                continue  # read at a later run, like Workday's
            fetched += 1
            try:
                detail = self._detail(ctx, host, number, job_id)
            except FetchError:
                detail = None
            if detail is None:
                result.detail_errors += 1
            elif self._expired(ctx, detail):
                continue
            raw = self._to_raw(host, tenant, site, row, detail, company, context)
            result.company = result.company or raw.company
            result.jobs.append(raw)
        return result

    def fetch_job(self, ctx: RunContext, url: str):
        native, board = detect(url)
        if not board or board.ats != "oracle" or not native:
            return None
        host, tenant, site = _host(board)
        job_id = native.rsplit(":", 1)[1]
        detail = self._detail(ctx, host, site, job_id)  # the detail API ignores siteNumber: a site code does as well
        if not detail or not detail.get("Title") or self._expired(ctx, detail):
            return []
        company = (ctx.registry.entry(board.key) or {}).get("company") if ctx.registry else None
        if not company:
            company = self._site(ctx, host, site)[2]
        return [self._to_raw(host, tenant, site, {"Id": job_id}, detail, company, False)]
