import gzip

import pytest
from conftest import NOW, FakeS3

from geojobbot.storage.base import LocalStore, StorageError
from geojobbot.storage.r2 import R2Store
from geojobbot.storage.state import (ConcurrentModificationError, StateCorruptError, StateManager, empty_state,
                                     encode_state)


@pytest.fixture(params=["r2", "local"])
def store(request, tmp_path):
    return R2Store("bucket", client=FakeS3()) if request.param == "r2" else LocalStore(str(tmp_path))


def test_store_contract(store):
    assert store.get_bytes("a/missing") is None and store.head("a/missing") is None
    etag = store.put_bytes("a/b.json", b"hello")
    assert etag and store.get_bytes("a/b.json") == b"hello" and store.head("a/b.json")["size"] == 5
    store.copy("a/b.json", "a/c.json")
    for i in range(5):
        store.put_bytes(f"a/x{i}", b"1")
    assert set(store.list_keys("a/")) >= {"a/b.json", "a/c.json", "a/x4"}  # pagination exercised on fake R2
    assert store.delete_keys(["a/b.json", "a/c.json"]) == 2
    assert store.get_bytes("a/b.json") is None


def test_missing_state_with_previous_runs_refuses_to_start_fresh(store):
    store.put_bytes("runs/latest.json", b"{}")
    with pytest.raises(StateCorruptError, match="refusing to start fresh"):
        StateManager(store).load()
    manager = StateManager(store, allow_reset=True)
    assert manager.load()["jobs"] == {} and manager.first_run


def test_save_reads_back_the_object(store):
    manager = StateManager(store)
    state = manager.load()
    assert manager.save(state, "run1")
    assert store.head("state/state.json.gz")["size"] > 0


def test_r2_unreachable_is_error_not_missing():
    s3 = FakeS3()
    s3.fail = ConnectionError("network down")
    store = R2Store("bucket", client=s3)
    with pytest.raises(StorageError):
        store.get_bytes("state/state.json.gz")


def test_first_run_then_recovery_on_new_runner():
    s3 = FakeS3()
    m1 = StateManager(R2Store("b", client=s3))
    state = m1.load()
    assert m1.first_run
    state["jobs"]["greenhouse:1"] = {"canonical_id": "greenhouse:1", "notified": True}
    m1.save(state, "run1")
    # brand-new runner, no local files at all
    m2 = StateManager(R2Store("b", client=s3))
    restored = m2.load()
    assert not m2.first_run and restored["jobs"]["greenhouse:1"]["notified"] is True


def test_backup_created_and_pruned():
    s3 = FakeS3()
    m = StateManager(R2Store("b", client=s3), backups_keep=2)
    state = m.load()
    m.save(state, "r0")
    for i in range(4):
        m = StateManager(R2Store("b", client=s3), backups_keep=2)
        st = m.load()
        m.save(st, f"r{i + 1}")
    assert len([k for k in s3.objects if k.startswith("state/backups/")]) == 2


def test_corrupt_state_restores_backup():
    s3 = FakeS3()
    good = empty_state()
    good["jobs"]["x"] = {"canonical_id": "x"}
    s3.objects["state/backups/20260915T000000Z-r.json.gz"] = encode_state(good)
    s3.objects["state/state.json.gz"] = b"garbage"
    m = StateManager(R2Store("b", client=s3))
    assert "x" in m.load()["jobs"] and m.restored_from_backup


def test_missing_state_with_backups_is_not_silently_empty():
    s3 = FakeS3()
    good = empty_state()
    good["jobs"]["kept"] = {}
    s3.objects["state/backups/20260915T000000Z-r.json.gz"] = encode_state(good)
    m = StateManager(R2Store("b", client=s3))
    assert "kept" in m.load()["jobs"] and not m.first_run


def test_all_corrupt_raises_unless_reset_allowed():
    s3 = FakeS3()
    s3.objects["state/state.json.gz"] = gzip.compress(b'{"schema_version": 99}')
    with pytest.raises(StateCorruptError):
        StateManager(R2Store("b", client=s3)).load()
    m = StateManager(R2Store("b", client=s3), allow_reset=True)
    assert m.load()["jobs"] == {}


def test_concurrent_modification_detected():
    s3 = FakeS3()
    StateManager(R2Store("b", client=s3)).save(empty_state(), "seed")
    a = StateManager(R2Store("b", client=s3))
    b = StateManager(R2Store("b", client=s3))
    sa, sb = a.load(), b.load()
    sa["jobs"]["a"] = {}
    a.save(sa, "runA")
    sb["jobs"]["b"] = {}
    with pytest.raises(ConcurrentModificationError):
        b.save(sb, "runB")
    assert "state/conflicts/runB.json.gz" in s3.objects
    assert "a" in StateManager(R2Store("b", client=s3)).load()["jobs"]


def test_invalid_state_never_uploaded():
    s3 = FakeS3()
    m = StateManager(R2Store("b", client=s3))
    m.load()
    with pytest.raises(StateCorruptError):
        m.save({"jobs": {}}, "bad")
    assert "state/state.json.gz" not in s3.objects


def test_retention_cleanup():
    s3 = FakeS3()
    m = StateManager(R2Store("b", client=s3), prefix="bot")
    for key in ("bot/raw/2026-08-01/r/x.jsonl.gz", "bot/raw/2026-09-15/r/x.jsonl.gz",
                "bot/runs/2026-01-01/r.json.gz", "bot/runs/latest.json"):
        s3.objects[key] = b"1"
    deleted = m.cleanup_retention(raw_days=14, run_days=90, now=NOW)
    assert deleted == {"raw/": 1, "runs/": 1}
    assert "bot/raw/2026-09-15/r/x.jsonl.gz" in s3.objects and "bot/runs/latest.json" in s3.objects
