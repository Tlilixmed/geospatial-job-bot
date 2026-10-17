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
    # French titles (Tunisia, Maghreb, France, Québec, West Africa). Accents are folded before matching.
    "Géomaticien", "Géomaticienne", "Ingénieur SIG", "Technicien SIG", "Technicienne SIG", "Analyste SIG",
    "Administrateur SIG", "Développeur SIG", "Opérateur SIG", "Dessinateur SIG", "Chargé d'études SIG",
    "Chef de projet SIG", "Ingénieur Géomatique", "Technicien Géomatique", "Spécialiste Géomatique",
    "Cartographe", "Topographe", "Géomètre", "Géomètre-Topographe", "Ingénieur Topographe",
    "Technicien Topographe", "Dessinateur Topographe", "Ingénieur Télédétection", "Chargé de Télédétection",
    "Photogrammètre", "Analyste Géospatial", "Ingénieur Géospatial", "Géodésien", "Arpenteur-Géomètre",
    # Arabic titles (Gulf and Maghreb postings); hamza forms are folded, so أخصائي and اخصائي both match
    "مهندس نظم معلومات جغرافية", "أخصائي نظم معلومات جغرافية", "فني نظم معلومات جغرافية", "محلل نظم معلومات جغرافية",
    "مهندس مساحة", "فني مساحة", "مساح", "رسام خرائط", "مهندس جيوماتكس",
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

# Arabic survey titles, folded (shared by the strong and the weak title lists, which must hold the same strings). The
# job word is a whole word ("مهندسة" counts, "مدير" too), and "مساحة العمل/المكتب" after it is a workspace.
_AR_SURVEYOR = r"مساح(?![اةته]|يق)"
_AR_SURVEY_JOB = (r"(?<![ء-ي])(?:ال)?(?:مهندس[ةه]?|فني[ةه]?|اخصايي[ةه]?|مشرف[ةه]?|مساعد|رييس|مدير|مسوول|مراقب|قسم|اعمال|"
                  r"ادار[ةه]|هندس[ةه])\s+(?:ال)?مساح[ةه](?!\s+(?:ال)?(?:عمل|مكتب\w*|تجاري\w*|تخزين\w*))")
_AR_SURVEY_ADJ = r"مساح[ةه]\s+(?:ال)?(?:جيوديسي[ةه]|تصويري[ةه]|طبوغرافي[ةه])"

# Title terms that are geospatial on their own (used for inverted/variant titles such as
# "Analyst, GIS" or "Senior Geospatial Data Scientist").
GEO_TITLE_TERMS = [
    r"\bgis\b", r"\bgeo-?spatial\b", r"\bgeomatic\w*", r"\blidar\b", r"\bcartograph\w*", r"\bphotogrammetr\w*",
    r"\bremote\s+sensing\b", r"\bpoint[\s-]?cloud\b", r"\bcadastr\w*", r"\bsurvey(?:or|ing)\b",
    r"\bsurvey\s+(?:technician|tech|cad|crew|party|assistant)\b", r"\bland\s+(?:information|tenure|records)\b",
    r"\bmineral\s+tenure\b", r"\bspatial\s+(?:data|analyst|analysis|information|scientist)\b",
    r"\b(?:mapping|mapper)\b", r"\bmap\s+(?:draft\w*|technician|editor|maker|production)\b",
    r"\bgeographic information\b", r"\bgeodes\w*", r"\bhydrograph\w*", r"\bbathymetr\w*",
    r"\bearth observation\b", r"\bgeodata\b",
    # French (titles are accent-folded before these run). Buried-network detection (DT-DICT) is surveying work in
    # France; "détection réseau(x)" is also intrusion detection, so the plural without a security word in the title, or a
    # buried-network word, is needed.
    r"\bsig\b", r"\bgeomatiq\w*", r"\bgeomaticien\w*", r"\btopograph\w*", r"\bgeometres?\b", r"\bteledetection\b",
    r"\bgeodesien\w*", r"\bsystemes? d.information geographique", r"\barpent\w*",
    r"^(?!.*\b(?:securite|cyber\w*|soc|ndr|edr|siem|ids|intrusions?|menaces?)\b)"
    r".*\bdetection\s+(?:de\s+|des\s+)?reseaux\b(?!\s+(?:informatiques?|it|telecom\w*|sociaux|et\s+systemes?|&\s+systemes?))",
    r"\bdetection\s+(?:de\s+|des\s+)?reseaux?\s+(?:enterre|souterrain|sensible)", r"\bgeodetection\b",
    # Arabic, as fold() leaves it: hamza carriers decompose (خرائط -> خرايط, رئيس -> رييس). "مساحة"/"مساحات" is
    # also an area or a space, so the bare word must not go on as مساحة/مساحه/مساحات or مساحيق (powders); the survey
    # sense of "مساحة" needs a job word before it or a survey adjective after it. "خرائط الطريق" is a roadmap.
    r"(?:ا?نظم[ةه]?|نظام)\s+(?:ال)?معلومات\s+(?:ال)?جغرافي", _AR_SURVEYOR, r"جيوماتكس", r"استشعار عن بعد",
    r"خرا[ئي]ط(?!\s+(?:ال)?طريق)", r"جغرفة\s+(?:ال)?رقمية", _AR_SURVEY_JOB, _AR_SURVEY_ADJ,
    r"طبوغراف", r"قيس\s+(?:ال)?اراضي", r"مسح\s+(?:ال)?اراضي",
]

# Weak title terms: words that are geospatial in a geomatics context but common elsewhere ("Quantity
# Surveyor", "Marine Surveyor", market-research "Survey ..." roles, business "Mapping"). A title whose only
# geospatial signal is one of these must be backed by SURVEY_EVIDENCE or another geospatial family in the
# description, and gets no title-only benefit of the doubt unless it is an explicit DIRECT/ADJACENT role.
WEAK_GEO_TITLE_TERMS = [
    r"\bsurvey(?:or|ors|ing|s)?\b", r"\b(?:mapping|mapper)\b", r"\btopograph\w*", r"\bgeometres?\b", r"\barpent\w*",
    _AR_SURVEYOR, _AR_SURVEY_JOB, _AR_SURVEY_ADJ,  # an interior designer of "مساحات" (spaces) is not a surveyor
    r"طبوغراف", r"قيس\s+(?:ال)?اراضي", r"مسح\s+(?:ال)?اراضي",
]
# Land-survey vocabulary that backs a weak title. Brand names alone are not evidence (Leica also makes cameras and
# microscopes, Topcon eye-care devices, Trimble fleet software) and neither is a bare theodolite (metrology labs): the
# guarded "Survey instruments" term covers them, and the terms in SURVEY_EVIDENCE_TERMS count as evidence as well. Plain
# GPS and RTK are fleet tracking and drone navigation as often as surveying, so they count only as survey kit.
SURVEY_EVIDENCE = (
    r"\b(?:gnss|rtk\s+(?:gps|gnss)|gps\s+(?:survey\w*|rovers?|receivers?|equipment|base\s+stations?)|survey[\s-]grade\s+gps|"
    r"total\s+stations?|stations?\s+totales?|levell?ing|nivellement|bornage|"
    r"(?:land|topographic|cadastral|geodetic|boundary|hydrographic|construction|engineering|legal|mine|as-?built)\s+survey\w*|"
    r"lev[ée]s?\s+(?:topographiques?|de\s+terrain)|control\s+points?|stake-?out|point\s+clouds?|"
    r"civil\s*3d|covadis|cadastr\w*)\b"
    r"|الرفع\s+المساحي|(?:ال)?محط[ةه]\s+(?:ال)?شامل[ةه]|توتال\s+ستيشن"
)
# Surveying-family terms concrete enough to back a weak title. The "Surveying" and "Topography" domain terms are not:
# they match the title's own word ("Surveyor", "Topographe") and would let the title vouch for itself.
SURVEY_EVIDENCE_TERMS = {"Survey office software", "Survey instruments", "French topographic works"}

ROLE_NOUNS = (
    r"analyst|specialist|technician|tech|officer|coordinator|developer|engineer|scientist|manager|lead|"
    r"processor|editor|consultant|intern|assistant|administrator|drafter|designer|operator|mapper|associate|"
    r"programmer|architect|professional|surveyor|expert|technologist|hydrographer|photogrammetrist|drafts(?:man|woman|person)|"
    # French role nouns
    r"ingenieur|technicien(?:ne)?|analyste|chargee?|cartographe|topographe|geometre|geomaticien(?:ne)?|"
    r"dessinat(?:eur|rice)|operat(?:eur|rice)|responsable|specialiste|developpeu(?:r|se)|administrat(?:eur|rice)|"
    r"stagiaire|geodesien|arpenteur|technologue|chef de projet|"
    # Arabic role nouns as they look after folding (hamza carriers decompose: أخصائي -> اخصايي)
    r"مهندس|فني|اخصايي|اخصائي|محلل|مساح|مطور|رسام"
)

# Generic titles need at least MIN_GEO_SIGNALS distinct geospatial families in the text.
GENERIC_TITLE_PATTERNS = [
    r"\bdata\s+analyst\b", r"\bdata\s+technician\b", r"\banalyst\b", r"\btechnician\b", r"\bspecialist\b",
    r"\bengineer\b", r"\bdeveloper\b", r"\bscientist\b", r"\bcoordinator\b", r"\bdrafter\b",
    r"\bdraftsperson\b", r"\bdata\s+engineer\b", r"\bconsultant\b",
    r"\bingenieur\b", r"\btechnicien(?:ne)?\b", r"\banalyste\b", r"\bdeveloppeu(?:r|se)\b", r"\bdessinat(?:eur|rice)\b",
    r"\bchargee?\s+d.etudes\b", r"\bspecialiste\b", r"\bstagiaire\b",
]
MIN_GEO_SIGNALS = 2

NEGATIVE_TITLES = [
    "Senior Director", "Director", "VP", "Vice President", "Executive", "Branch Manager", "Department Manager",
    "Sales Manager", "Account Manager", "Recruiter", "Executive Recruiter", "Accountant", "Nurse", "Doctor",
    "Physician", "Lawyer", "Attorney", "Architect", "Interior Designer", "Structural Engineer",
    "Civil Structural Engineer", "Mechanical Engineer", "Electrical Engineer", "Chief", "Head of",
    "Sales Representative", "Business Development",
    # "Surveyor"/"survey" roles outside geomatics
    "Quantity Surveyor", "Quantity Surveying", "Building Surveyor", "Building Surveying", "Marine Surveyor",
    "Cargo Surveyor", "Insurance Surveyor", "Chartered Surveyor", "Valuation Surveyor", "Estates Surveyor",
    "Party Wall Surveyor", "Rural Surveyor", "Property Surveyor", "Commercial Surveyor", "Pest Surveyor",
    "Survey Researcher", "Survey Interviewer", "Survey Methodologist", "Survey Statistician", "Survey Programmer",
    "Market Research", "Customer Survey", "Employee Survey", "Process Mapping", "Data Mapping", "Journey Mapping",
    "Métreur", "Métreuse", "Économiste de la construction",
    # French
    "Directeur", "Directrice", "Directeur Général", "Commercial", "Commerciale", "Responsable Commercial",
    "Comptable", "Infirmier", "Infirmière", "Avocat", "Architecte", "Ingénieur Électrique", "Ingénieur Mécanique",
    "Ingénieur Électromécanique", "Chef d'agence",
]
# Internships, co-ops, traineeships and student jobs (EXCLUDE_INTERNSHIPS, default on; /interns on|off in Telegram).
INTERNSHIP_TITLES = [
    "Intern", "Interns", "Internship", "Co-op", "Coop", "Trainee", "Traineeship", "Apprentice", "Apprenticeship",
    "Working Student", "Student", "Summer Student", "Werkstudent", "Praktikum", "Praktikant", "Graduate Program",
    "Stagiaire", "Stage", "Stage PFE", "PFE", "Alternance", "Alternant", "Alternante", "Apprenti", "Apprentie",
    "Étudiant", "Étudiante", "Becario", "Prácticas",
    # GeoRezo tags: "[Altern]" apprenticeship, "[VSC]" civic volunteering, PhD and post-doc positions
    "Altern", "VSC", "Volontariat", "Service civique", "Thèse", "Doctorat", "Doctorant", "Doctorante",
    "Post-doctorat", "Postdoctorat", "Post-doc", "Postdoc",
]
# Negative titles that are overridden when the title itself is clearly geospatial
# (e.g. "GIS Architect" or "Enterprise Geospatial Architect").
NEGATIVE_OVERRIDABLE = {"Architect", "Architecte"}
# Negative words that are also ordinary words or real roles. When the pattern matches the (folded) title, the word is
# not held against it: a survey party chief leads a field crew, "GIS Executive" is a junior role in India and the UK,
# "commercial" is French for a salesperson but plain English in "Commercial Drone Pilot", and "stage" is a French
# internship but not in "(Early Stage Startup)".
NEGATIVE_EXCEPTIONS = {
    "Chief": r"\b(?:party|crew|survey|instrument|field)\s+chief\b|\bchief\s+of\s+part(?:y|ies)\b|\bchief\s+(?:land\s+)?surveyor\b",
    "Executive": r"\b(?:gis|geospatial|mapping|survey|lidar|photogrammetry|cad|remote\s+sensing|geomatics)\s+executive\b",
    "Commercial": r"\bcommercial\s+(?:drone|uav|uas|rpas|pilot|gis|geospatial|mapping|surveys?|surveying|lidar|photogrammetr\w*|"
                  r"satellite|imagery|real\s+estate|property|properties|projects?|sector|and|&)\b|\b(?:and|&|/)\s*commercial\b",
    "Stage": r"\b(?:early|late|growth|seed|multi|first|second|final|clinical)[\s-]+stage\b|\bstage[\s-]+(?:gate|startup|start-up|company)\b",
    "Student": r"\bstudent\s+(?:services?|success|affairs|information|records|housing|life|support|experience|recruitment|accounts?)\b",
    # a hydrographic or geophysical marine surveyor maps the seabed; a plain one inspects ships and cargo
    "Marine Surveyor": r"\bhydrograph\w*|\bbathymetr\w*|\bmultibeam\b|\bgeophysic\w*",
}

# Survey instrument makers and the instruments they make (the "Survey instruments" term pairs them). Plain GPS is left
# out of the instruments: Trimble also sells GPS fleet tracking, so a brand pairs with GPS only as survey hardware.
_SURV_BRAND = r"(?:trimble|leica|topcon|sokkia|stonex|emlid|spectra\s+(?:precision|geospatial))"
_SURV_NOUN = (r"(?:gnss|rtk|stations?\s+(?:totales?|robotis[ée]es?)|total\s+stations?|robotic\s+stations?|r[ée]cepteurs?|"
              r"receivers?|tach[ée]om[èe]tres?|niveaux?\s+(?:num[ée]riques?|optiques?)|digital\s+levels?|laser\s+scanners?|"
              r"scanners?\s+(?:laser|3d))")

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
    Term("AutoCAD", 3, (r"\bauto\s?cad\b", r"\bcivil\s*3d\b", r"\bcovadis\b")),
    Term("MicroStation", 4, (r"\bmicro\s?station\b",)),
    Term("TerraScan", 6, (r"\bterra\s?scan\b", r"\bterrasolid\b"), family="lidar_tools"),
    Term("Smallworld", 6, (r"\bsmallworld\b",), family="utility_gis"),
    Term("Agisoft Metashape", 5, (r"\bmetashape\b", r"\bagisoft\b", r"\bphotoscan\b"), family="photogrammetry_tools"),
    Term("Arcade", 3, (r"\barcade\b",), requires="ArcGIS"),
    Term("GeoJSON", 3, (r"\bgeo\s?json\b",), family="geo_formats"),
    Term("Spatial databases", 4, (r"\bspatial\s+databases?\b", r"\b(?:enterprise\s+|file\s+)?geodatabases?\b",
                                  r"\bspatialite\b", r"\boracle\s+spatial\b",
                                  r"\bbases?\s+de\s+donn[ée]es\s+(?:spatiales?|g[ée]ographiques?)\b"), family="spatial_db"),
    Term("REST APIs", 2, (r"\brest(?:ful)?\s*(?:apis?|services?|endpoints?)\b",)),
    Term("GDAL/OGR", 3, (r"\bgdal\b", r"\bogr2ogr\b"), family="geo_libs"),
    Term("GeoPandas", 3, (r"\bgeopandas\b", r"\bshapely\b", r"\brasterio\b"), family="geo_libs"),
    Term("Google Earth Engine", 3, (r"\bgoogle\s+earth\s+engine\b",
                                    r"\bgee\b(?=[^.;\n]{0,40}\b(?:imagery|satellite|remote))"), family="remote_sensing"),
    Term("ENVI/eCognition", 3, (r"\benvi\b", r"\becognition\b", r"\berdas\b"), family="remote_sensing"),
    Term("Pix4D", 3, (r"\bpix4d\w*",), family="photogrammetry_tools"),
    Term("Global Mapper", 3, (r"\bglobal\s+mapper\b",), family="geo_desktop"),
    Term("CloudCompare/LAStools", 3, (r"\bcloudcompare\b", r"\blastools\b", r"\bpdal\b"), family="lidar_tools"),
    Term("Web mapping libraries", 3, (r"\bleaflet(?:\.js)?\b(?=[^.;\n]{0,60}\b(?:map|gis|spatial))", r"\bopenlayers\b",
                                      r"\bmapbox\b", r"\bgeoserver\b", r"\bcesium(?:js)?\b", r"\bmaplibre\b",
                                      r"\bdeck\.?gl\b", r"\bkepler\.?gl\b"), family="web_mapping"),
    # Surveying tools join the "surveying" family, which a generic title already gets from GNSS or a total station.
    # TBC alone is also "to be confirmed" ("Location TBC, Survey department", "Salary TBC, AutoCAD essential"): only
    # Trimble's name or a list with another survey package makes it Trimble Business Center; a general CAD package after
    # it does not. Mensura is also a Belgian occupational-health service.
    Term("Survey office software", 4, (
        r"\btrimble\s+business\s+cent(?:er|re)\b", r"\btrimble[\s(]+tbc\b", r"\btbc\s*\(\s*trimble\b",
        r"\btbc(?<!salary tbc)(?<!salary: tbc)(?<!location tbc)(?<!location: tbc)(?<!date tbc)(?<!date: tbc)"
        r"(?<!start tbc)(?<!rate tbc)(?<!hours tbc)\s*[,/&]\s*(?:and\s+|or\s+|et\s+|ou\s+)?(?:12d|carlson|magnet|mensura|leica)\b",
        r"\b(?:12d(?:\s+model)?|civil\s*3d|autocad|carlson|magnet|covadis|mensura)\s*[,/&]\s*(?:and\s+|or\s+|et\s+|ou\s+)?tbc\b",
        r"\bmensura\b(?=[^.;\n]{0,60}\b(?:autocad|covadis|civil\s*3d|topo\w*|cao|dao)\b)",
        r"\b(?:autocad|covadis|civil\s*3d|topo\w*|cao|dao|logiciels?)\b[^.;\n]{0,60}\bmensura\b",
        r"\bleica\s+infinity\b", r"\b12d\s+(?:model|synergy|field)\b", r"\bcarlson\s+(?:survey|survce|civil|field)\b",
        r"\bsurvce\b", r"\bmagnet\s+(?:office|field|tools)\b", r"\btrimble\s+access\b", r"\bleica\s+captivate\b",
    ), family="surveying"),
    # A brand counts only next to a survey instrument and a theodolite only next to other survey kit: Leica also makes
    # cameras, and metrology labs align optics with theodolites. RTK on its own is drone or robot navigation.
    Term("Survey instruments", 3, (
        rf"\b{_SURV_BRAND}\b(?=[^.;\n]{{0,40}}\b{_SURV_NOUN}\b)", rf"\b{_SURV_NOUN}\b[^.;\n]{{0,40}}\b{_SURV_BRAND}\b",
        rf"\b{_SURV_BRAND}\s+gps\s+(?:hardware|equipment|receivers?|rovers?|survey\w*)\b",
        r"\brtk\b(?=[^.;\n]{0,40}\b(?:rovers?|survey\w*|base\s+stations?|stake-?out|topograph\w*|gnss|ppk)\b)",
        r"\b(?:survey\w*|topograph\w*|gnss|ppk)\b[^.;\n]{0,40}\brtk\b",
        r"\b(?:total\s+stations?|stations?\s+totales?|gps|gnss|niveaux?|levels?|leica|trimble|topcon)\b[^.;\n]{0,40}"
        r"\bth[ée]odolites?\b",
        r"\bth[ée]odolites?\b(?=[^.;\n]{0,40}\b(?:gps|gnss|total\s+stations?|stations?\s+totales?|niveaux?|levels?)\b)",
        r"\btach[ée]om[èe]tre\w*",
    ), family="surveying"),
    Term("Hydrographic software", 4, (
        r"\bqinsy\b|\bqimera\b|\bhypack\b|\bhysweep\b|\bsonarwiz\b|\bnavi(?:suite|pac|edit|model)\b"
        r"|\beiva\s+(?:navi\w*|beamworks)\b|\bcaris\s+(?:hips|sips|base\s+editor|onboard)\b|\bteledyne\s+caris\b"
        r"|\bcaris\b(?=[^.;\n]{0,80}\b(?:qinsy|qimera|hypack|eiva|sonarwiz|hips|beamworks)\b)",
    ), family="hydrography"),
    # STAC, COG and OGC are also a finance benchmark council, "cost of goods sold" and the Office of General Counsel:
    # each needs a geospatial neighbour in the same sentence. COG is matched in capitals only for the same reason.
    Term("Cloud-native geo formats", 3, (
        r"\bstac\b(?=[^.;\n]{0,80}\b(?:catalog\w*|apis?|geotiffs?|cogs?|imagery|raster|items?|collections?|fastapi|pgstac"
        r"|ogc|geojson|zarr|netcdf|hdf\w*|tiles?)\b)",
        r"\b(?:geotiffs?|cogs?|imagery|raster|geojson|ogc|zarr|netcdf|hdf\w*|xarray)\b[^.;\n]{0,80}\bstac\b",
        r"\bpgstac\b", r"\bstac[-_]fastapi\b",
        r"(?-i:\bCOGs?\b)(?!\s+(?:for|across|of|by|reduction|savings?)\b)"
        r"(?=[^.;\n]{0,40}\b(?:geotiffs?|raster|imagery|stac|zarr|geoparquet|netcdf|xarray|gdal)\b)",
        r"\b(?:geospatial|raster|imagery|stac|xarray|netcdf|gdal|formats?)\b[^.;\n]{0,60}(?-i:\bCOGs?\b)"
        r"(?!\s+(?:for|across|of|by|reduction|savings?)\b)",
        r"\bcloud[\s-]optimi[sz]ed\s+geotiffs?\b", r"\bgeotiffs?\b",
        r"\bgeoparquet\b", r"(?<!ome-)\bzarr\b", r"\bpmtiles\b", r"\bflatgeobuf\b", r"\btitiler\b",
        r"\bpg_(?:featureserv|tileserv)\b",
    ), family="geo_formats"),
    Term("OGC web services", 3, (
        r"\bogc\b(?:[\s-]+(?:standards?|services?|apis?|compliant|specifications?)\b"
        r"|(?=[^.;\n]{0,40}\b(?:wms|wfs|wmts|wcs|stac|cog|geoserver|geopackage|i3s|3d\s+tiles)\b))",
        r"\b(?:wms|wfs|wmts|stac|geoserver)\b[^.;\n]{0,40}\bogc\b",
        # WMS and WFS are also a warehouse system and Kronos Workforce Software, together in logistics postings: the pair
        # alone is not enough, a map server or map layers must be named with them
        r"\b(?:wms|wfs|wmts|wcs)\b(?=[^.;\n]{0,40}\b(?:ogc|geoserver|mapserver|layers?|couches?)\b)",
        r"\b(?:ogc|geoserver|mapserver|qgis|arcgis)\b[^.;\n]{0,40}\b(?:wms|wfs|wmts|wcs)\b",
        r"\bflux\s+(?:wms|wfs|wmts)\b", r"\bflux\s+(?:de\s+donn[ée]es\s+)?cartograph\w*",
        r"\bweb\s+(?:map|feature|coverage)\s+services?\b",
        r"\bdirective\s+inspire\b",
        r"(?-i:\bINSPIRE\b)(?=[^.;\n]{0,40}\b(?:directive|compliant|conform\w*|metadata|m[ée]tadonn[ée]es)\b)",
        r"\bvector\s+tiles?\b", r"\btuiles?\s+vectorielles?\b",
    ), family="web_mapping"),
]

# Domain/responsibility patterns run on the raw text (case-insensitive, accents intact), so French
# variants spell out their accented letters as character classes.
DOMAIN_TERMS = [
    Term("GIS", 4, (r"\bgis\b", r"\bgeographic(?:al)?\s+information\s+systems?\b", r"\bsig\b",
                    r"\bsyst[èe]mes?\s+d.information\s+g[ée]ographique",
                    r"(?:انظم[ةه]?|نظم|نظام)\s+(?:ال)?معلومات\s+(?:ال)?جغرافي[ةه]?"), family="gis"),
    Term("Geospatial", 4, (r"\bg[ée]o-?spatial\w*",), family="gis"),
    Term("Geomatics", 5, (r"\bg[ée]omatics?\b", r"\bg[ée]omatique\b", r"\bg[ée]omaticien\w*"), family="geomatics"),
    Term("Cartography", 5, (r"\bcartograph\w*", r"خرا[ئي]ط(?!\s+(?:ال)?طريق)", r"جغرفة\s+(?:ال)?رقمية"),
         family="cartography"),
    Term("Surveying", 4, (r"\b(?:land|topographic|cadastral|geodetic|construction|hydrographic|boundary)\s+survey\w*",
                          r"\bsurveying\b", r"\bsurveyors?\b", r"\bgnss\b", r"\btotal\s+stations?\b",
                          r"\barpentage\b", r"\barpenteur\w*", r"\bg[ée]om[èe]tres?\b",
                          r"\blev[ée]s?\s+topographiques?\b", r"\bstations?\s+totales?\b",
                          # Arabic: the surveyor, with an attached article or particle ("المساح", "للمساحين"), but
                          # not "مساحة"/"مساحات" (area, space) unless a survey word sits next to it
                          r"(?<![ء-ي])[وفبلك]?(?:ال|لل)?مساح(?:ين|ون|و)?(?![ء-ي])",
                          r"(?<![ء-ي])(?:[وفبلك]?ال)?(?:اعمال|مجال|اجهز[ةه]|علم|هندس[ةه]|مهندس[ةه]?|فني[ةه]?|قسم|اخصا[ئي]ي[ةه]?|"
                          r"مشرف[ةه]?|مساعد|ر[ئي]يس|مدير|مس[ؤو]ول|مراقب)\s+(?:ال)?مساح[ةه]"
                          r"(?!\s+(?:ال)?(?:عمل|مكتب\w*|تجاري\w*|تخزين\w*))",
                          r"(?:ال)?مساح[ةه]\s+(?:ال)?(?:جيوديسي[ةه]|تصويري[ةه]|طبوغرافي[ةه])|المساح[ةه]\s+العقاري[ةه]",
                          r"(?:ال)?اعمال\s+(?:ال)?مساحي[ةه]|الرفع\s+المساحي|(?:قيس|مسح)\s+(?:ال)?اراضي"),
         family="surveying"),
    Term("Topography", 3, (r"\btopograph\w*", r"طبوغراف"), family="surveying"),
    # "السجل العقاري" is left out: in Gulf property ads it is the real-estate register, not the cadastre
    Term("Cadastral", 5, (r"\bcadastr\w*", r"(?:ال)?مسح\s+(?:ال)?عقاري"), family="land_admin"),
    Term("Land administration", 5, (r"\bland\s+administration\b", r"\bland\s+regist\w*", r"\bland\s+records?\b",
                                    r"\bfonci[èe]re?s?\b", r"\bregistre\s+foncier\b"), family="land_admin"),
    Term("Land tenure", 5, (r"\bland\s+tenure\b",), family="land_admin"),
    Term("Mineral tenure", 6, (r"\bmineral\s+(?:tenure|claims?|titles?|rights|concessions?)\b",
                               r"\bmining\s+(?:claims?|tenements?|concessions?|titles?)\b", r"\btenements?\b"),
         family="mineral_tenure"),
    Term("Mining", 3, (r"\bmining\b", r"\bmineral\s+exploration\b", r"\bmine\s+(?:site|planning|survey\w*)\b",
                       r"\bmini[èe]re?s?\b", r"\bexploration\s+(?:mini[èe]re|g[ée]ologique)\b",
                       r"\btitres?\s+miniers?\b", r"\bpermis\s+(?:minier|de\s+recherche|d.exploration)\b",
                       r"\bstaking\b", r"\bclaim\s+(?:maps?|staking|overlaps?)\b")),
    Term("Geology", 3, (r"\bgeolog\w*", r"\blitholog\w*", r"\bg[ée]olog\w*", r"\bdrill\s*holes?\b", r"\bore\s+bod\w+"),
         family="geology"),
    Term("LiDAR", 5, (r"\blidar\b", r"\blaser\s+scann\w*", r"\bbalayage\s+laser\b", r"\bscanner\s+laser\b"),
         family="lidar"),
    Term("Point clouds", 5, (r"\bpoint[\s-]?clouds?\b", r"\bnuages?\s+de\s+points\b"), family="lidar"),
    Term("Photogrammetry", 5, (r"\bphotogramm[ée]tr\w*", r"\bortho-?(?:photo|imagery|mosaic|rectif|image)\w*",
                               r"(?:ال)?مساح[ةه]\s+(?:ال)?تصويري[ةه]|فوتوغرامتري"), family="photogrammetry"),
    # Sentinel needs its mission number: plain "Sentinel" is also Microsoft's SIEM, and "Sentinel 3+ years" is still the
    # SIEM, so a spaced number counts only with imagery words after it. "SAR imagery" is left out: it is the company
    # introduction in every ICEYE posting, AOCS and DevOps roles included.
    Term("Remote sensing", 5, (r"\bremote\s+sensing\b", r"\bsatellite\s+(?:imagery|images|data)\b",
                               r"\bearth\s+observation\b", r"\bmultispectral\b", r"\bhyperspectral\b",
                               r"\bt[ée]l[ée]d[ée]tection\b", r"\bimage(?:s|rie)?\s+satellit\w*",
                               r"\bobservation\s+de\s+la\s+terre\b", r"\bsentinel-?[1-6][a-d]?\b",
                               r"\bsentinel\s[1-6][a-d]?\b(?=[^.;\n]{0,30}\b(?:imagery|images?|scenes?|tiles?|sar|msi|olci|"
                               r"radar|optical|mission|time\s+series)\b)",
                               r"(?:ال)?استشعار\s+عن\s+بعد"), family="remote_sensing"),
    Term("UAV", 4, (r"\buavs?\b", r"\bdrones?\b", r"\bunmanned\s+aerial\b", r"\brpas\b", r"\bsuas\b")),
    Term("Utility mapping", 5, (r"\butility\s+(?:mapping|gis|network\s+(?:data|model)|records|locat\w+)\b",
                                r"\bsubsurface\s+utilit\w+", r"\bcartographie\s+des?\s+r[ée]seaux\b",
                                r"\br[ée]seaux\s+enterr[ée]s\b"), family="utility_gis"),
    # French buried-network detection (DT-DICT works). It stays in the utility_gis family: DT-DICT alone also turns up
    # in water-works and admin postings, and must not open the generic-title gate by itself. "Détection réseaux" is also
    # intrusion detection (NDR, a SOC), so a sentence with a security word anywhere in it does not count. The precision
    # class is a capital A: "chaque classe a 12 élèves" is the verb.
    Term("Network detection (DT-DICT)", 5, (
        r"(?:^|(?<=[.;\n]))(?=[^.;\n]*?\bd[ée]tection\s)"  # only sentences with the word pay for the security scan
        r"(?![^.;\n]*\b(?:s[ée]curit\w*|cyber\w*|soc|ndr|edr|siem|ids|intrusions?|menaces?|logs?)\b)"
        r"[^.;\n]*?\bd[ée]tection\s+(?:de\s+|des\s+)?r[ée]seaux\b(?!\s+(?:informatiques?|it|t[ée]l[ée]com\w*|sociaux))",
        r"\bd[ée]tection\s+(?:de\s+|des\s+)?r[ée]seaux?\s+(?:enterr|souterrain|sensible)\w*", r"\bg[ée]od[ée]tection\b",
        r"\bg[ée]or[ée]f[ée]rencement\s+(?:de\s+|des\s+)?r[ée]seaux?\b",
        r"\bclasses?\s+(?:de\s+pr[ée]cision\s+)?(?-i:A)\b(?:\s*/\s*b\b)?(?=[^.,;\n]{0,60}\br[ée]seaux\b(?!\s+sociaux))",
        r"\br[ée]seaux\b(?!\s+sociaux)[^.,;\n]{0,60}\bclasses?\s+(?:de\s+pr[ée]cision\s+)?(?-i:A)\b",
        r"(?-i:\bPCRS\b)", r"\bplan\s+de\s+corps\s+de\s+rue\b",  # capitals only: "PCRs" are lab tests
        r"\bg[ée]o[\s-]?radar\b", r"\bground[\s-]penetrating\s+radar\b", r"\bdt\s*[-/]?\s*dict\b",
    ), family="utility_gis"),
    # "bassin hydrographique" is a river basin, and multibeam/single-beam also describe antennas and lasers ("multi-beam
    # phased array antennas for high data rate links"), so "data" is not a seabed word
    Term("Hydrography / bathymetry", 5, (
        r"\bbathym[ée]tri\w*",
        r"\b(?:multi|single)-?beam\b(?=[^.;\n]{0,40}\b(?:sonar|echo\w*|survey\w*|bathy\w*|hydro\w*))",
        r"\bside[\s-]?scan\s+sonar\b", r"\becho[\s-]?sound\w*", r"\bhydrographic\s+(?:survey\w*|data|processing|charting)\b",
        r"\blev[ée]s?\s+hydrographiques?\b", r"\bhydrographie\b(?!\s+(?:du|de\s+la|des)\b)",
    ), family="hydrography"),
    Term("Telecom", 2, (r"\bt[ée]l[ée]com(?:s|munications?)?\b",)),
    Term("Fiber", 2, (r"\bfib(?:er|re)\s+(?:optics?|optique|networks?|design|routes?)\b", r"\bftth\b",
                      r"\boutside\s+plant\b")),
    Term("Electric distribution", 3, (r"\belectric(?:al)?\s+distribution\b", r"\bpower\s+distribution\b",
                                      r"\bdistribution\s+network\b",
                                      r"\br[ée]seaux?\s+(?:de\s+)?distribution\s+[ée]lectrique\b")),
    Term("Spatial analysis", 4, (r"\bspatial\s+(?:analysis|analytics|modell?ing|statistics)\b", r"\bgeoprocessing\b",
                                 r"\banalyses?\s+spatiales?\b", r"\bg[ée]otraitement\b"), family="spatial_analysis"),
    Term("Spatial data", 3, (r"\bspatial\s+data\b", r"\bgeodata\b",
                             r"\bdonn[ée]es\s+(?:spatiales|g[ée]ographiques|g[ée]ospatiales|g[ée]or[ée]f[ée]renc[ée]es)\b"),
         family="gis"),
    Term("Mapping", 3, (r"\b(?:gis|web|digital|base|mobile|utility|topographic|thematic|field|aerial)\s+mapping\b",
                        r"\bmap\s+production\b", r"\bproduction\s+cartographique\b",
                        r"\bcartographie\s+(?:num[ée]rique|th[ée]matique|web|mobile|topographique)\b"), family="mapping"),
]

_RECOLEMENT = (r"\br[ée]coll?ements?\b"
               r"(?!\s+(?:d[ée]cennal|d(?:es|u|e\s+la|e\s+l.)\s*(?:collections?|[œo]e?uvres|biens|archives|fonds|inventaires?|"
               r"mus[ée]es?|mobiliers?|d[ée]p[ôo]ts?)))")

RESPONSIBILITY_TERMS = [
    Term("Digitizing / data capture", 2, (r"\bdigiti[sz]\w*", r"\bspatial\s+data\s+capture\b", r"\bvectori[sz]\w*",
                                          r"\bnum[ée]risation\b",
                                          r"\bsaisie\s+(?:de\s+)?donn[ée]es\s+(?:spatiales|g[ée]ographiques|sig)\b")),
    Term("Georeferencing", 2, (r"\bg[ée]o-?r[ée]f[ée]renc\w*", r"\brectification\b"), family="georef"),
    Term("Map production", 2, (r"\bmap\s+(?:production|making|creation|layouts?)\b", r"\b(?:produce|create|prepare)\s+maps\b",
                               r"\bproduction\s+de\s+cartes\b",
                               r"\b(?:r[ée]aliser|produire|[ée]laborer|cr[ée]er)\s+(?:des\s+|les\s+)?cartes\b",
                               r"\bmise\s+en\s+page\s+cartographique\b")),
    Term("QA/QC", 2, (r"\bqa\s*/\s*qc\b", r"\bquality\s+(?:control|assurance)\b",
                      r"\bcontr[ôo]le\s+(?:de\s+(?:la\s+)?)?qualit[ée]\b", r"\bassurance\s+qualit[ée]\b")),
    Term("Spatial data management", 2, (r"\b(?:spatial|gis|geospatial)\s+data\s+(?:editing|maintenance|management|processing|conversion|migration|integration)\b",
                                        r"\bdata\s+conversion\b",
                                        r"\b(?:gestion|mise\s+[àa]\s+jour|traitement|int[ée]gration)\s+(?:des?\s+)?(?:bases?\s+de\s+)?donn[ée]es\s+(?:spatiales|g[ée]ographiques|sig)\b")),
    Term("Coordinate systems", 2, (r"\bcoordinate\s+(?:reference\s+)?systems?\b", r"\bmap\s+projections?\b",
                                   r"\bgeodetic\s+datums?\b", r"\bsyst[èe]mes?\s+de\s+coordonn[ée]es\b",
                                   r"\bprojections?\s+cartographiques?\b",
                                   r"\bsyst[èe]mes?\s+de\s+r[ée]f[ée]rence\s+(?:spatiale?|g[ée]od[ée]sique)\b"), family="geodesy"),
    Term("Field data collection", 2, (r"\bfield\s+data\s+collection\b", r"\bsurvey123\b", r"\bfield\s+maps\b",
                                      r"\bgps\s+data\b", r"\bcollecte\s+de\s+donn[ée]es\s+(?:sur\s+le\s+)?terrain\b",
                                      r"\b(?:re)?lev[ée]s?\s+(?:de\s+)?(?:terrain|topographiques?|gps|gnss)\b"), family="field_gis"),
    Term("Topology / attribution", 2, (r"\btopology\b", r"\battribute\s+(?:data|tables?)\b", r"\btopologie\b")),
    Term("Web GIS / dashboards", 2, (r"\bweb\s+gis\b", r"\bweb\s+maps?\b", r"\bstory\s*maps?\b",
                                     r"\bexperience\s+builder\b", r"\bgis\s+dashboards?\b", r"\b(?:web\s*sig|sig\s+web)\b",
                                     r"\bcartes?\s+(?:web|en\s+ligne|interactives?)\b"), family="web_mapping"),
    # The candidate's current work: registering scans into one cloud, then meshes and 3D models for BIM / CAD
    Term("Point cloud registration & 3D modelling", 3, (
        r"\b(?:point[\s-]?clouds?|scans?|laser\s+scans?)\s+(?:registration|alignment|stitching|consolidation)\b",
        r"\bregist(?:ration|er(?:ing)?)\s+(?:of\s+)?(?:the\s+)?(?:point[\s-]?clouds?|(?:laser\s+)?scans?|scan\s+data)\b",
        r"\b(?:scan|point[\s-]?cloud)[\s-]*(?:to|2)[\s-]*(?:bim|cad|mesh|revit)\b",
        r"\b3d\s+(?:mesh(?:es|ing)?|models?|modell?ing)\s+(?:from|of)\s+(?:point[\s-]?clouds?|(?:laser\s+)?scans?|scan\s+data)\b",
        r"\b(?:recalage|consolidation|assemblage)\s+(?:de\s+|des\s+)?(?:nuages?\s+de\s+points|scans?)\b",
        r"\bmod[ée]lisation\s+3d\s+(?:[àa]\s+partir\s+(?:de\s+|des\s+)?)?(?:nuages?\s+de\s+points|scans?)\b",
    ), family="lidar"),
    Term("Point cloud classification", 2, (r"\bclassif\w+\s+(?:of\s+)?(?:lidar|point)",
                                           r"\bclassification\s+(?:des?\s+)?nuages?\s+de\s+points\b"), family="lidar"),
    # The daily work of a French géomètre-topographe. Museums also do a "récolement" (of their collections), shops an
    # "implantation" (of a store or a shelf) and HR a "nivellement" (of salaries), so each word needs survey company.
    Term("French topographic works", 3, (
        r"\bbornages?\b", r"\bdivisions?\s+parcellaires?\b",
        r"\b(?:plans?|dossiers?|doe)\s+(?:de\s+)?r[ée]coll?ement\b",
        r"\b(?:plans?|topo\w*|travaux|r[ée]seaux|[ée]x[ée]cution|exe)\b[^.;\n]{0,60}" + _RECOLEMENT,
        _RECOLEMENT + r"(?=[^.;\n]{0,40}\b(?:r[ée]seaux|travaux|topo\w*|plans?)\b)",
        r"\bimplantations?\s+(?:(?!commercia)\w+\s+)?(?:topographiques?|altim[ée]triques?|planim[ée]triques?|d.ouvrages?"
        r"|des\s+(?:axes|ouvrages?|b[âa]timents?|r[ée]seaux\s+(?:enterr|humides|secs|d.assainissement)\w*)"
        r"|de\s+(?:b[âa]timents?|chantiers?|points?\s+(?:topo\w*|de\s+(?:r[ée]f[ée]rence|contr[ôo]le)))"
        r"|sur\s+(?:le\s+|les\s+)?(?:terrain|chantiers?)\b|chantiers?\b)"
        r"(?![^.;\n]{0,30}\b(?:commercia\w*|de\s+vente|magasins?|boutiques?|enseignes?))",
        r"\b(?:tout\s+type|travaux|fichiers?)\s+d.implantations?\b",
        r"\bimplantations?\b(?=[^.;\n]{0,60}\b(?:topograph\w*|gnss|stations?\s+totales?|piquets?|tach[ée]om\w*)\b)",
        r"\bpiquetage\b",
        r"\bnivellements?\b(?!\s+(?:par\s+le\s+(?:bas|haut)|des\s+(?:salaires|comp[ée]tences|r[ée]mun\w+)))",
        r"\bcubatures?\b", r"\bprofils?\s+en\s+(?:long|travers)\b",
        r"\bpolygonation\b", r"\bcanevas\s+(?:topographiques?|polygona(?:l|ux)|g[ée]od[ée]siques?)\b",
    ), family="surveying"),
]

# Qualifier hints run on the accent-folded sentence around a skill, so French forms are written without accents.
REQUIRED_HINTS = (r"\b(required|must|minimum|essential|mandatory|need(?:s|ed)?\s+to\s+have|proficien\w+|"
                  r"experience\s+(?:with|in|using)|exig\w+|requis\w*|obligatoire|indispensable|imperati\w+|"
                  r"maitris\w+|bonne\s+connaissance|connaissance\s+approfondie|experience\s+(?:en|avec|dans|sur))\b")
PREFERRED_HINTS = (r"\b(prefer\w*|nice[\s-]to[\s-]have|asset|desirabl\w+|bonus|plus|ideally|advantage\w*|familiarity|"
                   r"souhait\w+|atout|appreci\w+|idealement)\b")

CATEGORY_CAPS = {"title": 40, "tech": 25, "domain": 20, "responsibilities": 10, "location": 5}

# The candidate's own skills (canonical names of TECH_SKILLS that the CV supports); MY_SKILLS or /skills replace them.
# The "tech" part of the score counts these, scaled by how much of what the posting asks for they cover: a posting that
# names 13 tools scores on the 6 the candidate has, not on all 13 (matcher.skill_points).
# Years of professional experience; a posting that asks for many more is a long shot (matcher.experience_points).
# MY_EXPERIENCE_YEARS overrides it.
CANDIDATE_YEARS = 3
# (years beyond the candidate's, points): one or two more are normal asks; at EXPERIENCE_REJECT_GAP or more the job is
# rejected (EXPERIENCE_GAP), as "10+ years" is for someone with 3.
EXPERIENCE_PENALTY = ((2, -5), (3, -10), (4, -15))
EXPERIENCE_REJECT_GAP = 5
CANDIDATE_SKILLS = ["ArcGIS Pro", "ArcGIS", "QGIS", "Python", "ArcPy", "AutoCAD", "MicroStation", "FME", "TerraScan",
                    "Smallworld", "Agisoft Metashape", "CloudCompare/LAStools", "Spatial databases"]
# Owning the left skill means owning the right ones too (the older or broader product of the same family).
SKILL_IMPLIES = {"ArcGIS Pro": ("ArcGIS", "ArcMap"), "ArcGIS": ("ArcMap",)}
# How much a skill the candidate lacks weighs against the ones they have, by how the posting asks for it.
MISSING_SKILL_FACTOR = {"required": 1.5, "preferred": 0.5}

# Work authorisation. Three kinds of wording, kept apart because they behave differently (geojobbot/matching/matcher.py):
#   SPONSORSHIP_REFUSED     the employer says it will not sponsor: rejects, and cancels any "offer" wording elsewhere
#   WORK_AUTH_DEMANDED      an existing right to work is demanded: rejects unless sponsorship is offered
#   CITIZENSHIP_RESTRICTED  citizenship, nationals-only, permanent residency, security clearance: rejects unless the
#                           sentence waives it ("is not required", "desirable") or is inclusive ("and non-EU nationals")
# None of them applies to a job in MatchConfig.home_countries. Patterns run on the accent-folded, lowercased text.
# Every sentence that ever misled these rules is a row in tests/unit/test_wording_golden.py: add one before changing a rule.
_PLACE = (r"(?:the |a |an |this |that |their |your |our )?(?:u\.?s\.?a?\b|u\.?k\b|eu\b|e\.u\.|united|europe\w*|canada|australia|new zealand|"
          r"ireland|germany|france|belgium|netherlands|switzerland|denmark|sweden|norway|singapore|country|countries|uae|emirates|"
          r"saudi|ksa|qatar|kuwait|oman|bahrain|schengen|region|location|jurisdiction)")
SPONSORSHIP_REFUSED = [
    r"\b(?:no|not|unable to|cannot|can not|can.?t|will not|won.?t|does not|do not|don.?t|doesn.?t|isn.?t able to|is not able to|are not able to) "
    r"(?:currently |be able to |be providing |be offering |offer |provide |consider |support )?(?:any |visa |immigration |employment |work )?sponsor",
    r"\bwithout (?:the need for |need of |requiring |current or future )?(?:visa |employer |employment )?sponsorship\b",
    r"\bsponsorship (?:is|will|can|could)(?: not|n.?t)\b", r"\bsponsorship (?:is |are )?(?:not |un)(?:available|offered|provided|possible)\b",
    r"\b(?:not eligible|ineligible) for (?:[a-z0-9-]+ ){0,3}(?:support|sponsorship|assistance)\b",
    r"\bno (?:visa|immigration|work permit) (?:support|assistance|sponsorship)\b",
    r"\b(?:visa|immigration) (?:support|assistance|sponsorship) (?:is |will be |are |can ?)?(?:not\b|unavailable)",
    r"\b(?:unable|not able|not in a position) to (?:provide|offer|support|assist with) (?:any )?(?:visa|immigration|work permit)\b",
    r"\b(?:require|requires|requiring|need|needs|needing) (?:visa |employer |employment )?sponsorship (?:[a-z]+ ){0,8}?(?:will|can) ?not be considered\b",
    r"\b(?:cannot|can not|can.?t|unable to|do not|does not|don.?t|doesn.?t|will not|won.?t) (?:currently )?(?:offer|provide|support) "
    r"(?:any )?(?:visa|immigration|work permit)\b",
    r"\bmust not (?:require|need) (?:any )?(?:visa |immigration |employment |work )?(?:sponsorship|support)\b",
    r"\b(?:visa|immigration) (?:support|assistance|sponsorship)\s*[:\-]\s*(?:not\b|no\b|none\b|unavailable)",
    r"\bsponsorship (?:is )?(?:available|offered|provided)\s*[:?\-]\s*(?:no|non|none|n/a)\b",
    r"\b(?:sans|pas de|aucun) parrainage\b", r"\bne (?:parraine|parrainons|parrainent) pas\b",
]
WORK_AUTH_DEMANDED = [
    r"\b(?:must|need|needs|required?|expected) (?:to )?(?:be |have |hold |possess )?(?:currently |already )?(?:legally )?"
    r"(?:authori[sz]ed|entitled|permitted|allowed) to (?:work|live and work)\b",
    r"\b(?:must|need|needs|required?|expected) (?:to )?(?:be )?(?:currently |already )?(?:legally )?(?:eligible|able) to "
    r"(?:legally |lawfully )?(?:work|live and work) (?:legally |lawfully )?in " + _PLACE,
    # "right to work in the UK", but not the identity check every employer must run ("proof of your right to work")
    r"(?<!proof of )(?<!evidence of )(?<!proof of your )(?<!evidence of your )(?<!confirm your )(?<!verify your )(?<!verification of )"
    r"\b(?:legal |unrestricted |existing |current |valid |permanent )?(?:authori[sz]ation|right|rights|eligibility|permission|entitlement) "
    r"to (?:work|live and work) in " + _PLACE,
    r"\b(?:must|have to|need to|required to|should) (?:already )?(?:have|hold|possess) (?:a |an )?(?:valid |current |existing )?"
    r"(?:work|employment) (?:permit|visa|authori[sz]ation)\b",
    r"\b(?:valid|current|existing) (?:work|employment) (?:permit|visa|authori[sz]ation) (?:is |are )?(?:required|needed|essential|mandatory)\b",
    r"\bopen work permit\b",
    r"\b(?:autorisation|permis) de travail (?:valide |en cours de validite )?(?:exige|requis|obligatoire|indispensable)\w*",
    r"\btitre de sejour (?:valide |en cours de validite )?(?:exige|requis|obligatoire|indispensable)\w*",
    r"\bautorise\w* a travailler (?:en|au|aux|sur le territoire) ", r"\bresiden(?:t|ts|ce) permanent(?:s|e)?\b",
    # "Employment is limited to candidates authorized to work in the United States", "only applicants eligible to work ..."
    r"\b(?:limited|restricted|reserved|open only|only open) to (?:candidates|applicants|individuals|persons|people|those) "
    r"(?:who are |that are )?(?:currently |already )?(?:legally )?(?:authori[sz]ed|eligible|permitted|entitled) to work\b",
    r"\bonly (?:candidates|applicants|individuals|persons|those) (?:who are |that are )?(?:currently |already )?(?:legally )?"
    r"(?:authori[sz]ed|eligible|permitted|entitled) to work\b",
    # a requirement bullet or label without "must": "- Eligible to work in Germany", "Work eligibility: authorized to work
    # in the US". Line-anchored (matcher.py runs (?m) patterns on the text that keeps line breaks), so that "we help you
    # become eligible to work in Germany" in a sentence never counts.
    r"(?m)(?:^|: )(?:[-*\u00b7]\s*)?(?:be |being )?(?:currently |already )?(?:legally )?(?:eligible|authori[sz]ed|entitled|permitted) "
    r"to (?:legally |lawfully )?(?:work|live and work) in " + _PLACE,
]
CITIZENSHIP_RESTRICTED = [
    r"\b(?:u\.?s\.?a?\.?|american|canadian|british|uk|australian|eu|german|french|dutch) (?:citizens?|citizenship|nationals?)\b",
    # jobs reserved for a country's own nationals (Gulf nationalisation programmes)
    r"\b(?:saudi|emirati|uae|qatari|kuwaiti|omani|bahraini|gcc) nationals?\b", r"\bnationals? only\b",
    r"\b(?:part of|under|in line with|as per|supports?|supporting) (?:our |the |a )?(?:saudi[sz]ation|emirati[sz]ation|omani[sz]ation|qatari[sz]ation)\b",
    r"\b(?:saudi[sz]ation|emirati[sz]ation|omani[sz]ation|qatari[sz]ation) (?:programme|program|initiative|role|position|hire|hiring|candidate|requirement|quota|drive)\b",
    r"\b(?:for|only|open to) (?:saudis|emiratis|qataris|kuwaitis|omanis|bahrainis)\b", r"\b(?:saudis|emiratis) only\b",
    r"لل(?:سعوديين|مواطنين|اماراتيين|قطريين|كويتيين|عمانيين) فقط", r"(?:سعودي|اماراتي|قطري|كويتي|عماني|بحريني)(?:ة)? الجنسية",
    r"(?<!pathway to )(?<!path to )(?<!route to )(?<!leading to )(?<!towards )\bpermanent residen(?:t|ts|cy|ce)\b", r"\bgreen card\b",
    r"\b(?:security|secret|top secret|government|dv|sc|baseline) clearance\b", r"\bts/sci\b",
    # US federal "Public Trust" positions: a suitability investigation reserved, in practice, for US citizens
    r"\bpublic trust (?:clearance|position|investigation|background|suitability|determination|eligibility)\b",
    r"\b(?:obtain|hold|maintain|requires?|eligible for|able to pass) (?:a |an )?(?:active |current )?public trust\b",
    r"\bcitoyennete (?:canadienne|francaise|americaine)\b", r"\bcitoyens? (?:canadiens?|francais)\b",
    r"\bhabilitation (?:de securite|secret)\b",
]
WORK_AUTH_REQUIRED = SPONSORSHIP_REFUSED + WORK_AUTH_DEMANDED + CITIZENSHIP_RESTRICTED  # kept for callers that want them all
# Wording that only asks for the right to work where the candidate already lives (remote-from-anywhere roles).
WORK_AUTH_COMPATIBLE = [
    r"\b(?:authori[sz]ed|eligible|entitled|permitted|able|allowed|right) to work in (?:their|your|the|his|her) "
    r"(?:own )?(?:country|location|place|jurisdiction) of residence\b",
    r"\bwork (?:from |in )?(?:the )?country (?:where|in which) (?:you|they) (?:live|reside|are based)\b",
    r"\bautoris\w+ (?:a|de) travailler dans (?:votre|son|leur) pays de residence\b",
]
# An offer needs a visa-type object: "we will sponsor your licence" and "relocation assistance" are not visa sponsorship.
SPONSORSHIP_OFFERED = [
    r"\b(?:visa |work permit |immigration )?sponsorship (?:is |will be |can be |may be )?(?:available|offered|provided|possible|considered)\b",
    r"\b(?:will|can|able to|happy to|willing to|open to|may) (?:offer |provide |consider |assist with )?"
    r"(?:(?:visa |work permit |immigration )sponsor(?:ship|ing)?|sponsorship)\b",
    r"\b(?:we |will |can |able to |happy to |willing to |may )sponsor (?:your |a |an |the |work |skilled worker |h-?1b |tier 2 )*"
    r"(?:visas?|work permits?|international (?:candidates|applicants)|the right candidate)\b",
    r"\bvisa (?:support|assistance|sponsorship and relocation)\b", r"\brelocation (?:and|&|\+) visa\b",
    r"\bimmigration (?:assistance|support|package) (?:is )?(?:available|offered|provided)\b",
    r"\b(?:work|employment|residence) (?:visa|permit)s? (?:and [a-z ]{3,30} )?(?:is |are |will be )?(?:provided|arranged|sponsored|covered)\b",
    r"\b(?:provides?|arranges?|sponsors?|covers?) (?:a |an |the |your )?(?:work|employment|residence) (?:visa|permit)\b",
    r"\b(?:employer[- ])?sponsored (?:[a-z0-9]+ ){0,2}visa\b",
    r"\b(?:open to|welcome|consider(?:ed)?|accept)\b[^.;\n]{0,80}\b(?:need|require)s? (?:visa )?sponsorship\b",
    r"\b(?:support|help|assist|accompagn\w+) (?:you )?(?:with|dans) (?:your |the |l.obtention de )?(?:votre )?(?:work permit|visa|autorisation de travail|titre de sejour)\b",
    r"\bparrainage (?:de )?visa (?:disponible|offert|possible)\b", r"\b(?:aide|soutien) (?:a l.|pour l.)?immigration\b",
]


@dataclass
class MatchConfig:
    high_threshold: int = 70
    medium_threshold: int = 55
    preferred_locations: list[str] = field(default_factory=list)
    accepted_remote_scopes: list[str] = field(default_factory=list)
    strict_location: bool = False
    extra_negative_titles: list[str] = field(default_factory=list)
    title_only_min_points: int = 34
    exclude_work_auth_required: bool = True
    home_countries: list[str] = field(default_factory=lambda: ["Tunisia"])  # no work authorisation needed there
    my_skills: list[str] = field(default_factory=list)  # empty = CANDIDATE_SKILLS
    my_years: int = CANDIDATE_YEARS
