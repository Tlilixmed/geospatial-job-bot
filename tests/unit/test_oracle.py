"""Oracle Recruiting Cloud adapter. Fixtures are trimmed from live answers of 2026-10-10 (WSP emit.ca3/CX_2001,
Michael Baker ebxs.us2/CX_2); dates of the expiry cases are moved around the tests' clock (2026-09-16)."""
from urllib.parse import unquote

import pytest
from conftest import GIS_DESCRIPTION, FakeResponse, FakeSession, make_ctx, make_settings

from geojobbot.scrapers.ats.detect import BoardRef, detect, find_boards_in_text, parse_board_url, parse_oracle_url
from geojobbot.scrapers.ats.oracle import ORACLE_SEARCH_TERMS, OracleAdapter

WSP = "https://emit.fa.ca3.oraclecloud.com"
MB = "https://ebxs.fa.us2.oraclecloud.com"
API = "hcmRestApi/resources/latest"


def site_page(site: str, name: str = "WSP") -> str:
    # the parts of the real page the adapter reads (the rest is a JavaScript app)
    return (f'<html lang="en"><head><meta property="og:site_name" content="{name}"/><title>{name}</title>'
            f'<base href="/hcmUI/CandidateExperience/en/sites/{site}" data-sitenumber="{site}"/></head></html>')


def row(job_id, title, place="Vancouver, BC, Canada", posted="2026-09-15", **extra):
    return {"Id": str(job_id), "Title": title, "PostedDate": posted, "PostingEndDate": None, "Language": "US",
            "PrimaryLocation": place, "PrimaryLocationCountry": "CA", "WorkplaceType": "", "JobSchedule": None,
            "LegalEmployer": None, "BusinessUnit": None, "ShortDescriptionStr": "Your impact start here",
            "secondaryLocations": [], **extra}


def listing(total, rows):
    # hasMore is always false on the live API, whatever is left
    return FakeResponse(200, {"items": [{"TotalJobsCount": total, "SiteNumber": "CX_2001", "requisitionList": rows}],
                              "count": 1, "hasMore": False, "limit": 200, "offset": 0})


# live list rows: 97345 and 97222 are search hits for "GIS"; 97147 came up for "GIS" too (the search reads the text)
WSP_ROWS = [
    row(97345, "Senior GIS Analyst", posted="2026-09-15",
        secondaryLocations=[{"Name": "Terrace, BC, Canada", "CountryCode": "CA"}]),
    row(97147, "Solar PV Engineer", "Montreal, QC, Canada"),
    row(97222, "Infrastructure Digital Delivery Lead", "Dubai, United Arab Emirates", PrimaryLocationCountry="AE"),
]

WSP_97345 = {
    "Id": "97345", "Title": "Senior GIS Analyst", "PrimaryLocation": "Vancouver, BC, Canada",
    "ExternalPostedStartDate": "2026-09-15T20:07:13+00:00", "ExternalPostedEndDate": None, "JobSchedule": None,
    "WorkplaceType": "", "LegalEmployer": None,
    "ExternalDescriptionStr": ("<p><strong>What if you could redefine what’s possible?</strong></p><p>" + GIS_DESCRIPTION +
                               "</p><p><strong>Compensation</strong></p><p><strong>AB, BC, NT, NU, SK &amp; YT: "
                               "$93,700 - $124,200</strong></p>"),
    "ExternalResponsibilitiesStr": None, "ExternalQualificationsStr": "",
    "CorporateDescriptionStr": "At <b>WSP</b>, we exist to shape communities: surveyors, hydrographers, photogrammetry and remote sensing experts.",
    "OrganizationDescriptionStr": "Our Geomatics business line operates LiDAR sensors across Canada.",
    "secondaryLocations": [{"Name": "Terrace, BC, Canada"}],
}

MB_309977 = {
    "Id": "309977", "Title": "GIS Support Representative", "PrimaryLocation": "United States",
    "ExternalPostedStartDate": "2026-09-14T17:49:01+00:00", "ExternalPostedEndDate": "2026-10-23T23:00:00+00:00",
    "WorkplaceType": "Hybrid", "JobSchedule": "Full time",
    "ExternalDescriptionStr": ("<p>" + GIS_DESCRIPTION + "</p><h2>Compensation</h2><p>The approximate compensation range "
                               "for this position is $57,000 to $67,000.</p>"),
    "CorporateDescriptionStr": "Michael Baker International, a leading provider of engineering and consulting services",
}


def finder(url: str) -> dict:
    """The finder's fields: {"siteNumber": "CX_2001", "limit": "200", "offset": "0", "keyword": "GIS", ...}"""
    value = unquote(url.split("finder=", 1)[1])
    name, _, fields = value.partition(";")
    out = {"finder": name}
    for part in fields.split(","):
        key, _, val = part.partition("=")
        out[key] = val.strip('"')
    return out


def wsp_session(lists, details=None, page=None):
    """`lists(fields) -> FakeResponse` answers the list calls; details by Id."""
    details = details if details is not None else {"97345": WSP_97345}

    def list_handler(method, url, params, body):
        return lists(finder(url))

    def detail_handler(method, url, params, body):
        job_id = finder(url)["Id"]
        return FakeResponse(200, {"items": [details[job_id]], "count": 1}) if job_id in details else FakeResponse(200, {"items": [], "count": 0})

    return FakeSession({
        f"{WSP}/hcmUI/CandidateExperience/en/sites/CX_2001": FakeResponse(200, page if page is not None else site_page("CX_2001")),
        f"{WSP}/{API}/recruitingCEJobRequisitions?": list_handler,
        f"{WSP}/{API}/recruitingCEJobRequisitionDetails?": detail_handler,
    })


def fetch(session, slug="emit.ca3/CX_2001", geo=True, state=None, settings=None):
    ctx = make_ctx(session, settings=settings, state=state)
    return OracleAdapter().fetch_board(ctx, BoardRef("oracle", slug), geo_context=geo, variant=None, company_hint=None), ctx


def list_calls(session):
    return [finder(c[1]) for c in session.calls if "recruitingCEJobRequisitions?" in c[1]]


# ------------------------------------------------------------------ (a) detection and config URLs
@pytest.mark.parametrize("url, native, slug", [
    (f"{WSP}/hcmUI/CandidateExperience/en/sites/CX_2001", None, "emit.ca3/CX_2001"),
    (f"{WSP}/hcmUI/CandidateExperience/en/sites/CX_2001/job/97345", "oracle:emit.ca3:97345", "emit.ca3/CX_2001"),
    (f"{WSP}/hcmUI/CandidateExperience/fr-CA/sites/CX_2001/job/97345/?utm_source=x", "oracle:emit.ca3:97345", "emit.ca3/CX_2001"),
    (f"{MB}/hcmUI/CandidateExperience/en/sites/CX_2/requisitions/preview/309977", "oracle:ebxs.us2:309977", "ebxs.us2/CX_2"),
    (f"{MB}/hcmUI/CandidateExperience/en/sites/CX_2/requisitions", None, "ebxs.us2/CX_2"),
    ("https://EMIT.fa.ca3.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001/job/1", "oracle:emit.ca3:1", "emit.ca3/CX_2001"),
    # the REST API, as found in page sources and logs
    (f"{WSP}/{API}/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations"
     "&finder=findReqs;siteNumber=CX_2001,limit=200,offset=0,keyword=%22GIS%22,sortBy=POSTING_DATES_DESC", None, "emit.ca3/CX_2001"),
    (f"{MB}/{API}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id=%22309977%22,siteNumber=CX_2",
     "oracle:ebxs.us2:309977", "ebxs.us2/CX_2"),
    # not a Candidate Experience URL
    ("https://www.oraclecloud.com/careers", None, None),
    (f"{WSP}/hcmUI/faces/FuseWelcome", None, None),
    (f"{WSP}/hcmUI/CandidateExperience/en/sites/", None, None),
    ("https://emit.fa.ca3.oraclecloud.com.evil.example/hcmUI/CandidateExperience/en/sites/CX_1", None, None),
])
def test_detect_oracle_urls(url, native, slug):
    found_native, board = detect(url)
    assert found_native == native
    assert (board.slug if board else None) == slug
    assert parse_oracle_url(url) == board
    if board:
        assert board.ats == "oracle" and board.key == f"oracle:{slug.lower()}"


@pytest.mark.parametrize("ats, value, slug", [
    ("oracle", f"{WSP}/hcmUI/CandidateExperience/en/sites/CX_2001", "emit.ca3/CX_2001"),
    ("oracle", f"{MB}/hcmUI/CandidateExperience/en/sites/CX_2", "ebxs.us2/CX_2"),
    ("oracle", "emit.ca3/CX_2001", None),       # a URL is expected, as for Workday
    ("oracle", "https://example.com/careers", None),
    ("workday", "https://t.wd5.myworkdayjobs.com/Site", "t/wd5/Site"),
    ("workday", "junk", None),
    ("greenhouse", "esri", "esri"),
])
def test_config_board_urls(ats, value, slug):
    ref = parse_board_url(ats, value)
    assert (ref.slug if ref else None) == slug


def test_career_page_links_register_oracle_boards():
    html = (f'<a href="{WSP}/hcmUI/CandidateExperience/en/sites/CX_2001">Search jobs</a>'
            f'<a href="{MB}/hcmUI/CandidateExperience/en/sites/CX_2/requisitions/preview/309977?keyword=gis&amp;mode=location">GIS</a>')
    assert {b.key for b in find_boards_in_text(html)} == {"oracle:emit.ca3/cx_2001", "oracle:ebxs.us2/cx_2"}


def test_sources_toml_oracle_and_workday_entries_all_parse():
    import tomllib
    from pathlib import Path

    ats = tomllib.loads((Path(__file__).parents[2] / "config" / "sources.toml").read_text(encoding="utf-8"))["ats"]
    assert ats["oracle"] and ats["workday"]
    for name in ("oracle", "workday"):
        assert all(parse_board_url(name, url) for url in ats[name]), name


def test_discovery_looks_for_oracle_sites():
    from geojobbot.scrapers.search import CC_PATTERNS, build_queries

    assert ("oraclecloud.com", "domain") in CC_PATTERNS
    assert 'site:oraclecloud.com "GIS"' in build_queries()


def test_build_backends_parses_oracle_site_urls():
    from geojobbot.core.pipeline import build_backends

    settings = make_settings(sources={"ats": {"oracle": [f"{WSP}/hcmUI/CandidateExperience/en/sites/CX_2001", "junk"]}})
    names = {b.name: b for b in build_backends(settings)}
    assert names["oracle"].configured_slugs == ["emit.ca3/CX_2001"]


# ------------------------------------------------------------------ listing: paging, limits, keyword mode
def test_small_site_is_read_whole_until_the_total_and_hasmore_is_ignored():
    def lists(f):
        offset = int(f["offset"])
        assert "keyword" not in f
        return listing(250, [row(1000 + i, f"GIS Technician {i}") for i in range(offset, min(offset + 200, 250))])

    session = wsp_session(lists, details={})
    result, _ = fetch(session, settings=make_settings(max_detail_fetches_per_board=0))
    calls = list_calls(session)
    assert [c["offset"] for c in calls] == ["0", "200"]  # the first page said hasMore=false: read on anyway, stop at 250
    assert {c["limit"] for c in calls} == {"200"} and {c["siteNumber"] for c in calls} == {"CX_2001"}
    assert result.status == "VALID" and result.listed == 250


def test_large_site_is_searched_by_keyword_two_pages_at_most_never_over_200_a_call():
    def lists(f):
        term, offset = f.get("keyword"), int(f["offset"])
        if term is None:
            return listing(4049, [row(1, "Accountant")])
        if term == "GIS":
            return listing(683, [row(10_000 + offset + i, f"GIS Analyst {offset + i}") for i in range(200)])
        if term == "geospatial":
            return listing(283, [row(20_000 + offset + i, f"Geospatial Developer {offset + i}") for i in range(200 if offset == 0 else 83)])
        return listing(0, [])

    session = wsp_session(lists, details={})
    result, _ = fetch(session, settings=make_settings(max_detail_fetches_per_board=0))
    calls = list_calls(session)
    by_term = {}
    for c in calls:
        by_term.setdefault(c.get("keyword"), []).append(c["offset"])
    assert by_term["GIS"] == ["0", "200"]          # 683 hits: two pages, not four
    assert by_term["geospatial"] == ["0", "200"]   # stops on the term's own total
    assert set(by_term) == {None, *ORACLE_SEARCH_TERMS} and by_term[None] == ["0"]
    assert {c["limit"] for c in calls} == {"200"}
    assert all(c["sortBy"] == "POSTING_DATES_DESC" for c in calls)
    assert result.listed == 4049


def test_same_posting_found_by_two_keywords_is_read_once():
    hit = row(97345, "Senior GIS Analyst")

    def lists(f):
        if f.get("keyword") is None:
            return listing(4049, [])
        return listing(1, [hit]) if f["keyword"] in ("GIS", "geospatial", "mapping") else listing(0, [])

    session = wsp_session(lists)
    result, _ = fetch(session)
    assert [j.native_id for j in result.jobs] == ["oracle:emit.ca3:97345"]
    assert sum("recruitingCEJobRequisitionDetails" in c[1] for c in session.calls) == 1


def test_prefilter_drops_generic_titles_that_were_no_search_hit():
    # the probe's newest postings of a large site are every kind of job: "Solar PV Engineer" needs a geospatial title there
    def lists(f):
        if f.get("keyword") is None:
            return listing(4049, WSP_ROWS)
        return listing(1, [WSP_ROWS[0]]) if f["keyword"] == "GIS" else listing(0, [])

    result, _ = fetch(wsp_session(lists))
    assert [j.title for j in result.jobs] == ["Senior GIS Analyst"] and result.prefiltered_out == 2
    # a search hit with a generic title is kept on a configured board, as on Workday; a rotation board is strict
    def hits(f):
        return listing(4049, []) if f.get("keyword") is None else listing(3, WSP_ROWS) if f["keyword"] == "GIS" else listing(0, [])

    configured, _ = fetch(wsp_session(hits))
    assert {j.title for j in configured.jobs} == {"Senior GIS Analyst", "Solar PV Engineer"}
    rotation, _ = fetch(wsp_session(hits), geo=False)
    assert [j.title for j in rotation.jobs] == ["Senior GIS Analyst"]


# ------------------------------------------------------------------ detail requests
def test_detail_budget_goes_to_unread_postings_and_known_ones_are_still_reported():
    rows = [row(1, "GIS Analyst 1"), row(2, "GIS Analyst 2"), row(3, "GIS Analyst 3")]
    details = {str(i): dict(WSP_97345, Id=str(i), Title=f"GIS Analyst {i}") for i in (1, 2, 3)}
    state = {"jobs": {"oracle:emit.ca3:1": {"text_seen": "2026-09-15T00:00:00Z", "description_length": 900,
                                            "aliases": ["oracle:emit.ca3:1"]}}}
    session = wsp_session(lambda f: listing(3, rows), details=details)
    result, ctx = fetch(session, state=state, settings=make_settings(max_detail_fetches_per_board=1))
    read = [finder(c[1])["Id"] for c in session.calls if "recruitingCEJobRequisitionDetails" in c[1]]
    assert read == ["2"]                                    # not 1 (text on file), not 3 (over budget: next run)
    jobs = {j.native_id: j for j in result.jobs}
    assert set(jobs) == {"oracle:emit.ca3:1", "oracle:emit.ca3:2"}
    assert jobs["oracle:emit.ca3:1"].description == "" and jobs["oracle:emit.ca3:2"].description


def test_failed_detail_keeps_the_posting_by_its_title():
    session = wsp_session(lambda f: listing(1, [WSP_ROWS[0]]), details={})
    result, _ = fetch(session)
    assert result.detail_errors == 1 and result.jobs[0].title == "Senior GIS Analyst" and result.jobs[0].description == ""


@pytest.mark.parametrize("end, kept", [
    (None, True),                          # null on many postings (WSP 97345): never required
    ("2026-10-23T23:00:00+00:00", True),
    ("2026-09-01T00:00:00+00:00", False),  # closed before the run
])
def test_closing_date_in_the_past_drops_the_posting(end, kept):
    detail = dict(WSP_97345, ExternalPostedEndDate=end)
    result, _ = fetch(wsp_session(lambda f: listing(1, [WSP_ROWS[0]]), details={"97345": detail}))
    assert bool(result.jobs) is kept


def test_fields_text_and_salary_from_the_detail():
    result, _ = fetch(wsp_session(lambda f: listing(1, [WSP_ROWS[0]])))
    job = result.jobs[0]
    assert job.native_id == "oracle:emit.ca3:97345" and job.source_job_id == "97345" and job.source_name == "oracle"
    assert job.company == "WSP" and result.company == "WSP"  # the career page's name: LegalEmployer is null
    assert job.url == job.apply_url == f"{WSP}/hcmUI/CandidateExperience/en/sites/CX_2001/job/97345"
    assert job.location_raw == "Vancouver, BC, Canada; Terrace, BC, Canada"
    assert job.posted_at.isoformat() == "2026-09-15T20:07:13+00:00" and job.posted_at_reliable
    assert job.workplace_type is None and job.employment_type is None  # "" and null on WSP
    assert "ArcGIS Pro" in job.description
    assert "$93,700 - $124,200" in job.description                      # (j) the pay range reaches the text
    # (i) the employer's boilerplate is not the job's text: it would lend every WSP posting LiDAR and photogrammetry
    assert "LiDAR sensors" not in job.description and "photogrammetry" not in job.description
    assert "Your impact" not in job.description                        # the list teaser is not a description


def test_michael_baker_detail_by_url():
    session = FakeSession({
        f"{MB}/{API}/recruitingCEJobRequisitionDetails?": FakeResponse(200, {"items": [MB_309977], "count": 1}),
        f"{MB}/hcmUI/CandidateExperience/en/sites/CX_2": FakeResponse(200, site_page("CX_2", "Michael Baker International")),
    })
    ctx = make_ctx(session)
    jobs = OracleAdapter().fetch_job(ctx, f"{MB}/hcmUI/CandidateExperience/en/sites/CX_2/requisitions/preview/309977")
    job = jobs[0]
    assert job.native_id == "oracle:ebxs.us2:309977" and job.company == "Michael Baker International"
    assert job.workplace_type == "Hybrid" and job.employment_type == "Full time"
    assert "$57,000 to $67,000" in job.description and "leading provider" not in job.description
    assert finder(session.calls[0][1]) == {"finder": "ById", "Id": "309977", "siteNumber": "CX_2"}
    assert OracleAdapter().fetch_job(ctx, f"{MB}/hcmUI/CandidateExperience/en/sites/CX_2") is None  # no job id


# ------------------------------------------------------------------ board validation
def test_made_up_site_is_invalid_although_the_api_would_answer():
    # live: siteNumber=CX_9999 returns WSP's jobs; the career page redirects to the default site and names it
    session = FakeSession({f"{WSP}/hcmUI/CandidateExperience/en/sites/CX_9999": FakeResponse(200, site_page("CX_2001")),
                           f"{WSP}/{API}/": listing(283, WSP_ROWS)})
    result, _ = fetch(session, slug="emit.ca3/CX_9999")
    assert result.status == "INVALID" and "CX_9999" in result.error
    assert not [c for c in session.calls if "hcmRestApi" in c[1]]


@pytest.mark.parametrize("page, rows, status", [
    (site_page("CX_2001"), WSP_ROWS, "VALID"),
    (site_page("cx_2001"), WSP_ROWS, "VALID"),                  # site numbers are not case-sensitive
    (site_page("CX_2001"), [], "VALID"),                         # the site exists, it has no job today
    ("<html>maintenance</html>", WSP_ROWS, "EMPTY_UNVERIFIED"),  # cannot tell which site the API answered for
    ("<html>maintenance</html>", [], "EMPTY_UNVERIFIED"),
])
def test_site_confirmation(page, rows, status):
    result, _ = fetch(wsp_session(lambda f: listing(len(rows), rows), page=page))
    assert result.status == status
    if rows and status == "EMPTY_UNVERIFIED":
        assert result.jobs  # the postings are still read


def test_api_errors_and_schema():
    def broken(f):
        return FakeResponse(404)

    assert fetch(wsp_session(broken))[0].status == "INVALID"
    # without expand=requisitionList... the rows are missing
    no_rows = FakeResponse(200, {"items": [{"TotalJobsCount": 283, "SiteNumber": "CX_2001"}], "count": 1, "hasMore": False})
    assert fetch(wsp_session(lambda f: no_rows))[0].status == "SCHEMA_MISMATCH"
    assert fetch(wsp_session(lambda f: FakeResponse(200, {"items": []})))[0].status == "SCHEMA_MISMATCH"
    assert fetch(wsp_session(lambda f: listing(0, [])), slug="emit/CX_2001")[0].status == "INVALID"  # not pod.dc/site
    unknown = FakeSession({f"{WSP}/hcmUI/": FakeResponse(404)})
    assert fetch(unknown)[0].status == "INVALID"


def test_list_urls_keep_the_finder_literal():
    url = OracleAdapter._list_url("emit.fa.ca3.oraclecloud.com", "CX_2001", 200, "remote sensing")
    assert url == (f"{WSP}/{API}/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations"
                   "&finder=findReqs;siteNumber=CX_2001,limit=200,offset=200,keyword=%22remote%20sensing%22,sortBy=POSTING_DATES_DESC")


# ------------------------------------------------------------------ wiring outside the adapter
def test_requisitions_with_the_same_title_never_merge():
    from conftest import NOW
    from geojobbot.core.fusion import fuse

    raws = []
    for job_id in ("97001", "97002"):  # WSP posts the same title in the same city as separate requisitions
        result, _ = fetch(wsp_session(lambda f, j=job_id: listing(1, [row(j, "GIS Technician")]),
                                      details={job_id: dict(WSP_97345, Id=job_id, Title="GIS Technician")}))
        raws += result.jobs
    assert len(fuse(raws, {}, NOW)) == 2


def test_hot_oracle_board_still_fetches_a_linked_job():
    from geojobbot.scrapers.ats.more_ats import all_adapters
    from geojobbot.scrapers.pages import GenericPagesBackend

    session = FakeSession({
        f"{WSP}/{API}/recruitingCEJobRequisitionDetails?": FakeResponse(200, {"items": [WSP_97345], "count": 1}),
        f"{WSP}/hcmUI/CandidateExperience/en/sites/CX_2001": FakeResponse(200, site_page("CX_2001")),
    })
    ctx = make_ctx(session)
    url = f"{WSP}/hcmUI/CandidateExperience/en/sites/CX_2001/job/97345"
    ctx.registry.register(detect(url)[1], "career_page")  # hot this run: its board fetch searches by keyword only
    ctx.pages.add(url, origin="career_page", geo_context=True)
    out = GenericPagesBackend(all_adapters()).run(ctx)
    assert [j.native_id for j in out.jobs] == ["oracle:emit.ca3:97345"] and out.details["api_fetched"] == 1
