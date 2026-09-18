"""Two-way Telegram: commands are executed only for the configured chat and change stored preferences."""
from datetime import timedelta

from conftest import NOW, FakeResponse, FakeS3, FakeSession, make_settings

from geojobbot.core.jobs import select_alerts
from geojobbot.core.prefs import apply_prefs, load_prefs
from geojobbot.notifications.commands import CommandProcessor
from geojobbot.notifications.weekly import format_weekly
from geojobbot.storage.r2 import R2Store
from geojobbot.storage.state import StateManager
from geojobbot.utils.text import job_code

TOKEN = "123456:SECRET"
API = f"https://api.telegram.org/bot{TOKEN}"
CHAT = "4242"


class Replies:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True, None


def job(cid, title, company="Acme", tier="high", score=80, hours=5, **kw):
    rec = {"canonical_id": cid, "title": title, "company": company, "tier": tier, "score": score,
           "posted_at": (NOW - timedelta(hours=hours)).isoformat(), "posted_at_reliable": True,
           "location_raw": "Tunis, Tunisie", "city": "Tunis", "country": "Tunisia", "matched_skills": ["QGIS (required)"],
           "matched_domains": ["LiDAR"], "why_matched": ["GIS Analyst title", "QGIS required"],
           "score_breakdown": {"title": 40, "tech": 20, "domain": 10, "responsibilities": 6, "location": 4},
           "sources": [{"source_name": "rss:gogeomatics"}], "apply_url": f"https://x.example/{cid}", "notified": False}
    rec.update(kw)
    return rec


def setup(messages, jobs=None):
    """Return (processor, replies, session, manager) with the given chat messages pending."""
    manager = StateManager(R2Store("b", client=FakeS3()))
    state = manager.load()
    for rec in jobs or []:
        state["jobs"][rec["canonical_id"]] = rec
    manager.save(state, "seed")
    updates = [{"update_id": 100 + i, "message": {"chat": {"id": int(chat)}, "text": text}}
               for i, (chat, text) in enumerate(messages)]

    def get_updates(method, url, params, body):
        return FakeResponse(200, {"ok": True, "result": [] if "offset" in (body or {}) else updates})

    session = FakeSession({f"{API}/getUpdates": get_updates})
    settings = make_settings(telegram_bot_token=TOKEN, telegram_chat_id=CHAT, preferred_locations=["Tunisia"])
    replies = Replies()
    return CommandProcessor(settings, manager, replies, session=session, now=NOW), replies, session, manager


def test_other_chats_are_ignored_and_updates_confirmed():
    proc, replies, session, _ = setup([("999", "/pause"), (CHAT, "/help")])
    counts = proc.run()
    assert counts["ignored_other_chat"] == 1 and counts["commands"] == 1
    assert len(replies.sent) == 1 and "/jobs" in replies.sent[0]
    assert load_prefs(proc.manager)["paused"] is False  # the stranger's /pause did nothing
    assert session.calls[-1][2]["offset"] == 102  # confirmed past the last update id


def test_jobs_listing_shows_codes_and_skips_stale_hidden_and_muted():
    jobs = [job("a:1", "GIS Analyst"), job("a:2", "LiDAR Technician", company="Leidos"),
            job("a:3", "Old Cartographer", hours=24 * 40), job("a:4", "Rejected", tier="rejected", score=10)]
    proc, replies, _, _ = setup([(CHAT, "/mute leidos"), (CHAT, "/jobs")], jobs)
    proc.run()
    listing = replies.sent[-1]
    assert "GIS Analyst" in listing and job_code("a:1") in listing
    assert "LiDAR Technician" not in listing and "Old Cartographer" not in listing and "Rejected" not in listing
    assert "Top 1 of 1 current matches" in listing


def test_applied_hide_why_and_search():
    jobs = [job("a:1", "GIS Analyst"), job("a:2", "LiDAR Technician", company="ScanCo")]
    code1, code2 = job_code("a:1"), job_code("a:2")
    proc, replies, _, manager = setup([(CHAT, f"/why {code1}"), (CHAT, f"/applied {code1}"), (CHAT, f"/hide {code2}"),
                                       (CHAT, "/applied"), (CHAT, "lidar scanco"), (CHAT, "/nonsense")], jobs)
    proc.run()
    why, applied, hidden, applied_list, search, unknown = replies.sent
    assert "Score 80/100" in why and "QGIS required" in why and "skills 20" in why
    assert "Marked as applied" in applied and "Hidden" in hidden and "GIS Analyst" in applied_list
    assert "LiDAR Technician" in search and "1 stored match" in search  # plain text is a search
    assert "/help" in unknown
    prefs = load_prefs(manager)
    assert "a:1" in prefs["applied"] and prefs["hidden"] == ["a:2"]
    # the scraper then never alerts on either
    settings = make_settings()
    apply_prefs(settings, prefs)
    state = manager.peek()
    selected, counts = select_alerts(state, {"a:1", "a:2"}, settings, NOW)
    assert selected == [] and counts["hidden"] == 2


def test_tuning_commands_persist_and_apply():
    proc, replies, _, manager = setup([(CHAT, "/threshold 80 60"), (CHAT, "/locations Canada, France"),
                                       (CHAT, "/interns on"), (CHAT, "/pause"), (CHAT, "/status")])
    proc.run()
    assert "High ≥ 80" in replies.sent[0] and "paused" in replies.sent[-1] and "internships included" in replies.sent[-1]
    settings = make_settings()
    apply_prefs(settings, load_prefs(manager))
    assert (settings.high_threshold, settings.medium_threshold) == (80, 60)
    assert settings.preferred_locations == ["Canada", "France"] and settings.alerts_paused
    assert settings.exclude_internships is False and "Intern" not in settings.negative_titles()


def test_run_dispatches_scraper_workflow(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "me/bot")
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_x")
    proc, replies, session, _ = setup([(CHAT, "/run")])
    session.routes["https://api.github.com/repos/me/bot/actions/workflows/scraper.yml/dispatches"] = FakeResponse(204, b"")
    proc.run()
    assert "run started" in replies.sent[0]
    assert any("dispatches" in call[1] and call[2] == {"ref": "main"} for call in session.calls)


def test_weekly_summary_tracks_applications_and_open_matches():
    listed = job("a:1", "GIS Analyst", last_seen=NOW.isoformat())
    gone = job("a:2", "LiDAR Technician", last_seen=(NOW - timedelta(days=9)).isoformat())
    open_high = job("a:3", "Cartographer", score=91, last_seen=NOW.isoformat(), notified=True,
                    notified_at=(NOW - timedelta(days=2)).isoformat())
    state = {"jobs": {r["canonical_id"]: r for r in (listed, gone, open_high)}}
    prefs = {"applied": {"a:1": {"title": "GIS Analyst", "company": "Acme", "at": (NOW - timedelta(days=3)).isoformat()},
                         "a:2": {"title": "LiDAR Technician", "company": "Acme", "at": (NOW - timedelta(days=10)).isoformat()}},
             "hidden": [], "muted": []}
    text = format_weekly(state, prefs, make_settings(), NOW)
    assert "1 alerted this week (1 high)" in text and "2 applications tracked" in text
    assert "🟢 GIS Analyst" in text and "applied 3d ago · still listed" in text
    assert "⚪ LiDAR Technician" in text and "no longer listed" in text
    assert "Cartographer" in text and job_code("a:3") in text and "GIS Analyst</a>" not in text  # applied ones are not re-offered
    assert format_weekly({"jobs": {}}, {"applied": {}, "hidden": [], "muted": []}, make_settings(), NOW) is None


def test_direct_text_path_used_by_the_worker_needs_no_polling():
    proc, replies, session, manager = setup([], [job("a:1", "GIS Analyst")])
    counts = proc.run_text(f"/applied {job_code('a:1')}")
    assert counts["commands"] == 1 and "Marked as applied" in replies.sent[0]
    assert "a:1" in load_prefs(manager)["applied"] and session.calls == []  # never touched getUpdates
    proc.run_text("/weekly")
    assert "Weekly job summary" in replies.sent[-1]


def test_owner_is_obeyed_from_another_chat_but_strangers_there_are_not():
    proc, replies, session, manager = setup([])
    group = -1001234567890
    updates = [
        {"update_id": 1, "message": {"chat": {"id": group, "type": "supergroup"}, "from": {"id": int(CHAT)}, "text": "/pause"}},
        {"update_id": 2, "message": {"chat": {"id": group, "type": "supergroup"}, "from": {"id": 777}, "text": "/resume"}},
        {"update_id": 3, "message": {"chat": {"id": group, "type": "supergroup"}, "from": {"id": int(CHAT), "is_bot": True},
                                     "text": "/resume"}},
    ]
    session.routes[f"{API}/getUpdates"] = lambda m, u, p, body: FakeResponse(
        200, {"ok": True, "result": [] if "offset" in (body or {}) else updates})
    counts = proc.run()
    assert counts["commands"] == 1 and counts["ignored_other_chat"] == 2
    assert load_prefs(manager)["paused"] is True  # the owner's /pause counted, the others' /resume did not


def test_outcomes_follow_ups_and_status_in_weekly():
    from geojobbot.core.jobs import due_follow_ups
    from geojobbot.notifications.intents import interpret
    from geojobbot.notifications.weekly import format_follow_ups
    codes = {job_code("a:1"), job_code("a:2")}
    is_code = lambda t: t in codes  # noqa: E731
    assert interpret(f"got an interview for {job_code('a:1')}", is_code) == ("outcome", f"{job_code('a:1')} interview")
    assert interpret(f"they rejected me {job_code('a:2')}", is_code) == ("outcome", f"{job_code('a:2')} rejected")
    assert interpret(f"pas de réponse {job_code('a:1')}", is_code) == ("outcome", f"{job_code('a:1')} ghosted")

    jobs = [job("a:1", "GIS Analyst", last_seen=NOW.isoformat()), job("a:2", "LiDAR Technician", last_seen=NOW.isoformat())]
    proc, replies, _, manager = setup([], jobs)
    proc.run_text(f"/applied {job_code('a:1')}")
    proc.run_text(f"got an interview for {job_code('a:1')}")
    proc.run_text(f"/outcome {job_code('a:2')} rejected")  # never marked as applied: recorded in one step
    assert "An interview" in replies.sent[1] and "on to the next" in replies.sent[-1]
    prefs = load_prefs(manager)
    assert prefs["applied"]["a:1"]["status"] == "interview" and [h["status"] for h in prefs["applied"]["a:1"]["history"]] == ["applied", "interview"]
    assert prefs["applied"]["a:2"]["status"] == "rejected"
    proc.run_text("/applied")
    listing = replies.sent[-1]
    assert "🎤 GIS Analyst" in listing and "❌ LiDAR Technician" in listing and "1 interview" in listing
    weekly = format_weekly(proc.state, prefs, make_settings(), NOW)
    assert "🎤 GIS Analyst" in weekly and "· interview" in weekly

    # follow-ups: only applications still at "applied", once per stage
    waiting = {"applied": {"w:1": {"title": "Cartographer", "company": "MapCo", "at": (NOW - timedelta(days=8)).isoformat(), "status": "applied"},
                           "w:2": {"title": "Fresh", "at": (NOW - timedelta(days=2)).isoformat(), "status": "applied"},
                           "a:1": prefs["applied"]["a:1"]}}
    due = due_follow_ups(waiting, {}, NOW)
    assert [(cid, stage) for cid, _, stage in due] == [("w:1", 7)]
    assert "Time to follow up" in format_follow_ups(due, {}, NOW) and "Cartographer" in format_follow_ups(due, {}, NOW)
    assert due_follow_ups(waiting, {"w:1": 7}, NOW) == []
    later = due_follow_ups(waiting, {"w:1": 7}, NOW + timedelta(days=14))
    assert sorted((cid, stage) for cid, _, stage in later) == [("w:1", 21), ("w:2", 7)]  # second stage, and w:2's first


def test_listings_drop_postings_no_source_has_seen_lately():
    gone = job("a:1", "GIS Analyst", last_seen=(NOW - timedelta(days=9)).isoformat())
    rotated = job("a:2", "LiDAR Technician", last_seen=(NOW - timedelta(days=9)).isoformat(), from_rotation=True)
    proc, replies, _, _ = setup([], [gone, rotated])
    proc.run_text("/jobs")
    assert "LiDAR Technician" in replies.sent[-1] and "GIS Analyst" not in replies.sent[-1]
