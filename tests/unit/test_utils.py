from datetime import timezone

from geojobbot.utils.dates import parse_datetime
from geojobbot.utils.location import parse_location
from geojobbot.utils.text import html_to_text, normalize_company, normalize_title
from geojobbot.utils.urls import canonicalize_url, is_aggregator, looks_like_job_url


def test_canonicalize_strips_tracking_and_normalises():
    a = canonicalize_url("HTTP://www.Example.com/jobs/123/?utm_source=x&gh_src=abc&b=2&a=1#apply")
    b = canonicalize_url("https://example.com/jobs/123?a=1&b=2")
    assert a == b == "https://example.com/jobs/123?a=1&b=2"


def test_canonicalize_keeps_job_identifier_params():
    assert "gh_jid=55" in canonicalize_url("https://acme.com/careers?gh_jid=55&utm_medium=x")


def test_canonicalize_invalid():
    assert canonicalize_url("not a url") is None
    assert canonicalize_url(None) is None


def test_aggregator_and_joblike():
    assert is_aggregator("https://www.linkedin.com/jobs/view/1")
    assert not is_aggregator("https://boards.greenhouse.io/acme/jobs/1")
    assert looks_like_job_url("https://acme.com/careers/gis-analyst")
    assert not looks_like_job_url("https://acme.com/about")


def test_parse_datetime_variants():
    assert parse_datetime("2026-09-15T10:00:00Z").tzinfo is not None
    assert parse_datetime("2026-09-15").day == 15
    assert parse_datetime(1757930400000).year == 2025
    assert parse_datetime("Tue, 15 Sep 2026 10:00:00 GMT").day == 15
    assert parse_datetime("2024-01-01 10:00:00 UTC").tzinfo == timezone.utc
    assert parse_datetime("garbage") is None
    assert parse_datetime("") is None
    assert parse_datetime(True) is None


def test_html_to_text_escaped_greenhouse_content():
    text = html_to_text("&lt;p&gt;Use &lt;strong&gt;ArcGIS Pro&lt;/strong&gt;&lt;/p&gt;&lt;ul&gt;&lt;li&gt;QGIS&lt;/li&gt;&lt;/ul&gt;")
    assert "ArcGIS Pro" in text and "QGIS" in text and "<" not in text


def test_html_to_text_malformed():
    assert "hello" in html_to_text("<div><p>hello<span></div></b>")


def test_normalizers():
    assert normalize_company("Acme Geospatial, Inc.") == normalize_company("ACME Geospatial LLC") == "acme geospatial"
    assert normalize_title("GIS-Analyst (m/w/d)") == "gis analyst"


# ------------------------------------------------------------------ location
def test_bare_remote_has_no_scope():
    loc = parse_location("Remote")
    assert loc.remote is True and loc.remote_scope is None and loc.work_mode == "remote"


def test_worldwide_only_when_explicit():
    assert parse_location("Remote - Anywhere").remote_scope == "Worldwide"
    assert parse_location("Worldwide", remote_flag=True).remote_scope == "Worldwide"


def test_remote_with_country_and_region():
    assert parse_location("Remote - US").remote_scope == "United States"
    assert parse_location("Remote (Canada)").remote_scope == "Canada"
    assert parse_location("EMEA Remote").remote_scope == "EMEA"


def test_city_region_country():
    loc = parse_location("Vancouver, BC, Canada")
    assert (loc.city, loc.region, loc.country) == ("Vancouver", "British Columbia", "Canada")
    assert loc.remote is None
    loc = parse_location("Denver, CO")
    assert loc.country == "United States" and loc.region == "Colorado"


def test_hybrid_and_onsite():
    loc = parse_location("Perth, Australia (Hybrid)")
    assert loc.work_mode == "hybrid" and loc.remote is False and loc.country == "Australia"
    assert parse_location("Tunis, Tunisia", workplace_type="onsite").work_mode == "onsite"


def test_structured_flags_override():
    loc = parse_location("Toronto, Canada", remote_flag=True)
    assert loc.remote is True and loc.remote_scope == "Canada"
    loc = parse_location("", workplace_type="hybrid")
    assert loc.work_mode == "hybrid"


def test_empty_location():
    loc = parse_location(None)
    assert loc.remote is None and loc.display() == "Location not specified"


def test_french_locations():
    loc = parse_location("Tunis, Tunisie")
    assert (loc.city, loc.country) == ("Tunis", "Tunisia")
    assert parse_location("Sfax").country == "Tunisia"
    assert parse_location("Tunis, Tunis").country == "Tunisia"  # LinkedIn style "city, governorate"
    remote = parse_location("Télétravail - Canada")
    assert remote.remote is True and remote.remote_scope == "Canada"
    hybrid = parse_location("Montréal, QC (Hybride)")
    assert hybrid.work_mode == "hybrid" and hybrid.country == "Canada" and hybrid.region == "Quebec"
