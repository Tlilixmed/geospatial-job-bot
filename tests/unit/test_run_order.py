"""The order of the adjustments after scoring: AI verdicts frame them, counts are taken last, stored jobs are rescored
from their kept text, and nothing the user should hear about is lost between runs."""
import json
from datetime import timedelta
from types import SimpleNamespace

from conftest import GIS_DESCRIPTION, NOW, FakeS3, FakeSession, make_settings
from test_ai import _run, ai_with
from test_pipeline import FakeNotifier, StaticBackend, gis_raw

from geojobbot.core.descriptions import DescriptionStore
from geojobbot.core.jobs import (SCORER_VERSION, ProcessOutcome, apply_ai_lift, apply_ai_veto, held_back_by_visa, is_listed,
                                 near_miss, select_alerts)
from geojobbot.core.pipeline import Pipeline
from geojobbot.insights.learning import build_model
from geojobbot.storage.r2 import R2Store
from geojobbot.storage.state import StateManager

FILLER = "You will join a friendly team and help colleagues with daily office duties, reporting and meetings. " * 4
PLANNER = "Plan fibre routes using QGIS and GIS data, digitizing of network assets and georeferencing of drawings. " + FILLER


def outcome_of(*ids):
    return ProcessOutcome(evaluated=[(None, None, SimpleNamespace(canonical_id=cid)) for cid in ids], seen_ids=set(ids))


def test_the_veto_is_judged_last_and_never_hides_what_the_user_was_told_about():
    settings = make_settings()
    veto = {"fit": 1, "veto": True}
    state = {"jobs": {
        "a:1": {"tier": "possible", "score": 58, "ai": dict(veto), "rejection_reasons": []},      # lifted by a bonus after the review
        "a:2": {"tier": "high", "score": 74, "ai": dict(veto), "rejection_reasons": []},          # the bonuses made it High: the rules win
        "a:3": {"tier": "possible", "score": 60, "ai": dict(veto), "rejection_reasons": [], "notified": True},
        "a:4": {"tier": "possible", "score": 60, "ai": dict(veto), "rejection_reasons": []},      # not scored this run: left alone
    }}
    pipeline = Pipeline(settings, store=R2Store("b", client=FakeS3()), now=NOW)
    pipeline.rescored = set()
    counts = pipeline._ai_vetoes(outcome_of("a:1", "a:2", "a:3"), state)
    assert counts["ai_vetoed"] == 1 and state["jobs"]["a:1"]["tier"] == "rejected"
    assert [state["jobs"][c]["tier"] for c in ("a:2", "a:3", "a:4")] == ["high", "possible", "possible"]
    assert apply_ai_veto(state["jobs"]["a:3"]) is False


def test_tier_counts_are_taken_once_at_the_end_and_match_the_state():
    weak = "Support the team with data tasks in QGIS. GIS exposure, digitizing and georeferencing of asset drawings. " * 5
    jobs = [gis_raw(), gis_raw(title="GIS Technician", url="https://acme.example/jobs/2", apply_url=None, description=weak,
                               source_job_id="static:2")]
    ai, _ = ai_with([json.dumps({"fit": 9, "summary": "Good."}), json.dumps({"fit": 1, "summary": "Warehouse tagging."})])
    s3 = FakeS3()
    report, _ = _run(s3, jobs, ai)
    stored = StateManager(R2Store("b", client=s3)).load()["jobs"]
    counts = report["counts"]
    assert {t: counts[f"tier_{t}"] for t in ("high", "possible", "rejected")} == {"high": 1, "possible": 0, "rejected": 1}
    assert all(counts[f"tier_{t}"] == sum(r["tier"] == t for r in stored.values()) for t in ("high", "possible", "rejected"))


def test_a_near_miss_gets_one_reading_and_a_strong_fit_lifts_it_for_good():
    settings = make_settings()
    rec = {"tier": "rejected", "score": 17, "rejection_reasons": ["NO_RELEVANT_TITLE"],
           "score_breakdown": {"title": 0, "tech": 6, "domain": 6, "responsibilities": 2, "location": 3}}
    assert near_miss(rec, settings)
    assert not near_miss({**rec, "rejection_reasons": ["NO_RELEVANT_TITLE", "WORK_AUTHORIZATION_REQUIRED"]}, settings)
    assert not near_miss({**rec, "score_breakdown": {"tech": 2, "domain": 2}}, settings)  # nothing geospatial in the text
    assert near_miss({"tier": "rejected", "score": 50, "rejection_reasons": ["LOW_SCORE"], "score_breakdown": {}}, settings)

    planner = gis_raw(title="Network Planner", description=PLANNER, source_job_id="static:9", url="https://acme.example/jobs/9", apply_url=None)
    ai, session = ai_with([json.dumps({"fit": 8, "summary": "Fibre network design in QGIS: the candidate's Smallworld work fits."})])
    s3 = FakeS3()
    report, notifier = _run(s3, [planner], ai)
    rec = StateManager(R2Store("b", client=s3)).load()["jobs"]["static:9"]
    assert report["counts"]["ai_second_chances"] == 1 and report["counts"]["ai_lifted"] == 1 and report["counts"]["tier_possible"] == 1
    rules_alone = sum(v for k, v in rec["score_breakdown"].items() if k != "ai")
    assert rec["tier"] == "possible" and rec["score"] == 55 and rec["score_breakdown"]["ai"] == 55 - rules_alone and rec["ai"]["lift"] is True
    assert "AI second opinion: strong fit (8/10)" in rec["why_matched"] and len(notifier.sent) == 1
    assert DescriptionStore.load(StateManager(R2Store("b", client=s3))).get("static:9")  # kept, like any accepted job's text
    # rescoring at the next run forgets the adjustment; the stored verdict puts it back without asking the model again
    ai2, session2 = ai_with([])
    _run(s3, [planner], ai2, now=NOW + timedelta(hours=2))
    again = StateManager(R2Store("b", client=s3)).load()["jobs"]["static:9"]
    assert session2.calls == [] and again["tier"] == "possible" and again["score"] == 55
    # a lukewarm reading changes nothing, and the job is not read again
    s3 = FakeS3()
    ai3, _ = ai_with([json.dumps({"fit": 5, "summary": "Telecom planning, little GIS."})])
    report, notifier = _run(s3, [planner], ai3)
    rec = StateManager(R2Store("b", client=s3)).load()["jobs"]["static:9"]
    assert rec["tier"] == "rejected" and rec["ai"]["lift"] is False and notifier.sent == []
    assert apply_ai_lift({"tier": "rejected", "score": 40, "rejection_reasons": ["NEGATIVE_TITLE"], "ai": {"lift": True}}, settings) is False


def test_the_clock_stops_the_reviews_and_second_chances_can_be_switched_off():
    ai, session = ai_with([json.dumps({"fit": 9, "summary": "Good."})] * 3)
    report, _ = _run(FakeS3(), [gis_raw()], ai, ai_time_budget_s=-1)
    assert session.calls == [] and report["counts"]["ai_deferred"] == 1 and report["counts"]["ai_stopped_by_clock"] == 1
    planner = gis_raw(title="Network Planner", description=PLANNER, source_job_id="static:9", url="https://acme.example/jobs/9", apply_url=None)
    ai, session = ai_with([])
    report, _ = _run(FakeS3(), [planner], ai, ai_second_chances_per_run=0)
    assert session.calls == [] and report["counts"].get("ai_second_chances", 0) == 0


def test_a_job_the_visa_penalty_holds_back_stays_in_play():
    settings = make_settings()
    held = {"tier": "rejected", "score": 50, "rejection_reasons": ["LOW_SCORE"], "score_breakdown": {"title": 40, "visa": -12}}
    assert held_back_by_visa(held, settings)
    assert not held_back_by_visa({**held, "score": 30}, settings)  # it would be rejected without the penalty too
    assert not held_back_by_visa({**held, "rejection_reasons": ["LOW_SCORE", "NEGATIVE_TITLE"]}, settings)
    store = DescriptionStore({"a:1": "x" * 400, "a:2": "y" * 400})
    store.prune({"a:1": held, "a:2": {"tier": "rejected"}}, keep_also=lambda rec: held_back_by_visa(rec, settings))
    assert set(store.data) == {"a:1"}


def test_descriptions_survive_a_failed_read_and_follow_an_edited_posting():
    class Broken:
        def read_json(self, suffix):
            from geojobbot.storage.base import StorageError
            raise StorageError("timeout")

        def write_json(self, *a, **kw):
            raise AssertionError("a store that could not be read must never be written over")

    store = DescriptionStore.load(Broken())
    store.put("a:1", "x" * 400)
    assert store.unreadable and store.save(Broken()) is False
    store = DescriptionStore({"a:1": "The old text of the posting. " * 40})
    edited = "The old text of the posting. " * 38 + "We are unable to sponsor visas for this role."
    store.put("a:1", edited)
    assert store.get("a:1") == edited  # similar length, new wording: the recheck must read today's text
    store.put("a:1", "A feed excerpt. " * 25)
    assert store.get("a:1") == edited  # never the excerpt over the posting


def test_stored_matches_are_rescored_when_the_rules_change_and_a_withdrawn_claim_takes_its_points_along():
    s3 = FakeS3()
    job = gis_raw(location_raw="Austin, TX", description=GIS_DESCRIPTION + " Visa sponsorship is available for this role.")
    _run(s3, [job], None)
    manager = StateManager(R2Store("b", client=s3))
    state = manager.load()
    rec = state["jobs"]["static:1"]
    assert rec["rules"] == SCORER_VERSION and "Visa sponsorship offered" in rec["why_matched"]
    offered_score = rec["score"]
    # the employer edits the posting text we keep; the job itself is not seen in the next run
    store = DescriptionStore.load(manager)
    store.data["static:1"] = GIS_DESCRIPTION + " We are unable to sponsor visas for this role."
    store.dirty = True
    store.save(manager)
    report, _ = _run(s3, [], None, now=NOW + timedelta(hours=2))
    rec = StateManager(R2Store("b", client=s3)).load()["jobs"]["static:1"]
    assert report["counts"]["sponsorship_claims_withdrawn"] == 1 and "Visa sponsorship offered" not in rec["why_matched"]
    assert rec["tier"] == "rejected" and "WORK_AUTHORIZATION_REQUIRED" in rec["rejection_reasons"] and rec["score"] < offered_score
    # an older scorer version: rescored from the kept text, place included ("Tunis, TN" was once Tennessee)
    s3 = FakeS3()
    _run(s3, [gis_raw(location_raw="Tunis, TN")], None)
    manager = StateManager(R2Store("b", client=s3))
    state = manager.load()
    state["jobs"]["static:1"].update(rules=1, country="United States", region="Tennessee", city="Tunis")
    manager.save(state, "test")
    report, _ = _run(s3, [], None, now=NOW + timedelta(hours=2))
    rec = StateManager(R2Store("b", client=s3)).load()["jobs"]["static:1"]
    assert report["counts"]["rescored_from_text"] == 1 and rec["country"] == "Tunisia" and rec["rules"] == SCORER_VERSION


def test_alerts_reach_back_to_matches_that_were_held_and_not_seen_again():
    settings = make_settings()
    base = {"tier": "high", "score": 80, "title": "GIS Analyst", "company": "Acme", "first_seen": (NOW - timedelta(days=2)).isoformat(),
            "last_seen": (NOW - timedelta(days=2)).isoformat(), "from_rotation": True}
    state = {"jobs": {"a:1": {**base, "canonical_id": "a:1"},
                      "a:2": {**base, "canonical_id": "a:2", "notified": True},
                      "a:3": {**base, "canonical_id": "a:3", "first_seen": (NOW - timedelta(days=40)).isoformat()},
                      "a:4": {**base, "canonical_id": "a:4", "last_seen": (NOW - timedelta(days=30)).isoformat()}}}
    selected, counts = select_alerts(state, set(), settings, NOW)
    assert [r["canonical_id"] for r in selected] == ["a:1"] and counts["carried_over"] == 1


def test_page_jobs_stay_listed_while_their_page_is_cached():
    seen = (NOW - timedelta(days=12)).isoformat()
    page = {"last_seen": seen, "sources": [{"source_type": "employer_page", "method": "jsonld"}]}
    feed = {"last_seen": seen, "sources": [{"source_type": "feed", "method": "api"}]}
    assert is_listed(page, NOW) and not is_listed(feed, NOW)
    assert not is_listed({**page, "last_seen": (NOW - timedelta(days=40)).isoformat()}, NOW)


def test_what_the_user_says_during_a_run_counts_for_its_digest():
    from geojobbot.core.prefs import default_prefs, save_prefs

    class PausingBackend(StaticBackend):
        def run(self, ctx):
            prefs = default_prefs()
            prefs["paused"] = True
            save_prefs(self.manager, prefs)  # the Worker writes /pause while the sources are being read
            return super().run(ctx)

    s3 = FakeS3()
    backend = PausingBackend("feed", [gis_raw()])
    backend.manager = StateManager(R2Store("b", client=s3))
    notifier = FakeNotifier()
    code, report = Pipeline(make_settings(), store=R2Store("b", client=s3), backends=[backend], notifier=notifier, now=NOW,
                            http_session=FakeSession(), sleep=lambda x: None).run()
    assert notifier.sent == [] and report["counts"]["paused"] == 1


def test_learning_is_judged_against_the_users_own_base_rate_and_forgotten_when_switched_off():
    applied = {f"a:{i}": {"title": "GIS Analyst", "company": f"Firm {i}", "at": NOW.isoformat()} for i in range(2)}
    hidden = {f"h:{i}": {"title": "GIS Sales Representative", "company": f"Shop {i}", "at": NOW.isoformat()} for i in range(10)}
    prefs = {"applied": applied, "hidden": list(hidden), "hidden_info": hidden}
    model = build_model(prefs, {})
    assert "word:gis" not in model  # every job says GIS: ten hides against two applications teach nothing about the word
    assert model["word:sales"] < 0 and model["word:analyst"] > 0

    from geojobbot.insights.learning import forget
    rec = {"score": 61, "tier": "possible", "score_breakdown": {"title": 40, "learned": -4}, "learned": {"adj": -4}, "rejection_reasons": []}
    assert forget(rec, make_settings()) and rec["score"] == 65 and "learned" not in rec and "learned" not in rec["score_breakdown"]


def test_marks_that_no_longer_hold_are_removed():
    from geojobbot.core.boards import BoardRegistry
    from geojobbot.insights import fx
    from geojobbot.scrapers.ats.detect import BoardRef

    settings = make_settings()
    settings.watch_list = []
    state = {"jobs": {"a:1": {"canonical_id": "a:1", "tier": "high", "company": "Fugro", "watched": True, "notified": True,
                              "last_seen": NOW.isoformat()}}}
    select_alerts(state, set(), settings, NOW)
    assert state["jobs"]["a:1"]["watched"] is False  # /unwatch reaches jobs that were not seen this run
    rec = {"salary": None, "salary_eur": [40000, 50000], "country": "France"}
    assert fx.annotate(rec, {"USD": 1.1}) is False and "salary_eur" not in rec
    registry = BoardRegistry({"boards": {"greenhouse:acme": {"ats": "greenhouse"}}}, NOW)
    assert registry.register(BoardRef("greenhouse", "esri"), "search") and registry._per_ats["greenhouse"] == 2
    assert registry.register(BoardRef("greenhouse", "esri"), "search") is False and registry._per_ats["greenhouse"] == 2
