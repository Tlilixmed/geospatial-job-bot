"""Sponsorship wording: an offer must not be read into a refusal (found on a live Greenhouse posting)."""
import pytest
from conftest import GIS_DESCRIPTION, make_settings

from geojobbot.matching import matcher as M

US = {"country": "United States"}
CFG = make_settings().match_config()

REFUSALS = [
    "This position is not eligible for visa support.",  # the posting that was listed under /sponsors
    "Visa sponsorship is not available for this role.",
    "Unfortunately we are unable to provide visa sponsorship at this time.",
    "We cannot offer visa assistance or relocation.",
    "No visa support will be provided.",
    "Candidates must not require immigration support now or in the future. Visa support: not available.",
    "Nous ne proposons pas de parrainage de visa disponible pour ce poste.",
    "Applicants will not be considered if they need sponsorship; we won't sponsor visas.",
]
OFFERS = [
    "Visa sponsorship is available for the right candidate.",
    "We sponsor visas and offer relocation assistance.",
    "We do not discriminate on any basis. Visa support and relocation are provided.",
    "No degree required. We will sponsor your work permit.",
]


@pytest.mark.parametrize("sentence", REFUSALS)
def test_refusals_are_never_read_as_offers(sentence):
    required, offered = M.work_authorization(f"{GIS_DESCRIPTION} {sentence}", US, CFG)
    assert offered is False and required is True, sentence
    result = M.score_job("Geospatial Analyst I", f"{GIS_DESCRIPTION} {sentence}", US, CFG)
    assert result.tier == "rejected" and "WORK_AUTHORIZATION_REQUIRED" in result.rejection_reasons
    assert "Visa sponsorship offered" not in result.why_matched


@pytest.mark.parametrize("sentence", OFFERS)
def test_real_offers_still_count(sentence):
    required, offered = M.work_authorization(f"{GIS_DESCRIPTION} {sentence}", US, CFG)
    assert offered is True and required is False, sentence


def test_a_posting_that_says_both_is_not_an_offer():
    text = f"{GIS_DESCRIPTION} Visa sponsorship is available for senior roles. This position is not eligible for visa support."
    assert M.work_authorization(text, US, CFG) == (True, False)
    assert M.work_authorization(text, {"country": "Tunisia"}, CFG) == (False, False)  # at home no visa is needed
