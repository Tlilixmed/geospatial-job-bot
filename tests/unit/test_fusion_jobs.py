from datetime import timedelta

from conftest import GIS_DESCRIPTION, NOW, make_settings

from geojobbot.core.fusion import fuse
from geojobbot.core.jobs import mark_failed, mark_notified, process_fused, prune_state, select_alerts
from geojobbot.models import RawJob


def raw(**kw):
    base = dict(source_type="ats", source_name="greenhouse", source_url="https://boards-api.greenhouse.io/v1/boards/acme/jobs/1",
                title="GIS Analyst", company="Acme Mapping", url="https://boards.greenhouse.io/acme/jobs/1",
                apply_url="https://acme.com/careers?gh_jid=1", description=GIS_DESCRIPTION, location_raw="Remote - Canada",
                posted_at=NOW - timedelta(hours=5), posted_at_reliable=True, native_id="greenhouse:1")
    base.update(kw)
    return RawJob(**base)


def state():
    return {"jobs": {}, "boards": {}, "page_cache": {}, "sources": {}, "cursors": {}}


def test_same_job_from_four_sources_fuses_to_one_with_best_fields():
    observations = [
        raw(source_type="search", source_name="generic_html", extraction_method="html", native_id=None,
            url="https://acme.com/careers?gh_jid=1&utm_source=ddg", apply_url=None, description="short", company=None),
        raw(source_type="aggregator", source_name="jobspy:indeed", native_id=None, url="https://www.indeed.com/viewjob?jk=abc",
            apply_url="https://acme.com/careers?gh_jid=1", salary="USD 70k", description=GIS_DESCRIPTION + " extra"),
        raw(),
        raw(source_type="employer_page", source_name="generic_jsonld", extraction_method="jsonld", native_id=None,
            url="https://acme.com/careers?gh_jid=1", apply_url="https://acme.com/careers?gh_jid=1"),
    ]
    fused = fuse(observations, {}, NOW)
    assert len(fused) == 1
    f = fused[0]
    assert f.canonical_id == "greenhouse:1" and f.priority == 105
    assert f.description == GIS_DESCRIPTION and f.company == "Acme Mapping"
    assert f.apply_url == "https://acme.com/careers?gh_jid=1" and f.salary == "USD 70k"
    assert len(f.sources(NOW)) == 4


def test_feed_excerpt_loses_to_full_page_description():
    excerpt = "An excerpt of the posting. " * 9  # ~240 chars: feed summaries are this long
    feed = raw(source_type="feed", source_name="rss:gogeomatics", native_id=None, url="https://gogeomatics.ca/job/x/",
               apply_url=None, description=excerpt)  # feed + api bonus = 60
    page = raw(source_type="feed", source_name="generic_jsonld", extraction_method="jsonld", native_id=None,
               url="https://gogeomatics.ca/job/x/", apply_url=None, description=GIS_DESCRIPTION)  # feed + jsonld = 58
    f = fuse([feed, page], {}, NOW)[0]
    assert f.priority == 60 and f.description == GIS_DESCRIPTION


def test_fuzzy_merge_for_aggregator_without_ids():
    a = raw()
    b = raw(source_type="feed", source_name="remotive", native_id=None, url="https://remotive.com/job/555",
            apply_url="https://remotive.com/job/555", source_job_id="remotive:555", company="Acme Mapping Inc.")
    assert len(fuse([a, b], {}, NOW)) == 1


def test_fuzzy_never_merges_two_distinct_ats_postings():
    a = raw()
    b = raw(native_id="greenhouse:2", url="https://boards.greenhouse.io/acme/jobs/2", apply_url="https://acme.com/careers?gh_jid=2")
    assert len(fuse([a, b], {}, NOW)) == 2


def test_cross_run_identity_via_aliases():
    settings = make_settings()
    st = state()
    first = raw(source_type="feed", source_name="remotive", native_id=None, url="https://remotive.com/job/9",
                apply_url="https://remotive.com/job/9", source_job_id="remotive:9")
    process_fused(fuse([first], st["jobs"], NOW), st, settings, NOW)
    cid = next(iter(st["jobs"]))
    # next run: the same job is seen through its ATS; fuzzy alias links it to the stored record
    later = NOW + timedelta(hours=4)
    outcome = process_fused(fuse([raw()], st["jobs"], later), st, settings, later)
    assert list(st["jobs"]) == [cid] and outcome.counts["already_seen"] == 1
    rec = st["jobs"][cid]
    assert rec["field_priority"] == 105 and "greenhouse:1" in rec["aliases"]


def test_change_detection_and_lower_priority_does_not_overwrite():
    settings = make_settings()
    st = state()
    process_fused(fuse([raw()], st["jobs"], NOW), st, settings, NOW)
    t2 = NOW + timedelta(hours=4)
    process_fused(fuse([raw(source_type="aggregator", source_name="jobspy:indeed", salary="USD 1", native_id="greenhouse:1")],
                       st["jobs"], t2), st, settings, t2)
    assert st["jobs"]["greenhouse:1"]["salary"] is None
    t3 = NOW + timedelta(hours=8)
    out = process_fused(fuse([raw(title="Senior GIS Analyst", salary="CAD 90k")], st["jobs"], t3), st, settings, t3)
    rec = st["jobs"]["greenhouse:1"]
    assert out.counts["updated"] == 1 and set(rec["changes"][-1]["fields"]) >= {"title", "salary"}
    assert len(rec["sources"]) == 2


def test_alert_selection_dedup_and_retry_budget():
    settings = make_settings(max_alerts_per_run=10, max_notify_attempts=2)
    st = state()
    out = process_fused(fuse([raw()], st["jobs"], NOW), st, settings, NOW)
    selected, _ = select_alerts(st, out.seen_ids, settings, NOW)
    assert len(selected) == 1
    mark_failed(selected[0], "HTTP 500")
    selected, _ = select_alerts(st, out.seen_ids, settings, NOW)
    assert len(selected) == 1  # retried next time
    mark_notified(selected[0], NOW)
    selected, counts = select_alerts(st, out.seen_ids, settings, NOW)
    assert selected == [] and counts["already_notified"] == 1
    rec = st["jobs"]["greenhouse:1"]
    rec["notified"], rec["notify_attempts"] = False, 2
    selected, counts = select_alerts(st, out.seen_ids, settings, NOW)
    assert selected == [] and counts["attempts_exhausted"] == 1


def test_stale_blocking_and_rotation_window_and_missing_date():
    settings = make_settings(max_job_age_hours=48, rotation_max_job_age_hours=336)
    st = state()
    old = raw(posted_at=NOW - timedelta(days=5))
    undated = raw(native_id="greenhouse:3", url="https://boards.greenhouse.io/acme/jobs/3", apply_url=None,
                  title="LiDAR Specialist", posted_at=None, posted_at_reliable=False)
    rotated = raw(native_id="greenhouse:4", url="https://boards.greenhouse.io/acme/jobs/4", apply_url=None,
                  title="Cartographer", posted_at=NOW - timedelta(days=5), from_rotation=True)
    out = process_fused(fuse([old, undated, rotated], st["jobs"], NOW), st, settings, NOW)
    selected, counts = select_alerts(st, out.seen_ids, settings, NOW)
    ids = {r["canonical_id"] for r in selected}
    assert ids == {"greenhouse:3", "greenhouse:4"} and counts["blocked_stale"] == 1


def test_alert_cap_orders_high_first():
    settings = make_settings(max_alerts_per_run=1)
    st = state()
    weak = raw(native_id="greenhouse:8", url="https://boards.greenhouse.io/acme/jobs/8", apply_url=None,
               title="Mapping Technician", description="")
    out = process_fused(fuse([weak, raw()], st["jobs"], NOW), st, settings, NOW)
    selected, counts = select_alerts(st, out.seen_ids, settings, NOW)
    assert selected[0]["canonical_id"] == "greenhouse:1" and counts["deferred_by_cap"] == 1


def test_prune():
    settings = make_settings()
    st = state()
    st["jobs"]["old_rej"] = {"tier": "rejected", "last_seen": (NOW - timedelta(days=40)).isoformat()}
    st["jobs"]["old_match"] = {"tier": "high", "notified": True, "last_seen": (NOW - timedelta(days=40)).isoformat()}
    st["page_cache"]["u"] = {"fetched_at": (NOW - timedelta(days=31)).isoformat()}
    counts = prune_state(st, settings, NOW)
    assert list(st["jobs"]) == ["old_match"] and counts["page_cache_pruned"] == 1
