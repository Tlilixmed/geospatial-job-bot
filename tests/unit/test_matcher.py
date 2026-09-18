from conftest import GIS_DESCRIPTION

from geojobbot.matching import matcher as M
from geojobbot.matching.profile import MatchConfig

REMOTE = {"remote": True, "remote_scope": None}


def test_strong_gis_job_is_high_with_evidence():
    r = M.score_job("GIS Analyst", GIS_DESCRIPTION, REMOTE)
    assert r.tier == "high" and r.score >= 70
    assert r.breakdown["title"] == 40 and r.breakdown["tech"] == 25
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
    assert M.classify_title("أخصائي نظم المعلومات الجغرافية")[0] == "geo_title"
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
