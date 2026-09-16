import pytest
import requests
from conftest import FakeResponse, FakeSession

from geojobbot.utils.http import FetchError, HttpClient
from geojobbot.utils.robots import RobotsCache


def client_for(session, **kw):
    sleeps = []
    c = HttpClient("TestBot/1.0", default_delay=0, session=session, sleep=sleeps.append, **kw)
    c.robots = RobotsCache(c, "TestBot")
    return c, sleeps


def test_retries_then_success_with_backoff():
    s = FakeSession({"https://api.x.com/a": [FakeResponse(503), FakeResponse(502), FakeResponse(200, {"ok": 1})]})
    c, sleeps = client_for(s)
    assert c.get_json("https://api.x.com/a", respect_robots=False) == {"ok": 1}
    assert c.stats.retries == 2 and 2.0 in sleeps and 4.0 in sleeps


def test_max_three_retries():
    s = FakeSession({"https://api.x.com/a": [FakeResponse(500)]})
    c, _ = client_for(s)
    with pytest.raises(FetchError) as e:
        c.get("https://api.x.com/a", respect_robots=False)
    assert e.value.kind == "SERVER_ERROR"
    assert len([x for x in s.calls if "api.x.com/a" in x[1]]) == 4


def test_retry_after_honoured_and_rate_limit_doubles_delay():
    s = FakeSession({"https://api.x.com/a": [FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(200, {"a": 1})]})
    c, sleeps = client_for(s)
    c.host_delays["api.x.com"] = 1.0
    c.get_json("https://api.x.com/a", respect_robots=False)
    assert 7.0 in sleeps and c.delay_for("api.x.com") == 2.0


def test_retry_after_too_long_gives_up():
    s = FakeSession({"https://api.x.com/a": [FakeResponse(429, headers={"Retry-After": "3600"})]})
    c, _ = client_for(s)
    with pytest.raises(FetchError) as e:
        c.get("https://api.x.com/a", respect_robots=False)
    assert e.value.kind == "RATE_LIMITED"


def test_timeout_and_connection_errors_retry():
    s = FakeSession({"https://api.x.com/a": [requests.exceptions.Timeout(), requests.exceptions.ConnectionError(),
                                             FakeResponse(200, {"x": 1})]})
    c, _ = client_for(s)
    assert c.get_json("https://api.x.com/a", respect_robots=False) == {"x": 1}


def test_404_not_retried():
    s = FakeSession({"https://api.x.com/a": [FakeResponse(404)]})
    c, _ = client_for(s)
    with pytest.raises(FetchError) as e:
        c.get("https://api.x.com/a", respect_robots=False)
    assert e.value.kind == "NOT_FOUND" and len(s.calls) == 1


def test_captcha_page_is_blocked_not_bypassed():
    s = FakeSession({"https://site.com/job": FakeResponse(200, "<html><div class='g-recaptcha'></div></html>")})
    c, _ = client_for(s)
    with pytest.raises(FetchError) as e:
        c.get("https://site.com/job")
    assert e.value.kind == "BLOCKED"


def test_bad_json():
    s = FakeSession({"https://api.x.com/a": FakeResponse(200, "not json", headers={"Content-Type": "application/json"})})
    c, _ = client_for(s)
    with pytest.raises(FetchError) as e:
        c.get_json("https://api.x.com/a", respect_robots=False)
    assert e.value.kind == "BAD_JSON"


def test_too_large():
    s = FakeSession({"https://site.com/big": FakeResponse(200, b"x" * 2000)})
    c, _ = client_for(s)
    with pytest.raises(FetchError) as e:
        c.get("https://site.com/big", max_bytes=1000, respect_robots=False)
    assert e.value.kind == "TOO_LARGE"


def test_robots_disallow_and_crawl_delay():
    robots = "User-agent: *\nDisallow: /private\nCrawl-delay: 4\nSitemap: https://site.com/jobs-sitemap.xml\n"
    s = FakeSession({"https://site.com/robots.txt": FakeResponse(200, robots, headers={"Content-Type": "text/plain"}),
                     "https://site.com/": FakeResponse(200, "<html>ok</html>")})
    c, _ = client_for(s)
    with pytest.raises(FetchError) as e:
        c.get("https://site.com/private/x")
    assert e.value.kind == "ROBOTS_DISALLOWED"
    assert c.get("https://site.com/careers").status_code == 200
    assert c.delay_for("site.com") == 4.0
    assert c.robots.sitemaps("https://site.com/") == ["https://site.com/jobs-sitemap.xml"]


def test_robots_unreachable_means_disallow():
    s = FakeSession({"https://down.com/robots.txt": [FakeResponse(503)], "https://down.com/": FakeResponse(200, "x")})
    c, _ = client_for(s)
    with pytest.raises(FetchError) as e:
        c.get("https://down.com/careers")
    assert e.value.kind == "ROBOTS_DISALLOWED"


def test_robots_404_allows():
    s = FakeSession({"https://open.com/": FakeResponse(200, "<html>hi</html>")})
    c, _ = client_for(s)
    assert c.get("https://open.com/jobs").status_code == 200
