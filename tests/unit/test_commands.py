"""Two-way Telegram: commands are executed only for the configured chat and change stored preferences."""
from datetime import timedelta

from conftest import NOW, FakeResponse, FakeS3, FakeSession, make_settings

from geojobbot.core.jobs import select_alerts
from geojobbot.core.prefs import apply_prefs, load_prefs
from geojobbot.notifications.commands import CommandProcessor
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
