"""Live integration tests against real R2 and Telegram.

Skipped unless RUN_LIVE_TESTS=1. They never touch the production state object:
R2 tests use a unique prefix under selftest/ and delete everything they create.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.environ.get("RUN_LIVE_TESTS") != "1",
                                                  reason="set RUN_LIVE_TESTS=1 to run live tests")]


def _r2_store():
    from geojobbot.storage.r2 import R2Store
    required = ["R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        pytest.fail(f"missing secrets: {missing}")
    return R2Store(os.environ["R2_BUCKET_NAME"], endpoint_url=os.environ["R2_ENDPOINT_URL"],
                   access_key_id=os.environ["R2_ACCESS_KEY_ID"], secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"])


def test_r2_object_roundtrip():
    store = _r2_store()
    prefix = f"selftest/{uuid.uuid4().hex}/"
    try:
        assert store.get_bytes(prefix + "missing") is None
        store.put_bytes(prefix + "a.bin", b"\x00\x01payload", "application/octet-stream")
        assert store.get_bytes(prefix + "a.bin") == b"\x00\x01payload"
        assert store.head(prefix + "a.bin")["size"] == 9
        store.copy(prefix + "a.bin", prefix + "b.bin")
        assert sorted(store.list_keys(prefix)) == [prefix + "a.bin", prefix + "b.bin"]
    finally:
        store.delete_keys(store.list_keys(prefix))
    assert store.list_keys(prefix) == []


def test_r2_state_manager_recovery_and_conflict_detection():
    from geojobbot.storage.state import ConcurrentModificationError, StateManager
    store = _r2_store()
    prefix = f"selftest/{uuid.uuid4().hex}"
    try:
        first = StateManager(store, prefix)
        state = first.load()
        assert first.first_run
        state["jobs"]["live:1"] = {"canonical_id": "live:1", "notified": True}
        first.save(state, "live-run-1")
        fresh = StateManager(store, prefix)  # simulates a brand-new GitHub runner
        recovered = fresh.load()
        assert recovered["jobs"]["live:1"]["notified"] is True
        recovered["jobs"]["live:2"] = {}
        fresh.save(recovered, "live-run-2")
        assert any("state/backups/" in k for k in store.list_keys(prefix + "/"))
        stale = StateManager(store, prefix)
        stale.etag = "not-the-current-etag"
        with pytest.raises(ConcurrentModificationError):
            stale.save(recovered, "live-run-3")
    finally:
        store.delete_keys(store.list_keys(prefix + "/"))


def test_telegram_delivery():
    from geojobbot.notifications.telegram import TelegramNotifier, format_job_message
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        pytest.fail("missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
    sample = {"tier": "high", "score": 99, "title": "Integration test — GIS Analyst", "company": "Test Co",
              "remote": True, "work_mode": "remote", "matched_skills": ["ArcGIS Pro (required)"],
              "why_matched": ["Integration test message"], "apply_url": "https://example.com"}
    ok, error = TelegramNotifier(token, chat).send(format_job_message(sample))
    assert ok, error
