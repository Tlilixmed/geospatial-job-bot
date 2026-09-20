"""Golden table for work-authorisation and sponsorship wording.

Every row is a sentence that misled these rules at some point (found by a user report, a live audit or a code review),
with the reading it must get: (requires_existing_authorization, sponsorship_offered). Add a row BEFORE changing a rule in
geojobbot/matching/profile.py: a fix for one wording must not flip the opposite one, which is exactly what happened when
"not eligible for visa support" was fixed with a negation check that then turned real offers into rejections.
"""
import pytest
from conftest import GIS_DESCRIPTION, make_settings

from geojobbot.matching import matcher as M

CFG = make_settings().match_config()
US = {"country": "United States"}

OFFERS = [  # (False, True)
    "Visa sponsorship is available for the right candidate.",
    "We sponsor visas and offer relocation assistance.",
    "We do not discriminate on any basis. Visa support and relocation are provided.",
    "No degree required. We will sponsor your work permit.",
    "What we offer:\n- No weekend work\n- Visa sponsorship available\n- 30 days of holiday",
    "Location: Omaha, NE\nVisa sponsorship is available",
    "If you do not have the right to work in the UK, visa sponsorship is available.",
    "Not an EU citizen? No problem, we can sponsor your visa.",
    "Non-EU citizens are welcome (visa sponsorship provided).",
    "Pour les candidats non européens, parrainage de visa possible.",
    "Please no recruiters!\nWe sponsor visas for the right candidates",
    "We do not discriminate on the basis of race, religion or nationality\nVisa sponsorship is available for this role",
    "We cannot wait to meet you! Visa sponsorship is available.",
    "Benefits: employment visa, medical insurance and annual flight ticket provided by the company. Employment visa is provided.",
    "The company provides a work visa, housing allowance and transportation.",
    "Work permit and residence permit are arranged by the employer.",
    "We support you with your work permit and relocation to Berlin.",
    "Nous vous accompagnons dans l'obtention de votre autorisation de travail.",
    "Employer sponsored 482 visa with a pathway to permanent residency.",
    "Open to Australian citizens, permanent residents and candidates who need sponsorship.",
    "Sponsorship available for exceptional candidates.",
]
REFUSALS = [  # (True, False)
    "This position is not eligible for visa support.",  # the Enviva posting, listed under /sponsors by an earlier version
    "Visa sponsorship is not available for this role.",
    "Unfortunately we are unable to provide visa sponsorship at this time.",
    "We cannot offer visa assistance or relocation.",
    "No visa support will be provided.",
    "Candidates must not require immigration support now or in the future. Visa support: not available.",
    "Nous ne proposons pas de parrainage de visa disponible pour ce poste.",
    "Applicants will not be considered if they need sponsorship; we won't sponsor visas.",
    "Relocation assistance provided. This position is not eligible for visa sponsorship.",
    "Relocation assistance is available. Applicants must be authorized to work in the United States without sponsorship.",
    "Relocation assistance available for candidates within Canada only. No visa sponsorship.",
    "Visa Sponsorship Available: No",
    "Sponsorship offered: No",
    "Unfortunately we can't sponsor visas for this role.",
    "Sponsorship isn't available.",
    "Sponsorship unavailable.",
    "We regret that visa sponsorship cannot be provided.",
    "Candidates who require sponsorship now or in the future will not be considered.",
    "This role is not eligible for Skilled Worker visa sponsorship.",
    "We will not be providing sponsorship.",
]
DEMANDS = [  # (True, False): an existing right to work, citizenship, nationals only, clearance
    "Must be authorized to work in the United States.",
    "We will sponsor your professional licensure (PLS) and continuing education. Must be authorized to work in the United States.",
    "Applicants must have the right to work in the UK.",
    "You must already hold a valid work permit.",
    "Titre de séjour valide exigé.",
    "Vous devez être autorisé à travailler en France.",
    "Poste ouvert aux personnes autorisées à travailler en France.",
    "US citizenship required due to federal contract.",
    "Active Secret security clearance is required.",
    "This role is part of our Emiratisation programme, UAE nationals only.",
    "للسعوديين فقط",
    "يشترط أن يكون المتقدم سعودي الجنسية",
]
NEUTRAL = [  # (False, False): ordinary wording that says nothing about visas
    "Must be able to work outdoors in all weather conditions and carry survey equipment.",
    "Must be able to work independently and as part of a team.",
    "You need to be able to work under pressure.",
    "Candidates are expected to be able to work flexible hours.",
    "Must be eligible to work towards chartered status (MRICS).",
    "You will need the right to work in the field with minimal supervision.",
    "Hot work permit and permit to work systems experience on site.",
    "Experience with work permit applications for drone flights (SORA).",
    "Autorisation de travail en hauteur requise.",
    "Work authorization forms and work orders are processed in the GIS.",
    "The firm can sponsor FAA Part 107 certification and may sponsor conference attendance.",
    "You will sponsor and champion GIS initiatives across the business.",
    "Relocation assistance is not provided.",
    "We welcome applications from EU citizens and non-EU nationals alike.",
    "US citizenship is not required.",
    "We hire regardless of nationality: French nationals and foreigners alike.",
    "Site clearance surveys and vegetation clearance required before construction.",
    "Utility clearance required prior to excavation.",
    "No security clearance is required for this role.",
    "DV clearance desirable but not essential.",
    "Open to GCC nationals and expatriates.",
    "Knowledge of Saudization (Nitaqat) reporting dashboards in GIS.",
    "As a condition of employment you will be required to provide proof of your right to work in the UK.",
    "Employment is contingent on proof of eligibility to work in the United States (Form I-9).",
    "We participate in E-Verify and will confirm your authorization to work in the U.S.",
    "We are an equal opportunity employer and consider applicants without regard to race, national origin, citizenship or immigration status.",
    "We are an E-Verify employer.",
]


def read(sentence, location=US):
    return M.work_authorization(f"{GIS_DESCRIPTION}\n{sentence}", location, CFG)


@pytest.mark.parametrize("sentence", OFFERS)
def test_offers(sentence):
    assert read(sentence) == (False, True), sentence


@pytest.mark.parametrize("sentence", REFUSALS)
def test_refusals(sentence):
    assert read(sentence) == (True, False), sentence


@pytest.mark.parametrize("sentence", DEMANDS)
def test_demands(sentence):
    assert read(sentence) == (True, False), sentence


@pytest.mark.parametrize("sentence", NEUTRAL)
def test_neutral_wording(sentence):
    assert read(sentence) == (False, False), sentence


def test_context_rules():
    refusal = "We cannot sponsor visas."
    assert read(refusal, {"country": "Tunisia"}) == (False, False)  # at home no visa is needed
    assert read(refusal, {"country": None, "remote": True, "remote_scope": "Worldwide"}) == (False, False)  # work from anywhere
    assert read("Must be authorized to work in the United States.", {"country": None, "remote": True, "remote_scope": "Worldwide"}) == (True, False)
    both = "Visa sponsorship is available for senior roles. This position is not eligible for visa support."
    assert read(both) == (True, False)
    assert read("You must be authorized to work in your country of residence.") == (False, False)
