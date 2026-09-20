from conftest import GIS_DESCRIPTION, NOW, FakeResponse, FakeSession, make_ctx

from geojobbot.scrapers.generic import extract_jobs
from geojobbot.scrapers.pages import CareerSitesBackend, GenericPagesBackend, parse_sitemap
from geojobbot.scrapers.ats.more_ats import all_adapters

JSONLD_GRAPH = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"X"},
{"@type":"JobPosting","title":"Photogrammetry Technician","description":"<p>%s</p>",
 "hiringOrganization":{"@type":"Organization","name":"SkyMap Ltd"},
 "jobLocation":[{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Leeds","addressCountry":{"name":"United Kingdom"}}}],
 "datePosted":"2026-09-15","validThrough":"2026-12-01","employmentType":["FULL_TIME"],
 "baseSalary":{"@type":"MonetaryAmount","currency":"GBP","value":{"minValue":30000,"maxValue":36000,"unitText":"YEAR"}},
 "identifier":{"@type":"PropertyValue","value":"REQ-77"},}]}
</script></head><body><h1>Photogrammetry Technician</h1></body></html>""" % GIS_DESCRIPTION


def test_jsonld_graph_with_trailing_comma():
    r = extract_jobs(JSONLD_GRAPH, "https://skymap.example/careers/photogrammetry-technician", NOW)
    assert r.method == "jsonld" and len(r.jobs) == 1
    job = r.jobs[0]
    assert job.company == "SkyMap Ltd" and job.location_raw == "Leeds, United Kingdom"
    assert "GBP 30000–36000 YEAR" == job.salary and job.employment_type == "FULL_TIME"
    assert job.source_job_id == "jsonld-skymap.example:REQ-77" and job.posted_at.day == 15  # a bare id means nothing off its site
    named = JSONLD_GRAPH.replace('"value":"REQ-77"', '"name":"SkyMap Ltd","value":"REQ-78"')
    assert extract_jobs(named, "https://skymap.example/careers/x", NOW).jobs[0].source_job_id == "jsonld-skymap.example:REQ-78"
    only_name = JSONLD_GRAPH.replace('"value":"REQ-77"', '"name":"SkyMap Ltd"')  # the employer's name is not a job id
    assert extract_jobs(only_name, "https://skymap.example/careers/x", NOW).jobs[0].source_job_id is None


def test_jsonld_excerpt_is_topped_up_from_page_body():
    page = ('<html><script type="application/ld+json">{"@type":"JobPosting","title":"Geomatics Technician",'
            '"description":"Short excerpt only.","hiringOrganization":{"name":"Kodiak"}}</script>'
            '<h1>Geomatics Technician</h1><div class="job-description"><p>%s</p></div></html>' % GIS_DESCRIPTION)
    job = extract_jobs(page, "https://gogeomatics.ca/job/geomatics-technician/", NOW).jobs[0]
    assert job.extraction_method == "jsonld" and job.company == "Kodiak" and "ArcGIS Pro" in job.description
    # a listing page with several postings is left alone: the body text cannot be attributed to one of them
    listing = ('<html><script type="application/ld+json">[{"@type":"JobPosting","title":"A","description":"a"},'
               '{"@type":"JobPosting","title":"B","description":"b"}]</script><main><p>%s</p></main></html>' % GIS_DESCRIPTION)
    assert [j.description for j in extract_jobs(listing, "https://x.example/jobs/", NOW).jobs] == ["a", "b"]


def test_jsonld_remote_telecommute_scope():
    html = """<script type="application/ld+json">{"@type":"JobPosting","title":"GIS Developer",
      "jobLocationType":"TELECOMMUTE","applicantLocationRequirements":{"@type":"Country","name":"Canada"},
      "description":"x"}</script>"""
    job = extract_jobs(html, "https://a.example/jobs/1", NOW).jobs[0]
    assert job.remote_flag is True and job.location_raw.startswith("Remote - Canada")


def test_jsonld_missing_fields_and_expired():
    html = """<script type="application/ld+json">[{"@type":"JobPosting","title":"LiDAR Analyst"},
      {"@type":"JobPosting","title":"Old job","validThrough":"2020-01-01"},{"@type":"JobPosting"}]</script>"""
    r = extract_jobs(html, "https://a.example/jobs/2", NOW)
    assert [j.title for j in r.jobs] == ["LiDAR Analyst"] and r.expired == 1
    assert r.jobs[0].company is None and r.jobs[0].description == ""


def test_invalid_jsonld_falls_through_without_crash():
    html = '<script type="application/ld+json">{not json</script><h1>Nothing</h1>'
    r = extract_jobs(html, "https://a.example/about", NOW)
    assert r.jobs == [] and "invalid JSON-LD block" in r.errors


def test_embedded_next_data():
    html = ('<h1>Cadastral Specialist</h1><script id="__NEXT_DATA__" type="application/json">'
            '{"props":{"pageProps":{"job":{"title":"Cadastral Specialist","description":"%s","location":{"addressLocality":"Lima","addressCountry":"Peru"}}}}}'
            '</script>') % GIS_DESCRIPTION
    r = extract_jobs(html, "https://b.example/jobs/cadastral", NOW)
    assert r.method == "embedded_json" and r.jobs[0].location_raw == "Lima, Peru"


def test_html_fallback_requires_job_signals():
    body = "<main><h2>Responsibilities</h2><p>%s</p><a>Apply now</a></main>" % GIS_DESCRIPTION
    r = extract_jobs(f"<html><h1>GIS Technician</h1>{body}</html>", "https://c.example/careers/gis-technician", NOW)
    assert r.method == "html" and r.jobs[0].extraction_method == "html"
    assert extract_jobs(f"<html><h1>Blog</h1>{body}</html>", "https://c.example/blog/post", NOW).jobs == []


def test_malformed_html_never_raises():
    for junk in ["", "<<<>>>", "<html><script type='application/ld+json'>", b"\xff\xfe<h1>x", "<a href='javascript:x'>"]:
        extract_jobs(junk, "https://d.example/jobs/x", NOW)


def test_parse_sitemap_gzip_and_index():
    import gzip
    xml = b"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://e.example/jobs/gis-analyst</loc><lastmod>2026-09-10</lastmod></url>
      <url><loc><![CDATA[https://e.example/jobs/chef]]></loc></url></urlset>"""
    urls, children = parse_sitemap(gzip.compress(xml))
    assert urls[0] == ("https://e.example/jobs/gis-analyst", "2026-09-10") and urls[1][0].endswith("chef")
    _, children = parse_sitemap(b"<sitemapindex><sitemap><loc>https://e.example/s1.xml</loc></sitemap></sitemapindex>")
    assert children == ["https://e.example/s1.xml"]


def test_career_site_discovery_then_generic_extraction():
    career = ('<html><a href="/jobs/gis-analyst">GIS Analyst</a><a href="/jobs/chef">Chef</a>'
              '<iframe src="https://boards.greenhouse.io/embed/job_board?for=geoco"></iframe></html>')
    sitemap = "<urlset><url><loc>https://geo.example/jobs/lidar-technician</loc></url></urlset>"
    s = FakeSession({
        "https://geo.example/careers": FakeResponse(200, career),
        "https://geo.example/sitemap-jobs.xml": FakeResponse(200, sitemap, headers={"Content-Type": "application/xml"}),
        "https://geo.example/jobs/gis-analyst": FakeResponse(200, JSONLD_GRAPH.replace("Photogrammetry Technician", "GIS Analyst")),
        "https://geo.example/jobs/lidar-technician": FakeResponse(503),
        "https://geo.example/jobs/chef": FakeResponse(200, "<h1>Chef</h1>"),
    })
    ctx = make_ctx(s)
    disc = CareerSitesBackend([{"name": "GeoCo", "url": "https://geo.example/careers",
                                "sitemap": "https://geo.example/sitemap-jobs.xml"}]).run(ctx)
    assert disc.details["boards_new"] == 1 and "greenhouse:geoco" in ctx.state["boards"]
    assert disc.details["links_queued"] >= 1 and disc.details["sitemap_urls_queued"] == 1
    out = GenericPagesBackend(all_adapters()).run(ctx)
    titles = [j.title for j in out.jobs]
    assert "GIS Analyst" in titles and out.status == "PARTIAL"  # lidar page 503 isolated
    assert ctx.state["page_cache"]
    # a later run (new context, same persisted state) inside the refetch window skips cached pages
    ctx2 = make_ctx(s, state=ctx.state)
    assert ctx2.pages.add("https://geo.example/jobs/gis-analyst", origin="sitemap")
    again = GenericPagesBackend(all_adapters()).run(ctx2)
    assert again.details.get("cached_skip") == 1


def test_job_board_search_page_keeps_feed_authority_and_never_names_board_as_employer():
    listing = ('<html><a href="/offres-emploi/123/ingenieur-sig/">Ingénieur SIG</a>'
               '<a href="/offres-emploi/124/comptable/">Comptable</a><a href="/a-propos/">À propos</a></html>')
    posting = '<script type="application/ld+json">{"@type":"JobPosting","title":"Ingénieur SIG","description":"%s"}</script>' % GIS_DESCRIPTION
    s = FakeSession({"https://board.example/offres-emploi/?keywords=SIG": FakeResponse(200, listing),
                     "https://board.example/offres-emploi/123/ingenieur-sig/": FakeResponse(200, posting),
                     "https://board.example/offres-emploi/124/comptable/": FakeResponse(200, "<h1>Comptable</h1>")})
    ctx = make_ctx(s)
    site = {"name": "Board", "url": "https://board.example/offres-emploi/?keywords=SIG", "source_type": "feed",
            "geospatial": False, "use_robots_sitemaps": False, "job_url_pattern": r"/offres-emploi/\d+/"}
    assert CareerSitesBackend([site]).run(ctx).details["links_queued"] == 2
    out = GenericPagesBackend(all_adapters()).run(ctx)
    assert [j.title for j in out.jobs] == ["Ingénieur SIG"]
    job = out.jobs[0]
    assert job.source_type == "feed" and job.company is None and job.geo_context is False
