"""The candidate profile: everything the matcher knows about relevance.

Edit this file to add roles, skills, domains or negative keywords. Each term has:
  canonical  - the name shown in alerts
  weight     - points contributed (category totals are capped)
  patterns   - case-insensitive regexes; all variants normalise to the canonical name
  family     - geospatial signal family (None = not a geospatial signal on its own).
               Distinct families are counted when a generic title needs geospatial evidence,
               so "GIS" + "geospatial" (same family) count once.
  requires   - optional canonical name that must also be present (context guard)
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Term:
    canonical: str
    weight: int
    patterns: tuple[str, ...]
    family: str | None = None
    requires: str | None = None


DIRECT_ROLES = [
    "GIS Analyst", "GIS Specialist", "GIS Technician", "GIS Officer", "GIS Coordinator", "GIS Developer",
    "GIS Engineer", "Geospatial Analyst", "Geospatial Specialist", "Geospatial Engineer", "Geospatial Developer",
    "Spatial Analyst", "Spatial Data Analyst", "Spatial Data Specialist", "Spatial Data Engineer",
    "Geomatics Engineer", "Geomatics Specialist", "Cartographer", "Cartographic Specialist",
    "Mapping Technician", "Mapping Specialist", "Survey Technician", "Survey Mapping Specialist",
    "Cadastral Specialist", "Land Information Specialist", "LiDAR Specialist", "LiDAR Analyst",
    "LiDAR Technician", "Point Cloud Specialist", "Photogrammetry Specialist", "Photogrammetry Technician",
    "Remote Sensing Specialist", "UAV Mapping Specialist", "Drone Mapping Specialist",
]

ADJACENT_ROLES = [
    "Geospatial Data Technician", "GIS Data Technician", "GIS Data Specialist", "Spatial Data Technician",
    "Spatial Information Specialist", "Land Information Technician", "Land Records GIS", "Utility GIS",
    "Utility Mapping Specialist", "Utility Mapping Technician", "Telecom GIS", "Fiber GIS",
    "Network Mapping Specialist", "Network GIS Analyst", "Electric Distribution GIS", "CAD/GIS Technician",
    "CAD/GIS Specialist", "GIS/CAD Technician", "Survey CAD Technician", "Geospatial QA/QC", "GIS QA Analyst",
    "GIS Quality Assurance", "Geospatial Data Quality Specialist", "GIS Data Processor",
    "Geospatial Data Processor", "Mining GIS", "Mining GIS Analyst", "Mineral Tenure GIS",
    "Land Tenure Specialist",
]

# Title terms that are geospatial on their own (used for inverted/variant titles such as
# "Analyst, GIS" or "Senior Geospatial Data Scientist").
GEO_TITLE_TERMS = [
    r"\bgis\b", r"\bgeo-?spatial\b", r"\bgeomatic\w*", r"\blidar\b", r"\bcartograph\w*", r"\bphotogrammetr\w*",
    r"\bremote\s+sensing\b", r"\bpoint[\s-]?cloud\b", r"\bcadastr\w*", r"\bsurvey(?:or|ing)\b",
    r"\bsurvey\s+(?:technician|tech|cad|crew|party|assistant)\b", r"\bland\s+(?:information|tenure|records)\b",
    r"\bmineral\s+tenure\b", r"\bspatial\s+(?:data|analyst|analysis|information|scientist)\b",
    r"\b(?:mapping|mapper)\b", r"\bgeographic information\b", r"\bgeodes\w*", r"\bhydrograph\w*",
    r"\bearth observation\b", r"\bgeodata\b",
]

ROLE_NOUNS = (
    r"analyst|specialist|technician|tech|officer|coordinator|developer|engineer|scientist|manager|lead|"
    r"processor|editor|consultant|intern|assistant|administrator|drafter|designer|operator|mapper|associate|"
    r"programmer|architect|professional|surveyor|expert"
)

# Generic titles need at least MIN_GEO_SIGNALS distinct geospatial families in the text.
GENERIC_TITLE_PATTERNS = [
    r"\bdata\s+analyst\b", r"\bdata\s+technician\b", r"\banalyst\b", r"\btechnician\b", r"\bspecialist\b",
    r"\bengineer\b", r"\bdeveloper\b", r"\bscientist\b", r"\bcoordinator\b", r"\bdrafter\b",
    r"\bdraftsperson\b", r"\bdata\s+engineer\b", r"\bconsultant\b",
]
MIN_GEO_SIGNALS = 2

NEGATIVE_TITLES = [
    "Senior Director", "Director", "VP", "Vice President", "Executive", "Branch Manager", "Department Manager",
    "Sales Manager", "Account Manager", "Recruiter", "Executive Recruiter", "Accountant", "Nurse", "Doctor",
    "Physician", "Lawyer", "Attorney", "Architect", "Interior Designer", "Structural Engineer",
    "Civil Structural Engineer", "Mechanical Engineer", "Electrical Engineer", "Chief", "Head of",
    "Sales Representative", "Business Development",
]
# Negative titles that are overridden when the title itself is clearly geospatial
# (e.g. "GIS Architect" or "Enterprise Geospatial Architect").
NEGATIVE_OVERRIDABLE = {"Architect"}

TECH_SKILLS = [
    Term("ArcGIS Pro", 7, (r"\b(?:esri\s+)?arcgis\s*pro\b",), family="esri"),
    Term("ArcGIS", 5, (r"\barcgis\b(?!\s*pro\b)", r"\besri\b(?!\s+arcgis\s*pro\b)"), family="esri"),
    Term("ArcMap", 4, (r"\barcmap\b",), family="esri"),
    Term("QGIS", 6, (r"\bqgis\b",), family="qgis"),
    Term("ArcPy", 6, (r"\barcpy\b",), family="gis_scripting"),
    Term("PyQGIS", 6, (r"\bpyqgis\b",), family="gis_scripting"),
    Term("Python", 5, (r"\bpython\b",)),
    Term("SQL", 3, (r"\bsql\b", r"\bpostgres(?:ql)?\b", r"\bt-sql\b", r"\bpl/?sql\b", r"\bmysql\b")),
    Term("PostGIS", 6, (r"\bpostgis\b",), family="spatial_db"),
    Term("FME", 6, (r"\bfme\b", r"\bsafe software\b"), family="fme"),
    Term("AutoCAD", 3, (r"\bauto\s?cad\b", r"\bcivil\s*3d\b")),
    Term("MicroStation", 4, (r"\bmicro\s?station\b",)),
    Term("TerraScan", 6, (r"\bterra\s?scan\b", r"\bterrasolid\b"), family="lidar_tools"),
    Term("Smallworld", 6, (r"\bsmallworld\b",), family="utility_gis"),
    Term("Agisoft Metashape", 5, (r"\bmetashape\b", r"\bagisoft\b", r"\bphotoscan\b"), family="photogrammetry_tools"),
    Term("Arcade", 3, (r"\barcade\b",), requires="ArcGIS"),
    Term("GeoJSON", 3, (r"\bgeo\s?json\b",), family="geo_formats"),
    Term("Spatial databases", 4, (r"\bspatial\s+databases?\b", r"\b(?:enterprise\s+|file\s+)?geodatabases?\b",
                                  r"\bspatialite\b", r"\boracle\s+spatial\b"), family="spatial_db"),
    Term("REST APIs", 2, (r"\brest(?:ful)?\s*(?:apis?|services?|endpoints?)\b",)),
    Term("GDAL/OGR", 3, (r"\bgdal\b", r"\bogr2ogr\b"), family="geo_libs"),
    Term("GeoPandas", 3, (r"\bgeopandas\b", r"\bshapely\b", r"\brasterio\b"), family="geo_libs"),
    Term("Google Earth Engine", 3, (r"\bgoogle\s+earth\s+engine\b", r"\bgee\b(?=.{0,40}\b(?:imagery|satellite|remote))"),
         family="remote_sensing"),
    Term("ENVI/eCognition", 3, (r"\benvi\b", r"\becognition\b", r"\berdas\b"), family="remote_sensing"),
    Term("Pix4D", 3, (r"\bpix4d\w*",), family="photogrammetry_tools"),
    Term("Global Mapper", 3, (r"\bglobal\s+mapper\b",), family="geo_desktop"),
    Term("CloudCompare/LAStools", 3, (r"\bcloudcompare\b", r"\blastools\b", r"\bpdal\b"), family="lidar_tools"),
    Term("Web mapping libraries", 3, (r"\bleaflet(?:\.js)?\b(?=.{0,60}\b(?:map|gis|spatial))", r"\bopenlayers\b",
                                      r"\bmapbox\b", r"\bgeoserver\b", r"\bcesium(?:js)?\b"), family="web_mapping"),
]

DOMAIN_TERMS = [
    Term("GIS", 4, (r"\bgis\b", r"\bgeographic(?:al)?\s+information\s+systems?\b"), family="gis"),
    Term("Geospatial", 4, (r"\bgeo-?spatial\b",), family="gis"),
    Term("Geomatics", 5, (r"\bgeomatics?\b",), family="geomatics"),
    Term("Cartography", 5, (r"\bcartograph\w*",), family="cartography"),
    Term("Surveying", 4, (r"\b(?:land|topographic|cadastral|geodetic|construction|hydrographic|boundary)\s+survey\w*",
                          r"\bsurveying\b", r"\bsurveyors?\b", r"\bgnss\b", r"\btotal\s+stations?\b"), family="surveying"),
    Term("Topography", 3, (r"\btopograph\w*",), family="surveying"),
    Term("Cadastral", 5, (r"\bcadastr\w*",), family="land_admin"),
    Term("Land administration", 5, (r"\bland\s+administration\b", r"\bland\s+regist\w*", r"\bland\s+records?\b"),
         family="land_admin"),
    Term("Land tenure", 5, (r"\bland\s+tenure\b",), family="land_admin"),
    Term("Mineral tenure", 6, (r"\bmineral\s+(?:tenure|claims?|titles?|rights|concessions?)\b",
                               r"\bmining\s+(?:claims?|tenements?|concessions?|titles?)\b", r"\btenements?\b"),
         family="mineral_tenure"),
    Term("Mining", 3, (r"\bmining\b", r"\bmineral\s+exploration\b", r"\bmine\s+(?:site|planning|survey\w*)\b")),
    Term("LiDAR", 5, (r"\blidar\b", r"\blaser\s+scann\w*"), family="lidar"),
    Term("Point clouds", 5, (r"\bpoint[\s-]?clouds?\b",), family="lidar"),
    Term("Photogrammetry", 5, (r"\bphotogrammetr\w*", r"\bortho-?(?:photo|imagery|mosaic|rectif)\w*"),
         family="photogrammetry"),
    Term("Remote sensing", 5, (r"\bremote\s+sensing\b", r"\bsatellite\s+(?:imagery|images|data)\b",
                               r"\bearth\s+observation\b", r"\bmultispectral\b", r"\bhyperspectral\b"),
         family="remote_sensing"),
    Term("UAV", 4, (r"\buavs?\b", r"\bdrones?\b", r"\bunmanned\s+aerial\b", r"\brpas\b", r"\bsuas\b")),
    Term("Utility mapping", 5, (r"\butility\s+(?:mapping|gis|network\s+(?:data|model)|records|locat\w+)\b",
                                r"\bsubsurface\s+utilit\w+"), family="utility_gis"),
    Term("Telecom", 2, (r"\btelecom(?:s|munications?)?\b",)),
    Term("Fiber", 2, (r"\bfib(?:er|re)\s+(?:optics?|networks?|design|routes?)\b", r"\bftth\b", r"\boutside\s+plant\b")),
    Term("Electric distribution", 3, (r"\belectric(?:al)?\s+distribution\b", r"\bpower\s+distribution\b",
                                      r"\bdistribution\s+network\b")),
    Term("Spatial analysis", 4, (r"\bspatial\s+(?:analysis|analytics|modell?ing|statistics)\b", r"\bgeoprocessing\b"),
         family="spatial_analysis"),
    Term("Spatial data", 3, (r"\bspatial\s+data\b", r"\bgeodata\b"), family="gis"),
    Term("Mapping", 3, (r"\b(?:gis|web|digital|base|mobile|utility|topographic|thematic|field|aerial)\s+mapping\b",
                        r"\bmap\s+production\b"), family="mapping"),
]

RESPONSIBILITY_TERMS = [
    Term("Digitizing / data capture", 2, (r"\bdigiti[sz]\w*", r"\bspatial\s+data\s+capture\b", r"\bvectori[sz]\w*")),
    Term("Georeferencing", 2, (r"\bgeo-?referenc\w*", r"\brectification\b"), family="georef"),
    Term("Map production", 2, (r"\bmap\s+(?:production|making|creation|layouts?)\b", r"\b(?:produce|create|prepare)\s+maps\b")),
    Term("QA/QC", 2, (r"\bqa\s*/\s*qc\b", r"\bquality\s+(?:control|assurance)\b")),
    Term("Spatial data management", 2, (r"\b(?:spatial|gis|geospatial)\s+data\s+(?:editing|maintenance|management|processing|conversion|migration|integration)\b",
                                        r"\bdata\s+conversion\b")),
    Term("Coordinate systems", 2, (r"\bcoordinate\s+(?:reference\s+)?systems?\b", r"\bmap\s+projections?\b",
                                   r"\bgeodetic\s+datums?\b"), family="geodesy"),
    Term("Field data collection", 2, (r"\bfield\s+data\s+collection\b", r"\bsurvey123\b", r"\bfield\s+maps\b",
                                      r"\bgps\s+data\b"), family="field_gis"),
    Term("Topology / attribution", 2, (r"\btopology\b", r"\battribute\s+(?:data|tables?)\b")),
    Term("Web GIS / dashboards", 2, (r"\bweb\s+gis\b", r"\bweb\s+maps?\b", r"\bstory\s*maps?\b",
                                     r"\bexperience\s+builder\b", r"\bgis\s+dashboards?\b"), family="web_mapping"),
    Term("Point cloud classification", 2, (r"\bclassif\w+\s+(?:of\s+)?(?:lidar|point)",), family="lidar"),
]

REQUIRED_HINTS = r"\b(required|must|minimum|essential|mandatory|need(?:s|ed)?\s+to\s+have|proficien\w+|experience\s+(?:with|in|using))\b"
PREFERRED_HINTS = r"\b(prefer\w*|nice[\s-]to[\s-]have|asset|desirabl\w+|bonus|plus|ideally|advantage\w*|familiarity)\b"

CATEGORY_CAPS = {"title": 40, "tech": 25, "domain": 20, "responsibilities": 10, "location": 5}


@dataclass
class MatchConfig:
    high_threshold: int = 70
    medium_threshold: int = 55
    preferred_locations: list[str] = field(default_factory=list)
    accepted_remote_scopes: list[str] = field(default_factory=list)
    strict_location: bool = False
    extra_negative_titles: list[str] = field(default_factory=list)
    title_only_min_points: int = 34
