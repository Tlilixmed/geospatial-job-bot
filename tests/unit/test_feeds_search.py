from conftest import GIS_DESCRIPTION, FakeResponse, FakeSession, make_ctx, make_settings

from geojobbot.scrapers.feeds import (AdzunaBackend, HimalayasBackend, JobSpyBackend, JoobleBackend, RemoteOKBackend,
                                      RemotiveBackend, RssFeedBackend, UsaJobsBackend)
from geojobbot.scrapers.search import CommonCrawlBackend, DuckDuckGoBackend, SearxngBackend


def test_remotive_parse_and_native_link():
    s = FakeSession({"https://remotive.com/api/remote-jobs": FakeResponse(200, {"jobs": [
        {"id": 1, "title": "GIS Analyst", "company_name": "Acme", "url": "https://boards.greenhouse.io/acme/jobs/77",
         "description": GIS_DESCRIPTION, "candidate_required_location": "USA", "publication_date": "2026-09-15T10:00:00"},
        {"id": 2, "title": "Sales Lead", "description": "x"}]})})
    out = RemotiveBackend().run(make_ctx(s))
    assert out.status == "SUCCESS" and len(out.jobs) == 1 and out.prefiltered_out >= 1
    assert out.jobs[0].native_id == "greenhouse:77" and out.jobs[0].location_raw == "Remote - USA"


def test_feed_schema_mismatch_not_silent():
    s = FakeSession({"https://himalayas.app/jobs/api/search": FakeResponse(200, {"unexpected": True})})
    out = HimalayasBackend().run(make_ctx(s))
    assert out.status == "FAILED" and "unexpected response structure" in out.error


def test_remoteok_skips_legal_notice():
    s = FakeSession({"https://remoteok.com/api": FakeResponse(200, [{"legal": "notice"},
        {"id": 3, "position": "Geospatial Developer", "company": "Geo", "url": "https://remoteok.com/x", "epoch": 1789000000}])})
    out = RemoteOKBackend().run(make_ctx(s))
    assert [j.title for j in out.jobs] == ["Geospatial Developer"]


def test_rss_feed_and_parse_error_isolated():
    rss = ("<rss><channel><item><title>Cartographer</title><link>https://maps.example/jobs/1</link>"
           "<description>Map production</description><pubDate>Tue, 15 Sep 2026 10:00:00 GMT</pubDate></item></channel></rss>")
    s = FakeSession({"https://a.example/feed": FakeResponse(200, rss, headers={"Content-Type": "application/rss+xml"}),
                     "https://b.example/feed": FakeResponse(200, "<rss><broken", headers={"Content-Type": "application/rss+xml"})})
    out = RssFeedBackend([{"name": "a", "url": "https://a.example/feed"}, {"name": "b", "url": "https://b.example/feed"}]).run(make_ctx(s))
    assert out.status == "PARTIAL" and out.jobs[0].title == "Cartographer"


def test_usajobs_disabled_without_key_and_parses():
    ctx = make_ctx(FakeSession())
    assert UsaJobsBackend().enabled(ctx)[0] is False
    payload = {"SearchResult": {"SearchResultItems": [{"MatchedObjectDescriptor": {
        "PositionID": "P1", "PositionTitle": "Cartographer", "PositionURI": "https://www.usajobs.gov/job/1",
        "OrganizationName": "USGS", "PositionLocationDisplay": "Denver, Colorado", "PublicationStartDate": "2026-09-14",
        "UserArea": {"Details": {"JobSummary": GIS_DESCRIPTION}}}}]}}
    s = FakeSession({"https://data.usajobs.gov/api/search": FakeResponse(200, payload)})
    ctx = make_ctx(s, make_settings(usajobs_api_key="k", usajobs_email="me@example.com"))
    out = UsaJobsBackend().run(ctx)
    assert out.jobs[0].source_type == "government" and out.jobs[0].company == "USGS"


def test_jobspy_degrades_when_missing():
    ctx = make_ctx(FakeSession(), make_settings(jobspy_enabled=True))
    ok, reason = JobSpyBackend().enabled(ctx)
    assert ok or reason is None or "not installed" in reason


def test_jobspy_location_specs():
    s = make_settings(jobspy_locations=["Remote", "Canada@canada", "Tunisia@worldwide", " "], jobspy_country_indeed="USA")
    assert JobSpyBackend.location_specs(s) == [("Remote", "usa"), ("Canada", "canada"), ("Tunisia", "worldwide")]
    assert JobSpyBackend.location_specs(make_settings(jobspy_locations=[])) == [("Remote", "usa")]


def test_rss_wordpress_content_encoded_preferred_and_excerpts_queue_the_page():
    rss = ('<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item><title>Geomatics Engineer</title>'
           '<link>https://gogeomatics.ca/job/x/</link><description>short</description>'
           '<content:encoded><![CDATA[<p>Full posting with QGIS and LiDAR</p>]]></content:encoded></item>'
           '<item><title>GIS Technician</title><link>https://gogeomatics.ca/job/y/</link><description>excerpt</description></item>'
           '</channel></rss>')
    s = FakeSession({"https://gogeomatics.ca/?feed=job_feed": FakeResponse(200, rss, headers={"Content-Type": "application/rss+xml"})})
    ctx = make_ctx(s)
    out = RssFeedBackend([{"name": "gogeomatics", "url": "https://gogeomatics.ca/?feed=job_feed", "geospatial": True,
                           "source_type": "feed"}]).run(ctx)
    assert out.jobs[0].description == "Full posting with QGIS and LiDAR"
    assert out.details["pages_queued"] == 2 and len(ctx.pages) == 2  # both are under 300 chars
    queued = ctx.pages.pop_all()
    assert {q.url for q in queued} == {"https://gogeomatics.ca/job/x/", "https://gogeomatics.ca/job/y/"}
    assert all(q.source_type == "feed" and q.geo_context for q in queued)
    assert RssFeedBackend.phase == "discovery"  # so the generic extractor reads the queued pages this run


def test_rss_html_page_for_empty_search_is_not_an_error():
    page = "<!DOCTYPE html><html><head><title>Aucun résultat</title></head><body>rien</body></html>"
    s = FakeSession({"https://t.example/search/x/feed/": FakeResponse(200, page, headers={"Content-Type": "text/html; charset=UTF-8"})})
    out = RssFeedBackend([{"name": "t-x", "url": "https://t.example/search/x/feed/"}]).run(make_ctx(s))
    assert out.status == "SUCCESS" and out.jobs == [] and out.details["empty_html"] == ["t-x"] and out.details["errors"] == []


def test_rss_html_named_entities_are_repaired():
    rss = ('<?xml version="1.0" encoding="UTF-8"?><rss><channel><title>Recherche &raquo; cartographe</title>'
           '<item><title>Carto &amp; Topo recrute Cartographe &eacute;</title><link>https://t.example/p/1</link>'
           '<description>x</description></item></channel></rss>')
    s = FakeSession({"https://t.example/feed": FakeResponse(200, rss, headers={"Content-Type": "application/rss+xml"})})
    out = RssFeedBackend([{"name": "t", "url": "https://t.example/feed"}]).run(make_ctx(s))
    assert out.status == "SUCCESS" and out.jobs[0].title == "Carto & Topo recrute Cartographe é"


def test_adzuna_disabled_without_keys_parses_and_dedupes_across_terms():
    assert AdzunaBackend().enabled(make_ctx(FakeSession()))[0] is False
    payload = {"results": [
        {"id": "1", "title": "<strong>GIS</strong> Technician", "description": "Maps and ArcGIS",
         "redirect_url": "https://www.adzuna.ca/land/ad/1", "company": {"display_name": "MapCo"},
         "location": {"display_name": "Calgary, Alberta"}, "created": "2026-09-15T10:00:00Z",
         "salary_min": 50000, "salary_max": 60000, "contract_time": "full_time"},
        {"id": "2", "title": "Chef", "redirect_url": "https://www.adzuna.ca/land/ad/2"}]}
    s = FakeSession({"https://api.adzuna.com/v1/api/jobs/ca/search/1": FakeResponse(200, payload)})
    ctx = make_ctx(s, make_settings(adzuna_app_id="id", adzuna_app_key="key", adzuna_countries=["ca"]))
    out = AdzunaBackend().run(ctx)
    assert out.status == "SUCCESS" and [j.title for j in out.jobs] == ["GIS Technician"]
    job = out.jobs[0]
    assert (job.company, job.source_job_id, job.salary, job.source_type) == ("MapCo", "adzuna:1", "50000–60000", "aggregator")
    assert job.location_raw == "Calgary, Alberta" and job.employment_type == "full_time"
    assert out.prefiltered_out == 1  # the same id from every term query is counted once


def test_adzuna_auth_failure_stops_early_and_hides_key():
    s = FakeSession({"https://api.adzuna.com/v1/api/jobs/ca/search/1": FakeResponse(401, {"error": "bad key"})})
    out = AdzunaBackend().run(make_ctx(s, make_settings(adzuna_app_id="id", adzuna_app_key="SECRETKEY", adzuna_countries=["ca", "gb"])))
    assert out.status == "FAILED" and out.error == "ca/GIS: AUTH_REQUIRED" and len(s.calls) == 1
    assert "SECRETKEY" not in str(out.details)


def test_jooble_posts_per_term_and_location():
    payload = {"totalCount": 1, "jobs": [{"id": 9, "title": "Ingénieur SIG", "location": "Tunis", "snippet": "SIG et <b>QGIS</b>",
                                          "salary": "", "type": "CDI", "link": "https://jooble.org/jdp/9", "company": "GeoTun",
                                          "updated": "2026-09-15T00:00:00"}]}
    s = FakeSession({"https://jooble.org/api/SECRET": FakeResponse(200, payload)})
    ctx = make_ctx(s, make_settings(jooble_api_key="SECRET", jooble_locations=["Tunisia"]))
    out = JoobleBackend().run(ctx)
    assert out.status == "SUCCESS" and len(out.jobs) == 1
    job = out.jobs[0]
    assert (job.title, job.company, job.source_job_id, job.location_raw, job.description) == ("Ingénieur SIG", "GeoTun", "jooble:9", "Tunis", "SIG et QGIS")
    assert s.calls[0][0] == "POST" and s.calls[0][2] == {"keywords": "GIS", "location": "Tunisia", "page": 1}
    assert len(s.calls) == len(JoobleBackend.terms)
    assert JoobleBackend().enabled(make_ctx(FakeSession()))[0] is False


def test_duckduckgo_results_and_robots_stop():
    html = ('<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fjobs.lever.co%2Fgeoco%2F0a1b2c3d-1111-2222-3333-444455556666">GIS Analyst - GeoCo</a>'
            '<a class="result__a" href="https://www.linkedin.com/jobs/view/1">x</a>'
            '<a class="result__a" href="https://surveyco.example/careers/lidar-technician">LiDAR Technician</a>')
    assert len(DuckDuckGoBackend.parse_results(html)) == 3
    s = FakeSession({"https://html.duckduckgo.com/html/": FakeResponse(200, html)})
    ctx = make_ctx(s, make_settings(search_queries_per_run=2))
    out = DuckDuckGoBackend().run(ctx)
    assert "lever:geoco" in ctx.state["boards"] and out.details["excluded_domain"] >= 1
    assert out.details["pages_queued"] >= 2 and ctx.cursor("search_duckduckgo")["index"] == 2
    blocked = FakeSession({"https://html.duckduckgo.com/robots.txt": FakeResponse(200, "User-agent: *\nDisallow: /", headers={"Content-Type": "text/plain"})})
    out = DuckDuckGoBackend().run(make_ctx(blocked, make_settings(search_queries_per_run=5)))
    assert out.status == "FAILED" and out.details["stopped_reason"] == "ROBOTS_DISALLOWED"
    assert len([c for c in blocked.calls if "html/" in c[1]]) == 0


def test_searxng_requires_url():
    assert SearxngBackend().enabled(make_ctx(FakeSession()))[0] is False
    s = FakeSession({"https://searx.example/search": FakeResponse(200, {"results": [{"url": "https://boards.greenhouse.io/mapco/jobs/9", "title": "GIS"}]})})
    ctx = make_ctx(s, make_settings(searxng_url="https://searx.example", search_queries_per_run=1))
    SearxngBackend().run(ctx)
    assert "greenhouse:mapco" in ctx.state["boards"]


def test_commoncrawl_discovers_boards_and_paginates():
    collinfo = [{"id": "CC-MAIN-2026-35", "cdx-api": "https://index.commoncrawl.org/CC-MAIN-2026-35-index"}]
    lines = "\n".join(['{"url": "https://boards.greenhouse.io/alpha/jobs/1"}', '{"url": "https://boards.greenhouse.io/beta"}',
                       'garbage', '{"url": "https://boards.greenhouse.io/embed/x"}'])

    def cdx(method, url, params, body):
        if "showNumPages" in url:
            return FakeResponse(200, {"pages": 2})
        return FakeResponse(200, lines, headers={"Content-Type": "text/plain"})

    s = FakeSession({"https://index.commoncrawl.org/collinfo.json": FakeResponse(200, collinfo),
                     "https://index.commoncrawl.org/CC-MAIN-2026-35-index": cdx})
    ctx = make_ctx(s, make_settings(commoncrawl_enabled=True, commoncrawl_pages_per_run=1))
    out = CommonCrawlBackend().run(ctx)
    assert out.details["boards_new"] == 2 and {"greenhouse:alpha", "greenhouse:beta"} <= set(ctx.state["boards"])
    assert ctx.cursor("commoncrawl")["patterns"]["boards.greenhouse.io/*"]["next_page"] == 1
