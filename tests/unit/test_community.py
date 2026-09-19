"""Hacker News 'Who is hiring' as a source."""
from conftest import GIS_DESCRIPTION, NOW, FakeResponse, FakeSession, make_ctx

from geojobbot.scrapers.community import ALGOLIA, HackerNewsHiringBackend, split_header

THREAD = {"objectID": "49522897", "title": "Ask HN: Who is hiring? (September 2026)", "created_at": NOW.isoformat()}


def comment(oid, text, parent=49522897):
    return {"objectID": oid, "parent_id": parent, "created_at": NOW.isoformat(), "comment_text": text}


def test_header_parsing():
    assert split_header("Acme Geo | Senior GIS Developer | Toronto, ON | REMOTE (Canada) | Full-time\nWe build...") == (
        "Acme Geo", "Senior GIS Developer", "Toronto, ON")
    assert split_header("Foo Inc | REMOTE | Backend Engineer\nblah")[1] == "Backend Engineer"
    assert split_header("just prose without pipes") == (None, None, "")


def test_only_geospatial_top_level_posts_become_jobs():
    hits = [comment("1", "Acme Geo | Senior GIS Developer | Toronto, ON | REMOTE | " + GIS_DESCRIPTION + " https://acme.example/jobs"),
            comment("2", "Bank Co | Backend Engineer | NYC | ONSITE. We do payments with Kafka."),  # not geospatial
            comment("3", "Great role, is it open to EU?", parent=1),  # a reply
            comment("4", "Satellite Co | Cloud Infrastructure SRE | Denver | satellite imagery pipelines on AWS")]  # geo words, wrong title
    session = FakeSession({f"{ALGOLIA}/search_by_date": FakeResponse(200, {"hits": [THREAD]}),
                           f"{ALGOLIA}/search": FakeResponse(200, {"hits": hits})})
    out = HackerNewsHiringBackend().run(make_ctx(session))
    assert out.status == "SUCCESS" and [j.title for j in out.jobs] == ["Senior GIS Developer"]
    job = out.jobs[0]
    assert job.company == "Acme Geo" and job.apply_url == "https://acme.example/jobs" and job.source_job_id == "hn:1"
    assert job.url == "https://news.ycombinator.com/item?id=1" and job.location_raw == "Toronto, ON"
    assert out.details["geospatial"] == 2 and out.prefiltered_out == 1
    failing = FakeSession({f"{ALGOLIA}/search_by_date": FakeResponse(500, {})})
    assert HackerNewsHiringBackend().run(make_ctx(failing)).status == "FAILED"
