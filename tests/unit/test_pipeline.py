"""End-to-end pipeline tests with injected backends, fake HTTP, fake R2 and fake Telegram."""
from datetime import timedelta

from conftest import GIS_DESCRIPTION, NOW, FakeResponse, FakeS3, FakeSession, make_settings

from geojobbot.core.pipeline import Pipeline, build_backends
from geojobbot.models import BackendOutput, RawJob
from geojobbot.scrapers.ats.base import ATSBackend
from geojobbot.scrapers.ats.core_ats import GreenhouseAdapter
from geojobbot.scrapers.base import Backend
from geojobbot.storage.r2 import R2Store
from geojobbot.storage.state import StateManager
from geojobbot.utils.text import job_code


class StaticBackend(Backend):
    def __init__(self, name, jobs, source_type="feed"):
        self.name, self.jobs, self.source_type = name, jobs, source_type

    def run(self, ctx):
        return BackendOutput(jobs=list(self.jobs))


class CrashingBackend(Backend):
    name = "crasher"

    def run(self, ctx):
        raise RuntimeError("boom")


class FakeNotifier:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, text):
        if self.fail:
            return False, "HTTP 502: Bad Gateway"
        self.sent.append(text)
        return True, None


def gis_raw(**kw):
    base = dict(source_type="feed", source_name="static", source_url="https://feed.example/api", title="GIS Analyst",
                company="Acme", url="https://acme.example/jobs/1", apply_url="https://acme.example/jobs/1",
                description=GIS_DESCRIPTION, location_raw="Remote", posted_at=NOW - timedelta(hours=2),
                posted_at_reliable=True, source_job_id="static:1")
    base.update(kw)
    return RawJob(**base)


def run(s3, backends, notifier, now=NOW, **settings_kw):
    settings = make_settings(**settings_kw)
    return Pipeline(settings, store=R2Store("b", client=s3), backends=backends, notifier=notifier, now=now,
                    http_session=FakeSession(), sleep=lambda x: None).run()


def test_full_run_alerts_once_across_fresh_runners():
    s3 = FakeS3()
    n1 = FakeNotifier()
    code, report = run(s3, [StaticBackend("feed_a", [gis_raw()]), CrashingBackend(),
                            StaticBackend("feed_b", [gis_raw(source_name="other", source_job_id="other:1")])], n1)
    assert code == 0 and len(n1.sent) == 1
    statuses = {r["name"]: r["status"] for r in report["sources"]}
    assert statuses["crasher"] == "FAILED" and statuses["feed_a"] == "SUCCESS"
    assert report["counts"]["unique"] == 1 and report["storage"]["saved"]
    assert any(k.startswith("runs/") for k in s3.objects) and "runs/latest.json" in s3.objects
    assert any(k.startswith("raw/") for k in s3.objects) is False  # static backends take no snapshots

    # new runner: nothing local, same R2 -> no duplicate alert
    n2 = FakeNotifier()
    code, report = run(s3, [StaticBackend("feed_a", [gis_raw()])], n2, now=NOW + timedelta(hours=4))
    assert code == 0 and n2.sent == [] and report["counts"]["already_seen"] == 1


def test_failed_delivery_is_retried_next_run():
    s3 = FakeS3()
    run(s3, [StaticBackend("feed", [gis_raw()])], FakeNotifier(fail=True))
    rec = StateManager(R2Store("b", client=s3)).load()["jobs"]["static:1"]
    assert rec["notified"] is False and rec["notify_attempts"] == 1
    n = FakeNotifier()
    run(s3, [StaticBackend("feed", [gis_raw()])], n, now=NOW + timedelta(hours=1))
    assert len(n.sent) == 1


def test_state_load_failure_aborts_before_scraping():
    s3 = FakeS3()
    s3.fail = ConnectionError("r2 down")
    n = FakeNotifier()
    backend = StaticBackend("feed", [gis_raw()])
    code, report = run(s3, [backend], n)
    assert code == 1 and "state load failed" in report["fatal"] and n.sent == [] and report["sources"] == []


def test_dry_run_prints_and_does_not_write(capsys):
    s3 = FakeS3()
    code, report = run(s3, [StaticBackend("feed", [gis_raw()])], None, dry_run=True)
    assert code == 0 and "DRY RUN ALERT" in capsys.readouterr().out
    assert s3.objects == {} and report["counts"]["alerts_pending"] == 1


def test_all_sources_failed_exit_code():
    code, report = run(FakeS3(), [CrashingBackend()], FakeNotifier())
    assert code == 2


def test_min_interval_skips_feed():
    class Interval(StaticBackend):
        min_interval_hours = 12
    s3 = FakeS3()
    run(s3, [Interval("slow", [gis_raw()])], FakeNotifier())
    _, report = run(s3, [Interval("slow", [gis_raw()])], FakeNotifier(), now=NOW + timedelta(hours=1))
    assert report["sources"][0]["status"] == "SKIPPED"


def test_real_ats_backend_invalid_board_in_report():
    gh = "https://boards-api.greenhouse.io/v1/boards"
    session = FakeSession({f"{gh}/bad/jobs": FakeResponse(404),
                           f"{gh}/good/jobs": FakeResponse(200, {"jobs": [{"id": 5, "title": "GIS Technician"}]}),
                           f"{gh}/good": FakeResponse(200, {"name": "Good Geo"}),
                           f"{gh}/good/jobs/5": FakeResponse(200, {"id": 5, "title": "GIS Technician", "content": GIS_DESCRIPTION,
                                                                  "location": {"name": "Remote"},
                                                                  "absolute_url": "https://boards.greenhouse.io/good/jobs/5",
                                                                  "first_published": (NOW - timedelta(hours=1)).isoformat()})})
    n = FakeNotifier()
    settings = make_settings()
    code, report = Pipeline(settings, store=R2Store("b", client=FakeS3()), notifier=n, now=NOW, http_session=session,
                            backends=[ATSBackend(GreenhouseAdapter(), ["bad", "good"])], sleep=lambda x: None).run()
    assert report["invalid_configured"] == {"greenhouse": ["bad"]} and "Good Geo" in n.sent[0]
    assert len(n.sent) == 2 and "Bot health" in n.sent[1] and "greenhouse:bad" in n.sent[1]  # told once about the dead slug


def test_build_backends_from_config():
    settings = make_settings(sources={"ats": {"greenhouse": ["a"], "workday": ["https://t.wd5.myworkdayjobs.com/Site", "junk"]}})
    names = {b.name: b for b in build_backends(settings)}
    assert names["workday"].configured_slugs == ["t/wd5/Site"]
    assert {"greenhouse", "lever", "ashby", "commoncrawl", "generic_pages", "career_sites", "jobspy"} <= set(names)


def test_empty_generic_queue_does_not_mask_total_failure():
    from geojobbot.scrapers.pages import GenericPagesBackend
    from geojobbot.scrapers.ats.more_ats import all_adapters
    code, report = run(FakeS3(), [CrashingBackend(), GenericPagesBackend(all_adapters())], FakeNotifier())
    assert code == 2
    assert {r["name"]: r["status"] for r in report["sources"]}["generic_pages"] == "SKIPPED"


def many_jobs(n):
    return [gis_raw(title=f"GIS Analyst {i}", url=f"https://acme.example/jobs/{i}", apply_url=f"https://acme.example/jobs/{i}",
                    description=GIS_DESCRIPTION + f" Team {i}.", source_job_id=f"static:{i}") for i in range(n)]


def test_digest_sends_one_list_and_marks_every_job_delivered():
    s3, n = FakeS3(), FakeNotifier()
    code, report = run(s3, [StaticBackend("feed", many_jobs(5))], n)
    assert code == 0 and len(n.sent) == 1
    assert report["counts"]["alerts_sent"] == 5 and report["counts"]["messages_sent"] == 1
    assert "5 new matches" in n.sent[0] and "5. <b>GIS Analyst" in n.sent[0]
    jobs = StateManager(R2Store("b", client=s3)).load()["jobs"]
    assert all(jobs[f"static:{i}"]["notified"] for i in range(5))


def test_digest_failure_leaves_every_job_for_retry():
    s3 = FakeS3()
    run(s3, [StaticBackend("feed", many_jobs(3))], FakeNotifier(fail=True))
    jobs = StateManager(R2Store("b", client=s3)).load()["jobs"]
    assert all(not rec["notified"] and rec["notify_attempts"] == 1 for rec in jobs.values())
    n = FakeNotifier()
    _, report = run(s3, [StaticBackend("feed", many_jobs(3))], n, now=NOW + timedelta(hours=1))
    assert len(n.sent) == 1 and report["counts"]["alerts_sent"] == 3


def test_individual_format_sends_one_message_per_job():
    n = FakeNotifier()
    run(FakeS3(), [StaticBackend("feed", many_jobs(2))], n, alert_format="individual")
    assert len(n.sent) == 2 and all("HIGH MATCH" in message for message in n.sent)


def test_weekly_summary_sent_once_per_week():
    s3, n = FakeS3(), FakeNotifier()
    _, report = run(s3, [StaticBackend("feed", many_jobs(1))], n, weekly_summary=True)
    assert report["counts"]["weekly_summary_sent"] == 1 and any("Weekly job summary" in m for m in n.sent)
    n2 = FakeNotifier()
    _, report = run(s3, [StaticBackend("feed", many_jobs(1))], n2, now=NOW + timedelta(days=2), weekly_summary=True)
    assert report["counts"]["weekly_summary_sent"] == 0 and n2.sent == []
    n3 = FakeNotifier()
    _, report = run(s3, [StaticBackend("feed", many_jobs(1))], n3, now=NOW + timedelta(days=8), weekly_summary=True)
    assert report["counts"]["weekly_summary_sent"] == 1


def test_health_alerts_fire_once_on_a_failure_streak_and_on_recovery():
    from geojobbot.core.health import FAIL_STREAK
    s3 = FakeS3()
    sent = []
    for i in range(FAIL_STREAK + 1):
        n = FakeNotifier()
        run(s3, [CrashingBackend(), StaticBackend("feed", [])], n, now=NOW + timedelta(hours=2 * i))
        sent.append([m for m in n.sent if "Bot health" in m])
    assert [len(x) for x in sent] == [0, 0, 1, 0]  # told once, on the third consecutive failure
    assert "crasher" in sent[2][0] and "3 runs in a row" in sent[2][0]

    class Recovered(StaticBackend):
        pass
    n = FakeNotifier()
    run(s3, [Recovered("crasher", [])], n, now=NOW + timedelta(hours=10))
    assert any("working again" in m for m in n.sent)


def test_possible_matches_are_alerted_only_when_enabled():
    weak = "Support the team with data tasks in QGIS. GIS exposure, digitizing and georeferencing of asset drawings. " * 5
    possible = gis_raw(title="GIS Technician", description=weak, source_job_id="static:9", url="https://acme.example/jobs/9",
                       apply_url="https://acme.example/jobs/9")
    n = FakeNotifier()
    run(FakeS3(), [StaticBackend("feed", [possible])], n, notify_possible=False)
    assert n.sent == []
    n = FakeNotifier()
    run(FakeS3(), [StaticBackend("feed", [possible])], n, notify_possible=True)
    assert len(n.sent) == 1 and "Possible matches" in n.sent[0]


def test_run_publishes_the_index_the_worker_answers_from():
    s3 = FakeS3()
    run(s3, [StaticBackend("feed", [gis_raw()])], FakeNotifier())
    index = StateManager(R2Store("b", client=s3)).read_json("state/index.json")
    assert index["schema"] == 1 and index["generated_at"] and "/jobs" in index["help"]
    assert index["settings"]["high"] == 70 and index["run"]["jobs_in_state"] == 1 and index["run"]["code"] == "local"
    (entry,) = index["jobs"]
    assert entry["id"] == "static:1" and entry["code"] == job_code("static:1") and entry["tier"] == "high"
    assert entry["t"] == "GIS Analyst" and entry["c"] == "Acme" and entry["url"] == "https://acme.example/jobs/1"
    assert entry["rel"] is True and entry["seen"] and entry["bd"]["title"] > 0 and entry["src"] == ["static"]
