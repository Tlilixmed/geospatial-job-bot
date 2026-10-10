"""Golden table for the matching taxonomy (geojobbot/matching/profile.py).

Every row is a sentence or title that a term must read one way: matched, not matched, or a title kind. The negatives
are the false positives found while building the terms on a live corpus of 1,600 postings (2026-10-10) and by an
independent review: Leica cameras, metrology theodolites, "Salary TBC", museum "récolement", store "implantation",
finance "COGS", the Office of General Counsel, "مساحة" meaning floor area. Add a row BEFORE changing a pattern.
"""
import pytest

from geojobbot.matching import matcher as M


def terms(text: str) -> set[str]:
    r = M.score_job("", text, {})
    return {h.canonical for h in r.skills + r.domains + r.responsibilities}


HITS = [  # (text, canonical that must be found)
    # survey office software
    ("GNSS processing (Trimble Business Center or similar)", "Survey office software"),
    ("Proficient in Trimble TBC and Trimble Access", "Survey office software"),
    ("TBC, 12d Model and Civil 3D", "Survey office software"),
    ("Exposure to 12d Model/12d Field highly regarded", "Survey office software"),
    ("levés et dessins sous Autocad Mensura", "Survey office software"),
    ("vous maitrisez l'utilisation des logiciels AUTOCAD, MENSURA et COVADIS", "Survey office software"),
    ("Carlson Survey and SurvCE data collection", "Survey office software"),
    ("Leica Infinity post-processing", "Survey office software"),
    ("Topcon Magnet Office for adjustments", "Survey office software"),
    # survey instruments
    ("matériel GNSS Trimble et stations totales", "Survey instruments"),
    ("station totale Trimble", "Survey instruments"),
    ("Experience gathering GPS data with Trimble GPS hardware", "Survey instruments"),
    ("A plus if you have experience with GIS systems, using RTK rovers", "Survey instruments"),
    ("Using surveying equipment (e.g., total stations, GPS, theodolites)", "Survey instruments"),
    ("appareils de topographie (Niveau, Théodolite, GPS)", "Survey instruments"),
    ("utilisation du tachéomètre", "Survey instruments"),
    ("Leica laser scanners and Sokkia digital levels", "Survey instruments"),
    ("Topcon GPS receivers for the site team", "Survey instruments"),
    # French topographic works
    ("Le bornage fera partie de vos responsabilités", "French topographic works"),
    ("plans d'execution, plans de recolement", "French topographic works"),
    ("Réaliser des plans précis (Topo, Exe, Recollement)", "French topographic works"),
    ("Implantation des ouvrages (batiment, voirie)", "French topographic works"),
    ("implantation précise des axes", "French topographic works"),
    ("Effectuer les implantations sur chantier", "French topographic works"),
    ("Réaliser des travaux d'implantation en génie civil", "French topographic works"),
    ("Réalisation de métrés, quantitatifs, cubatures en 3D", "French topographic works"),
    ("profils en long et en travers", "French topographic works"),
    ("Mise en place du canevas polygonal et nivellement", "French topographic works"),
    ("levés de certificats de localisation, de piquetage", "French topographic works"),
    # network detection (DT-DICT)
    ("détection de réseaux enterrés en classe A par méthode électromagnétique", "Network detection (DT-DICT)"),
    ("Teledetection et gestion DT-DICT", "Network detection (DT-DICT)"),
    ("Detecter les reseaux enterres avec un georadar", "Network detection (DT-DICT)"),
    ("géoréférencement des réseaux au GPS", "Network detection (DT-DICT)"),
    ("mise à jour du PCRS", "Network detection (DT-DICT)"),
    ("utility locating with ground penetrating radar", "Network detection (DT-DICT)"),
    ("détection des réseaux enterrés pour la sécurité des chantiers", "Network detection (DT-DICT)"),
    ("Détection de réseaux / Réalisation de plans de récolement", "Network detection (DT-DICT)"),
    # hydrography
    ("Multibeam and singlebeam hydrographic survey", "Hydrography / bathymetry"),
    ("processing of bathymetric lidar", "Hydrography / bathymetry"),
    ("side scan sonar and sub-bottom profilers", "Hydrography / bathymetry"),
    ("levés hydrographiques en zone portuaire", "Hydrography / bathymetry"),
    ("QINSy, CARIS,EIVA Beamworks", "Hydrographic software"),
    ("using QPS Qimera", "Hydrographic software"),
    # cloud-native formats and OGC services
    ("formats (e.g., COGs, Xarray, GDAL)", "Cloud-native geo formats"),
    ("serve geospatial data (NetCDF, Zarr, HDF, STAC, tile stores) at scale", "Cloud-native geo formats"),
    ("Develop and operate the REST and STAC APIs that serve this data", "Cloud-native geo formats"),
    ("data formats such as NITF, GeoTIFF, CADRG", "Cloud-native geo formats"),
    ("cloud-optimized formats (COG, GeoParquet, Zarr)", "Cloud-native geo formats"),
    ("Familiarity with STAC, OGC API standards", "OGC web services"),
    ("Working knowledge of OGC standards: 3D Tiles, I3S, CityGML", "OGC web services"),
    ("industry standards (OGC, STAC, COG)", "OGC web services"),
    ("publish WMS and WFS layers from GeoServer", "OGC web services"),
    ("QGIS Server and MapServer WMS endpoints", "OGC web services"),
    ("diffusion des données publiques, directive INSPIRE", "OGC web services"),
    ("INSPIRE compliant metadata", "OGC web services"),
    ("Web Map Service endpoints", "OGC web services"),
    ("vector tiles for the basemap", "OGC web services"),
    ("frontend in React with MapLibre and deck.gl", "Web mapping libraries"),
    # remote sensing missions
    ("Experience processing radar data (for example Sentinel-1)", "Remote sensing"),
    ("Sentinel-2 time series", "Remote sensing"),
    ("Copernicus Sentinel 2 imagery for crop monitoring", "Remote sensing"),
    # Arabic (vowel marks and hamza forms vary in real postings)
    ("المساح", "Surveying"),
    ("يعمل المساح في الموقع", "Surveying"),
    ("للمساحين", "Surveying"),
    ("فني المساحة", "Surveying"),
    ("مساحون", "Surveying"),
    ("مساح أراضي", "Surveying"),
    ("للعمل كمساح في الموقع", "Surveying"),
    ("ومساح أراضي", "Surveying"),
    ("بمساح", "Surveying"),
    ("مساحو الأراضي", "Surveying"),
    ("أخصائي مساحة", "Surveying"),
    ("مدير المساحة", "Surveying"),
    ("خبرة في أعمال المساحة", "Surveying"),
    ("خبرة في الأعمال المساحية", "Surveying"),
    ("الرفع المساحي بالمحطة الشاملة", "Surveying"),
    ("ديوان قيس الأراضي", "Surveying"),
    ("خبرة في المساحة التصويرية", "Photogrammetry"),
    ("الطبوغرافيا", "Topography"),
    ("المسح العقاري", "Cadastral"),
    ("خبرة في نظم المعلومات الجغرافية", "GIS"),
    ("خبرة في أنظمة المعلومات الجغرافيه", "GIS"),
    ("نظام المعلومات الجغرافي", "GIS"),
    ("الاستشعار عَنْ بُعْد", "Remote sensing"),
    ("الإستشعار عن بعد", "Remote sensing"),
    ("إنتاج الخرائط", "Cartography"),
    ("الجغرفة الرقمية", "Cartography"),
]

MISSES = [  # (text, canonical that must NOT be found)
    ("Salary TBC, depending on experience. Survey of benefits", "Survey office software"),
    ("Location TBC, Survey department", "Survey office software"),
    ("Start date TBC - surveying team", "Survey office software"),
    ("Proficient in AutoCAD, start date TBC", "Survey office software"),
    ("Salary TBC, AutoCAD experience essential", "Survey office software"),
    ("Salary: TBC, AutoCAD skills required", "Survey office software"),
    ("Location: TBC / AutoCAD required", "Survey office software"),
    ("Start date TBC, Civil 3D desirable", "Survey office software"),
    ("Votre bien-être au travail est suivi par Mensura, notre service externe de prévention", "Survey office software"),
    ("Leica Camera AG drives total sales growth", "Survey instruments"),
    ("Stonex is hiring; total compensation is competitive", "Survey instruments"),
    ("metrology with laser trackers, theodolites or CMM", "Survey instruments"),
    ("interferometers, theodolite/laser tracker, optical alignment", "Survey instruments"),
    ("Integrate RTK GPS for precise landing of the drone", "Survey instruments"),
    ("Leica microscopes for the histology lab", "Survey instruments"),
    ("Fleet tracked with Trimble GPS units", "Survey instruments"),
    ("Trimble is a leader in GPS fleet management and telematics for trucking", "Survey instruments"),
    ("implantation de nouveaux points de vente", "French topographic works"),
    ("implantation de magasins en centre-ville", "French topographic works"),
    ("implantation commerciale sur le terrain", "French topographic works"),
    ("Implantation d'un ERP sur les chantiers", "French topographic works"),
    ("implantation des réseaux de distribution commerciale", "French topographic works"),
    ("refuser le nivellement par le bas", "French topographic works"),
    ("récolement des collections du musée", "French topographic works"),
    ("travaux de récolement décennal des collections", "French topographic works"),
    ("implantation de la nouvelle agence commerciale", "French topographic works"),
    ("travaux de récolement de la collection du musée et du fonds", "French topographic works"),
    ("récolement du fonds patrimonial du musée", "French topographic works"),
    ("Implantation de boîtiers GPS dans les véhicules", "French topographic works"),
    ("implantation sur le terrain de nos magasins", "French topographic works"),
    ("Implantation des méthodes agiles", "French topographic works"),
    ("Bâtiment de classe A, gestion des réseaux sociaux", "Network detection (DT-DICT)"),
    ("bâtiment de classe A et réseaux sociaux", "Network detection (DT-DICT)"),
    ("Analyste SOC: détection réseau et sondes", "Network detection (DT-DICT)"),
    ("détection des réseaux informatiques malveillants", "Network detection (DT-DICT)"),
    ("Supervision et détection réseaux, analyse des logs", "Network detection (DT-DICT)"),
    ("Développement d'une solution de détection réseaux pour notre SOC", "Network detection (DT-DICT)"),
    ("Microsoft Sentinel 2 ans minimum et la détection réseaux (NDR)", "Network detection (DT-DICT)"),
    ("Formateur réseaux et systèmes : chaque classe a 12 élèves", "Network detection (DT-DICT)"),
    ("PCRs and qPCR assays", "Network detection (DT-DICT)"),
    ("phased array antennas with multibeam steering", "Hydrography / bathymetry"),
    ("multi-beam laser alignment of the payload", "Hydrography / bathymetry"),
    ("Design multi-beam phased array antennas for high data rate links", "Hydrography / bathymetry"),
    ("multibeam antenna data for 5G", "Hydrography / bathymetry"),
    ("bassin hydrographique de la Loire", "Hydrography / bathymetry"),
    ("l'hydrographie du bassin versant", "Hydrography / bathymetry"),
    ("Drive down COGS for our satellite imagery products", "Cloud-native geo formats"),
    ("Reduce COGS across raster data delivery", "Cloud-native geo formats"),
    ("Speak at the STAC Summit. Our imagery team", "Cloud-native geo formats"),
    ("OME-Zarr microscopy volumes", "Cloud-native geo formats"),
    ("Customs Specialist: ERP, WMS and TMS records", "OGC web services"),
    ("work with the OGC (Office of General Counsel) on services agreements", "OGC web services"),
    ("World Class Safety (WCS) programme and Kronos WFS", "OGC web services"),
    ("Experience with WMS and WFS (Kronos) systems", "OGC web services"),
    ("WMS, WFS and TMS platforms", "OGC web services"),
    ("Inspire our customers with services they love", "OGC web services"),
    ("LEAD AND INSPIRE\n- Manage and mentor a team", "OGC web services"),
    ("Microsoft Sentinel and Splunk detections", "Remote sensing"),
    ("Monitor alerts in Microsoft Sentinel 3+ years experience required", "Remote sensing"),
    ("Azure Sentinel 2 years minimum", "Remote sensing"),
    ("SIEM: Splunk or Sentinel 1 day per week on call", "Remote sensing"),
    ("Hospital Infection surveillance, Sentinel 3 sites", "Remote sensing"),
    ("ICEYE builds SAR imagery products", "Remote sensing"),
    ("Leaflet. Our map team", "Web mapping libraries"),
    ("GEE. Satellite team", "Google Earth Engine"),
    ("مصمم مساحات داخلية", "Surveying"),
    ("مساحة أرضية 300 متر مربع", "Surveying"),
    ("مساحة بحرية واسعة", "Surveying"),
    ("بمساحة 300 متر", "Surveying"),
    ("مدير مساحة العمل المشتركة", "Surveying"),
    ("خرائط الطريق للمنتج", "Cartography"),
    ("السجل العقاري", "Cadastral"),
]

TITLES = [  # (title, classify_title kind)
    ("GIS Technologist", "geo_title"),
    ("Geomatics Technologist", "geo_title"),
    ("Technologue en géomatique", "geo_title"),
    ("Hydrographer", "geo_title"),
    ("Photogrammetrist", "geo_title"),
    ("Hydrographer/Marine Surveyor", "geo_title"),
    ("Marine Surveyor (Bathymetric)", "geo_title"),
    ("Aide Opérateur détection de réseaux", "geo_title"),
    ("Chargé de Détection réseaux électriques", "geo_title"),
    ("Technicien détection de réseaux", "geo_title"),
    ("Technicien en géodétection", "geo_title"),
    ("Analyste détection réseaux informatiques", "generic"),
    ("Ingénieur Sécurité - Détection Réseaux", "generic"),
    ("Ingénieur détection réseaux sécurité", "generic"),
    ("المساح", "direct"),
    ("مساحون", "direct"),
    ("مساح أراضي", "direct"),
    ("مطلوب مساحين", "direct"),
    ("فني المساحة", "direct"),
    ("فني مساحة", "direct"),
    ("مهندس المساحة", "direct"),
    ("أخصائي مساحة", "geo_title"),
    ("مدير المساحة", "geo_term"),
    ("مدير مساحة", "geo_term"),
    ("مسؤول مساحة", "geo_term"),
    ("مراقب مساحة", "geo_term"),
    ("مهندسة مساحة", "geo_term"),
    ("مهندس طبوغرافيا", "geo_title"),
    ("مهندس قيس الأراضي", "geo_title"),
    ("أخصائي أنظمة المعلومات الجغرافية", "geo_title"),
    ("أخصائي الجغرفة الرقمية", "geo_title"),
    ("رسام خرائط", "direct"),
    ("مصمم مساحات داخلية", "none"),
    ("مدير مساحة العمل", "none"),
    ("مدير مساحة المكتب", "none"),
    ("مساحه", "none"),
    ("مصمم مساحه داخليه", "none"),
    ("مندوب مبيعات مساحيق", "none"),
    ("مدير خرائط الطريق", "none"),
]

NEGATIVE_TITLES = ["Marine Surveyor", "Senior Marine Surveyor (Cargo)", "Quantity Surveyor"]

FILLER = (" The role reports to the operations manager, works across several sites and offers a full-time contract, "
          "a company car and the usual benefits. We value safety, teamwork and clear communication with clients.")


def pad(text: str) -> str:
    """Weak-title and gate rules only look at a real description (300 characters or more)."""
    return text + FILLER * 3


# Generic titles need two geospatial families. A new pattern adds a family, so each incidental mention below, with
# one other family already present (a "geospatial" company introduction), must leave the posting rejected.
INCIDENTAL = [  # (title, description)
    ("Staff Software Engineer", "ICEYE operates the largest SAR constellation; our SAR imagery and SAR data serve "
                                "geospatial intelligence customers. You will run CI/CD pipelines on Kubernetes."),
    ("Embedded Software Engineer", "Integrate RTK GPS for precise landing of our drones. Our geospatial customers "
                                   "rely on the flight controller you write in C++."),
    ("Security Engineer", "Build detections in Microsoft Sentinel and Splunk for a geospatial software company."),
    ("Compliance Specialist", "Work with the OGC (Office of General Counsel) on services agreements for our geospatial "
                              "business."),
    ("Customs Specialist", "Keep ERP, WMS and TMS records accurate for a geospatial hardware company."),
    ("Financial Analyst", "Reduce COGS across our data delivery for a geospatial analytics company."),
    ("AIT Engineer", "Earth observation satellites: metrology with laser trackers, theodolites or CMM."),
    ("Technicien travaux", "Suivi des chantiers d'eau potable, déclarations DT-DICT et relation avec les entreprises."),
    # found by review: each one opened the gate before its fix
    ("Engineer", "Start date TBC, Civil 3D desirable; GIS knowledge."),
    ("Security Analyst", "Monitor alerts in Microsoft Sentinel 3+ years experience required. Our geospatial platform team."),
    ("Analyste SOC", "Microsoft Sentinel 2 ans minimum et la détection réseaux (NDR)."),
    ("RF Engineer", "Design multi-beam phased array antennas for high data rate links. Process satellite data from our "
                    "constellation."),
    ("Engineer", "Our satellites provide multi-beam coverage and earth observation data products."),
    ("Logistics Analyst", "Experience with WMS and WFS (Kronos) systems. Fleet tracked with Trimble GPS units."),
]


@pytest.mark.parametrize("text,canonical", HITS)
def test_term_hits(text, canonical):
    assert canonical in terms(text), text


@pytest.mark.parametrize("text,canonical", MISSES)
def test_term_misses(text, canonical):
    assert canonical not in terms(text), text


@pytest.mark.parametrize("title,kind", TITLES)
def test_title_kinds(title, kind):
    assert M.classify_title(title)[0] == kind, title


@pytest.mark.parametrize("title", NEGATIVE_TITLES)
def test_plain_marine_surveyor_stays_negative(title):
    assert M.classify_title(title)[3] is not None, title


@pytest.mark.parametrize("title,description", INCIDENTAL)
def test_incidental_terms_keep_generic_titles_out(title, description):
    r = M.score_job(title, pad(description), {})
    assert r.tier == "rejected" and M.INSUFFICIENT_GEOSPATIAL_SIGNALS in r.rejection_reasons, (title, r.geo_families)


def test_incidental_geotiff_opens_the_gate_but_not_an_alert():
    # GeoTIFF is a geospatial format wherever it appears, so it counts as a family: the score keeps the role out
    r = M.score_job("DevOps Engineer", pad("Run the storage that serves GeoTIFF downloads to our geospatial customers. "
                                           "Kubernetes, Terraform and AWS."), {})
    assert r.tier == "rejected" and M.LOW_SCORE in r.rejection_reasons


def test_french_topographe_posting_reaches_possible():
    # Actual (Le Mans), 2026-10-10: scored 54 and was rejected while every line is surveying work
    text = ("Vous interviendrez sur des projets de voirie et réseaux divers. Votre mission principale consiste à réaliser "
            "des plans précis (Topo, Exe, Recollement, Assainissements, Terrassement) via le logiciel Mensura.\n"
            "Vous serez en charge des relevés topographiques et des implantations sur le terrain en utilisant du\n"
            "matériel de haute technologie, notamment des équipements GNSS Trimble et Galaxéo,\n"
            "ainsi que des stations Trimble et Leica. Le bornage fera également partie intégrante de vos\n"
            "responsabilités. Votre expertise repose sur une maîtrise parfaite des outils de mesure modernes tels que "
            "le théodolite, le scanner 3D et les stations totales, ainsi qu'une excellente aisance avec les logiciels de\n"
            "CAO/DAO comme AutoCAD ou Covadis.")
    r = M.score_job("Geometre topographe (H/F)", text, {"country": "France"})
    assert r.score >= 55 and r.tier in ("possible", "high"), (r.score, r.why_matched)
    assert {"Survey office software", "Survey instruments", "French topographic works"} <= {
        h.canonical for h in r.skills + r.responsibilities}


@pytest.mark.parametrize("description", [
    "Process field data in Trimble Business Center and 12d Model, then deliver the files to the design team.",
    "Réaliser les implantations sur chantier, les plans de récolement et le bornage des parcelles.",
    "Utiliser la station totale Trimble et le niveau numérique Leica sur les chantiers routiers.",
    "Operate survey-grade GPS and run RTK GPS stake-outs on construction sites.",
])
def test_survey_terms_back_a_weak_title(description):
    r = M.score_job("Surveyor", pad(description), {})
    assert M.INSUFFICIENT_GEOSPATIAL_SIGNALS not in r.rejection_reasons, r.rejection_reasons


@pytest.mark.parametrize("description", [
    "Sell Leica microscopes and Topcon eye-care devices to hospitals across the region.",
    "Align the payload optics with theodolites, laser trackers and CMM in the metrology lab.",
    "Trimble Transportation fleet software rollout for our trucking customers.",
    "Trimble is a leader in GPS fleet management and telematics for trucking.",
])
def test_brand_names_alone_do_not_back_a_weak_title(description):
    r = M.score_job("Mapping Specialist", pad(description), {})
    assert M.INSUFFICIENT_GEOSPATIAL_SIGNALS in r.rejection_reasons


@pytest.mark.parametrize("description", [
    "Salary TBC, AutoCAD experience essential for the role.",
    "Location: TBC / AutoCAD required.",
])
def test_to_be_confirmed_does_not_back_a_surveyor_title(description):
    r = M.score_job("Surveyor", pad(description), {})
    assert M.INSUFFICIENT_GEOSPATIAL_SIGNALS in r.rejection_reasons


def test_arabic_weak_title_reads_a_vowelled_description():
    # the weak-title check reads the description on its own, so it is normalised too (tashkeel, hamza forms)
    text = "يَعْمَلُ المساح في الموقع: أعمال الرفع المساحي باستخدام المحطة الشاملة وإعداد المخططات. " * 5
    r = M.score_job("مساح", text, {})
    assert M.INSUFFICIENT_GEOSPATIAL_SIGNALS not in r.rejection_reasons
    unrelated = "مطلوب مساح لمكتب استشارات يتولى إدارة العقود والتواصل مع العملاء وإعداد التقارير الشهرية. " * 5
    assert M.INSUFFICIENT_GEOSPATIAL_SIGNALS in M.score_job("مساح", unrelated, {}).rejection_reasons
