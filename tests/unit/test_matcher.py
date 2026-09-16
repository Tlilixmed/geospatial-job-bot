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
