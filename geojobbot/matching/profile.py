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

# Title terms that are geospatial on their own (used for inverted/variant titles such as
# "Analyst, GIS" or "Senior Geospatial Data Scientist").
GEO_TITLE_TERMS = [
    r"\bgis\b", r"\bgeo-?spatial\b", r"\bgeomatic\w*", r"\blidar\b", r"\bcartograph\w*", r"\bphotogrammetr\w*",
    r"\bremote\s+sensing\b", r"\bpoint[\s-]?cloud\b", r"\bcadastr\w*", r"\bsurvey(?:or|ing)\b",
    r"\bsurvey\s+(?:technician|tech|cad|crew|party|assistant)\b", r"\bland\s+(?:information|tenure|records)\b",
    r"\bmineral\s+tenure\b", r"\bspatial\s+(?:data|analyst|analysis|information|scientist)\b",
    r"\b(?:mapping|mapper)\b", r"\bmap\s+(?:draft\w*|technician|editor|maker|production)\b",
    r"\bgeographic information\b", r"\bgeodes\w*", r"\bhydrograph\w*",
    r"\bearth observation\b", r"\bgeodata\b",
    # French (titles are accent-folded before these run)
    r"\bsig\b", r"\bgeomatiq\w*", r"\bgeomaticien\w*", r"\btopograph\w*", r"\bgeometres?\b", r"\bteledetection\b",
    r"\bgeodesien\w*", r"\bsystemes? d.information geographique", r"\barpent\w*",
    # Arabic
    r"نظم (?:ال)?معلومات (?:ال)?جغرافي", r"مساح", r"جيوماتكس", r"استشعار عن بعد", r"خرائط",
]

# Weak title terms: words that are geospatial in a geomatics context but common elsewhere ("Quantity
# Surveyor", "Marine Surveyor", market-research "Survey ..." roles, business "Mapping"). A title whose only
# geospatial signal is one of these must be backed by SURVEY_EVIDENCE or another geospatial family in the
# description, and gets no title-only benefit of the doubt unless it is an explicit DIRECT/ADJACENT role.
WEAK_GEO_TITLE_TERMS = [
    r"\bsurvey(?:or|ors|ing|s)?\b", r"\b(?:mapping|mapper)\b", r"\btopograph\w*", r"\bgeometres?\b", r"\barpent\w*",
    r"مساح",
]
SURVEY_EVIDENCE = (
    r"\b(?:gnss|gps|rtk|total\s+stations?|stations?\s+totales?|th[ée]odolite|levell?ing|nivellement|bornage|"
    r"(?:land|topographic|cadastral|geodetic|boundary|hydrographic|construction|engineering|legal|mine|as-?built)\s+survey\w*|"
    r"lev[ée]s?\s+(?:topographiques?|de\s+terrain)|control\s+points?|stake-?out|point\s+clouds?|"
    r"civil\s*3d|covadis|trimble|leica|topcon|cadastr\w*)\b"
)

ROLE_NOUNS = (
    r"analyst|specialist|technician|tech|officer|coordinator|developer|engineer|scientist|manager|lead|"
    r"processor|editor|consultant|intern|assistant|administrator|drafter|designer|operator|mapper|associate|"
    r"programmer|architect|professional|surveyor|expert|drafts(?:man|woman|person)|"
    # French role nouns
    r"ingenieur|technicien(?:ne)?|analyste|chargee?|cartographe|topographe|geometre|geomaticien(?:ne)?|"
    r"dessinat(?:eur|rice)|operat(?:eur|rice)|responsable|specialiste|developpeu(?:r|se)|administrat(?:eur|rice)|"
    r"stagiaire|geodesien|arpenteur|chef de projet|"
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
]
# Negative titles that are overridden when the title itself is clearly geospatial
# (e.g. "GIS Architect" or "Enterprise Geospatial Architect").
NEGATIVE_OVERRIDABLE = {"Architect", "Architecte"}

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
    Term("Google Earth Engine", 3, (r"\bgoogle\s+earth\s+engine\b", r"\bgee\b(?=.{0,40}\b(?:imagery|satellite|remote))"),
         family="remote_sensing"),
    Term("ENVI/eCognition", 3, (r"\benvi\b", r"\becognition\b", r"\berdas\b"), family="remote_sensing"),
    Term("Pix4D", 3, (r"\bpix4d\w*",), family="photogrammetry_tools"),
    Term("Global Mapper", 3, (r"\bglobal\s+mapper\b",), family="geo_desktop"),
    Term("CloudCompare/LAStools", 3, (r"\bcloudcompare\b", r"\blastools\b", r"\bpdal\b"), family="lidar_tools"),
    Term("Web mapping libraries", 3, (r"\bleaflet(?:\.js)?\b(?=.{0,60}\b(?:map|gis|spatial))", r"\bopenlayers\b",
                                      r"\bmapbox\b", r"\bgeoserver\b", r"\bcesium(?:js)?\b"), family="web_mapping"),
]

# Domain/responsibility patterns run on the raw text (case-insensitive, accents intact), so French
# variants spell out their accented letters as character classes.
DOMAIN_TERMS = [
    Term("GIS", 4, (r"\bgis\b", r"\bgeographic(?:al)?\s+information\s+systems?\b", r"\bsig\b",
                    r"\bsyst[èe]mes?\s+d.information\s+g[ée]ographique",
                    r"نظم\s+(?:ال)?معلومات\s+(?:ال)?جغرافية"), family="gis"),
    Term("Geospatial", 4, (r"\bg[ée]o-?spatial\w*",), family="gis"),
    Term("Geomatics", 5, (r"\bg[ée]omatics?\b", r"\bg[ée]omatique\b", r"\bg[ée]omaticien\w*"), family="geomatics"),
    Term("Cartography", 5, (r"\bcartograph\w*", r"خرائط"), family="cartography"),
    Term("Surveying", 4, (r"\b(?:land|topographic|cadastral|geodetic|construction|hydrographic|boundary)\s+survey\w*",
                          r"\bsurveying\b", r"\bsurveyors?\b", r"\bgnss\b", r"\btotal\s+stations?\b",
                          r"\barpentage\b", r"\barpenteur\w*", r"\bg[ée]om[èe]tres?\b",
                          r"\blev[ée]s?\s+topographiques?\b", r"\bstations?\s+totales?\b", r"المساحة|مساح"),
         family="surveying"),
    Term("Topography", 3, (r"\btopograph\w*",), family="surveying"),
    Term("Cadastral", 5, (r"\bcadastr\w*",), family="land_admin"),
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
    Term("Photogrammetry", 5, (r"\bphotogramm[ée]tr\w*", r"\bortho-?(?:photo|imagery|mosaic|rectif|image)\w*"),
         family="photogrammetry"),
    Term("Remote sensing", 5, (r"\bremote\s+sensing\b", r"\bsatellite\s+(?:imagery|images|data)\b",
                               r"\bearth\s+observation\b", r"\bmultispectral\b", r"\bhyperspectral\b",
                               r"\bt[ée]l[ée]d[ée]tection\b", r"\bimage(?:s|rie)?\s+satellit\w*",
                               r"\bobservation\s+de\s+la\s+terre\b", r"استشعار\s+عن\s+بعد"), family="remote_sensing"),
    Term("UAV", 4, (r"\buavs?\b", r"\bdrones?\b", r"\bunmanned\s+aerial\b", r"\brpas\b", r"\bsuas\b")),
    Term("Utility mapping", 5, (r"\butility\s+(?:mapping|gis|network\s+(?:data|model)|records|locat\w+)\b",
                                r"\bsubsurface\s+utilit\w+", r"\bcartographie\s+des?\s+r[ée]seaux\b",
                                r"\br[ée]seaux\s+enterr[ée]s\b"), family="utility_gis"),
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
    Term("Point cloud classification", 2, (r"\bclassif\w+\s+(?:of\s+)?(?:lidar|point)",
                                           r"\bclassification\s+(?:des?\s+)?nuages?\s+de\s+points\b"), family="lidar"),
]

# Qualifier hints run on the accent-folded sentence around a skill, so French forms are written without accents.
REQUIRED_HINTS = (r"\b(required|must|minimum|essential|mandatory|need(?:s|ed)?\s+to\s+have|proficien\w+|"
                  r"experience\s+(?:with|in|using)|exig\w+|requis\w*|obligatoire|indispensable|imperati\w+|"
                  r"maitris\w+|bonne\s+connaissance|connaissance\s+approfondie|experience\s+(?:en|avec|dans|sur))\b")
PREFERRED_HINTS = (r"\b(prefer\w*|nice[\s-]to[\s-]have|asset|desirabl\w+|bonus|plus|ideally|advantage\w*|familiarity|"
                   r"souhait\w+|atout|appreci\w+|idealement)\b")

CATEGORY_CAPS = {"title": 40, "tech": 25, "domain": 20, "responsibilities": 10, "location": 5}

# Work authorisation. Postings that demand an existing right to work, citizenship or a security
# clearance, or state that no visa sponsorship is offered, are rejected unless the job is in one of
# MatchConfig.home_countries or the posting says sponsorship is available. Patterns run on the
# accent-folded, lowercased text.
WORK_AUTH_REQUIRED = [
    r"\b(?:must|need|needs|required?|expected) (?:to )?(?:be |have |hold |possess )?(?:currently |already )?(?:legally )?"
    r"(?:authori[sz]ed|eligible|entitled|permitted|able|allowed) to (?:work|live and work)\b",
    r"\b(?:legal |unrestricted |existing |current |valid |permanent )?(?:authori[sz]ation|right|rights|eligibility|permission|entitlement) "
    r"to (?:work|live and work) in\b",
    r"\b(?:no|not|unable to|cannot|can not|will not|won.t|does not|do not|don.t|isn.t able to|is not able to) "
    r"(?:currently |be able to |offer |provide |consider )?(?:any |visa |immigration |employment |work )?sponsor",
    r"\bwithout (?:the need for |need of |requiring |current or future )?(?:visa |employer )?sponsorship\b",
    r"\bsponsorship (?:is|will) (?:not|unavailable)|\bsponsorship (?:is )?not (?:available|offered|provided|possible)\b",
    r"\bnot eligible for (?:any )?(?:visa|immigration|employment|work)? ?(?:visa )?(?:support|sponsorship|assistance)\b",
    r"\bno (?:visa|immigration|work permit) (?:support|assistance|sponsorship)\b",
    r"\b(?:visa|immigration) (?:support|assistance|sponsorship) (?:is |will be |are )?(?:not|unavailable)\b",
    r"\b(?:unable|not able|not in a position) to (?:provide|offer|support|assist with) (?:any )?(?:visa|immigration|work permit)\b",
    r"\b(?:u\.?s\.?a?\.?|american|canadian|british|uk|australian|eu|german|french|dutch) (?:citizens?|citizenship|nationals?)\b",
    # jobs reserved for a country's own nationals (Gulf nationalisation programmes)
    r"\b(?:saudi|emirati|uae|qatari|kuwaiti|omani|bahraini|gcc) nationals?\b", r"\bnationals? only\b",
    r"\b(?:saudi[sz]ation|emirati[sz]ation|omani[sz]ation|qatari[sz]ation|nitaqat|tawteen)\b",
    r"\b(?:for|only|open to) (?:saudis|emiratis|qataris|kuwaitis|omanis|bahrainis)\b", r"\b(?:saudis|emiratis) only\b",
    r"\bpermanent residen(?:t|ts|cy|ce)\b", r"\bgreen card\b",
    r"\b(?:security|secret|top secret|government|dv|sc|baseline) clearance\b", r"\bts/sci\b", r"\bclearance (?:is )?required\b",
    r"\b(?:valid |current )?(?:work|employment) (?:permit|visa|authori[sz]ation)\b", r"\bopen work permit\b",
    # French
    r"\b(?:autorisation|permis) de travail\b", r"\bcitoyennete (?:canadienne|francaise|americaine)\b",
    r"\bcitoyens? (?:canadiens?|francais)\b", r"\bresiden(?:t|ts|ce) permanent(?:s|e)?\b",
    r"\b(?:sans|pas de|aucun) parrainage\b", r"\bne (?:parraine|parrainons|parrainent) pas\b",
    r"\bhabilitation (?:de securite|secret)\b",
]
# Wording that only asks for the right to work where the candidate already lives (remote-from-anywhere roles).
WORK_AUTH_COMPATIBLE = [
    r"\b(?:authori[sz]ed|eligible|entitled|permitted|able|allowed|right) to work in (?:their|your|the|his|her) "
    r"(?:own )?(?:country|location|place|jurisdiction) of residence\b",
    r"\bwork (?:from |in )?(?:the )?country (?:where|in which) (?:you|they) (?:live|reside|are based)\b",
    r"\bautoris\w+ (?:a|de) travailler dans (?:votre|son|leur) pays de residence\b",
]
SPONSORSHIP_OFFERED = [
    r"\b(?:visa |work permit |immigration )?sponsorship (?:is |will be |can be |may be )?(?:available|offered|provided|possible|considered)\b",
    r"\b(?:we |company |employer )?(?:will|can|able to|happy to|willing to|open to|may) (?:offer |provide |consider |assist with )?"
    r"(?:visa |work permit |immigration )?sponsor(?:ship|ing)?\b",
    r"\bwe sponsor\b", r"\bvisa (?:support|assistance|sponsorship and relocation)\b", r"\brelocation (?:and|&|\+) visa\b",
    r"\b(?:relocation|immigration) (?:assistance|support|package) (?:is )?(?:available|offered|provided)\b",
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
