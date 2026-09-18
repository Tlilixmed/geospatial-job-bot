"""Visa routes: salary parsing, the checks per country, commands and the shipped rules file."""
from datetime import timedelta

from conftest import NOW, FakeS3, FakeSession, make_settings
from test_commands import job, setup
from test_pipeline import FakeNotifier, StaticBackend, gis_raw

from geojobbot.core.pipeline import Pipeline
from geojobbot.insights import visa
from geojobbot.notifications.intents import interpret
from geojobbot.notifications.telegram import format_digest
from geojobbot.storage.r2 import R2Store
from geojobbot.storage.state import StateManager
from geojobbot.utils.text import job_code

UK_HIT = {"register": "uk", "label": "UK licensed sponsor", "icon": "🛂", "country": "United Kingdom", "name": "ACME LTD", "match": "exact"}
CA_HIT = {"register": "ca", "label": "Canada LMIA employer", "icon": "🍁", "country": "Canada", "name": "GEO INC", "match": "exact",
          "geo": True, "occupations": ["Land surveyors"]}


def test_salary_parsing_handles_the_formats_sources_produce():
    parse = visa.parse_salary
    assert parse("GBP 40000.0–50000.0 yearly", "United Kingdom") == {"currency": "GBP", "low": 40000.0, "high": 50000.0, "period": "year"}
    assert parse("£45,000 - £52,000 a year", None)["high"] == 52000
    assert parse("$45 - $55 an hour", "Canada") == {"currency": "CAD", "low": 45.0, "high": 55.0, "period": "hour"}
    assert parse("$45 an hour", "France")["currency"] == "USD"
    assert parse("45000–52000", "Germany")["currency"] == "EUR" and parse("60k-75k", "Australia")["high"] == 75000
    assert parse("EUR 4.500 per month", "Netherlands") == {"currency": "EUR", "low": 4500.0, "high": 4500.0, "period": "month"}
    assert parse("5 200 € par mois", "France")["low"] == 5200 and parse("5200", "Netherlands")["period"] == "month"
    assert parse("Competitive", "France") is None and parse("45000", None) is None and parse(None, "France") is None


def test_shipped_rules_are_complete_and_dated():
    rules = visa.load_rules()
    assert rules["as_of"] and {"United Kingdom", "Canada", "Netherlands", "Germany", "France", "Ireland", "Australia",
                               "United Arab Emirates", "United States"} <= set(rules["paths"])
    for country, paths in rules["paths"].items():
        for rule in paths:
            assert rule["sponsor"].split(":")[0] in ("register", "employer", "default"), (country, rule["name"])
            assert rule.get("note") and rule.get("occupation") in ("any", "graduate", "spatial", "bilateral")
            if rule.get("salary_min"):
                assert rule.get("currency") and rule.get("period") in ("year", "month")


def test_uk_route_needs_licence_salary_and_a_graduate_level_title():
    s = make_settings()
    strong = visa.assess(job("u:1", "GIS Analyst", country="United Kingdom", salary="£45,000 a year", sponsor=[UK_HIT]), s)
    assert strong["verdict"] == "strong" and strong["path"] == "Skilled Worker visa"
    assert [c["ok"] for c in strong["checks"]] == [True, True, True] and "≥ £41,700/year" in strong["checks"][1]["text"]
    low = visa.assess(job("u:2", "GIS Analyst", country="United Kingdom", salary="£28,000 - £31,000 a year", sponsor=[UK_HIT]), s)
    assert low["verdict"] == "blocked" and "below the minimum £33,400" in low["checks"][1]["text"]
    between = visa.assess(job("u:3", "GIS Analyst", country="United Kingdom", salary="£36,000", sponsor=[UK_HIT]), s)
    assert between["verdict"] == "open" and "new entrants" in between["checks"][1]["text"]
    unknown = visa.assess(job("u:4", "GIS Technician", country="United Kingdom"), s)
    assert unknown["verdict"] == "open" and [c["ok"] for c in unknown["checks"]] == [None, None, None]
    assert "not found on the official register" in unknown["checks"][0]["text"] and "technician-level" in unknown["checks"][2]["text"]
    refused = visa.assess(job("u:5", "GIS Analyst", country="United Kingdom", ai={"fit": 7, "sponsorship": "not_offered"}), s)
    assert refused["verdict"] == "blocked"


def test_routes_opened_by_language_and_nationality():
    s = make_settings()
    ottawa = visa.assess(job("c:1", "GIS Analyst", country="Canada", region="Ontario", location_raw="Ottawa, ON"), s)
    assert ottawa["path"] == "Francophone Mobility (C16)" and ottawa["no_lmia"] and ottawa["verdict"] == "open"
    assert visa.badge({"visa": ottawa}) == "🍁 No LMIA needed for French speakers (Francophone Mobility)"
    lmia = visa.assess(job("c:2", "Land Surveyor", country="Canada", region="Alberta", sponsor=[CA_HIT]), s)
    assert lmia["verdict"] == "strong" and "already hired Land surveyors" in lmia["checks"][0]["text"]
    quebec = visa.assess(job("c:3", "Technicien en géomatique", country="Canada", region="Quebec", location_raw="Montréal, QC"), s)
    assert quebec["path"] == "LMIA work permit"  # Francophone Mobility is for jobs outside Québec
    english_only = visa.assess(job("c:4", "GIS Analyst", country="Canada", region="Ontario"), make_settings(my_languages=["English"]))
    assert english_only["path"] == "LMIA work permit"

    geometre = visa.assess(job("f:1", "Géomètre-topographe", country="France"), s)
    assert geometre["path"].startswith("Titre de séjour salarié") and geometre["checks"][-1]["ok"] is True
    assert geometre["others"][0]["path"] == "Talent – carte bleue européenne"
    developer = visa.assess(job("f:2", "Développeur SIG", country="France"), s)
    assert developer["path"].startswith("Titre de séjour salarié") and developer["checks"][-1]["ok"] is None
    other_nationality = visa.assess(job("f:3", "Géomètre", country="France"), make_settings(home_countries=["Morocco"]))
    assert other_nationality["path"] == "Talent – carte bleue européenne"

    assert visa.assess(job("g:1", "GIS Specialist", country="United Arab Emirates"), s)["verdict"] == "strong"
    assert visa.assess(job("s:1", "GIS Analyst", country="United States"), s)["verdict"] == "hard"
    nl = visa.assess(job("n:1", "GIS Developer", country="Netherlands", salary="EUR 70000 yearly"), s)
    assert nl["checks"][1]["ok"] is None and "reduced minimum €4,357" in nl["checks"][1]["text"]  # 70,000 / 12.96 = 5,401 a month
    assert visa.assess(job("t:1", "GIS Analyst", country="Tunisia"), s) is None  # home country
    assert visa.assess(job("x:1", "GIS Analyst", country="Brazil"), s) is None and visa.assess(job("r:1", "GIS Analyst"), s) is None


def test_visa_in_digest_why_and_commands():
    s = make_settings()
    strong = job("u:1", "GIS Analyst", country="United Kingdom", salary="£45,000 a year", sponsor=[UK_HIT], last_seen=NOW.isoformat())
    blocked = job("u:2", "GIS Officer", company="Council", country="United Kingdom", salary="£25,000 a year", last_seen=NOW.isoformat())
    for rec in (strong, blocked):
        assert visa.annotate(rec, s)
    text = format_digest([strong, blocked], now=NOW)[0][0]
    assert "🟢 Visa route looks open — Skilled Worker visa: sponsor ✓ · salary ✓ · occupation ✓" in text
    assert "⛔ Visa: salary £25,000 is below the minimum £33,400/year (Skilled Worker visa)" in text

    proc, replies, _, _ = setup([], [strong, blocked])
    proc.run_text(f"/why {job_code('u:1')}")
    assert "Visa route: Skilled Worker visa</b> — looks open" in replies.sent[-1] and "official source" in replies.sent[-1]
    assert "rules as of" in replies.sent[-1] and "not legal advice" in replies.sent[-1]
    proc.run_text("/visa")
    listing = replies.sent[-1]
    assert listing.index("GIS Analyst") < listing.index("GIS Officer") and "🟢 1 looks open" in listing and "⛔ 1 blocked" in listing
    proc.run_text("/visa france")
    assert "Work-visa routes: France" in replies.sent[-1] and "accord franco-tunisien" in replies.sent[-1] and "€59,373/year" in replies.sent[-1]
    proc.run_text(f"/visa {job_code('u:2')}")
    assert "blocked" in replies.sent[-1]
    proc.run_text("/visa atlantis")
    assert replies.sent[-1].startswith("Usage: /visa")

    assert interpret("can i get a visa for these jobs") == ("visa", "")
    assert interpret("what are the visa rules for germany") == ("visa", "Germany")
    assert interpret("visa rules united kingdom") == ("visa", "United Kingdom")
    assert interpret("who sponsors visas") == ("sponsors", "")
    stale = dict(strong["visa"], as_of=(NOW - timedelta(days=500)).date().isoformat())
    assert "over a year old" in "\n".join(visa.detail_lines({"visa": stale}, NOW))


def test_pipeline_annotates_and_publishes_visa_views():
    s3 = FakeS3()
    raw = gis_raw(location_raw="London, United Kingdom", salary="£50,000 a year")
    _, report = Pipeline(make_settings(), store=R2Store("b", client=s3), backends=[StaticBackend("feed", [raw])],
                         notifier=FakeNotifier(), now=NOW, http_session=FakeSession(), sleep=lambda x: None).run()
    manager = StateManager(R2Store("b", client=s3))
    rec = manager.load()["jobs"]["static:1"]
    assert rec["visa"]["path"] == "Skilled Worker visa" and rec["visa"]["verdict"] == "open" and report["counts"]["visa_open"] == 1
    index = manager.read_json("state/index.json")
    assert index["jobs"][0]["visa"]["v"] == "open" and any("Skilled Worker" in line for line in index["jobs"][0]["visa"]["d"])
    assert "Visa routes of current matches" in index["views"]["visa"] and "Work-visa routes: United Kingdom" in index["visa_cards"]["uk"]
