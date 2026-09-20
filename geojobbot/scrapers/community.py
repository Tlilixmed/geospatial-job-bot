"""Community hiring threads.

hn_hiring   Hacker News "Ask HN: Who is hiring?" (a new thread on the first weekday of each month, hundreds of
            comments, many remote roles and small companies that never use a job board). Read through the free
            Algolia API: one request finds the latest thread, one reads its comments; once a day. A comment is a
            job when its text passes a strict geospatial filter and its first line, in the thread's customary
            "Company | Role | Location | REMOTE | ..." shape, yields a title the prefilter accepts.
"""
from __future__ import annotations

import html
import logging
import re

from ..models import BackendOutput, RawJob
from ..utils.dates import parse_datetime
from ..utils.http import FetchError
from .base import Backend, RunContext

log = logging.getLogger(__name__)

ALGOLIA = "https://hn.algolia.com/api/v1"
GEO_RE = re.compile(r"\b(gis|geospatial|geo-spatial|remote sensing|earth observation|lidar|photogrammetr\w*|cartograph\w*|"
                    r"geodata|geomatics|point clouds?|satellite imagery|postgis|arcgis|qgis|geoserver|mapbox|openlayers|"
                    r"orthomosaic|digital elevation|surveying)\b", re.I)
ROLE_HINT_RE = re.compile(r"\b(engineer|developer|scientist|analyst|specialist|lead|manager|architect|researcher|surveyor|"
                          r"technician|cartographer|geospatial|gis)\b", re.I)


def split_header(text: str) -> tuple[str | None, str | None, str]:
    """(company, role, location) from the first line 'Company | Role | Location | ...'."""
    first = text.strip().split("\n", 1)[0]
    parts = [p.strip(" -–—:") for p in re.split(r"\s*\|\s*", first) if p.strip(" -–—:")]
    if len(parts) < 2:
        return None, None, ""
    company = parts[0][:80]
    role = next((p for p in parts[1:4] if ROLE_HINT_RE.search(p)), parts[1])
    rest = [p for p in parts[1:] if p != role]
    location = next((p for p in rest if re.search(r"\b(remote|onsite|hybrid|[A-Z][a-z]+,? [A-Z]{2}\b|[A-Z][a-z]+, [A-Z][a-z]+)", p)), "")
    return company, role[:120], location[:120]


class HackerNewsHiringBackend(Backend):
    name = "hn_hiring"
    phase = "extraction"
    source_type = "feed"
    min_interval_hours = 24

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        try:
            stories = ctx.client.get(f"{ALGOLIA}/search_by_date", params={"tags": "story,author_whoishiring", "hitsPerPage": 6},
                                     respect_robots=False, detect_challenge=False).json()
        except (FetchError, ValueError) as exc:
            out.status, out.error = "FAILED", f"thread lookup: {getattr(exc, 'kind', 'non-JSON')}"
            return out
        hits = [h for h in (stories.get("hits") or []) if "who is hiring" in (h.get("title") or "").lower()]
        if not hits:
            out.status, out.error = "FAILED", "no 'Who is hiring' thread found"
            return out
        thread = hits[0]
        try:
            comments = ctx.client.get(f"{ALGOLIA}/search", params={"tags": f"comment,story_{thread['objectID']}", "hitsPerPage": 1000},
                                      respect_robots=False, detect_challenge=False).json()
        except (FetchError, ValueError) as exc:
            out.status, out.error = "FAILED", f"comments: {getattr(exc, 'kind', 'non-JSON')}"
            return out
        seen, considered = set(), 0
        for hit in comments.get("hits") or []:
            raw = hit.get("comment_text") or ""
            if not raw or hit.get("parent_id") != int(thread["objectID"]):  # replies to a job post are not jobs
                continue
            text = html.unescape(re.sub(r"<p>", "\n", re.sub(r"<[^>]+>", " ", raw.replace("<p>", "\n")))).strip()
            if not GEO_RE.search(text):
                continue
            considered += 1
            company, role, location = split_header(text)
            title = role or ""
            if not title or not ctx.prefilter(title, False):  # a geo word in the text is a hint, not a geospatial board
                out.prefiltered_out += 1
                continue
            url = f"https://news.ycombinator.com/item?id={hit['objectID']}"
            if url in seen:
                continue
            seen.add(url)
            posted = parse_datetime(hit.get("created_at"))
            # the visible text of a long link is cut ("https://acme.com/careers/gis-an..."): the address is in the href
            hrefs = [html.unescape(h) for h in re.findall(r'href="(https?://[^"]+)"', raw)]
            typed = re.search(r"https?://[^\s<>\"]+", text)
            typed = typed.group(0).rstrip(".,)") if typed and ".." not in typed.group(0)[-4:] else None
            link = next((h for h in hrefs if "news.ycombinator.com" not in h), None) or typed
            out.jobs.append(RawJob(
                source_type="feed", source_name="hn_hiring", source_url=f"https://news.ycombinator.com/item?id={thread['objectID']}",
                title=title, company=company, url=url, apply_url=link or url,
                description=text[:6000], location_raw=location, posted_at=posted, posted_at_reliable=posted is not None,
                source_job_id=f"hn:{hit['objectID']}", extraction_method="api", geo_context=False,
            ))
        out.details = {"thread": thread.get("title"), "comments": len(comments.get("hits") or []), "geospatial": considered}
        if out.jobs:
            ctx.snapshot(self.name, [j.snapshot() for j in out.jobs])
        return out
