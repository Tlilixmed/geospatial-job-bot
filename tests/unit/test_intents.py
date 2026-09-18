"""Plain-language messages map to the same commands as the slash forms."""
import pytest
from test_commands import CHAT, job, setup

from geojobbot.core.prefs import load_prefs
from geojobbot.notifications.intents import interpret
from geojobbot.utils.text import job_code

CODES = {"a3f9c", "0b12e"}

CASES = [
    # the user's own examples, typos included
    ("iwant top matching offers", ("jobs", "")),
    ("resume the job", ("resume", "")),
    ("select jobs in threshold 60-70", ("range", "60 70")),
    ("waht command you can do", ("help", "")),
    # listing
    ("show me the best 5 jobs", ("jobs", "5")),
    ("give me the latest offers", ("jobs", "")),
    ("top 20", ("jobs", "20")),
    ("high matches only please", ("high", "")),
    ("show me intresting jobs", ("jobs", "")),
    # score ranges and threshold
    ("jobs between 60 and 70", ("range", "60 70")),
    ("jobs above 80", ("range", "80 100")),
    ("anything scoring below 65?", ("range", "0 65")),
    ("set treshold to 75", ("threshold", "75")),
    ("change the threshold to 80 and 60", ("threshold", "80 60")),
    ("what is the threshold", ("threshold", "")),
    # search
    ("lidar jobs in montreal", ("search", "lidar montreal")),
    ("any photogrammetry offers in france", ("search", "photogrammetry france")),
    ("jobs in canada", ("search", "canada")),
    ("remote gis developer", ("search", "remote gis developer")),
    # job codes
    ("why a3f9c", ("why", "a3f9c")),
    ("tell me more about a3f9c", ("why", "a3f9c")),
    ("a3f9c", ("why", "a3f9c")),
    ("i applied to a3f9c", ("applied", "a3f9c")),
    ("not interested in 0b12e", ("hide", "0b12e")),
    ("bring back 0b12e", ("unhide", "0b12e")),
    ("what did i apply to", ("applied", "")),
    ("my applications", ("applied", "")),
    # mute
    ("stop showing leidos", ("mute", "leidos")),
    ("no more jobs from Parsons Corporation", ("mute", "Parsons Corporation")),
    ("unmute esri", ("unmute", "esri")),
    ("what is muted", ("muted", "")),
    # control
    ("pause", ("pause", "")),
    ("stop the alerts", ("pause", "")),
    ("stop", ("pause", "")),
    ("continue", ("resume", "")),
    ("run now", ("run", "")),
    ("start a new scan", ("run", "")),
    ("no internships", ("interns", "off")),
    ("include internships please", ("interns", "on")),
    ("how are you doing, status?", ("status", "")),
    ("last run", ("status", "")),
    ("weekly summary", ("weekly", "")),
    ("prefer Canada and France", ("locations", "Canada, France")),
    ("set locations to Tunisia, UAE", ("locations", "Tunisia, UAE")),
    ("reset locations", ("locations", "reset")),
    ("hello", ("help", "")),
    # French
    ("montre moi les meilleures offres", ("jobs", "")),
    ("je veux les 10 dernières offres", ("jobs", "10")),
    ("offres entre 60 et 70", ("range", "60 70")),
    ("mets le seuil à 75", ("threshold", "75")),
    ("arrête les alertes", ("pause", "")),
    ("reprends", ("resume", "")),
    ("j'ai postulé à a3f9c", ("applied", "a3f9c")),
    ("pourquoi a3f9c", ("why", "a3f9c")),
    ("plus d'offres de leidos", ("mute", "leidos")),
    ("lance une recherche maintenant", ("run", "")),
    ("que peux tu faire", ("help", "")),
    ("sans stages", ("interns", "off")),
    ("mes candidatures", ("applied", "")),
    ("offres SIG en Tunisie", ("search", "SIG Tunisie")),
    ("géomaticien montréal", ("search", "géomaticien montréal")),
]


@pytest.mark.parametrize("sentence,expected", CASES)
def test_sentences_map_to_commands(sentence, expected):
    assert interpret(sentence, lambda token: token in CODES) == expected


def test_search_terms_and_company_names_are_never_typo_corrected():
    assert interpret("montreal leidos esri lidar")[1] == "montreal leidos esri lidar"
    assert interpret("a3f9c") == ("search", "a3f9c")  # an unknown code is just a search word


def test_plain_language_runs_the_command_and_echoes_the_interpretation():
    jobs = [job("a:1", "GIS Analyst", score=82), job("a:2", "LiDAR Technician", tier="possible", score=64),
            job("a:3", "Survey Drafter", tier="rejected", score=50, rejection_reasons=["LOW_SCORE"]),
            job("a:4", "US-only GIS role", tier="rejected", score=66, rejection_reasons=["WORK_AUTHORIZATION_REQUIRED"])]
    code = job_code("a:1")
    proc, replies, _, manager = setup([(CHAT, "i want top matching offers"), (CHAT, "jobs between 45 and 70"),
                                       (CHAT, f"i applied to {code}"), (CHAT, "stop showing acme"),
                                       (CHAT, "set treshold to 75"), (CHAT, "/jobs")], jobs)
    proc.run()
    top, ranged, applied, muted, threshold, slash = replies.sent
    assert top.startswith("↪ <i>/jobs</i>") and "GIS Analyst" in top
    assert ranged.startswith("↪ <i>/range 45 70</i>") and "2 jobs scoring 45–70" in ranged
    assert "LiDAR Technician" in ranged and "Survey Drafter" in ranged and "US-only" not in ranged  # other rejections stay out
    assert applied.startswith(f"↪ <i>/applied {code}</i>") and "Marked as applied" in applied
    assert "Muted “acme”" in muted and "High ≥ 75" in threshold
    assert not slash.startswith("↪")  # explicit commands are not echoed
    prefs = load_prefs(manager)
    assert "a:1" in prefs["applied"] and prefs["muted"] == ["acme"] and prefs["high_threshold"] == 75


def test_ai_hint_only_fills_in_when_rules_fall_back_and_is_validated():
    jobs = [job("a:1", "GIS Analyst")]
    code = job_code("a:1")
    proc, replies, _, manager = setup([], jobs)
    # rules understand this one: the (wrong) hint is ignored
    proc.run_text("pause alerts", "/run")
    assert replies.sent[-1].startswith("↪ <i>/pause</i>") and load_prefs(manager)["paused"] is True
    # rules fall back to a search: a valid hint takes over and is labelled
    proc.run_text("alright let the offers flow to me once more", "/resume")
    assert replies.sent[-1].startswith("↪ <i>/resume · AI</i>") and load_prefs(manager)["paused"] is False
    proc.run_text("that first one looks perfect, I sent my CV", f"/applied {code}")
    assert "· AI" in replies.sent[-1] and "a:1" in load_prefs(manager)["applied"]
    # invalid hints are discarded: unknown command, unknown job code, multi-line or oversized output
    for bad in ("/delete everything", "/hide zzzzz", "/why 00000", "/mute x\n/pause", "rm -rf", "/search " + "x" * 200):
        proc.run_text("something unusual entirely", bad)
        assert "· AI" not in replies.sent[-1] and replies.sent[-1].startswith("↪ <i>/search")


def test_inbox_messages_are_processed_in_order_and_deleted():
    import json
    proc, replies, session, manager = setup([], [job("a:1", "GIS Analyst")])
    store = manager.store
    store.put_bytes("inbox/000000000002.json", json.dumps({"text": "resume", "hint": ""}).encode())
    store.put_bytes("inbox/000000000001.json", json.dumps({"text": "/pause"}).encode())
    store.put_bytes("inbox/000000000003.json", b"not json")
    counts = proc.run_inbox()
    assert counts["commands"] == 2 and counts["unreadable"] == 1 and session.calls == []
    assert "paused" in replies.sent[0] and "resumed" in replies.sent[1]   # oldest first: pause, then resume
    assert load_prefs(manager)["paused"] is False and store.list_keys("inbox/") == []


def test_new_commands_do_not_hijack_ordinary_searches():
    """Words shared with /visa, /sources, /approach, /watch and /prep must stay searches when they are about jobs."""
    for text in ("gis jobs with relocation support", "immigration consultant jobs", "which sites have lidar jobs",
                 "contact details for acme", "je suis interesse par les offres sig", "watch jobs in canada",
                 "survey jobs until december", "software developer gis"):
        assert interpret(text)[0] == "search", text
    assert interpret("which sources work best") == ("sources", "")
    assert interpret("which job boards are useful") == ("sources", "")
    assert interpret("questions about a3f9c", lambda t: t == "a3f9c") == ("why", "a3f9c")
    assert interpret("interview questions for a3f9c", lambda t: t == "a3f9c") == ("prep", "a3f9c")
