"""A sponsorship claim stored by an older version is withdrawn from the kept description, without the job being seen again."""
from datetime import timedelta

from conftest import GIS_DESCRIPTION, NOW, FakeS3
from test_pipeline import FakeNotifier, StaticBackend, gis_raw, run

from geojobbot.core.descriptions import DescriptionStore
from geojobbot.storage.r2 import R2Store
from geojobbot.storage.state import StateManager


def test_false_sponsorship_claim_is_withdrawn_on_the_next_run():
    s3 = FakeS3()
    refusing = GIS_DESCRIPTION + " This position is not eligible for visa support."
    run(s3, [StaticBackend("feed", [gis_raw(location_raw="Raleigh, NC, United States")])], FakeNotifier())
    manager = StateManager(R2Store("b", client=s3))
    state = manager.load()
    rec = state["jobs"]["static:1"]
    # what the older version stored: accepted, with the false claim, and the refusing text kept
    rec.update(tier="high", rejection_reasons=[], why_matched=rec["why_matched"] + ["Visa sponsorship offered"])
    manager.save(state, "seed")
    store = DescriptionStore.load(manager)
    store.put("static:1", refusing)
    store.save(manager)

    other = gis_raw(title="GIS Technician", source_job_id="static:2", url="https://acme.example/jobs/2", apply_url="https://acme.example/jobs/2")
    _, report = run(s3, [StaticBackend("feed", [other])], FakeNotifier(), now=NOW + timedelta(hours=3))
    rec = StateManager(R2Store("b", client=s3)).load()["jobs"]["static:1"]
    assert report["counts"]["sponsorship_claims_withdrawn"] == 1
    assert "Visa sponsorship offered" not in rec["why_matched"]
    assert rec["tier"] == "rejected" and "WORK_AUTHORIZATION_REQUIRED" in rec["rejection_reasons"]
