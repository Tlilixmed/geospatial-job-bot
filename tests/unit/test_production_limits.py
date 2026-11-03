"""What the production run of 2026-11-03 showed: a library release stopping every search, sources leaving the AI no time,
Workday taking the whole clock, an index past the Worker's comfort, and failures that hid their reason."""
import sys
from collections import Counter
from datetime import timedelta
from types import SimpleNamespace

from conftest import NOW, FakeS3, FakeSession, make_ctx, make_settings
from test_pipeline import FakeNotifier

from geojobbot.core.index import MAX_NEAR_MISSES, build_index
from geojobbot.core.pipeline import Pipeline, build_backends
from geojobbot.core.report import _status_line
from geojobbot.scrapers.ats.base import ATSAdapter, ATSBackend, BoardResult
from geojobbot.scrapers.ats.detect import BoardRef
from geojobbot.scrapers.ats.more_ats import WorkdayAdapter
from geojobbot.scrapers.base import Backend, BackendOutput
from geojobbot.scrapers.feeds import JobSpyBackend
from geojobbot.scrapers.mail_alerts import jobspy_reader
from geojobbot.storage.r2 import R2Store


# ------------------------------------------------------------------ the clock
class ClockReader(Backend):
    name = "clock"

    def __init__(self, seen):
        self.seen = seen

    def run(self, ctx):
        self.seen["source"] = ctx.time_left()
        return BackendOutput()


def clock_run(monkeypatch, **settings_kw):
    seen = {}

    def review(self, outcome, state, ctx=None):
        seen["ai"] = ctx.time_left()
        return Counter()

    monkeypatch.setattr(Pipeline, "_ai_review", review)
    Pipeline(make_settings(run_time_budget_s=3000, ai_time_budget_s=400, **settings_kw), store=R2Store("b", client=FakeS3()), backends=[ClockReader(seen)],
             notifier=FakeNotifier(), now=NOW, http_session=FakeSession(), sleep=lambda x: None).run()
    return seen


def test_sources_stop_early_by_the_ai_budget_and_the_ai_gets_that_time_back(monkeypatch):
    seen = clock_run(monkeypatch, cloudflare_ai_token="token")
    assert 2500 < seen["source"] <= 2600 and seen["ai"] > 2900


def test_without_ai_the_sources_keep_the_whole_budget(monkeypatch):
    assert clock_run(monkeypatch)["source"] > 2900


class CountingAdapter(ATSAdapter):
    ats = "workday"

    def __init__(self, rotation_limit):
        self.rotation_limit = rotation_limit
        self.fetched = []

    def fetch_board(self, ctx, ref, *, geo_context, variant, company_hint):
        self.fetched.append(ref.slug)
        return BoardResult("VALID")


def rotation_run(limit):
    ctx = make_ctx(FakeSession(), settings=make_settings(rotation_boards_per_ats=60))
    for n in range(20):
        ctx.registry.register(BoardRef("workday", f"tenant{n}/wd3/careers"), "commoncrawl")
    adapter = CountingAdapter(limit)
    ATSBackend(adapter, ["configured/wd3/careers"]).run(ctx)
    return adapter.fetched


def test_a_slow_ats_rotates_through_fewer_discovered_boards_and_configured_ones_always_run():
    assert WorkdayAdapter.rotation_limit == 10 and ATSAdapter.rotation_limit is None
    limited = rotation_run(3)
    assert len(limited) == 4 and limited[0] == "configured/wd3/careers"
    assert len(rotation_run(None)) == 21  # other systems keep ROTATION_BOARDS_PER_ATS


# ------------------------------------------------------------------ JobSpy
def fake_jobspy(monkeypatch):
    asked = []

    def scrape_jobs(**kwargs):
        asked.append(kwargs)
        return None

    monkeypatch.setitem(sys.modules, "jobspy", SimpleNamespace(scrape_jobs=scrape_jobs))
    return asked


def test_worldwide_is_no_country_for_the_library(monkeypatch):
    asked = fake_jobspy(monkeypatch)
    settings = make_settings(jobspy_terms_per_run=1, jobspy_locations_per_run=2, jobspy_sites=["indeed", "linkedin"], jobspy_locations=["Tunisia@worldwide", "Canada@canada"])
    out = JobSpyBackend().run(make_ctx(FakeSession(), settings=settings))
    assert out.status == "SUCCESS" and len(asked) == 2
    tunisia, canada = asked
    assert tunisia["site_name"] == ["linkedin"] and "country_indeed" not in tunisia and tunisia["location"] == "Tunisia"
    assert canada["country_indeed"] == "canada" and canada["site_name"] == ["indeed", "linkedin"]


def test_the_alert_reader_is_off_when_jobspy_linkedin_is_off():
    assert jobspy_reader(make_settings(jobspy_enabled=False)) is None
    assert jobspy_reader(make_settings(jobspy_sites=["indeed"])) is None


# ------------------------------------------------------------------ the index
def record(tier, score, **kw):
    return {"tier": tier, "score": score, "title": "GIS Analyst", "company": "Acme", "last_seen": NOW.isoformat(), "rejection_reasons": ["LOW_SCORE"] if tier == "rejected" else [], **kw}


def test_the_index_lists_every_current_match_the_best_near_misses_and_nothing_stale():
    jobs = {f"a:{n}": record("high", 90) for n in range(3)}
    jobs |= {f"p:{n}": record("possible", 60) for n in range(3)}
    jobs |= {f"n:{n}": record("rejected", 35 + n % 20) for n in range(MAX_NEAR_MISSES + 50)}
    old = (NOW - timedelta(days=40)).isoformat()
    jobs["old:1"] = record("high", 99, posted_at=old, posted_at_reliable=True)
    jobs["old:2"] = record("possible", 80, posted_at=old)  # an unreliable date never hides a job
    index = build_index({"jobs": jobs}, make_settings(), {"counts": {}}, NOW, "help")
    tiers = Counter(j["tier"] for j in index["jobs"])
    assert tiers["high"] == 3 and tiers["possible"] == 4 and tiers["rejected"] == MAX_NEAR_MISSES
    assert "old:1" not in {j["id"] for j in index["jobs"]} and "old:2" in {j["id"] for j in index["jobs"]}
    near = [j["s"] for j in index["jobs"] if j["tier"] == "rejected"]
    assert near == sorted(near, reverse=True)


# ------------------------------------------------------------------ what the report says
def test_the_report_shows_each_source_time():
    assert "991s" in _status_line({"name": "workday", "status": "PARTIAL", "jobs": 12, "duration_s": 991.4, "error": None, "details": {}})
    assert "0s" not in _status_line({"name": "himalayas", "status": "SKIPPED", "jobs": 0, "duration_s": 0.0, "error": None, "details": {}})


class Refused(Backend):
    name = "metered"
    min_interval_hours = 4
    quota_bound = True

    def run(self, ctx):
        return BackendOutput(status="FAILED", error="a@b: AUTH_REQUIRED")


def test_a_quota_source_waiting_after_failures_keeps_saying_why():
    s3 = FakeS3()
    runs = []
    for minutes in (0, 5):
        _, report = Pipeline(make_settings(), store=R2Store("b", client=s3), backends=[Refused()], notifier=FakeNotifier(), now=NOW + timedelta(minutes=minutes),
                             http_session=FakeSession(), sleep=lambda x: None).run()
        runs.append(report["sources"][0])
    assert runs[0]["status"] == "FAILED"
    assert runs[1]["status"] == "SKIPPED" and "failed 1x (a@b: AUTH_REQUIRED)" in runs[1]["error"]


def test_the_real_backend_list_has_the_new_limit_in_place():
    names = {b.name: b for b in build_backends(make_settings())}
    assert names["workday"].adapter.rotation_limit == 10 and names["linkedin_alerts"].manager is None
