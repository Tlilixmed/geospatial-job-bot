import pytest
from conftest import GIS_DESCRIPTION

from geojobbot.matching import matcher as M
from geojobbot.matching.profile import MatchConfig

REMOTE = {"remote": True, "remote_scope": None}


def test_strong_gis_job_is_high_with_evidence():
    r = M.score_job("GIS Analyst", GIS_DESCRIPTION, REMOTE)
    assert r.tier == "high" and r.score >= 70
    assert r.breakdown["title"] == 40 and r.breakdown["tech"] == 23  # PostGIS is asked for and not on the candidate's list
    names = r.skill_names
    assert "ArcGIS Pro" in names and "ArcPy" in names and "FME" in names and "PostGIS" in names
    assert "ArcGIS" not in names  # "ArcGIS Pro" must not double count as "ArcGIS"
    qual = {h.canonical: h.qualifier for h in r.skills}
    assert qual["ArcGIS Pro"] == "required"
    assert qual["Python"] == "preferred"
    assert "ArcGIS Pro required" in r.why_matched


def test_explanations_only_contain_terms_present_in_text():
    text = "GIS work using QGIS. Georeferencing of historic maps. " * 10
    r = M.score_job("GIS Technician", text, {})
    joined = " ".join(r.why_matched)
    assert "QGIS" in joined and "Georeferencing" in joined
    for absent in ("ArcGIS", "Python", "FME", "PostGIS", "LiDAR", "Remote"):
        assert absent not in joined


def test_inverted_title():
    kind, points, _, _ = M.classify_title("Analyst, GIS")
    assert kind in ("direct", "geo_title") and points >= 34


def test_logistics_does_not_match_gis():
    r = M.score_job("Logistics Coordinator", "Manage logistics and registered shipments " * 20, {})
    assert r.tier == "rejected" and "GIS" not in r.domain_names


def test_negative_title_rejected():
    r = M.score_job("Director of GIS", GIS_DESCRIPTION, REMOTE)
    assert r.tier == "rejected" and M.NEGATIVE_TITLE in r.rejection_reasons


def test_architect_override_for_geo_title():
    assert M.classify_title("GIS Architect")[3] is None
    assert M.classify_title("Landscape Architect")[3] == "Architect"


def test_engineer_not_globally_excluded():
    r = M.score_job("Geospatial Engineer", GIS_DESCRIPTION, REMOTE)
    assert r.tier == "high"
    assert M.classify_title("Electrical Engineer")[3] == "Electrical Engineer"


def test_generic_title_needs_two_geo_signals():
    weak = M.score_job("Data Analyst", "Work with SQL and Python dashboards. GIS exposure. " * 10, REMOTE)
    assert M.INSUFFICIENT_GEOSPATIAL_SIGNALS in weak.rejection_reasons
    strong = M.score_job("Data Analyst", GIS_DESCRIPTION, REMOTE)
    assert M.INSUFFICIENT_GEOSPATIAL_SIGNALS not in strong.rejection_reasons
    assert strong.tier in ("high", "possible")


def test_gis_and_geospatial_same_family():
    r = M.score_job("Data Analyst", "GIS and geospatial work. " * 30, REMOTE)
    assert r.geo_families == ["gis"]
    assert M.INSUFFICIENT_GEOSPATIAL_SIGNALS in r.rejection_reasons


def test_title_only_floor():
    r = M.score_job("LiDAR Technician", "", {})
    assert r.tier == "possible" and r.title_only
    r2 = M.score_job("Sales Manager", "", {})
    assert r2.tier == "rejected"


def test_no_relevant_title_rejected_even_with_skills():
    r = M.score_job("Barista", GIS_DESCRIPTION, REMOTE)
    assert r.tier == "rejected" and M.NO_RELEVANT_TITLE in r.rejection_reasons


def test_arcade_requires_arcgis():
    assert "Arcade" not in M.score_job("GIS Technician", "Retro arcade game testing " * 20, {}).skill_names
    assert "Arcade" in M.score_job("GIS Technician", "Write Arcade expressions in ArcGIS Online " * 5, {}).skill_names


def test_mapping_is_qualified():
    r = M.score_job("GIS Technician", "Data mapping of business processes " * 20, {})
    assert "Mapping" not in r.domain_names


def test_location_strict_mismatch():
    cfg = MatchConfig(preferred_locations=["Canada"], strict_location=True)
    r = M.score_job("GIS Analyst", GIS_DESCRIPTION, {"city": "Berlin", "country": "Germany", "raw": "Berlin, Germany"}, cfg)
    assert M.LOCATION_MISMATCH in r.rejection_reasons and r.tier == "rejected"
    ok = M.score_job("GIS Analyst", GIS_DESCRIPTION, {"city": "Calgary", "country": "Canada", "raw": "Calgary"}, cfg)
    assert ok.tier == "high" and ok.breakdown["location"] == 5


def test_prefilter():
    assert M.title_prefilter("Senior GIS Specialist", False)
    assert not M.title_prefilter("Account Executive", True)
    assert not M.title_prefilter("Software Engineer", False)
    assert M.title_prefilter("Software Engineer", True)
    assert not M.title_prefilter("Director, Geospatial", True)


def test_deterministic():
    a = M.score_job("GIS Analyst", GIS_DESCRIPTION, REMOTE).to_dict()
    b = M.score_job("GIS Analyst", GIS_DESCRIPTION, REMOTE).to_dict()
    assert a == b


def test_internships_excluded_by_default_and_switchable():
    from conftest import make_settings
    on = make_settings()
    for title in ("GIS Intern", "Geospatial Analyst Co-op", "Stagiaire SIG", "Stage PFE Géomatique", "GIS Working Student",
                  "Alternance Cartographe"):
        assert not M.title_prefilter(title, True, tuple(on.negative_titles())), title
        assert M.score_job(title, GIS_DESCRIPTION, REMOTE, on.match_config()).tier == "rejected"
    for title in ("GIS Analyst, International Programs", "Internal GIS Specialist"):
        assert M.title_prefilter(title, True, tuple(on.negative_titles())), title
    off = make_settings(exclude_internships=False)
    assert M.title_prefilter("GIS Intern", True, tuple(off.negative_titles()))


def test_work_authorization_exclusion_and_exemptions():
    us = GIS_DESCRIPTION + " Applicants must be authorized to work in the United States; we are unable to sponsor visas."
    r = M.score_job("GIS Analyst", us, {"city": "Denver", "country": "United States", "raw": "Denver, CO"})
    assert r.tier == "rejected" and M.WORK_AUTHORIZATION_REQUIRED in r.rejection_reasons
    # the same wording on a job in the candidate's own country is irrelevant
    tn = M.score_job("GIS Analyst", us, {"city": "Tunis", "country": "Tunisia", "raw": "Tunis"})
    assert M.WORK_AUTHORIZATION_REQUIRED not in tn.rejection_reasons and tn.tier == "high"
    # an explicit sponsorship offer overrides authorisation boilerplate and is shown as evidence
    ok = M.score_job("GIS Analyst", GIS_DESCRIPTION + " Security clearance not needed. Visa sponsorship is available.", REMOTE)
    assert M.WORK_AUTHORIZATION_REQUIRED not in ok.rejection_reasons and M.SPONSORSHIP_EVIDENCE in ok.why_matched
    clearance = M.score_job("GIS Analyst", GIS_DESCRIPTION + " Active TS/SCI clearance required.", REMOTE)
    assert M.WORK_AUTHORIZATION_REQUIRED in clearance.rejection_reasons
    fr = M.score_job("Ingénieur SIG", GIS_DESCRIPTION + " Citoyenneté canadienne ou résidence permanente exigée.",
                     {"country": "Canada", "raw": "Montréal"})
    assert M.WORK_AUTHORIZATION_REQUIRED in fr.rejection_reasons
    anywhere = M.score_job("GIS Analyst", GIS_DESCRIPTION + " Visa sponsorship is not available. Applicants must be "
                           "authorized to work in their country of residence.", REMOTE)
    assert M.WORK_AUTHORIZATION_REQUIRED not in anywhere.rejection_reasons  # remote from home is fine
    off = M.score_job("GIS Analyst", us, REMOTE, MatchConfig(exclude_work_auth_required=False))
    assert M.WORK_AUTHORIZATION_REQUIRED not in off.rejection_reasons
    assert M.score_job("GIS Analyst", GIS_DESCRIPTION, REMOTE).rejection_reasons == []


# ------------------------------------------------------------------ French postings
FR_DESCRIPTION = (
    "Nous recrutons un Ingénieur SIG pour la cartographie des réseaux. Missions : analyse spatiale, numérisation, "
    "géoréférencement de plans, production de cartes thématiques et contrôle qualité des données géographiques. "
    "Maîtrise de QGIS et ArcGIS Pro exigée. Connaissance de PostGIS et Python souhaitée. Levés topographiques au GPS, "
    "traitement de nuages de points LiDAR et télédétection. Systèmes de coordonnées et projections cartographiques."
)


def test_french_title_and_description_score_high():
    r = M.score_job("Ingénieur SIG (H/F)", FR_DESCRIPTION, {"city": "Tunis", "country": "Tunisia", "raw": "Tunis, Tunisie"})
    assert r.tier == "high" and r.breakdown["title"] == 40
    qual = {h.canonical: h.qualifier for h in r.skills}
    assert qual["QGIS"] == "required" and qual["ArcGIS Pro"] == "required" and qual["PostGIS"] == "preferred"
    for domain in ("Remote sensing", "Point clouds", "LiDAR", "Utility mapping", "Spatial analysis"):
        assert domain in r.domain_names
    resp = [h.canonical for h in r.responsibilities]
    assert "Georeferencing" in resp and "Coordinate systems" in resp and "QA/QC" in resp


def test_french_titles_classify_like_english():
    assert M.classify_title("Géomaticien / Géomaticienne")[0] == "direct"
    assert M.classify_title("Dessinateur Géomètre Topographe")[0] == "direct"
    assert M.classify_title("Chargé d'études en géomatique")[0] in ("direct", "geo_title")
    assert M.classify_title("Technicien en télédétection")[0] == "geo_title"
    assert M.classify_title("map draftsman/woman")[0] == "geo_title"  # Job Bank occupation title
    assert M.classify_title("Architecte SIG")[3] is None
    assert M.classify_title("Directeur Commercial")[3] is not None
    assert M.title_prefilter("Ingénieur SIG", False)
    assert not M.title_prefilter("Ingénieur Mécanique", True)
    assert not M.title_prefilter("Ingénieur", False)  # generic French title needs geospatial context


def test_arabic_titles_and_sponsorship_location_bonus():
    assert M.classify_title("مهندس نظم معلومات جغرافية")[0] == "direct"
    assert M.classify_title("أخصائي نظم المعلومات الجغرافية")[0] == "direct"  # the article does not hide the role
    assert M.classify_title("مصمم مساحات داخلية")[0] == "none"  # "مساحات" (spaces) is not "مساح" (surveyor)
    assert [M.classify_title(t)[0] for t in ("المساح", "مساحون", "مساح أراضي", "فني المساحة")] == ["direct"] * 4
    assert M.classify_title("فني مساحة")[0] == "direct"
    assert M.classify_title("مهندس ميكانيكي")[0] == "none"
    r = M.score_job("مهندس نظم معلومات جغرافية", "خبرة في نظم المعلومات الجغرافية والاستشعار عن بعد وإنتاج الخرائط. ArcGIS Pro.", {})
    assert {"GIS", "Remote sensing", "Cartography"} <= set(r.domain_names)
    cfg = MatchConfig(preferred_locations=["Canada"])
    plain = M.score_job("GIS Analyst", GIS_DESCRIPTION, {"city": "Dubai", "country": "United Arab Emirates", "raw": "Dubai"}, cfg)
    sponsored = M.score_job("GIS Analyst", GIS_DESCRIPTION + " Visa sponsorship is available.",
                            {"city": "Dubai", "country": "United Arab Emirates", "raw": "Dubai"}, cfg)
    assert plain.breakdown["location"] == 0 and sponsored.breakdown["location"] == 5


def test_surveyor_and_mapping_titles_need_geomatics_evidence():
    # explicit non-geomatics "surveyor" roles are negatives
    for title in ("Quantity Surveyor", "Senior Building Surveyor", "Marine Surveyor", "Survey Researcher", "Process Mapping Analyst"):
        assert not M.title_prefilter(title, True), title
    costs = ("Prepare bills of quantities, cost plans and valuations for residential developments. Manage subcontractor "
             "accounts, tender documentation and final accounts, reporting commercial performance to the director. " * 3)
    assert M.weak_only_title("Surveyor") and M.weak_only_title("Mapping Specialist") and not M.weak_only_title("GIS Surveyor")
    rejected = M.score_job("Surveyor", costs, {})
    assert rejected.tier == "rejected" and M.INSUFFICIENT_GEOSPATIAL_SIGNALS in rejected.rejection_reasons
    land = ("Carry out topographic survey and boundary survey work with GNSS and total station, process data in AutoCAD "
            "Civil 3D and ArcGIS Pro, register LiDAR point clouds and prepare cadastral plans. Field work with a survey crew. " * 3)
    accepted = M.score_job("Surveyor", land, {})
    assert M.INSUFFICIENT_GEOSPATIAL_SIGNALS not in accepted.rejection_reasons and accepted.tier in ("high", "possible")
    # with no description, a bare weak title gets no benefit of the doubt, an explicit role still does
    assert M.score_job("Surveyor", "", {}).tier == "rejected"
    assert M.score_job("Survey Technician", "", {}).tier == "possible"
    assert M.score_job("Topographe", "", {}).tier == "possible"
    assert M.score_job("GIS Analyst", "", {}).tier == "possible"  # strong titles unchanged


def test_jobs_reserved_for_nationals_are_rejected_and_iso_prefixed_locations_parse():
    from conftest import make_settings
    from geojobbot.utils.location import parse_location

    score_job = M.score_job
    loc = parse_location("SA - Riyadh, Qiddiya")
    assert (loc.city, loc.country) == ("Riyadh", "Saudi Arabia")
    winnipeg = parse_location("CA - MB, Winnipeg")
    assert (winnipeg.city, winnipeg.region, winnipeg.country) == ("Winnipeg", "Manitoba", "Canada")
    reserved = score_job("GIS Specialist (Saudi National)", GIS_DESCRIPTION, loc.to_dict(), make_settings().match_config())
    assert reserved.tier == "rejected" and "WORK_AUTHORIZATION_REQUIRED" in reserved.rejection_reasons
    emiratisation = score_job("GIS Analyst", GIS_DESCRIPTION + " This role is part of our Emiratisation programme, UAE nationals only.",
                              parse_location("AE - Dubai").to_dict(), make_settings().match_config())
    assert emiratisation.tier == "rejected"
    open_role = score_job("GIS Specialist", GIS_DESCRIPTION, loc.to_dict(), make_settings().match_config())
    assert open_role.tier != "rejected"


def test_negative_words_that_are_also_ordinary_words_or_real_roles():
    from geojobbot.matching.profile import INTERNSHIP_TITLES
    extra = tuple(INTERNSHIP_TITLES)
    fine = ["Survey Party Chief", "Survey Crew Chief", "Chief Surveyor", "GIS Executive", "Commercial Drone Pilot / Photogrammetry",
            "Commercial GIS Analyst", "GIS Developer (Early Stage Startup)", "GIS Analyst - Student Services"]
    still_negative = ["Chief Geospatial Officer", "Sales Executive GIS", "Executive Assistant", "Ingénieur Commercial SIG",
                      "Technico-commercial SIG", "Stage SIG", "Stage - Géomatique", "Student GIS Assistant", "Commercial Surveyor"]
    assert [t for t in fine if M.classify_title(t, extra)[3]] == []
    assert [t for t in still_negative if not M.classify_title(t, extra)[3]] == []


def test_the_skills_part_counts_the_candidates_skills_not_every_skill_a_posting_names():
    from geojobbot.matching.profile import MatchConfig

    many = ("We use ArcGIS Pro, QGIS, Python, FME, AutoCAD and MicroStation daily. Required: PostGIS, SQL, GDAL/OGR, "
            "GeoPandas, Google Earth Engine, Leaflet and STAC catalogs with Cloud Optimized GeoTIFFs. ") * 3
    lacking = M.score_job("GIS Analyst", many, REMOTE)
    owning = M.score_job("GIS Analyst", many, REMOTE, MatchConfig(my_skills=[h.canonical for h in lacking.skills]))
    assert owning.breakdown["tech"] == 25 and lacking.breakdown["tech"] < 15  # 6 of 13 is not a full match
    assert owning.score - lacking.score == owning.breakdown["tech"] - lacking.breakdown["tech"]
    # a missing requirement weighs more than a missing "nice to have"
    required = M.score_job("GIS Analyst", ("Required: ArcGIS Pro and PostGIS. " + GIS_DESCRIPTION[:400]) * 2, REMOTE)
    preferred = M.score_job("GIS Analyst", ("ArcGIS Pro required; PostGIS is a plus. " + GIS_DESCRIPTION[:400]) * 2, REMOTE)
    assert M.skill_points(required.skills, [], 25) <= M.skill_points(preferred.skills, [], 25)
    # nothing of yours in it: no skills points at all
    assert M.score_job("GIS Analyst", ("Daily work in PostGIS, SQL and GeoPandas. " * 12), REMOTE).breakdown["tech"] == 0
    # ArcGIS Pro covers ArcMap
    assert "arcmap" in M.owned_skills(["ArcGIS Pro"]) and "arcmap" not in M.owned_skills(["QGIS"])


def test_skills_set_with_slash_skills_reach_the_scorer():
    from geojobbot.core.prefs import apply_prefs, default_prefs
    from geojobbot.config import Settings

    settings = Settings()
    prefs = default_prefs()
    prefs["my_skills"] = ["QGIS", "PostGIS", "SQL"]
    apply_prefs(settings, prefs)
    assert settings.match_config().my_skills == ["QGIS", "PostGIS", "SQL"]
    assert Settings().match_config().my_skills == []  # nothing set: the built-in list from the CV


YEARS = [  # (sentence from a real posting, years it asks for)
    ("Our team is solid and experienced, with an average of 15 years of professional expertise. "
     "Minimum of 2 years of professional GIS experience (excluding coursework).", 2),
    ("Minimum 10+ years of experience in GIS, geospatial information management", 10),
    ("Years of experience: A minimum of 10 years of experience in geospatial data integration", 10),
    ("8 to 12 years of experience in delivering GIS products", 8),
    ("The successful candidate will have at least 1 year of mapping technician work experience", 1),
    ("3–5 years of experience with Esri GIS applications", 3),
    ("Over the past 75 years, our efforts to improve forest health. • 3+ years of experience in GIS", 3),
    ("15+ years of GIS experience with leadership of complex workstreams", 15),
    ("5 years' plus experience working in ESRI platforms", 5),
    ("CDI - Bac+2/Bac+3 - 2 à 5 ans d'expérience", 2),
    ("Expérience : 3 ans d'expérience minimum sur un poste similaire", 3),
    ("OR high school diploma plus 2 years of applicable work experience in a GIS-related field", 2),
    # not requirements: the employer's history, a contract length, a range without the word experience
    ("Contrat de catégorie B par CDD de 3 ans maximum, avec possibilité de renouvellement", None),
    ("Forte du travail mené depuis 13 ans sur le territoire", None),
    ("Junior to Intermediate (2-6 years preferred)", None),
    ("Staff stay with the company for more than 10 years in average; a great experience.", None),
]


@pytest.mark.parametrize("sentence,years", YEARS)
def test_years_of_experience_asked(sentence, years):
    assert M.required_years(sentence) == years


def test_far_more_years_than_the_candidate_has_marks_down_or_rejects():
    from geojobbot.matching.profile import MatchConfig

    def with_years(n):
        return M.score_job("GIS Analyst", GIS_DESCRIPTION + f"\n{n}+ years of GIS experience required.", REMOTE)

    plain = M.score_job("GIS Analyst", GIS_DESCRIPTION, REMOTE)
    assert with_years(3).score == plain.score and "experience" not in with_years(3).breakdown
    assert with_years(5).breakdown["experience"] == -5 and with_years(6).breakdown["experience"] == -10
    senior = with_years(10)
    assert senior.tier == "rejected" and "EXPERIENCE_GAP" in senior.rejection_reasons
    assert M.score_job("GIS Analyst", GIS_DESCRIPTION + "\n10+ years of GIS experience required.", REMOTE,
                       MatchConfig(my_years=9)).tier == "high"  # MY_EXPERIENCE_YEARS moves the bar


def test_point_cloud_registration_is_the_candidates_own_work():
    text = ("Register laser scans in Cyclone, perform point cloud registration and deliver scan-to-BIM models. "
            "Recalage des nuages de points et modélisation 3D à partir de nuages de points.")
    names = [h.canonical for h in M.extract_terms(text, "resp")]
    assert "Point cloud registration & 3D modelling" in names
    assert "Point cloud registration & 3D modelling" not in [h.canonical for h in M.extract_terms(
        "Register for our webinar on 3D printing; cloud registration of devices.", "resp")]
