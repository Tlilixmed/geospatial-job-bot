"""LinkedIn job alerts, read from the mail they arrive in.

The mail reaches the Cloudflare Worker through Email Routing (cloudflare/README.md, step 10). The Worker checks the sender
and leaves the text in R2 under mail-alerts/, without any link's parameters: LinkedIn's carry one-time sign-in tokens.
This backend reads what is there on every run and keeps each mail for a week, so its jobs stay listed while the alert is
likely to be current. An alert names only a title, an employer and a place. The posting's text comes from JobSpy's
LinkedIn reader when that is on (the request JOBSPY_LINKEDIN_FETCH_DESCRIPTION already makes); when it fails or is off, the
job stays title-only evidence, which the matcher floors at Possible for a strong title.
"""
from __future__ import annotations

import html as html_lib
import json
import re
from datetime import timedelta
from types import SimpleNamespace

from ..models import RawJob
from ..utils.dates import parse_datetime
from ..utils.text import clean_whitespace, html_to_text
from .ats.base import title_rank
from .base import Backend, BackendOutput, RunContext

JOB_LINK_RE = re.compile(r"https?://(?:[\w-]+\.)*linkedin\.com/(?:comm/)?jobs/view/(?:[^\s\"'<>?]*-)?(\d{6,})(?=[/?\s\"'<>]|$)")
ANCHOR_RE = re.compile(r"<a\b[^>]*\bhref=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
SEPARATOR_RE = re.compile(r"-{4,}")
# What an alert prints around a job, in English and French. A title is never one of these.
NOISE_RE = re.compile(r"^(?:view job|voir l.offre|see all|voir tou|your job alert|votre alerte|new jobs?\b|\d+\+? (?:new )?jobs?\b|\d*\+? ?nouvel|"
                      r"easy apply|candidature simplifi|apply with|postuler|actively recruit|recrute activement|be an early|promoted|sponsoris|"
                      r"\d+ (?:connections?|school|company|alum|relations?|ancien)|unsubscribe|se d.sabonner|©)", re.I)
PLACE_GLUE_RE = re.compile(r"\s+[·•]\s+")
MAX_JOBS_PER_MAIL = 60


def linkedin_key(job_id: str) -> str:
    """JobSpy's id for the same posting: seen both ways it is one job, even when JobSpy reports the employer's own link."""
    return f"jobspy:li-{job_id}"


def _lines(text: str) -> list[str]:
    cleaned = (clean_whitespace(html_lib.unescape(line)) for line in (text or "").splitlines())
    return [line for line in cleaned if line and len(line) <= 200 and "http" not in line and "@" not in line and not NOISE_RE.match(line)]


def _row(lines: list[str]) -> dict | None:
    """Title, employer, place, in that order. The employer and the place may share a line ("Acme · Paris"), and anything
    an alert adds below them ("Actively recruiting") is ignored."""
    if not lines:
        return None
    first = PLACE_GLUE_RE.split(lines[0], maxsplit=2)
    if len(lines) == 1 and len(first) > 1:
        lines = first
    title, company, location = lines[0], (lines[1] if len(lines) > 1 else ""), (lines[2] if len(lines) > 2 else "")
    glued = PLACE_GLUE_RE.split(company, maxsplit=1)
    if len(glued) == 2:
        company, location = glued
    return {"title": title, "company": company or None, "location": location}


def parse_plain(text: str) -> tuple[dict[str, dict], set[str]]:
    """The text part: each job ends with its link. Returns ({job id: row}, ids whose title could not be read)."""
    rows, unread, last = {}, set(), 0
    for match in JOB_LINK_RE.finditer(text or ""):
        block = text[last:match.start()]
        last = match.end()
        job_id = match.group(1)
        block = SEPARATOR_RE.split(block)[-1]
        block = block[:max(block.rfind("\n"), 0)]  # the label on the link's own line, in whichever language the account uses
        row = _row(_lines(block))
        if row and job_id not in rows:
            rows[job_id] = row
        elif not row and job_id not in rows:
            unread.add(job_id)
    return rows, unread - set(rows)


def parse_html(page: str) -> tuple[dict[str, dict], set[str]]:
    """The HTML part: a job's title is a link's text, and the employer and the place follow it. Image links have no text."""
    rows, unread = {}, set()
    anchors = [m for m in ANCHOR_RE.finditer(page or "") if JOB_LINK_RE.match(m.group(1))]
    for number, anchor in enumerate(anchors):
        job_id = JOB_LINK_RE.match(anchor.group(1)).group(1)
        end = anchors[number + 1].start() if number + 1 < len(anchors) else anchor.end() + 2000
        row = _row(_lines(html_to_text(anchor.group(2))) + _lines(html_to_text(page[anchor.end():end])[:400]))
        if row and job_id not in rows:
            rows[job_id] = row
        elif not row:
            unread.add(job_id)
    return rows, unread - set(rows)


def parse_alert(item: dict) -> tuple[dict[str, dict], set[str]]:
    rows, unread = parse_plain(str(item.get("plain") or ""))
    if not rows:
        rows, unread = parse_html(str(item.get("html") or ""))
    return dict(list(rows.items())[:MAX_JOBS_PER_MAIL]), unread


def jobspy_reader(settings):
    """A function from a job id to the posting's text, or None when JobSpy's LinkedIn reading is off or not installed."""
    sites = [s.strip().lower() for s in settings.jobspy_sites]
    if not (settings.jobspy_enabled and "linkedin" in sites and settings.jobspy_linkedin_fetch_description):
        return None
    try:
        from jobspy.linkedin import LinkedIn
        from jobspy.model import DescriptionFormat
    except Exception:
        return None
    reader = LinkedIn()
    reader.scraper_input = SimpleNamespace(description_format=DescriptionFormat.MARKDOWN)
    return lambda job_id: str(reader._get_job_details(job_id).get("description") or "")


class MailAlertsBackend(Backend):
    name = "linkedin_alerts"
    phase = "extraction"
    source_type = "feed"
    folder = "mail-alerts/"
    keep_days = 7
    text_per_run = 12    # postings read per run; the others stay title-only until a later run
    text_delay_s = 2.0
    text_failures_allowed = 3  # in a row: LinkedIn is refusing this run, so the rest stay title-only

    def __init__(self, manager=None, read_text=None):
        self.manager = manager  # the state manager: the bucket and its prefix
        self.read_text = read_text

    def enabled(self, ctx):
        ok, reason = super().enabled(ctx)
        if not ok:
            return ok, reason
        return (True, None) if self.manager is not None else (False, "no bucket")

    def _read_mail(self, ctx: RunContext, out: BackendOutput) -> tuple[dict[str, dict], int, set[str]]:
        store = self.manager.store
        rows, mails, unread, finished = {}, 0, set(), []
        for key in sorted(store.list_keys(self.manager.key(self.folder))):  # oldest first: a job keeps the date it was first alerted
            try:
                item = json.loads(store.get_bytes(key).decode("utf-8"))
                received = parse_datetime(item.get("received_at"))
            except (AttributeError, ValueError, UnicodeDecodeError):
                ctx.record_parser_error(self.name)
                finished.append(key)
                continue
            if received is None or ctx.now - received > timedelta(days=self.keep_days):
                finished.append(key)
                continue
            mails += 1
            found, missed = parse_alert(item)
            unread |= missed
            for job_id, row in found.items():
                rows.setdefault(job_id, {**row, "received": received})
        if finished and (not ctx.settings.dry_run or ctx.settings.dry_run_write_state):
            store.delete_keys(finished)
        out.details["expired"] = len(finished)
        return rows, mails, unread - set(rows)

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        out.details = {}
        rows, mails, unread = self._read_mail(ctx, out)
        if not mails:
            out.status, out.error = "SKIPPED", "no alert mail waiting"
            return out
        read_text = self.read_text or jobspy_reader(ctx.settings)
        read, failed, in_a_row = 0, 0, 0
        for job_id, row in sorted(rows.items(), key=lambda kv: title_rank(kv[1]["title"])):  # the text budget goes to geospatial titles first
            if not ctx.prefilter(row["title"], True):
                out.prefiltered_out += 1
                continue
            key = linkedin_key(job_id)
            text = ""
            if read_text and read + failed < self.text_per_run and in_a_row < self.text_failures_allowed and not ctx.knows_text(key) and not ctx.out_of_time(120):
                if read + failed:
                    ctx.client.sleep(self.text_delay_s)
                try:
                    text = read_text(job_id)
                except Exception:  # the library raises many types; a posting without its text is still a job
                    text = ""
                read, failed = (read + 1, failed) if text else (read, failed + 1)
                in_a_row = 0 if text else in_a_row + 1
            url = f"https://www.linkedin.com/jobs/view/{job_id}"  # rebuilt from the id: nothing from the mail's link is kept
            out.jobs.append(RawJob(
                source_type="feed", source_name=self.name, source_url=url, title=row["title"], company=row["company"], url=url, apply_url=url,
                description=text, location_raw=row["location"], posted_at=row["received"], posted_at_reliable=False, source_job_id=key,
                extraction_method="email", geo_context=True,
            ))
        out.details.update(mails=mails, jobs_listed=len(rows), no_title=len(unread), text_read=read, text_failed=failed)
        if out.jobs:
            ctx.snapshot(self.name, [j.snapshot() for j in out.jobs])
        if not rows:
            out.status, out.error = "PARTIAL", "no job could be read from the alert mail: its format may have changed"
        elif unread:
            out.status = "PARTIAL"
        return out
