"""LinkedIn job alerts: what the Worker leaves in the bucket, as the scraper reads it."""
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from conftest import GIS_DESCRIPTION, NOW, FakeSession, make_ctx, make_settings

from geojobbot.core.fusion import fuse
from geojobbot.matching.matcher import score_job
from geojobbot.models import RawJob
from geojobbot.scrapers.mail_alerts import MailAlertsBackend, linkedin_key, parse_alert, parse_html, parse_plain
from geojobbot.storage.base import LocalStore

SEP = "-" * 57
PLAIN = "\r\n".join([
    "Your job alert for GIS in France", "3 new jobs match your preferences.", "", SEP, "",
    "Géomaticien (H/F)", "Acme Topo", "Paris, Île-de-France, France", "Apply with resume & profile", "", "View job: https://www.linkedin.com/comm/jobs/view/4100000001/", "", SEP, "",
    "GIS Analyst", "MapCo &amp; Sons", "Montreal, Quebec, Canada", "Actively recruiting", "", "View job: https://www.linkedin.com/comm/jobs/view/4100000002/", "", SEP, "",
    "Marketing Manager", "Brand Inc", "Remote", "", "View job: https://www.linkedin.com/comm/jobs/view/4100000003/", "", SEP, "",
    "See all jobs: https://www.linkedin.com/comm/jobs/search/", "This email was intended for Someone (GIS Engineer)", "Unsubscribe: https://www.linkedin.com/comm/jobs/alerts",
])


def test_the_text_part_gives_title_employer_and_place():
    rows, unread = parse_plain(PLAIN)
    assert list(rows) == ["4100000001", "4100000002", "4100000003"] and not unread
    assert rows["4100000001"] == {"title": "Géomaticien (H/F)", "company": "Acme Topo", "location": "Paris, Île-de-France, France"}
    assert rows["4100000002"]["company"] == "MapCo & Sons"


@pytest.mark.parametrize("block,expected", [
    ("Topographe\nAcme · Lyon, France\nVoir l'offre : https://www.linkedin.com/comm/jobs/view/4100000009/", ("Topographe", "Acme", "Lyon, France")),  # a French account, one line for both
    ("Votre alerte emploi pour SIG\n" + SEP + "\nCartographe\nGeoCo\nTunis\nhttps://fr.linkedin.com/jobs/view/cartographe-at-geoco-4100000009", ("Cartographe", "GeoCo", "Tunis")),
    ("LiDAR Engineer\nView job: https://www.linkedin.com/jobs/view/4100000009", ("LiDAR Engineer", None, "")),
])
def test_layouts_and_languages_that_vary(block, expected):
    rows, _ = parse_plain(block)
    row = rows["4100000009"]
    assert (row["title"], row["company"], row["location"]) == expected


def test_a_link_with_no_readable_title_is_counted_not_guessed():
    rows, unread = parse_plain("https://www.linkedin.com/comm/jobs/view/4100000009/")
    assert not rows and unread == {"4100000009"}


def test_the_html_part_when_the_text_part_has_no_links():
    page = ('<a href="https://www.linkedin.com/comm/jobs/view/4100000004/"><img src="x.png"></a>'
            '<a href="https://www.linkedin.com/comm/jobs/view/4100000004/">GIS Technician</a><p>Geo SA · Geneva, Switzerland</p>'
            '<a href="https://www.linkedin.com/comm/jobs/view/4100000005/">Surveyor</a><p>Topo Ltd · Dublin, Ireland</p>')
    rows, unread = parse_html(page)
    assert rows["4100000004"] == {"title": "GIS Technician", "company": "Geo SA", "location": "Geneva, Switzerland"}
    assert rows["4100000005"]["location"] == "Dublin, Ireland" and not unread
    assert parse_alert({"plain": "No links here.", "html": page})[0].keys() == rows.keys()


@pytest.fixture
def bucket(tmp_path):
    store = LocalStore(str(tmp_path))

    def leave(name, age_days=0, **item):
        item.setdefault("received_at", (NOW - timedelta(days=age_days)).isoformat())
        item.setdefault("plain", PLAIN)
        store.put_bytes(f"mail-alerts/{name}.json", json.dumps(item).encode("utf-8"))
    return SimpleNamespace(store=store, key=lambda suffix: suffix, leave=leave)


def run(bucket, read_text=lambda job_id: "", settings=None, state=None):
    ctx = make_ctx(FakeSession({}), settings=settings, state=state)
    return MailAlertsBackend(bucket, read_text=read_text).run(ctx)


def test_an_empty_folder_says_so(bucket):
    out = run(bucket)
    assert out.status == "SKIPPED" and "no alert mail" in out.error


def test_without_a_bucket_the_source_is_off():
    assert MailAlertsBackend().enabled(make_ctx(FakeSession({})))[0] is False


def test_jobs_keep_only_the_id_from_the_link_and_share_jobspys_identity(bucket):
    bucket.leave("a", plain=PLAIN.replace("4100000001/", "4100000001/?otpToken=stale"))
    out = run(bucket)
    assert [j.title for j in out.jobs] == ["Géomaticien (H/F)", "GIS Analyst"] and out.prefiltered_out == 1  # no job for the marketing manager
    job = out.jobs[0]
    assert job.url == job.apply_url == job.source_url == "https://www.linkedin.com/jobs/view/4100000001"
    assert job.source_job_id == linkedin_key("4100000001") == "jobspy:li-4100000001" and job.location_raw.endswith("France")
    assert job.extraction_method == "email" and not job.posted_at_reliable and "otpToken" not in json.dumps(job.snapshot())
    assert out.details["mails"] == 1 and out.details["jobs_listed"] == 3


def test_a_job_alerted_twice_is_one_job_with_its_first_date(bucket):
    bucket.leave("2026-a", age_days=3)
    bucket.leave("2026-b", age_days=1)
    jobs = run(bucket).jobs
    assert len(jobs) == 2 and all((NOW - j.posted_at).days == 3 for j in jobs)


def test_old_and_broken_mail_is_removed_but_a_dry_run_touches_nothing(bucket):
    bucket.leave("old", age_days=8)
    bucket.store.put_bytes("mail-alerts/broken.json", b"{not json")
    bucket.leave("fresh", age_days=6)
    assert run(bucket, settings=make_settings(dry_run=True)).details["expired"] == 2 and len(bucket.store.list_keys("mail-alerts/")) == 3
    out = run(bucket)
    assert out.details["expired"] == 2 and bucket.store.list_keys("mail-alerts/") == ["mail-alerts/fresh.json"] and len(out.jobs) == 2


def test_a_format_change_shows_in_sources_rather_than_as_silence(bucket):
    bucket.leave("a", plain="Nothing recognisable at all.")
    out = run(bucket)
    assert out.status == "PARTIAL" and "format" in out.error and not out.jobs


def test_the_posting_text_is_read_within_a_budget_geospatial_titles_first(bucket):
    many = "\r\n".join(f"{SEP}\r\n{title} {n}\r\nCo {n}\r\nParis, France\r\nView job: https://www.linkedin.com/comm/jobs/view/42000000{n:02d}/"
                       for n, title in enumerate(["Analyst"] * 3 + ["GIS Analyst"] * 20))
    bucket.leave("a", plain=many)
    asked = []

    def read(job_id):
        asked.append(job_id)
        return GIS_DESCRIPTION

    out = run(bucket, read_text=read)
    assert len(asked) == MailAlertsBackend.text_per_run == out.details["text_read"]
    assert all(int(job_id[-2:]) >= 3 for job_id in asked)  # the plain "Analyst" titles wait
    assert sum(1 for j in out.jobs if j.description) == MailAlertsBackend.text_per_run


def test_a_refusing_linkedin_leaves_the_jobs_title_only(bucket):
    bucket.leave("a")

    def refuse(job_id):
        raise RuntimeError("429")

    out = run(bucket, read_text=refuse)
    assert len(out.jobs) == 2 and not any(j.description for j in out.jobs) and out.details["text_failed"] == 2 and out.status == "SUCCESS"


def test_reading_stops_after_failures_in_a_row(bucket):
    many = "\r\n".join(f"{SEP}\r\nGIS Analyst {n}\r\nCo {n}\r\nParis, France\r\nView job: https://www.linkedin.com/comm/jobs/view/42000000{n:02d}/" for n in range(9))
    bucket.leave("a", plain=many)
    asked = []
    out = run(bucket, read_text=lambda job_id: asked.append(job_id) or "")
    assert len(asked) == MailAlertsBackend.text_failures_allowed and len(out.jobs) == 9


def test_text_read_recently_is_not_read_again(bucket):
    bucket.leave("a")
    state = {"boards": {}, "cursors": {}, "page_cache": {}, "sources": {},
             "jobs": {"gh:x": {"text_seen": NOW.isoformat(), "description_length": 900, "aliases": [linkedin_key("4100000001")]}}}
    asked = []
    run(bucket, read_text=lambda job_id: asked.append(job_id) or "", state=state)
    assert asked == ["4100000002"]


def test_a_strong_title_from_an_alert_scores_as_title_only_and_merges_with_jobspy(bucket):
    bucket.leave("a")
    alert = run(bucket).jobs[0]
    result = score_job(alert.title, alert.description, {"country": "France"})
    assert result.tier == "possible" and any("Title-only" in w for w in result.why_matched)
    spy = RawJob(source_type="aggregator", source_name="jobspy:linkedin", source_url="https://www.linkedin.com/jobs/view/4100000001", title=alert.title, company="Acme Topo",
                 url="https://careers.acme.example/42", apply_url="https://careers.acme.example/42", description=GIS_DESCRIPTION, source_job_id="jobspy:li-4100000001")
    fused = fuse([alert, spy], {}, NOW)
    assert len(fused) == 1 and fused[0].description == GIS_DESCRIPTION
