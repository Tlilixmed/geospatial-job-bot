"""Application deadlines, closing-soon reminders and repost detection."""
from datetime import date, timedelta

from conftest import GIS_DESCRIPTION, NOW, FakeS3
from test_commands import job, setup
from test_pipeline import FakeNotifier, StaticBackend, gis_raw, run

from geojobbot.core.jobs import alert_block_reason
from geojobbot.insights import timing
from geojobbot.notifications.telegram import format_digest
from geojobbot.storage.r2 import R2Store
from geojobbot.storage.state import StateManager
from geojobbot.utils.text import job_code
from conftest import make_settings


def test_deadlines_are_read_only_after_a_deadline_phrase():
    year = NOW.year
    find = lambda text, country=None: timing.find_deadline(text, NOW, country)  # noqa: E731
    soon = (NOW + timedelta(days=12)).date()
    assert find(f"Closing date: {soon.day} {soon.strftime('%B')} {soon.year} at 5pm") == soon
    assert find(f"Date limite de candidature : {soon.strftime('%d/%m/%Y')}", "France") == soon
    assert find(f"Applications close {soon.strftime('%m/%d/%Y')}", "United States") == soon
    assert find(f"Application deadline: {soon.isoformat()}") == soon
    assert find(f"<p>Closing Date:</p><p>{soon.day} {soon.strftime('%b')} {soon.year}</p>") == soon
    assert find(f"Posting End Date: {soon.strftime('%B')} {soon.day}, {soon.year}", "Canada") == soon
    assert find(f"Please apply by {soon.strftime('%b')} {soon.day}.") == soon  # no year: the next such day
    assert find(f"The project runs until 31 December {year + 3}.") is None
    assert find("We were founded on 3 March 1999. Open until filled.") is None
    assert find(f"Start date: {soon.day} {soon.strftime('%B')} {soon.year}") is None and find(None) is None


def test_badges_block_after_the_deadline_and_reposts():
    closing = job("d:1", "GIS Analyst", deadline=(NOW + timedelta(days=2)).date().isoformat(), last_seen=NOW.isoformat())
    assert timing.deadline_badge(closing, NOW) == "⏳ closes in 2 days"
    assert timing.deadline_badge(dict(closing, deadline=NOW.date().isoformat()), NOW) == "⏳ closes today"
    gone = dict(closing, deadline=(NOW - timedelta(days=1)).date().isoformat())
    assert alert_block_reason(gone, make_settings(), NOW) == "DEADLINE_PASSED" and alert_block_reason(closing, make_settings(), NOW) is None

    def posted(cid, days_ago, **kw):
        return job(cid, "LiDAR Technician", company="ScanCo", country="Canada", first_seen=(NOW - timedelta(days=days_ago)).isoformat(), **kw)

    jobs = {"a": posted("a", 120), "b": posted("b", 118), "c": posted("c", 60), "d": posted("d", 1),
            "other": job("other", "LiDAR Technician", company="Else Inc", country="Canada", first_seen=NOW.isoformat())}
    assert timing.mark_reposts(jobs) == 2
    assert "reposts" not in jobs["a"] and "reposts" not in jobs["b"] and "reposts" not in jobs["other"]  # b is the same wave as a
    assert jobs["c"]["reposts"]["count"] == 1 and jobs["d"]["reposts"]["count"] == 2
    since = date.fromisoformat(jobs["d"]["reposts"]["first"]).strftime("%b %Y")
    assert timing.repost_badge(jobs["d"]) == f"♻️ posted 2× again since {since}"
    text = format_digest([dict(jobs["d"], deadline=(NOW + timedelta(days=400)).date().isoformat())], now=NOW)[0][0]
    assert "♻️ posted 2× again" in text and "⏳ closes" in text


def test_pipeline_reads_the_deadline_and_reminds_once():
    s3 = FakeS3()
    closes = (NOW + timedelta(days=2)).date()
    raw = gis_raw(description=GIS_DESCRIPTION + f" Closing date: {closes.day} {closes.strftime('%B')} {closes.year}.")
    n = FakeNotifier()
    run(s3, [StaticBackend("feed", [raw])], n)
    rec = StateManager(R2Store("b", client=s3)).load()["jobs"]["static:1"]
    assert rec["deadline"] == closes.isoformat() and rec["deadline_reminded"] is True
    assert any("Closing soon — not applied yet" in m and job_code("static:1") in m for m in n.sent)
    n2 = FakeNotifier()
    run(s3, [StaticBackend("feed", [raw])], n2, now=NOW + timedelta(hours=4))
    assert not any("Closing soon" in m for m in n2.sent)
    index = StateManager(R2Store("b", client=s3)).read_json("state/index.json")
    assert index["jobs"][0]["dl"] == closes.isoformat()

    proc, replies, _, _ = setup([], [rec])
    proc.run_text(f"/why {job_code('static:1')}")
    assert "⏳ closes" in replies.sent[-1]
