"""Workers AI second opinion: validated, capped, soft-failing, and never above the deterministic scorer."""
import json
from datetime import timedelta

from conftest import GIS_DESCRIPTION, NOW, FakeResponse, FakeS3, FakeSession, make_settings
from test_commands import job, setup
from test_pipeline import FakeNotifier, StaticBackend, gis_raw

from geojobbot.ai.client import WorkersAI, account_id_from
from geojobbot.ai.review import clean_review, review_job
from geojobbot.core.pipeline import Pipeline
from geojobbot.notifications.telegram import format_digest
from geojobbot.storage.r2 import R2Store
from geojobbot.storage.state import StateManager
from geojobbot.utils.text import job_code

ACCOUNT = "0123456789abcdef0123456789abcdef"
URL = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/run/@cf/meta/llama-3.1-8b-instruct"


def ai_with(responses, budget=25):
    """WorkersAI whose HTTP layer answers with the given texts, one per call."""
    queue = list(responses)

    def handler(method, url, params, body):
        text = queue.pop(0) if queue else "{}"
        if isinstance(text, FakeResponse):
            return text
        return FakeResponse(200, {"success": True, "result": {"response": text}})

    session = FakeSession({URL: handler})
    return WorkersAI(ACCOUNT, "token", model="@cf/meta/llama-3.1-8b-instruct", session=session, budget=budget), session


def test_account_id_is_derived_from_the_r2_endpoint():
    s = make_settings(r2_endpoint_url=f"https://{ACCOUNT}.r2.cloudflarestorage.com", cloudflare_ai_token="t")
    assert account_id_from(s) == ACCOUNT and WorkersAI.from_settings(s) is not None
    assert WorkersAI.from_settings(make_settings()) is None  # no token: AI is simply off


def test_review_is_parsed_from_chatty_output_and_clamped():
    chatty = 'Sure! Here is the JSON:\n```json\n{"fit": 14, "summary": "GIS analyst role using ArcGIS Pro; strong fit.", ' \
             '"concerns": "None", "years": "3", "sponsorship": "Offered", "languages": ["English"], ' \
             '"requirements": ["ArcGIS Pro", "Python", "x", "y", "z", "extra"],}\n```'
    ai, session = ai_with([chatty])
    review = review_job(ai, "", title="GIS Analyst", company="Acme", location="Dubai", description=GIS_DESCRIPTION)
    assert review["fit"] == 10 and review["concerns"] == "" and review["years"] == 3 and review["sponsorship"] == "offered"
    assert len(review["requirements"]) == 5 and "GIS Analyst" in session.calls[0][2]["messages"][1]["content"]
    assert clean_review({"summary": "no fit value"}) is None and clean_review("nonsense") is None


def test_client_fails_soft_and_respects_budget():
    ai, session = ai_with([FakeResponse(500, {"success": False, "errors": [{"message": "down"}]})] * 5, budget=10)
    assert [ai.chat("s", "u") for _ in range(5)] == [None] * 5
    assert len(session.calls) == 3  # three strikes, then it stops calling
    ai, session = ai_with(['{"fit": 5}'] * 5, budget=2)
    assert [ai.chat_json("s", "u") is not None for _ in range(4)] == [True, True, False, False] and len(session.calls) == 2


def _run(s3, jobs, ai, now=NOW, **kw):
    settings = make_settings(**kw)
    notifier = FakeNotifier()
    code, report = Pipeline(settings, store=R2Store("b", client=s3), backends=[StaticBackend("feed", jobs)], notifier=notifier,
                            now=now, http_session=FakeSession(), sleep=lambda x: None, ai=ai).run()
    return report, notifier


def test_pipeline_reviews_once_shows_summary_and_vetoes_only_possible():
    strong = gis_raw(title="GIS Analyst", source_job_id="static:1")
    weak_text = "Support the team with data tasks in QGIS. GIS exposure, digitizing and georeferencing of asset drawings. " * 5
    borderline = gis_raw(title="GIS Technician", url="https://acme.example/jobs/2", apply_url="https://acme.example/jobs/2",
                         description=weak_text, source_job_id="static:2")
    good = json.dumps({"fit": 9, "summary": "Utility GIS analyst role with ArcGIS Pro and Python.", "concerns": "requires 5+ years",
                       "requirements": ["ArcGIS Pro"]})
    bad = json.dumps({"fit": 1, "summary": "Mostly warehouse asset tagging.", "concerns": ""})
    ai, session = ai_with([good, bad])
    s3 = FakeS3()
    report, notifier = _run(s3, [strong, borderline], ai)
    jobs = StateManager(R2Store("b", client=s3)).load()["jobs"]
    assert jobs["static:1"]["tier"] == "high" and jobs["static:1"]["ai"]["fit"] == 9 and not jobs["static:1"]["ai"]["veto"]
    assert jobs["static:2"]["tier"] == "rejected" and "AI_NOT_RELEVANT" in jobs["static:2"]["rejection_reasons"]
    assert report["counts"]["ai_reviewed"] == 2 and report["counts"]["ai_vetoed"] == 1
    assert len(notifier.sent) == 1 and "💡 Utility GIS analyst role" in notifier.sent[0] and "⚠️ requires 5+ years" in notifier.sent[0]
    assert "GIS Technician" not in notifier.sent[0]
    # next run: nothing is reviewed again and the veto survives rescoring
    ai2, session2 = ai_with([])
    report, _ = _run(s3, [strong, borderline], ai2, now=NOW + timedelta(hours=4))
    jobs = StateManager(R2Store("b", client=s3)).load()["jobs"]
    assert session2.calls == [] and report["counts"].get("ai_reviewed", 0) == 0 and jobs["static:2"]["tier"] == "rejected"


def test_high_matches_are_never_vetoed_and_ai_outage_changes_nothing():
    ai, _ = ai_with([json.dumps({"fit": 0, "summary": "Model is confused."})])
    s3 = FakeS3()
    _run(s3, [gis_raw()], ai)
    rec = StateManager(R2Store("b", client=s3)).load()["jobs"]["static:1"]
    assert rec["tier"] == "high" and rec["ai"]["veto"] is True  # recorded, but a High match stays High
    ai, _ = ai_with([FakeResponse(503, {"success": False})] * 3)
    s3 = FakeS3()
    report, notifier = _run(s3, [gis_raw()], ai)
    assert report["counts"]["ai_failed"] == 1 and len(notifier.sent) == 1  # alert goes out regardless


def test_digest_without_review_is_unchanged_and_pitch_command():
    assert "💡" not in format_digest([job("a:1", "GIS Analyst")], now=NOW)[0][0]
    rec = job("a:1", "Ingénieur SIG", ai={"fit": 8, "summary": "French GIS role.", "requirements": ["QGIS", "PostGIS"]})
    ai, session = ai_with(["Madame, Monsieur, je suis ingénieur géomaticien <expérimenté> ..."])
    proc, replies, _, _ = setup([], [rec])
    proc.ai = ai
    proc.run_text(f"write a cover letter for {job_code('a:1')}")
    draft = replies.sent[-1]
    assert draft.startswith("↪ <i>/pitch") and "Draft for Ingénieur SIG" in draft and "&lt;expérimenté&gt;" in draft
    assert "QGIS; PostGIS" in session.calls[0][2]["messages"][1]["content"]
    proc.ai = None
    proc.run_text(f"/pitch {job_code('a:1')}")
    assert "CLOUDFLARE_AI_TOKEN" in replies.sent[-1]  # clear message when AI is not configured


def test_ai_view_weekly_lines_status_coverage_and_intent():
    from geojobbot.notifications.intents import interpret
    from geojobbot.notifications.weekly import format_weekly
    assert interpret("ai summary") == ("ai", "") and interpret("what does the AI think of the top 5") == ("ai", "5")
    assert interpret("weekly summary") == ("weekly", "") and interpret("deuxième avis") == ("ai", "")
    good = {"fit": 9, "summary": "Utility GIS role, strong ArcGIS Pro match.", "concerns": "requires 5+ years", "years": 5,
            "sponsorship": "offered", "languages": ["English", "French"]}
    jobs = [job("a:1", "GIS Data Analyst", company="Pomerleau", score=96, ai=good, last_seen=NOW.isoformat()),
            job("a:2", "GIS Data Analyst", company="Pomerleau", score=96, last_seen=NOW.isoformat()),
            job("a:3", "LiDAR Technician", company="ScanCo", score=88, last_seen=NOW.isoformat(),
                ai={"fit": 6, "summary": "Point-cloud processing.", "concerns": ""})]
    proc, replies, _, _ = setup([], jobs)
    proc.settings.cloudflare_ai_token = "t"
    proc.run_text("ai summary")
    view = replies.sent[-1]
    assert view.startswith("↪ <i>/ai</i>") and "2 of 3 current matches reviewed" in view
    assert view.index("GIS Data Analyst") < view.index("LiDAR Technician")  # best fit first
    assert "🎯 fit 9/10 · score 96" in view and "💡 Utility GIS role" in view and "⚠️ requires 5+ years" in view
    assert "5+ yrs · 🛂 sponsorship offered · English, French" in view
    proc.run_text("/status")
    assert "AI second opinion: 2 of 3 current matches reviewed" in replies.sent[-1]
    weekly = format_weekly(proc.state, proc.prefs, proc.settings, NOW)
    assert weekly.count("GIS Data Analyst") == 1 and "×2" in weekly and "🎯 fit 9/10" in weekly and "💡 Utility GIS role" in weekly
    assert "2 of them reviewed by the AI so far" in weekly
    # nothing reviewed yet: say so, and say why
    proc2, replies2, _, _ = setup([], [job("b:1", "GIS Analyst")])
    proc2.run_text("/ai")
    assert "is off" in replies2.sent[-1]
    proc2.settings.cloudflare_ai_token = "t"
    proc2.run_text("/ai")
    assert "No current match has an AI review yet (1 waiting)" in replies2.sent[-1]


def test_descriptions_are_kept_and_the_backlog_gets_reviewed_later():
    from geojobbot.core.descriptions import DescriptionStore
    s3 = FakeS3()
    _run(s3, [gis_raw()], None)  # no AI yet: the accepted job's text is remembered anyway
    manager = StateManager(R2Store("b", client=s3))
    assert GIS_DESCRIPTION[:40] in DescriptionStore.load(manager).get("static:1")
    ai, session = ai_with([json.dumps({"fit": 8, "summary": "Backlog review worked."})])
    # a later run that does not see the job at all still reviews it from the stored text
    report, _ = _run(s3, [], ai, now=NOW + timedelta(hours=2))
    rec = StateManager(R2Store("b", client=s3)).load()["jobs"]["static:1"]
    assert report["counts"]["ai_reviewed"] == 1 and rec["ai"]["summary"] == "Backlog review worked."
    # descriptions of jobs that are no longer accepted are pruned
    store = DescriptionStore({"static:1": "x" * 400, "gone:1": "y" * 400})
    assert store.prune({"static:1": {"tier": "high"}}) == 1 and list(store.data) == ["static:1"]


def test_interview_sheet_and_speculative_application():
    from test_commands import job, setup

    from geojobbot.notifications.intents import interpret
    from geojobbot.utils.text import job_code

    rec = job("a:1", "GIS Analyst", company="Acme", country="United Kingdom", salary="£45,000 a year",
              sponsor=[{"label": "UK licensed sponsor", "name": "ACME LTD", "country": "United Kingdom"}])
    proc, replies, _, manager = setup([], [rec])
    code = job_code("a:1")
    proc.run_text(f"/prep {code}")  # no AI configured: the known facts still come
    assert "Interview sheet: GIS Analyst" in replies.sent[-1] and "UK licensed sponsor: ACME LTD" in replies.sent[-1]
    assert "needs Workers AI" in replies.sent[-1]

    ai, session = ai_with(["LIKELY QUESTIONS: Tell us about your LiDAR work -> describe the TerraScan classification project",
                           "Objet : Candidature spontanée\nBonjour, vous venez de remporter le marché LiDAR au Sénégal…"])
    proc.ai = ai
    proc.run_text(f"/outcome {code} interview")
    assert "An interview!" in replies.sent[-2] and "LIKELY QUESTIONS" in replies.sent[-1] and "What I know" in replies.sent[-1]
    assert "Posted salary: £45,000 a year" in str(session.calls[0][2])  # the AI is given the facts the bot established

    proc._state = {"jobs": {}, "signals": {"items": [{"kind": "award", "winner": "GEOFIT EXPERT", "winner_country": "France",
                                                      "title": "Levé LiDAR côtier", "country": "Senegal", "value": "EUR 250000"}]}}
    proc.run_text("/approach")
    assert "Firms with a reason to hire" in replies.sent[-1] and "GEOFIT EXPERT" in replies.sent[-1]
    proc.run_text("/approach geofit")
    assert "Speculative application: GEOFIT EXPERT" in replies.sent[-1] and "Objet" in replies.sent[-1]
    assert "just won the contract" in str(session.calls[1][2])
    assert interpret("prepare me for the interview " + code, lambda t: t == code) == ("prep", code)
    assert interpret("write to geofit expert") == ("approach", "geofit expert")
    assert interpret("candidature spontanée pour Geofit") == ("approach", "Geofit")
