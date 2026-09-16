"""Career-page discovery, sitemap discovery and generic public job-page extraction.

Discovery (CareerSitesBackend) and extraction (GenericPagesBackend) are separate: discovery only
queues URLs and registers ATS boards; extraction fetches each queued page once, honouring
robots.txt, per-host caps and a refetch window stored in state["page_cache"].
"""
from __future__ import annotations

import gzip
import logging
import re
from collections import Counter
from datetime import timedelta

from ..models import BackendOutput
from ..utils.dates import parse_datetime, to_iso
from ..utils.http import FetchError
from ..utils.urls import canonicalize_url, host_of, is_aggregator, looks_like_job_url
from .ats.detect import detect, find_boards_in_text
from .base import Backend, RunContext
from .generic import extract_jobs, is_probable_job_link

log = logging.getLogger(__name__)

SITEMAP_HINT_RE = re.compile(r"job|career|vacanc|position|posting|opening|recruit", re.I)
MAX_SITEMAP_FILES = 6
MAX_SITEMAP_URLS = 5000


def parse_sitemap(body: bytes) -> tuple[list[tuple[str, str | None]], list[str]]:
    """Return ([(url, lastmod)], [child sitemap urls]). Tolerates gzip and namespaces."""
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except OSError:
            return [], []
    text = body.decode("utf-8", "replace")
    urls, children = [], []
    for kind, inner in re.findall(r"<(url|sitemap)\b[^>]*>(.*?)</\1>", text, flags=re.S | re.I):
        loc = re.search(r"<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>", inner, flags=re.S | re.I)
        if not loc:
            continue
        url = loc.group(1).replace("&amp;", "&").strip()
        lastmod = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", inner, flags=re.S | re.I)
        if kind.lower() == "sitemap":
            children.append(url)
        else:
            urls.append((url, lastmod.group(1) if lastmod else None))
        if len(urls) >= MAX_SITEMAP_URLS:
            break
    return urls, children


class CareerSitesBackend(Backend):
    name = "career_sites"
    phase = "discovery"
    source_type = "employer_page"

    def __init__(self, sites: list[dict]):
        self.sites = [s for s in sites if isinstance(s, dict) and s.get("url")]

    def enabled(self, ctx):
        ok, reason = super().enabled(ctx)
        if ok and not self.sites:
            return False, "no career_sites configured"
        return ok, reason

    def _queue_link(self, ctx, url: str, anchor: str, site: dict, origin: str, lastmod=None) -> bool:
        geo = bool(site.get("geospatial", True))
        priority = 85 if ctx.prefilter(anchor or url.rsplit("/", 1)[-1].replace("-", " "), False) else 40
        return ctx.pages.add(url, origin=origin, priority=priority, geo_context=geo, lastmod=lastmod,
                             company_hint=site.get("name"))

    def _sitemaps(self, ctx, site: dict, pattern) -> tuple[int, list[str]]:
        queued, errors = 0, []
        configured = site.get("sitemap")
        if configured:
            pending = [configured] if isinstance(configured, str) else list(configured)
        else:
            robots = ctx.client.robots
            pending = [s for s in (robots.sitemaps(site["url"]) if robots else []) if SITEMAP_HINT_RE.search(s)]
        seen = set()
        cache = ctx.state.get("page_cache", {})
        files = 0
        max_pages = int(site.get("max_pages", 40))
        while pending and files < MAX_SITEMAP_FILES and queued < max_pages:
            sitemap_url = pending.pop(0)
            if sitemap_url in seen:
                continue
            seen.add(sitemap_url)
            files += 1
            try:
                body = ctx.client.get(sitemap_url, detect_challenge=False).content
            except FetchError as exc:
                errors.append(f"sitemap {sitemap_url}: {exc.kind}")
                continue
            urls, children = parse_sitemap(body)
            pending.extend(c for c in children if not configured or SITEMAP_HINT_RE.search(c) or len(children) < 5)
            scored = []
            for url, lastmod in urls:
                if pattern is not None:
                    if not pattern.search(url):
                        continue
                elif not looks_like_job_url(url):
                    continue
                canon = canonicalize_url(url)
                cached = cache.get(canon) if canon else None
                if cached and lastmod and cached.get("lastmod") == lastmod:
                    continue
                slug_text = url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").replace("_", " ")
                relevance = 1 if ctx.prefilter(slug_text, False) else 0
                scored.append((relevance, lastmod or "", url))
            scored.sort(reverse=True)
            for relevance, lastmod, url in scored:
                if queued >= max_pages:
                    break
                if ctx.pages.add(url, origin="sitemap", priority=70 if relevance else 25,
                                 geo_context=bool(site.get("geospatial", True)), lastmod=lastmod or None,
                                 company_hint=site.get("name")):
                    queued += 1
        return queued, errors

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        stats = Counter()
        errors = []
        for site in self.sites:
            if ctx.out_of_time(300):
                stats["skipped_for_time"] += 1
                continue
            pattern = re.compile(site["job_url_pattern"], re.I) if site.get("job_url_pattern") else None
            try:
                response = ctx.client.get(site["url"])
                html = response.text
                stats["pages_ok"] += 1
            except FetchError as exc:
                errors.append(f"{site.get('name', site['url'])}: {exc.kind}")
                stats["pages_failed"] += 1
                html = None
            if html:
                for ref in find_boards_in_text(html):
                    if ctx.registry.register(ref, "career_page"):
                        stats["boards_new"] += 1
                    stats["boards_found"] += 1
                extraction = extract_jobs(html, site["url"], ctx.now)
                for job in extraction.jobs:
                    job.company = job.company or site.get("name")
                    job.geo_context = bool(site.get("geospatial", True))
                    if ctx.prefilter(job.title, job.geo_context):
                        out.jobs.append(job)
                    else:
                        out.prefiltered_out += 1
                for url, anchor in extraction.links:
                    if is_probable_job_link(url, anchor, pattern) and self._queue_link(ctx, url, anchor, site, "career_page"):
                        stats["links_queued"] += 1
            if site.get("sitemap") or site.get("use_robots_sitemaps", True):
                try:
                    queued, sm_errors = self._sitemaps(ctx, site, pattern)
                    stats["sitemap_urls_queued"] += queued
                    errors.extend(sm_errors)
                except Exception as exc:
                    ctx.record_parser_error(self.name)
                    errors.append(f"sitemap {site.get('name')}: {type(exc).__name__}")
        out.details = {**dict(stats), "errors": errors[:20], "sites": len(self.sites)}
        if out.jobs:
            ctx.snapshot(self.name, [j.snapshot() for j in out.jobs])
        if stats["pages_ok"] == 0 and stats["pages_failed"]:
            out.status, out.error = "FAILED", errors[0] if errors else "all career pages failed"
        elif errors:
            out.status = "PARTIAL"
        return out


class GenericPagesBackend(Backend):
    name = "generic_pages"
    phase = "extraction"
    source_type = "employer_page"

    def __init__(self, adapters: dict):
        self.adapters = adapters

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        stats = Counter()
        errors = []
        methods = Counter()
        cache = ctx.state.setdefault("page_cache", {})
        per_host = Counter()
        refetch_after = timedelta(days=ctx.settings.page_refetch_days)
        items = ctx.pages.pop_all()
        stats["queued"] = len(items)
        for item in items:
            if stats["fetched"] + stats["api_fetched"] >= ctx.settings.generic_pages_per_run:
                stats["over_budget"] += 1
                continue
            if ctx.out_of_time(150):
                stats["skipped_for_time"] += 1
                continue
            canon = canonicalize_url(item.url)
            cached = cache.get(canon) if canon else None
            if cached:
                fetched_at = parse_datetime(cached.get("fetched_at"))
                changed = item.lastmod and cached.get("lastmod") and item.lastmod != cached.get("lastmod")
                if fetched_at and ctx.now - fetched_at < refetch_after and not changed:
                    stats["cached_skip"] += 1
                    continue
            native, board = detect(item.url)
            if board is not None:
                ctx.registry.register(board, "generic_page" if item.origin != "commoncrawl" else "commoncrawl")
                if board.key in ctx.registry.hot_this_run and board.ats != "workday":
                    stats["covered_by_board_fetch"] += 1
                    continue
                adapter = self.adapters.get(board.ats)
                if adapter is not None and native:
                    try:
                        jobs = adapter.fetch_job(ctx, item.url)
                    except FetchError as exc:
                        jobs = None
                        errors.append(f"{host_of(item.url)}: {exc.kind}")
                        stats["failed"] += 1
                        cache[canon] = {"fetched_at": to_iso(ctx.now), "lastmod": item.lastmod, "status": exc.kind}
                        continue
                    if jobs is not None:
                        stats["api_fetched"] += 1
                        for job in jobs:
                            job.company = item.company_hint or job.company
                            self._keep(ctx, out, job, item.geo_context)
                        cache[canon] = {"fetched_at": to_iso(ctx.now), "lastmod": item.lastmod, "status": "OK",
                                        "jobs": len(jobs)}
                        continue
            host = host_of(item.url)
            if per_host[host] >= ctx.settings.generic_pages_per_host:
                stats["host_cap"] += 1
                continue
            per_host[host] += 1
            try:
                response = ctx.client.get(item.url, max_bytes=5 * 1024 * 1024)
            except FetchError as exc:
                stats["failed"] += 1
                stats[f"error_{exc.kind}"] += 1
                errors.append(f"{host}: {exc.kind}")
                if canon:
                    cache[canon] = {"fetched_at": to_iso(ctx.now), "lastmod": item.lastmod, "status": exc.kind}
                continue
            stats["fetched"] += 1
            ctype = response.headers.get("Content-Type", "")
            if "html" not in ctype.lower() and "xml" not in ctype.lower() and ctype:
                stats["not_html"] += 1
                continue
            html = response.text
            for ref in find_boards_in_text(html, limit=5):
                ctx.registry.register(ref, "generic_page")
            extraction = extract_jobs(html, response.url or item.url, ctx.now)
            if extraction.errors:
                ctx.record_parser_error(self.name)
                stats["parser_warnings"] += len(extraction.errors)
            stats["expired"] += extraction.expired
            if extraction.method:
                methods[extraction.method] += 1
            else:
                stats["no_job_found"] += 1
            for job in extraction.jobs:
                if is_aggregator(item.url):
                    job.source_type = "aggregator"
                job.company = job.company or item.company_hint
                self._keep(ctx, out, job, item.geo_context)
            if canon:
                cache[canon] = {"fetched_at": to_iso(ctx.now), "lastmod": item.lastmod, "status": "OK",
                                "jobs": len(extraction.jobs), "method": extraction.method}
        out.details = {**dict(stats), "methods": dict(methods), "errors": errors[:20]}
        if out.jobs:
            ctx.snapshot(self.name, [j.snapshot() for j in out.jobs])
        attempted = stats["fetched"] + stats["failed"] + stats["api_fetched"]
        if attempted and stats["failed"] == attempted:
            out.status, out.error = "FAILED", errors[0] if errors else "all page fetches failed"
        elif stats["failed"]:
            out.status = "PARTIAL"
        elif not items:
            out.status = "SKIPPED"
            out.error = "no pages queued by discovery"
        return out

    @staticmethod
    def _keep(ctx, out: BackendOutput, job, geo_context: bool) -> None:
        job.geo_context = job.geo_context or geo_context
        if ctx.prefilter(job.title, job.geo_context):
            out.jobs.append(job)
        else:
            out.prefiltered_out += 1
