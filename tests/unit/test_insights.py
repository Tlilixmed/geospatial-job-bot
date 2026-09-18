"""Learning from the user's actions, the skills radar and procurement signals."""
from datetime import timedelta

from conftest import NOW, FakeResponse, FakeS3, FakeSession, make_ctx, make_settings
from test_commands import job, setup
from test_pipeline import FakeNotifier, StaticBackend, gis_raw

from geojobbot.core.pipeline import Pipeline
from geojobbot.core.prefs import load_prefs
from geojobbot.insights import radar, signals
from geojobbot.insights.learning import adjustment, apply_learning, build_model
from geojobbot.notifications.intents import interpret
from geojobbot.storage.r2 import R2Store
from geojobbot.storage.state import StateManager
from geojobbot.utils.text import job_code


def labelled_prefs():
    applied = {f"p:{i}": {"title": f"LiDAR Technician {i}", "company": "ScanCo", "skills": ["TerraScan"], "at": NOW.isoformat(),
                          "status": "interview" if i == 0 else "applied"} for i in range(3)}
    hidden = [f"n:{i}" for i in range(3)]
    hidden_info = {cid: {"title": "GIS Analyst, Defence Programs", "company": "Leidos", "skills": ["ArcGIS"], "at": NOW.isoformat()}
                   for cid in hidden}
    return {"applied": applied, "hidden": hidden, "hidden_info": hidden_info}


def test_model_learns_likes_and_dislikes_and_adjusts_once():
    model = build_model(labelled_prefs(), {})
    assert model["word:lidar"] > 0 and model["company:scanco"] > 0 and model["company:leidos"] < 0 and model["word:defence"] < 0
    liked = {"title": "Senior LiDAR Analyst", "company": "ScanCo", "score": 66, "tier": "possible", "score_breakdown": {},
             "rejection_reasons": [], "matched_skills": ["TerraScan (required)"]}
    assert apply_learning(liked, model, make_settings()) == 6  # clamped at +6
    assert liked["score"] == 72 and liked["tier"] == "high" and any("lidar" in b for b in liked["learned"]["because"])
    assert apply_learning(liked, model, make_settings()) == 0 and liked["score"] == 72  # not rescored: no double nudge
    disliked = {"title": "GIS Analyst, Defence", "company": "Leidos", "score": 74, "tier": "high", "score_breakdown": {}}
    assert apply_learning(disliked, model, make_settings()) == -8 and disliked["tier"] == "possible"
    blocked = {"title": "LiDAR Technician", "company": "ScanCo", "score": 80, "tier": "rejected", "score_breakdown": {},
               "rejection_reasons": ["WORK_AUTHORIZATION_REQUIRED"]}
    assert apply_learning(blocked, model, make_settings()) == 0 and blocked["tier"] == "rejected"


def test_learning_needs_enough_actions_and_can_be_switched_off_or_reset():
    few = {"applied": {"p:1": {"title": "LiDAR Technician", "at": NOW.isoformat()}}, "hidden": []}
    assert build_model(few, {}) == {}
    prefs = labelled_prefs()
    assert build_model({**prefs, "learning": False}, {}) == {}
    assert build_model({**prefs, "learning_since": (NOW + timedelta(hours=1)).isoformat()}, {}) == {}  # all of it predates the reset
    assert adjustment({"title": "Accountant"}, build_model(prefs, {})) == (0, [])


def test_learning_commands_and_why_line():
    rec = job("a:1", "LiDAR Technician", learned={"adj": 4, "because": ["+3 lidar", "+1 scanco"]})
    proc, replies, _, manager = setup([], [rec] + [job(f"h:{i}", "GIS Analyst, Defence Programs", company="Leidos") for i in range(3)])
    proc.run_text("/learning")
    assert "Nothing learned yet" in replies.sent[-1]
    for i in range(3):
        proc.run_text(f"/hide {job_code(f'h:{i}')}")
    proc.run_text(f"/applied {job_code('a:1')}")
    assert load_prefs(manager)["hidden_info"]["h:0"]["company"] == "Leidos"  # a snapshot survives the job being pruned
    proc.run_text("what have you learned")
    assert "You skip" in replies.sent[-1] and "leidos" in replies.sent[-1]
    proc.run_text(f"/why {job_code('a:1')}")
    assert "Learned from you: +4 points (+3 lidar, +1 scanco)" in replies.sent[-1]
    proc.run_text("forget what you learned")
    assert load_prefs(manager)["learning_since"] is not None
    assert interpret("stop learning from me") == ("learning", "off")


def test_pipeline_applies_learning():
    s3 = FakeS3()
    manager = StateManager(R2Store("b", client=s3))
    manager.write_json("state/prefs.json", labelled_prefs(), compress=False)
    raw = gis_raw(title="LiDAR Technician", company="ScanCo", source_job_id="static:1")
    _, report = Pipeline(make_settings(), store=R2Store("b", client=s3), backends=[StaticBackend("feed", [raw])],
                         notifier=FakeNotifier(), now=NOW, http_session=FakeSession(), sleep=lambda x: None).run()
    rec = StateManager(R2Store("b", client=s3)).load()["jobs"]["static:1"]
    assert report["counts"]["learned_adjustments"] == 1 and rec["learned"]["adj"] > 0 and rec["score_breakdown"]["learned"] > 0


def test_radar_separates_strengths_from_gaps():
    jobs = {}
    for i in range(10):
        skills = ["ArcGIS Pro (required)", "Python"] + (["PostGIS (required)"] if i < 5 else []) + (["FME (preferred)"] if i < 2 else [])
        jobs[f"j:{i}"] = job(f"j:{i}", "GIS Analyst", matched_skills=skills, matched_domains=["GIS", "LiDAR"], last_seen=NOW.isoformat())
    jobs["old"] = job("old", "GIS Analyst", matched_skills=["Oracle"], last_seen=(NOW - timedelta(days=90)).isoformat())
    data = radar.compute(jobs, ["ArcGIS Pro", "Python", "FME"], NOW)
    assert data["jobs"] == 10 and [g["skill"] for g in data["gaps"]] == ["PostGIS"]
    assert data["gaps"][0] == {"skill": "PostGIS", "share": 50, "required": 50, "have": False}
    assert [s["skill"] for s in data["strengths"]][:2] == ["ArcGIS Pro", "Python"]
    text = radar.format_radar(data)
    assert "PostGIS — 50% of matches, required in 50%" in text and "Gaps worth closing" in text
    assert "need at least" in radar.format_radar(radar.compute({}, [], NOW))

    proc, replies, _, manager = setup([], list(jobs.values()))
    proc.run_text("what should i learn")
    assert replies.sent[-1].startswith("↪ <i>/radar</i>") and "Skills radar" in replies.sent[-1]
    proc.run_text("/skills add postgis")
    assert "PostGIS" in load_prefs(manager)["my_skills"]  # canonical spelling
    proc.run_text("/radar")
    assert "Gaps worth closing" not in replies.sent[-1]


AWARD_TEXT = ("<p>Contract Award</p> Scope of Contract: LiDAR survey Awarded Firm(s): GEOFIT EXPERT (12345) Country: France "
              "Final Evaluation Price EUR 250000.00 Signed Contract Price EUR 250000.00")


def notice(i, **kw):
    base = {"id": f"OP{i}", "notice_type": "Request for Expression of Interest", "bid_description": "Consultant SIG et cartographie",
            "project_ctry_name": "Tunisia", "project_name": "Land Project", "submission_date": (NOW - timedelta(days=2)).isoformat(),
            "procurement_method_name": "Individual Consultant Selection"}
    base.update(kw)
    return base


def test_signals_classify_filter_and_dedupe():
    assert signals.classify(notice(1))["kind"] == "consultancy"
    award = signals.classify(notice(2, notice_type="Contract Award", bid_description="LiDAR survey of the coastal zone",
                                    procurement_method_name="Request for Bids", notice_text=AWARD_TEXT))
    assert (award["kind"], award["winner"], award["winner_country"], award["value"]) == ("award", "GEOFIT EXPERT", "France", "EUR 250000.00")
    assert signals.classify(notice(3, bid_description="Supply of office furniture for the cadastre agency")) is None
    assert signals.classify(notice(4, bid_description="Recruitment of a financial auditor")) is None

    payload = {"procnotices": [notice(1), notice(2, notice_type="Contract Award", bid_description="LiDAR survey",
                                                  notice_text=AWARD_TEXT, procurement_method_name="Request for Bids"),
                               notice(5, bid_description="GIS database", submission_date=(NOW - timedelta(days=90)).isoformat())]}
    s = FakeSession({"https://search.worldbank.org/api/v2/procnotices": FakeResponse(200, payload)})
    state = {"boards": {}, "cursors": {}, "page_cache": {}}
    ctx = make_ctx(s, state=state)
    new = signals.check(ctx, state)
    assert {x["id"] for x in new} == {"OP1", "OP2"}  # the three-month-old notice is ignored
    api_calls = [c for c in s.calls if "/procnotices" in c[1]]
    assert signals.check(ctx, state) == [] and len(api_calls) == signals.TERMS_PER_CHECK  # once a day, nothing twice
    text = signals.format_signals(new)
    assert text.index("Individual consultancies") < text.index("Firms that just won") and "won by <b>GEOFIT EXPERT</b>" in text
    assert interpret("any new tenders or consultancies?") == ("signals", "")

    proc, replies, _, manager = setup([], [])
    proc._state = {"jobs": {}, "signals": state["signals"]}
    proc.run_text("/signals")
    assert "Recent market signals" in replies.sent[-1] and "GEOFIT EXPERT" in replies.sent[-1]


def test_source_yield_counts_what_each_source_alone_delivered():
    from geojobbot.insights import yields
    from geojobbot.models import RawJob

    def raw(name):
        return RawJob(source_type="feed", source_name=name, source_url="https://x.example", title="t")

    state = {"jobs": {}}
    yields.record_run(state, [raw("jobspy:linkedin")] * 40 + [raw("jsearch:LinkedIn")] * 60 + [raw("remoteok")] * 200, NOW)
    yields.record_run(state, [raw("jsearch:Indeed")] * 10 + [raw("generic_html")] * 3, NOW)
    assert state["yield"]["days"][NOW.strftime("%Y-%m-%d")] == {"jobspy:linkedin": 40, "jsearch": 70, "remoteok": 200,
                                                                "career pages": 3}
    fresh = {"first_seen": NOW.isoformat(), "last_seen": NOW.isoformat()}
    state["jobs"] = {
        "a": job("a", "GIS Analyst", sources=[{"source_name": "jobspy:linkedin"}], **fresh),
        "b": job("b", "GIS Analyst", sources=[{"source_name": "jobspy:linkedin"}, {"source_name": "jsearch:Indeed"}], **fresh),
        "c": job("c", "GIS Analyst", tier="possible", sources=[{"source_name": "generic_jsonld"}], **fresh),
        "old": job("old", "GIS Analyst", sources=[{"source_name": "remoteok"}], first_seen=(NOW - timedelta(days=60)).isoformat()),
        "no": job("no", "Accountant", tier="rejected", sources=[{"source_name": "remoteok"}], **fresh),
    }
    for extra in range(4):  # jsearch finds five jobs, every one of them also found elsewhere
        state["jobs"][f"d{extra}"] = job(f"d{extra}", "GIS Analyst", sources=[{"source_name": "greenhouse"},
                                                                              {"source_name": "jsearch:Glassdoor"}], **fresh)
    data = yields.compute(state, {"applied": {"a": {}}}, NOW)
    rows = {r["source"]: r for r in data["rows"]}
    assert rows["jobspy:linkedin"] == {"source": "jobspy:linkedin", "raw": 40, "found": 2, "high": 2, "only": 1, "applied": 1}
    assert rows["jsearch"]["found"] == 5 and rows["jsearch"]["only"] == 0 and rows["career pages"]["only"] == 1
    assert rows["remoteok"]["found"] == 0 and data["accepted"] == 7
    text = yields.format_yield(data)
    assert "Noise so far: remoteok (200 raw" in text and "Redundant so far" in text and "jsearch" in text.split("Redundant")[1]
    assert "Uses an API quota without adding anything unique: jsearch" in text
    assert any("Best sources" in line for line in yields.weekly_lines(data))
    assert "No source statistics yet" in yields.format_yield(yields.compute({}, {}, NOW))

    proc, replies, _, _ = setup([], [])
    proc._state = state
    proc.run_text("which sources work best?")
    assert replies.sent[-1].startswith("↪ <i>/sources</i>") and "Source yield" in replies.sent[-1]
    # old buckets are dropped
    yields.record_run(state, [], NOW + timedelta(days=40))
    assert NOW.strftime("%Y-%m-%d") not in state["yield"]["days"]
