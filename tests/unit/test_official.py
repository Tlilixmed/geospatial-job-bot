"""Official employment-service APIs: Bundesagentur (Germany) and France Travail, against mocked responses."""
from conftest import GIS_DESCRIPTION, NOW, FakeResponse, FakeSession, make_ctx, make_settings

from geojobbot.scrapers.official import BundesagenturBackend, FranceTravailBackend, mostly_german

BA = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc"
GERMAN = ("Wir suchen für unser Team eine GIS-Fachkraft. Sie arbeiten mit ArcGIS und QGIS und sind für die Pflege der Geodaten "
          "zuständig. Ihre Aufgaben sind die Analyse und die Aufbereitung von Daten sowie die Beratung der Fachbereiche. ") * 3


def ba_item(ref, title, **kw):
    return dict({"referenznummer": ref, "stellenangebotsTitel": title, "firma": "GeoFirma GmbH", "arbeitszeitVollzeit": True,
                 "stellenlokationen": [{"adresse": {"ort": "Berlin", "region": "BERLIN", "land": "DEUTSCHLAND"}}],
                 "datumErsteVeroeffentlichung": NOW.date().isoformat()}, **kw)


def test_bundesagentur_keeps_only_postings_the_candidate_can_read():
    assert mostly_german(GERMAN) and not mostly_german(GIS_DESCRIPTION) and not mostly_german("")
    listing = {"ergebnisliste": [ba_item("10000-1-S", "GIS Developer (m/f/d)", verguetungsangabe="JAHRESGEHALT", gehaltsspanneVon=60000,
                                         gehaltsspanneBis=75000, externeURL="https://geofirma.example/jobs/1"),
                                 ba_item("10000-2-S", "GIS-Spezialist (m/w/d)"), ba_item("10000-3-S", "Buchhalter (m/w/d)")]}
    details = {f"{BA}/v4/jobdetails/MTAwMDAtMS1T": FakeResponse(200, {"stellenangebotsBeschreibung": GIS_DESCRIPTION}),
               f"{BA}/v4/jobdetails/MTAwMDAtMi1T": FakeResponse(200, {"stellenangebotsBeschreibung": GERMAN})}
    session = FakeSession({f"{BA}/v6/jobs": FakeResponse(200, listing), **details})
    state = {"boards": {}, "cursors": {}, "page_cache": {}}
    out = BundesagenturBackend().run(make_ctx(session, state=state))
    assert out.status == "SUCCESS" and [j.title for j in out.jobs] == ["GIS Developer (m/f/d)"] and out.prefiltered_out == 1
    job = out.jobs[0]
    assert job.location_raw == "Berlin, Berlin, Germany" and job.salary == "EUR 60000–75000 yearly" and job.source_type == "government"
    assert job.apply_url == "https://geofirma.example/jobs/1" and job.source_job_id == "bundesagentur:10000-1-S"
    assert state["cursors"]["bundesagentur"]["german_refs"] == ["10000-2-S"]
    # the German posting's details are not fetched again
    before = len([c for c in session.calls if "jobdetails" in c[1]])
    out = BundesagenturBackend().run(make_ctx(session, state=state))
    after = len([c for c in session.calls if "jobdetails" in c[1]])
    assert out.details["german_language_skipped"] >= 1 and after - before <= 2
    assert all(c[1].split("?")[0] != f"{BA}/v4/jobdetails/MTAwMDAtMi1T" for c in session.calls[-(after - before):] if "jobdetails" in c[1])


def test_francetravail_needs_credentials_and_parses_offers():
    backend = FranceTravailBackend()
    assert backend.enabled(make_ctx(FakeSession()))[0] is False
    settings = make_settings(francetravail_client_id="id", francetravail_client_secret="secret-value")
    offer = {"id": "198XYZ", "intitule": "Géomaticien / Géomaticienne SIG", "description": GIS_DESCRIPTION,
             "dateCreation": NOW.isoformat(), "lieuTravail": {"libelle": "34 - MONTPELLIER"}, "entreprise": {"nom": "GeoSud"},
             "typeContratLibelle": "CDI", "salaire": {"libelle": "Annuel de 38000.0 Euros à 45000.0 Euros sur 12.0 mois"},
             "origineOffre": {"urlOrigine": "https://candidat.francetravail.fr/offres/recherche/detail/198XYZ"}}
    session = FakeSession({
        backend.token_url: FakeResponse(200, {"access_token": "tok", "expires_in": 1499}),
        backend.api: FakeResponse(200, {"resultats": [offer, dict(offer, id="2", intitule="Comptable")]}),
    })
    out = backend.run(make_ctx(session, settings=settings))
    assert out.status == "SUCCESS" and len(out.jobs) == 1 and out.prefiltered_out >= 1
    job = out.jobs[0]
    assert (job.company, job.location_raw, job.employment_type) == ("GeoSud", "Montpellier, France", "CDI")
    assert job.source_job_id == "francetravail:198XYZ" and "38000" in job.salary
    assert "secret-value" in settings.secrets()  # redacted from logs
    failing = FakeSession({backend.token_url: FakeResponse(401, {"error": "invalid_client"})})
    out = backend.run(make_ctx(failing, settings=settings))
    assert out.status == "FAILED" and out.error.startswith("token:") and "secret" not in out.error


# ------------------------------------------------------------------ Job Bank (Canada): search pages and RDFa postings
JOBBANK_RESULTS = """<div class="results-jobs">
<article id="article-50309689" class="action-buttons"><a href="/jobsearch/jobposting/50309689;jsessionid=59194C.jobsearch76?source=searchresults" class="resultJobItem">
 <h3 class="title"><span class="flag"><span class="new"> New </span></span> <span class="noctitle"> geomatics technician </span></h3>
 <ul class="list-unstyled"><li class="date">September 17, 2026 </li><li class="business">MTE Consultants Inc.</li>
 <li class="location"><span class="wb-inv">Location</span> Burlington (ON) </li>
 <li class="salary"><span class="fa fa-dollar"></span> Salary $62,000.00 to $75,000.00 annually</li></ul></a></article>
<article id="article-50302594"><a class="resultJobItem" href="/jobsearch/jobposting/50302594"><h3 class="title"><span class="noctitle">quantity surveyor - construction</span></h3>
 <ul><li class="date">September 16, 2026</li><li class="business">A.W. Hooker</li><li class="location">Location Oakville (ON)</li></ul></a></article>
<article id="article-50296705"><a class="resultJobItem" href="/x"><h3 class="title"><span class="noctitle">géomaticien/géomaticienne</span></h3>
 <ul><li class="date">16 septembre 2026</li><li class="business">HYDRO-QUEBEC</li><li class="location">Emplacement Montréal (QC)</li>
 <li class="salary">Salaire 52 852,00 $ par année</li></ul></a></article></div>"""
JOBBANK_POSTING = """<html><body><h1><span property="title">geomatics technician</span></h1>
<span property="datePosted"> Posted on September 17, 2026 </span><span property="hiringOrganization"> MTE Consultants Inc. </span>
<span property="addressLocality">Burlington</span><span property="addressRegion">ON</span><span property="employmentType">Full time</span>
<span property="baseSalary">$ 62,000 to $ 75,000 YEAR annually</span><div property="description"><p>%s</p></div></body></html>"""


def test_jobbank_is_read_from_its_search_pages_and_postings_from_their_rdfa():
    from conftest import GIS_DESCRIPTION
    from geojobbot.scrapers.generic import extract_jobs
    from geojobbot.scrapers.official import JobBankBackend

    rows = JobBankBackend.parse(JOBBANK_RESULTS, "https://www.jobbank.gc.ca")
    assert [(r.title, r.company, r.location_raw) for r in rows] == [
        ("geomatics technician", "MTE Consultants Inc.", "Burlington, ON, Canada"),
        ("quantity surveyor - construction", "A.W. Hooker", "Oakville, ON, Canada"),
        ("géomaticien/géomaticienne", "HYDRO-QUEBEC", "Montréal, QC, Canada")]
    first, french = rows[0], rows[2]
    assert first.url == "https://www.jobbank.gc.ca/jobsearch/jobposting/50309689" and first.source_job_id == "jobbank:50309689"  # no session id
    assert first.salary == "$62,000.00 to $75,000.00 annually" and first.posted_at.day == 17 and first.posted_at_reliable
    assert french.salary == "52 852,00 $ par année" and (french.posted_at.month, french.posted_at.day) == (9, 16)

    session = FakeSession({"https://www.jobbank.gc.ca/jobsearch/jobsearch": FakeResponse(200, JOBBANK_RESULTS),
                           "https://www.guichetemplois.gc.ca/jobsearch/jobsearch": FakeResponse(200, "<html></html>")})
    ctx = make_ctx(session)
    out = JobBankBackend().run(ctx)
    assert [j.title for j in out.jobs] == ["geomatics technician", "géomaticien/géomaticienne"] and out.prefiltered_out == 1  # no quantity surveyors
    assert out.details["pages_queued"] == 2 and out.status == "SUCCESS"
    # the posting page has no JSON-LD: its schema.org attributes (RDFa) carry the full text
    result = extract_jobs(JOBBANK_POSTING % GIS_DESCRIPTION, first.url, NOW)
    job = result.jobs[0]
    assert result.method == "rdfa" and job.title == "geomatics technician" and job.company == "MTE Consultants Inc."
    assert job.location_raw == "Burlington, ON" and job.posted_at.day == 17 and len(job.description) >= 300 and job.url == first.url
    assert extract_jobs('<span property="title">x</span><div property="description">too short</div>', first.url, NOW).jobs == []
