from conftest import GIS_DESCRIPTION, FakeResponse, FakeSession, make_ctx

from geojobbot.scrapers.ats.base import ATSBackend
from geojobbot.scrapers.ats.core_ats import AshbyAdapter, GreenhouseAdapter, LeverAdapter
from geojobbot.scrapers.ats.detect import BoardRef, detect, find_boards_in_text
from geojobbot.scrapers.ats.more_ats import (PersonioAdapter, RecruiteeAdapter, SmartRecruitersAdapter,
                                             WorkableAdapter, WorkdayAdapter)

GH = "https://boards-api.greenhouse.io/v1/boards"
UUID = "0a1b2c3d-1111-2222-3333-444455556666"


def fetch(adapter, session, slug, geo=True, **kw):
    ctx = make_ctx(session)
    return adapter.fetch_board(ctx, BoardRef(adapter.ats, slug), geo_context=geo, variant=kw.get("variant"),
                               company_hint=None), ctx


# ------------------------------------------------------------------ detection
def test_detect_native_ids():
    assert detect("https://boards.greenhouse.io/acme/jobs/12345")[0] == "greenhouse:12345"
    assert detect("https://job-boards.greenhouse.io/acme/jobs/12345?gh_src=x")[1].key == "greenhouse:acme"
    assert detect("https://www.acme.com/careers/job?gh_jid=987")[0] == "greenhouse:987"
    assert detect(f"https://jobs.lever.co/acme/{UUID}/apply")[0] == f"lever:{UUID}"
    assert detect(f"https://jobs.ashbyhq.com/Acme/{UUID}")[1].slug == "Acme"
    assert detect("https://apply.workable.com/acme/j/AB12CD34EF/")[0] == "workable:AB12CD34EF"
    assert detect("https://jobs.smartrecruiters.com/Acme/743999912345678-gis-analyst")[0] == "smartrecruiters:743999912345678"
    assert detect("https://acme.recruitee.com/o/gis-analyst")[0] == "recruitee:acme:gis-analyst"
    wd = detect("https://acme.wd5.myworkdayjobs.com/en-US/External/job/Denver-CO/GIS-Analyst_R12345")
    assert wd[1].slug == "acme/wd5/External" and wd[0] == "workday:acme:R12345"
    assert detect("https://boards.greenhouse.io/embed/job_board?for=acme")[1].key == "greenhouse:acme"
    assert detect("https://boards.greenhouse.io/robots.txt")[1] is None
    assert detect("https://example.com/") == (None, None)


def test_find_boards_in_career_page_html():
    html = ('<script src="https://boards.greenhouse.io/embed/job_board/js?for=geoco"></script>'
            f'<a href="https://jobs.lever.co/mapco/{UUID}">x</a><iframe src="https://jobs.ashbyhq.com/survey"></iframe>')
    keys = {b.key for b in find_boards_in_text(html)}
    assert {"greenhouse:geoco", "lever:mapco", "ashby:survey"} <= keys


# ------------------------------------------------------------------ greenhouse
def gh_routes():
    return {
        f"{GH}/acme/jobs/1": FakeResponse(200, {"id": 1, "title": "GIS Analyst", "content": "&lt;p&gt;" + GIS_DESCRIPTION + "&lt;/p&gt;",
                                                 "location": {"name": "Remote - Canada"}, "absolute_url": "https://acme.com/careers?gh_jid=1",
                                                 "first_published": "2026-09-15T08:00:00Z", "updated_at": "2026-09-16T00:00:00Z"}),
        f"{GH}/acme/jobs": FakeResponse(200, {"jobs": [
            {"id": 1, "title": "GIS Analyst", "location": {"name": "Remote"}},
            {"id": 2, "title": "Account Executive"},
            {"title": None},
        ]}),
        f"{GH}/acme": FakeResponse(200, {"name": "Acme Mapping"}),
    }


def test_greenhouse_valid_board_with_detail_and_prefilter():
    result, _ = fetch(GreenhouseAdapter(), FakeSession(gh_routes()), "acme")
    assert result.status == "VALID" and result.listed == 3 and result.prefiltered_out == 2
    job = result.jobs[0]
    assert job.native_id == "greenhouse:1" and job.company == "Acme Mapping"
    assert "ArcGIS Pro" in job.description and job.posted_at_reliable
    assert job.location_raw == "Remote - Canada"


def test_greenhouse_invalid_token_reported():
    result, _ = fetch(GreenhouseAdapter(), FakeSession({f"{GH}/nope/jobs": FakeResponse(404)}), "nope")
    assert result.status == "INVALID"


def test_greenhouse_schema_mismatch():
    result, _ = fetch(GreenhouseAdapter(), FakeSession({f"{GH}/odd/jobs": FakeResponse(200, {"postings": []})}), "odd")
    assert result.status == "SCHEMA_MISMATCH"


def test_greenhouse_detail_failure_falls_back_to_title_only():
    routes = gh_routes()
    routes[f"{GH}/acme/jobs/1"] = FakeResponse(500)
    result, _ = fetch(GreenhouseAdapter(), FakeSession(routes), "acme")
    assert result.jobs[0].title == "GIS Analyst" and result.jobs[0].description == ""


# ------------------------------------------------------------------ lever / ashby
def test_lever_eu_fallback_and_fields():
    item = {"id": UUID, "text": "Geospatial Analyst", "categories": {"location": "Amsterdam, Netherlands", "commitment": "Full-time"},
            "descriptionPlain": GIS_DESCRIPTION, "lists": [{"text": "Requirements", "content": "<li>QGIS</li>"}],
            "hostedUrl": f"https://jobs.eu.lever.co/acme/{UUID}", "applyUrl": f"https://jobs.eu.lever.co/acme/{UUID}/apply",
            "createdAt": 1789000000000, "workplaceType": "hybrid", "salaryRange": {"min": 50000, "max": 60000, "currency": "EUR", "interval": "per-year-salary"}}
    s = FakeSession({"https://api.lever.co/v0/postings/acme": FakeResponse(404),
                     "https://api.eu.lever.co/v0/postings/acme": FakeResponse(200, [item])})
    result, _ = fetch(LeverAdapter(), s, "acme")
    assert result.status == "VALID" and result.variant == "eu"
    job = result.jobs[0]
    assert "QGIS" in job.description and job.workplace_type == "hybrid" and "EUR" in job.salary


def test_lever_invalid_on_both_hosts():
    s = FakeSession({"https://api.lever.co/v0/postings/x": FakeResponse(404),
                     "https://api.eu.lever.co/v0/postings/x": FakeResponse(404)})
    assert fetch(LeverAdapter(), s, "x")[0].status == "INVALID"


def test_ashby_empty_is_unverified_and_fields():
    s = FakeSession({"https://api.ashbyhq.com/posting-api/job-board/empty": FakeResponse(200, {"jobs": []})})
    assert fetch(AshbyAdapter(), s, "empty")[0].status == "EMPTY_UNVERIFIED"
    s = FakeSession({"https://api.ashbyhq.com/posting-api/job-board/geo": FakeResponse(200, {"jobs": [
        {"title": "Remote Sensing Specialist", "location": "Remote", "isRemote": True, "isListed": True,
         "descriptionPlain": GIS_DESCRIPTION, "jobUrl": f"https://jobs.ashbyhq.com/geo/{UUID}",
         "publishedAt": "2026-09-15T00:00:00Z", "compensation": {"compensationTierSummary": "$80K – $100K"}},
        {"title": "GIS Analyst", "isListed": False, "jobUrl": "x"},
    ]})})
    result, _ = fetch(AshbyAdapter(), s, "geo")
    assert len(result.jobs) == 1 and result.jobs[0].native_id == f"ashby:{UUID}" and result.jobs[0].remote_flag


# ------------------------------------------------------------------ other ATS
def test_smartrecruiters_pagination_and_empty():
    s = FakeSession({"https://api.smartrecruiters.com/v1/companies/none/postings": FakeResponse(200, {"totalFound": 0, "content": []})})
    assert fetch(SmartRecruitersAdapter(), s, "none")[0].status == "EMPTY_UNVERIFIED"
    base = "https://api.smartrecruiters.com/v1/companies/acme/postings"
    s = FakeSession({
        f"{base}?limit=100&offset=0": FakeResponse(200, {"totalFound": 1, "content": [
            {"id": "744000001", "name": "GIS Technician", "location": {"city": "Perth", "country": "au"},
             "releasedDate": "2026-09-14T00:00:00Z", "company": {"name": "Acme"}}]}),
        f"{base}/744000001": FakeResponse(200, {"jobAd": {"sections": {"jobDescription": {"text": "<p>" + GIS_DESCRIPTION + "</p>"}}},
                                                "postingUrl": "https://jobs.smartrecruiters.com/acme/744000001"}),
    })
    result, _ = fetch(SmartRecruitersAdapter(), s, "acme")
    assert result.status == "VALID" and "ArcGIS" in result.jobs[0].description


def test_workable_recruitee_personio():
    s = FakeSession({"https://apply.workable.com/api/v1/widget/accounts/acme": FakeResponse(200, {"name": "Acme", "jobs": [
        {"title": "Cartographer", "shortcode": "ab12cd34", "city": "London", "country": "United Kingdom",
         "telecommuting": False, "description": GIS_DESCRIPTION, "published_on": "2026-09-15"}]})})
    job = fetch(WorkableAdapter(), s, "acme")[0].jobs[0]
    assert job.native_id == "workable:AB12CD34" and job.location_raw == "London, United Kingdom"

    s = FakeSession({"https://geo.recruitee.com/api/offers/": FakeResponse(200, {"offers": [
        {"id": 5, "slug": "gis-specialist", "title": "GIS Specialist", "description": GIS_DESCRIPTION, "remote": True,
         "city": "Berlin", "country": "Germany", "published_at": "2024-01-01 10:00:00 UTC"}]})})
    job = fetch(RecruiteeAdapter(), s, "geo")[0].jobs[0]
    assert job.native_id == "recruitee:geo:gis-specialist" and job.remote_flag is True

    s = FakeSession({"https://bad.jobs.personio.de/xml": FakeResponse(200, "<html><body>Not found</body></html>")})
    assert fetch(PersonioAdapter(), s, "bad")[0].status == "INVALID"
    xml = ("<?xml version='1.0'?><workzag-jobs><position><id>77</id><office>Munich</office><name>Geomatics Engineer</name>"
           "<jobDescriptions><jobDescription><name>Tasks</name><value>&lt;p&gt;LiDAR processing&lt;/p&gt;</value></jobDescription>"
           "</jobDescriptions><createdAt>2026-09-10T10:00:00+00:00</createdAt></position></workzag-jobs>")
    s = FakeSession({"https://geo.jobs.personio.de/xml": FakeResponse(200, xml, headers={"Content-Type": "text/xml"})})
    job = fetch(PersonioAdapter(), s, "geo")[0].jobs[0]
    assert job.title == "Geomatics Engineer" and "LiDAR" in job.description


def test_workday_search_and_detail():
    origin = "https://acme.wd5.myworkdayjobs.com"
    api = f"{origin}/wday/cxs/acme/External"

    def jobs(method, url, params, body):
        if body and body.get("searchText") == "GIS":
            return FakeResponse(200, {"total": 1, "jobPostings": [{"title": "GIS Analyst", "externalPath": "/job/Denver/GIS-Analyst_R1", "locationsText": "Denver, CO"}]})
        return FakeResponse(200, {"total": 0, "jobPostings": []})

    s = FakeSession({f"{api}/jobs": jobs,
                     f"{api}/job/Denver/GIS-Analyst_R1": FakeResponse(200, {"jobPostingInfo": {
                         "title": "GIS Analyst", "jobDescription": "<p>" + GIS_DESCRIPTION + "</p>", "location": "Denver, CO",
                         "startDate": "2026-09-15", "jobReqId": "R1"}, "hiringOrganization": {"name": "Acme Utility"}})})
    result, _ = fetch(WorkdayAdapter(), s, "acme/wd5/External")
    assert result.status == "VALID" and result.jobs[0].native_id == "workday:acme:R1"
    assert result.jobs[0].company == "Acme Utility"
    invalid = FakeSession({f"{api}/jobs": FakeResponse(404)})
    assert fetch(WorkdayAdapter(), invalid, "acme/wd5/External")[0].status == "INVALID"


# ------------------------------------------------------------------ backend isolation + reporting
def test_ats_backend_isolates_crashing_board_and_reports_invalid():
    routes = gh_routes()
    routes[f"{GH}/gone/jobs"] = FakeResponse(404)
    routes[f"{GH}/broken/jobs"] = FakeResponse(200, {"jobs": [{"id": 9, "title": "GIS Analyst"}]})
    routes[f"{GH}/broken"] = FakeResponse(200, {"name": "Broken"})
    routes[f"{GH}/broken/jobs/9"] = FakeResponse(200, {"id": 9, "title": "GIS Analyst", "location": "not-a-dict"})
    ctx = make_ctx(FakeSession(routes))
    out = ATSBackend(GreenhouseAdapter(), ["acme", "gone", "broken"]).run(ctx)
    assert out.details["invalid_configured"] == ["gone"]
    assert any(j.native_id == "greenhouse:1" for j in out.jobs)
    assert out.status == "PARTIAL"
    assert ctx.state["boards"]["greenhouse:gone"]["status"] == "INVALID"
    assert ctx.state["boards"]["greenhouse:broken"]["status"] == "SCHEMA_MISMATCH"


def test_rotation_scheduling_budget():
    from geojobbot.core.boards import BoardRegistry
    from conftest import NOW
    state = {"boards": {}}
    reg = BoardRegistry(state, NOW)
    for i in range(10):
        reg.register(BoardRef("lever", f"co{i}"), "commoncrawl")
    reg.register(BoardRef("lever", "hot"), "search")
    picked = reg.select("lever", ["mine"], rotation_budget=3)
    reasons = {ref.slug: why for ref, why in picked}
    assert reasons["mine"] == "config" and reasons["hot"] == "hot"
    assert sum(1 for w in reasons.values() if w == "rotation") == 3
