"""Official sponsor registers: parsing, name matching, the one-time score bonus and how it is shown."""
import io
import zipfile

from conftest import NOW, FakeResponse, FakeS3, FakeSession, make_ctx, make_settings
from test_commands import job, setup
from test_pipeline import FakeNotifier, StaticBackend, gis_raw

from geojobbot.core.pipeline import Pipeline
from geojobbot.insights.sponsors import (SponsorRegistry, annotate_record, core_name, parse_ca_rows, parse_nl_html,
                                         parse_uk_csv, read_xlsx_rows, sponsor_badge)
from geojobbot.notifications.intents import interpret
from geojobbot.notifications.telegram import format_digest
from geojobbot.storage.r2 import R2Store
from geojobbot.storage.state import StateManager

UK_CSV = ("Organisation Name,Town/City,County,Type & Rating,Route\n"
          "Esri (UK) Ltd,Aylesbury,,Worker (A rating),Skilled Worker\n"
          "Jane Doe T/A Doe Mapping,Leeds,,Worker (A rating),Skilled Worker\n"
          "Some Football Club,Leeds,,Temporary Worker (A rating),International Sportsperson\n").encode()
NL_HTML = ('<table><tr><th>Organisation</th><th>KvK</th></tr><tr><td>TomTom International B.V.</td><td>33006957</td></tr>'
           '<tr><td>""Geo"" Data &amp; Maps B.V.</td><td>12345678</td></tr></table>')


def xlsx(rows):
    """A minimal .xlsx with inline strings, as the parser must also cope with files without sharedStrings."""
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    body = "".join("<row>" + "".join(f'<c t="inlineStr"><is><t>{c}</t></is></c>' for c in r) + "</row>" for r in rows)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{body}</sheetData></worksheet>")
    return buf.getvalue()


CA_ROWS = [["Employers Who Were Issued a Positive LMIA, January to March 2026"],
           ["Province/Territory", "Program Stream", "Employer", "Address", "Occupation", "Incorporate Status",
            "Approved LMIAs", "Approved Positions"],
           ["Quebec", "High Wage", "Pomerleau inc.", "Montreal", "22213-Land survey technologists and technicians", "Corp", "2", "3"],
           ["Quebec", "High Wage", "Pomerleau inc.", "Montreal", "72010-Contractors", "Corp", "1", "39"],
           ["Ontario", "Low Wage", "Farm Fresh Ltd", "Guelph", "85100-Livestock labourers", "Corp", "1", "8"]]


def registry():
    names = {}
    parse_ca_rows(CA_ROWS, names)
    return SponsorRegistry({"checked_at": NOW.isoformat(),
                            "registers": {"uk": parse_uk_csv(UK_CSV), "ca": names, "nl": parse_nl_html(NL_HTML)}})


def test_parsers_keep_work_routes_trading_names_and_occupations():
    uk = parse_uk_csv(UK_CSV)
    assert set(uk) == {"esri uk", "jane doe", "doe mapping"}  # the sportsperson route is not a work-visa route
    assert read_xlsx_rows(xlsx(CA_ROWS))[2][2] == "Pomerleau inc."
    ca = {}
    assert parse_ca_rows(read_xlsx_rows(xlsx(CA_ROWS)), ca) == 3
    assert ca["pomerleau"] == {"n": "Pomerleau inc.", "p": 42, "o": ["land survey technologists"], "g": 1}
    assert set(parse_nl_html(NL_HTML)) == {"tomtom international", "geo data maps"}
    assert core_name("esri uk") == "esri" and core_name("tomtom international") == "tomtom"


def test_lookup_exact_then_core_variant_and_never_on_short_or_unknown_names():
    reg = registry()
    assert [(h["register"], h["match"]) for h in reg.lookup("Esri UK Limited")] == [("uk", "exact")]
    assert [(h["register"], h["match"], h["name"]) for h in reg.lookup("Esri")] == [("uk", "variant", "Esri (UK) Ltd")]
    assert reg.lookup("TomTom")[0]["label"] == "NL recognised sponsor"
    pomerleau = reg.lookup("Pomerleau")[0]
    assert pomerleau["positions"] == 42 and pomerleau["geo"] is True
    assert reg.lookup("Acme Widgets") == [] and reg.lookup("AB") == [] and reg.lookup(None) == []


def test_bonus_applies_once_in_the_jobs_own_country_and_never_lifts_hard_rejections():
    reg, settings = registry(), make_settings()
    rec = {"company": "Pomerleau", "country": "Canada", "score": 66, "tier": "possible", "score_breakdown": {"title": 40},
           "rejection_reasons": [], "why_matched": []}
    assert annotate_record(rec, reg, settings) == 6  # 4 + 2 for having hired land-survey staff
    assert rec["score"] == 72 and rec["tier"] == "high" and "Canada LMIA employer (Pomerleau inc.)" in rec["why_matched"]
    assert annotate_record(rec, reg, settings) == 0 and rec["score"] == 72  # not rescored since: no double bonus
    abroad = {"company": "Esri", "country": "United States", "score": 60, "tier": "possible", "score_breakdown": {}}
    assert annotate_record(abroad, reg, settings) == 0 and abroad["sponsor"] and "not this country" in sponsor_badge(abroad)
    blocked = {"company": "Esri", "country": "United Kingdom", "score": 80, "tier": "rejected", "score_breakdown": {},
               "rejection_reasons": ["WORK_AUTHORIZATION_REQUIRED"]}
    assert annotate_record(blocked, reg, settings) == 0 and blocked["tier"] == "rejected"
    lifted = {"company": "Esri UK", "country": "United Kingdom", "score": 52, "tier": "rejected", "score_breakdown": {},
              "rejection_reasons": ["LOW_SCORE"]}
    annotate_record(lifted, reg, settings)
    assert lifted["tier"] == "possible" and lifted["rejection_reasons"] == []


def test_refresh_downloads_all_three_and_survives_one_failure():
    page = '<a href="https://assets.publishing.service.gov.uk/media/x/register.csv">csv</a>'
    big_uk = UK_CSV + b"".join(f"Geodata Firm {i} Ltd,Leeds,,Worker (A rating),Skilled Worker\n".encode() for i in range(150))
    s = FakeSession({"https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers": FakeResponse(200, page),
                     "https://assets.publishing.service.gov.uk/media/x/register.csv": FakeResponse(200, big_uk, headers={"Content-Type": "text/csv"}),
                     "https://open.canada.ca/data/en/dataset/": FakeResponse(500, "down"),
                     "https://ind.nl/": FakeResponse(200, "<table></table>")})
    reg = SponsorRegistry({"registers": {"ca": {"old employer": {"n": "Old Employer"}}}})
    status = reg.refresh(make_ctx(s))
    assert status["uk"] > 150 and status["ca"].startswith("failed") and status["nl"].startswith("failed")
    assert "old employer" in reg.data["registers"]["ca"] and not reg.stale(NOW)  # previous data kept; checked today


def test_pipeline_annotates_and_digest_why_and_sponsors_view_show_it():
    s3 = FakeS3()
    n = FakeNotifier()
    raw = gis_raw(company="Pomerleau", location_raw="Montreal, QC, Canada", source_job_id="static:1")
    Pipeline(make_settings(), store=R2Store("b", client=s3), backends=[StaticBackend("feed", [raw])], notifier=n, now=NOW,
             http_session=FakeSession(), sleep=lambda x: None, sponsors=registry()).run()
    rec = StateManager(R2Store("b", client=s3)).load()["jobs"]["static:1"]
    assert rec["sponsor"][0]["register"] == "ca" and rec["score_breakdown"]["sponsor"] == 6
    assert "🍁 Canada LMIA employer (hired land survey technologists)" in n.sent[0]

    assert interpret("show me jobs from licensed sponsors") == ("sponsors", "") and interpret("who sponsors visas, top 5") == ("sponsors", "5")
    proc, replies, _, _ = setup([], [rec, job("a:2", "GIS Analyst", company="Nobody Ltd"),
                                     job("a:3", "LiDAR Analyst", company="OpenCo", why_matched=["Visa sponsorship offered"])])
    proc.run_text("jobs with visa sponsorship")
    view = replies.sent[-1]
    assert "Sponsorship evidence" in view and "2 current matches" in view and "Nobody" not in view
    assert view.index("LiDAR Analyst") < view.index("Pomerleau")  # a posting that says so outranks a register entry
    assert "42 foreign hires approved" in view and "hired land survey technologists" in view
    from geojobbot.utils.text import job_code
    proc.run_text(f"/why {job_code('static:1')}")
    assert "🍁 Canada LMIA employer: Pomerleau inc." in replies.sent[-1]
    assert "🛂" not in format_digest([job("z:1", "GIS Analyst")], now=NOW)[0][0]  # no badge without evidence


def test_irish_and_danish_registers_parse_and_match():
    from geojobbot.insights.sponsors import parse_dk_html, parse_ie_rows

    rows = [["Employer Name", "Permits Issued Jan", "Permits Issued Grand Total"], ["Murphy Geospatial Limited", "2", "5"],
            ["Murphy Geospatial Limited", "", "3"], ["Total", "9", "8"], [""]]
    ie: dict = {}
    assert parse_ie_rows(rows, ie) == 2 and ie == {"murphy geospatial": {"n": "Murphy Geospatial Limited", "p": 8}}
    html_text = ('<table><tr><td class="x"><p class="b">Company</p></td><td><p>CVR no.</p></td></tr>'
                 '<tr><td class="x">\n<p class="b">NIRAS A/S</p>\n</td>\n<td class="y">\n<p class="b">37295728</p>\n</td></tr>'
                 '<tr><td><p>&amp;TRADITION A/S</p></td><td><p>18169304</p></td></tr></table>')
    dk = parse_dk_html(html_text)
    assert [e["n"] for e in dk.values()] == ["NIRAS A/S", "&TRADITION A/S"]
    registry = SponsorRegistry({"registers": {"ie": ie, "dk": dk}})
    hit = registry.lookup("NIRAS")[0]
    assert (hit["register"], hit["country"], hit["match"]) == ("dk", "Denmark", "exact")  # "A/S" is a legal form, like "Ltd"
    assert registry.lookup("Murphy Geospatial")[0]["positions"] == 8
    assert registry.stale(NOW) is True  # registers added since the last download trigger a refresh


def test_a_similar_name_is_not_the_same_company():
    from geojobbot.insights import visa
    from geojobbot.insights.sponsors import NORMALISER, distinctive
    from geojobbot.utils.text import normalize_company

    # legal forms fall only at the end of a name; dotted initials collapse
    assert normalize_company("AG Survey Ltd") == "ag survey" and normalize_company("SA Water") == "sa water"
    assert normalize_company("SAS Institute Inc.") == "sas institute" and normalize_company("Fugro B.V.") == "fugro"
    assert normalize_company("Esri (U.K.) Limited") == normalize_company("Esri UK Ltd")
    assert not distinctive("mapping") and not distinctive("survey") and distinctive("fugro") and distinctive("land survey")

    uk = {normalize_company(n): {"n": n} for n in ("Mapping International Ltd", "Fugro GB Limited", "Water Research Centre Limited")}
    reg = SponsorRegistry({"normaliser": NORMALISER, "registers": {"uk": uk, "ca": {}, "nl": {}, "ie": {}, "dk": {}}})
    assert reg.lookup("Global Mapping Ltd") == [] and reg.lookup("SA Water") == []  # a shared trade word is not a match
    hit = reg.lookup("Fugro")[0]
    assert hit["match"] == "variant"
    rec = {"company": "Fugro", "country": "United Kingdom", "score": 60, "tier": "possible", "score_breakdown": {}, "rejection_reasons": []}
    assert annotate_record(rec, reg, make_settings()) == 0 and rec["sponsor"]  # shown for checking, but it earns no points
    assert visa._sponsor_check(rec, {"register": "uk"})["ok"] is None
    assert SponsorRegistry({"registers": {"uk": uk, "ca": {}, "nl": {}, "ie": {}, "dk": {}}}).stale(NOW)  # keyed by the older normaliser
