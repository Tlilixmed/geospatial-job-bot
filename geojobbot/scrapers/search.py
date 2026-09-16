"""Discovery-only backends: web search and the Common Crawl URL index.

Search results are never trusted as job data. They only yield (a) ATS boards to register and
(b) public job-page URLs queued for real extraction.

* SearXNG: JSON API of an instance you operate (SEARXNG_URL).
* DuckDuckGo HTML endpoint: robots.txt is honoured. If DuckDuckGo disallows automated access the
  backend reports ROBOTS_DISALLOWED and stops - it is never circumvented.
* Common Crawl CDX index: public API listing crawled URLs. Enumerating ATS host URL patterns
  discovers thousands of company board slugs, which the board registry then checks on rotation.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup

from ..matching import profile as P
from ..models import BackendOutput
from ..utils.http import FetchError
from ..utils.urls import is_aggregator, is_http_url, looks_like_job_url, registrable_domain, host_of
from .ats.detect import detect
from .base import Backend, RunContext

log = logging.getLogger(__name__)

SEARCH_EXCLUDED_DOMAINS = {"linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "facebook.com",
                           "twitter.com", "x.com", "youtube.com", "reddit.com", "wikipedia.org", "pinterest.com"}
STOP_KINDS = {"ROBOTS_DISALLOWED", "BLOCKED", "RATE_LIMITED", "AUTH_REQUIRED"}


def build_queries(extra: list[str] | None = None) -> list[str]:
    queries = []
    for site in ("boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com",
                 "apply.workable.com", "myworkdayjobs.com", "jobs.smartrecruiters.com", "recruitee.com"):
        for term in ("GIS", "geospatial", "LiDAR", "cartographer", "photogrammetry", "remote sensing"):
            queries.append(f'site:{site} "{term}"')
    for role in P.DIRECT_ROLES:
        queries.append(f'"{role}" job')
    for domain in ("mining", "utility", "telecom", "fiber", "land administration", "cadastral", "mineral tenure",
                   "electric distribution", "surveying", "drone"):
        queries.append(f'"GIS" "{domain}" job opening')
    queries.extend(extra or [])
    return list(dict.fromkeys(queries))


class _SearchBase(Backend):
    phase = "discovery"
    source_type = "search"

    def __init__(self, extra_queries: list[str] | None = None):
        self.queries = build_queries(extra_queries)

    def search(self, ctx: RunContext, query: str) -> list[tuple[str, str]]:
        raise NotImplementedError

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        cursor = ctx.cursor(self.name)
        start = int(cursor.get("index", 0)) % max(1, len(self.queries))
        n = min(ctx.settings.search_queries_per_run, len(self.queries))
        stats = Counter()
        errors = []
        for offset in range(n):
            if ctx.out_of_time(300):
                break
            query = self.queries[(start + offset) % len(self.queries)]
            try:
                results = self.search(ctx, query)
                stats["queries_ok"] += 1
            except FetchError as exc:
                stats["queries_failed"] += 1
                errors.append(f"{query[:60]}: {exc.kind}")
                out.http_status = exc.status
                if exc.kind in STOP_KINDS:
                    out.details["stopped_reason"] = exc.kind
                    break
                continue
            finally:
                cursor["index"] = (start + offset + 1) % len(self.queries)
            for url, title in results:
                self._handle_result(ctx, url, title, stats)
        out.details.update(dict(stats), errors=errors[:10], total_queries=len(self.queries))
        if stats["queries_ok"] == 0 and stats["queries_failed"]:
            out.status, out.error = "FAILED", errors[0]
        elif stats["queries_failed"]:
            out.status = "PARTIAL"
        return out

    @staticmethod
    def _handle_result(ctx: RunContext, url: str, title: str, stats: Counter) -> None:
        if not is_http_url(url):
            return
        stats["results"] += 1
        if registrable_domain(host_of(url)) in SEARCH_EXCLUDED_DOMAINS:
            stats["excluded_domain"] += 1
            return
        native, board = detect(url)
        if board is not None:
            stats["ats_results"] += 1
            if ctx.registry.register(board, "search"):
                stats["boards_new"] += 1
        if native or (looks_like_job_url(url) and not is_aggregator(url)):
            priority = 90 if ctx.prefilter(title or "", True) else 55
            if ctx.pages.add(url, origin="search", priority=priority, geo_context=True):
                stats["pages_queued"] += 1


class SearxngBackend(_SearchBase):
    name = "search_searxng"

    def enabled(self, ctx):
        ok, reason = super().enabled(ctx)
        if ok and not ctx.settings.searxng_url:
            return False, "SEARXNG_URL not set"
        return ok, reason

    def search(self, ctx: RunContext, query: str) -> list[tuple[str, str]]:
        base = ctx.settings.searxng_url.rstrip("/")
        # A self-operated instance: the operator has authorised this automated use.
        data = ctx.client.get_json(f"{base}/search", params={"q": query, "format": "json"}, respect_robots=False)
        results = data.get("results", []) if isinstance(data, dict) else []
        return [(r.get("url"), r.get("title") or "") for r in results if isinstance(r, dict) and r.get("url")]


class DuckDuckGoBackend(_SearchBase):
    name = "search_duckduckgo"
    endpoint = "https://html.duckduckgo.com/html/"

    def enabled(self, ctx):
        ok, reason = super().enabled(ctx)
        if ok and not ctx.settings.duckduckgo_enabled:
            return False, "DUCKDUCKGO_ENABLED=false"
        return ok, reason

    @staticmethod
    def parse_results(html: str) -> list[tuple[str, str]]:
        soup = BeautifulSoup(html or "", "html.parser")
        results = []
        for a in soup.select("a.result__a"):
            href = a.get("href") or ""
            if "uddg=" in href:
                target = parse_qs(urlsplit(href if href.startswith("http") else "https:" + href).query).get("uddg")
                href = unquote(target[0]) if target else ""
            if is_http_url(href):
                results.append((href, a.get_text(" ").strip()))
        return results

    def search(self, ctx: RunContext, query: str) -> list[tuple[str, str]]:
        response = ctx.client.get(self.endpoint, params={"q": query})
        return self.parse_results(response.text)


# ---------------------------------------------------------------------------- Common Crawl
CC_PATTERNS = [
    ("boards.greenhouse.io/*", None),
    ("job-boards.greenhouse.io/*", None),
    ("jobs.lever.co/*", None),
    ("jobs.ashbyhq.com/*", None),
    ("apply.workable.com/*", None),
    ("jobs.smartrecruiters.com/*", None),
    ("recruitee.com", "domain"),
    ("jobs.personio.de", "domain"),
    ("myworkdayjobs.com", "domain"),
]


class CommonCrawlBackend(Backend):
    name = "commoncrawl"
    phase = "discovery"
    source_type = "search"
    collinfo_url = "https://index.commoncrawl.org/collinfo.json"

    def enabled(self, ctx):
        ok, reason = super().enabled(ctx)
        if ok and not ctx.settings.commoncrawl_enabled:
            return False, "COMMONCRAWL_ENABLED=false"
        return ok, reason

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        cursor = ctx.cursor(self.name)
        try:
            collections = ctx.client.get_json(self.collinfo_url, timeout=60)
        except FetchError as exc:
            out.status, out.error, out.http_status = "FAILED", f"collinfo.json: {exc}", exc.status
            return out
        if not isinstance(collections, list) or not collections or not isinstance(collections[0], dict):
            out.status, out.error = "FAILED", "unexpected collinfo.json structure"
            return out
        latest = collections[0]
        cdx_api = latest.get("cdx-api")
        if not cdx_api:
            out.status, out.error = "FAILED", "collinfo.json lacks cdx-api"
            return out
        if cursor.get("collection") != latest.get("id"):
            cursor.clear()
            cursor["collection"] = latest.get("id")
            cursor["patterns"] = {}
            cursor["rr"] = 0
        stats = Counter()
        errors = []
        pages_budget = ctx.settings.commoncrawl_pages_per_run
        new_budget = ctx.settings.commoncrawl_max_new_boards
        attempts = 0
        while pages_budget > 0 and attempts < len(CC_PATTERNS) and not ctx.out_of_time(400):
            attempts += 1
            idx = int(cursor.get("rr", 0)) % len(CC_PATTERNS)
            cursor["rr"] = idx + 1
            pattern, match_type = CC_PATTERNS[idx]
            pstate = cursor["patterns"].setdefault(pattern, {"next_page": 0, "num_pages": None})
            params = {"url": pattern, "output": "json", "fl": "url", "filter": "status:200"}
            if match_type:
                params["matchType"] = match_type
            try:
                if pstate["num_pages"] is None:
                    info = ctx.client.get_json(cdx_api, params={**params, "showNumPages": "true"}, timeout=90)
                    pstate["num_pages"] = int((info or {}).get("pages", 0)) if isinstance(info, dict) else 0
                if pstate["next_page"] >= (pstate["num_pages"] or 0):
                    stats["patterns_exhausted"] += 1
                    continue
                response = ctx.client.get(cdx_api, params={**params, "page": pstate["next_page"]}, timeout=120,
                                          max_bytes=40 * 1024 * 1024, detect_challenge=False)
                pstate["next_page"] += 1
                pages_budget -= 1
                stats["pages_read"] += 1
            except FetchError as exc:
                errors.append(f"{pattern}: {exc.kind}")
                out.http_status = exc.status
                if exc.kind in STOP_KINDS:
                    break
                continue
            for line in response.text.splitlines():
                try:
                    url = json.loads(line).get("url")
                except (ValueError, AttributeError):
                    continue
                _, board = detect(url)
                if board is None:
                    continue
                stats["urls_with_boards"] += 1
                if new_budget > 0 and ctx.registry.register(board, "commoncrawl"):
                    stats["boards_new"] += 1
                    new_budget -= 1
        out.details = {**dict(stats), "collection": cursor.get("collection"), "errors": errors}
        if stats["pages_read"] == 0 and errors:
            out.status, out.error = "FAILED", errors[0]
        elif errors:
            out.status = "PARTIAL"
        return out
