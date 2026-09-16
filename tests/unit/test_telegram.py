import requests
from conftest import FakeResponse, FakeSession

from geojobbot.notifications.telegram import TelegramNotifier, format_job_message

TOKEN = "123456:SECRET-TOKEN-VALUE"
URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"


def rec(**kw):
    base = {"tier": "high", "score": 86, "title": "GIS Analyst <Senior>", "company": "Acme & Co", "remote": True,
            "remote_scope": None, "work_mode": "remote", "employment_type": "Full-time", "location_raw": "Remote",
            "matched_skills": ["ArcGIS Pro (required)", "Python"], "matched_domains": ["LiDAR"],
            "why_matched": ["GIS Analyst title", "ArcGIS Pro required"], "apply_url": "https://acme.com/j?a=1&b=2",
            "sources": [{"source_name": "greenhouse"}], "posted_at": "2026-09-15T00:00:00Z", "posted_at_reliable": True}
    base.update(kw)
    return base


def test_format_escapes_and_contains_sections():
    text = format_job_message(rec())
    assert "🔥 <b>HIGH MATCH — 86/100</b>" in text
    assert "GIS Analyst &lt;Senior&gt;" in text and "Acme &amp; Co" in text
    assert "📍 Remote" in text and "Worldwide" not in text
    assert "ArcGIS Pro, Python, LiDAR" in text and "• ArcGIS Pro required" in text
    assert 'href="https://acme.com/j?a=1&amp;b=2"' in text
    assert format_job_message(rec(tier="possible")).startswith("🟡")


def test_missing_fields_do_not_crash():
    text = format_job_message({"title": "X", "tier": "possible"})
    assert "Location not specified" in text


def test_success_requires_ok_true():
    s = FakeSession({URL: FakeResponse(200, {"ok": True, "result": {}})})
    assert TelegramNotifier(TOKEN, "1", session=s, sleep=lambda x: None).send("hi") == (True, None)
    s = FakeSession({URL: FakeResponse(200, {"ok": False, "description": "weird"})})
    ok, err = TelegramNotifier(TOKEN, "1", session=s, sleep=lambda x: None).send("hi")
    assert not ok


def test_429_retry_after_then_success():
    sleeps = []
    s = FakeSession({URL: [FakeResponse(429, {"ok": False, "parameters": {"retry_after": 3}}), FakeResponse(200, {"ok": True})]})
    assert TelegramNotifier(TOKEN, "1", session=s, sleep=sleeps.append).send("hi")[0]
    assert 3 in sleeps


def test_error_redacts_token_and_network_error():
    s = FakeSession({URL: FakeResponse(400, {"ok": False, "description": f"bad chat for bot{TOKEN}"})})
    ok, err = TelegramNotifier(TOKEN, "1", session=s, sleep=lambda x: None).send("hi")
    assert not ok and TOKEN not in err
    s = FakeSession({URL: [requests.exceptions.ConnectionError()]})
    ok, err = TelegramNotifier(TOKEN, "1", session=s, sleep=lambda x: None, max_retries=1).send("hi")
    assert not ok and "network" in err and TOKEN not in err
