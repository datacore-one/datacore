"""Attack ledger framing, shared signing state, and session namespaces."""
import json
from pathlib import Path
import sys
from concurrent.futures import ProcessPoolExecutor

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1]))
from ledger.keys import ensure_keypair, sign, verify
from ledger.log import CorruptLogError, EventLog, read_events
from ledger.verify import verify_chain
from state_store import YamlStateStore
import session_state


def test_stale_session_cleanup_rechecks_after_lock(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import os
    import time
    monkeypatch.setattr(session_state, "STATE_DIR", str(tmp_path))
    path = tmp_path / "active.json"
    path.write_text('{"active": true}')
    os.utime(path, (0, 0))
    real_lock = session_state.file_lock
    @contextmanager
    def refreshed_lock(target):
        # An updater completes while cleanup is waiting for its lock.
        os.utime(target, (time.time(), time.time()))
        with real_lock(target):
            yield
    monkeypatch.setattr(session_state, "file_lock", refreshed_lock)
    session_state.cleanup_stale_sessions()
    assert path.exists()


def test_witness_write_failure_preserves_old_mark_and_reports_durable_event(tmp_path, monkeypatch):
    import importlib
    log_module = importlib.import_module('ledger.log')
    log = EventLog(tmp_path, 'worker', sign=False)
    first = log.append('item.create', {'id': 'one'})
    mark = tmp_path / '.datacore/state/seq-hwm/worker.seq'
    before = mark.read_bytes()
    def fail(*args):
        raise OSError('injected witness failure')
    monkeypatch.setattr(log_module, 'atomic_write_text', fail)
    with pytest.warns(RuntimeWarning, match='event committed'):
        second = log.append('item.create', {'id': 'two'})
    assert second.seq == first.seq + 1
    assert mark.read_bytes() == before
    assert len(read_events(tmp_path)) == 2


def test_invalid_witness_never_verifies_as_known_good(tmp_path):
    from ledger.verify import check_not_rewound
    log = EventLog(tmp_path, 'worker', sign=False)
    log.append('item.create', {'id': 'one'})
    (tmp_path / '.datacore/state/seq-hwm/worker.seq').write_text('invalid')
    assert check_not_rewound(log.path)


def _key_worker(args):
    root, actor = args
    root = Path(root)
    return actor, ensure_keypair(actor, keys_dir=root / "keys", registry_path=root / "registry.yaml")


def _session_worker(args):
    directory, number = args
    session_state.STATE_DIR = directory
    session_state.update_session("shared", **{f"field{number}": number})


def test_all_actors_survive_concurrent_registry_updates(tmp_path):
    with ProcessPoolExecutor(max_workers=4) as pool:
        expected = dict(pool.map(_key_worker, [(str(tmp_path), f"actor{i}") for i in range(24)]))
    assert yaml.safe_load((tmp_path / "registry.yaml").read_text())["actors"] == expected
    for actor in expected:
        signature = sign(actor, b"data", keys_dir=tmp_path / "keys")
        assert verify(actor, b"data", signature, registry_path=tmp_path / "registry.yaml")


def test_missing_private_key_never_silently_rotates_registered_identity(tmp_path):
    registry = tmp_path / "registry.yaml"
    ensure_keypair("writer", keys_dir=tmp_path / "keys", registry_path=registry)
    original = registry.read_bytes()
    (tmp_path / "keys" / "writer.key").unlink()
    with pytest.raises(ValueError, match="registered identity"):
        ensure_keypair("writer", keys_dir=tmp_path / "keys", registry_path=registry)
    assert registry.read_bytes() == original
    assert not (tmp_path / "keys" / "writer.key").exists()


@pytest.mark.parametrize("actor", ["../escape", "/absolute", "a/b", "a\\b", "", ".."])
def test_key_identifiers_cannot_escape_storage(tmp_path, actor):
    with pytest.raises(ValueError):
        ensure_keypair(actor, keys_dir=tmp_path / "keys", registry_path=tmp_path / "registry.yaml")
    assert not list(tmp_path.rglob("*.key"))


@pytest.mark.parametrize("tail", [b'not json\n', b'{}\n', b'\xff\n'])
def test_complete_corrupt_last_record_is_never_discarded(tmp_path, tail):
    log = EventLog(tmp_path, "writer", sign=False)
    log.append("item.create", {"id": "one"})
    original = log.path.read_bytes() + tail
    log.path.write_bytes(original)
    with pytest.raises(CorruptLogError):
        read_events(tmp_path)
    with pytest.raises(CorruptLogError):
        log.append("item.create", {"id": "two"})
    assert log.path.read_bytes() == original


def test_valid_record_without_newline_is_preserved_on_append(tmp_path):
    log = EventLog(tmp_path, "writer", sign=False)
    first = log.append("item.create", {"id": "one"})
    log.path.write_bytes(log.path.read_bytes().rstrip(b"\n"))
    log.append("item.create", {"id": "two"})
    events = read_events(tmp_path)
    assert [e.payload["id"] for e in events] == ["one", "two"]
    assert events[0].hash == first.hash
    assert verify_chain(log.path) == []


def test_ledger_flush_error_is_not_acknowledged(tmp_path, monkeypatch):
    log = EventLog(tmp_path, "writer", sign=False)
    def fail(fd):
        raise OSError("injected sync failure")
    monkeypatch.setattr("ledger.log.os.fsync", fail)
    with pytest.raises(OSError, match="sync failure"):
        log.append("item.create", {"id": "one"})


@pytest.mark.parametrize("default", [[{"items": []}], {"items": []}])
def test_missing_state_default_does_not_share_nested_mutable_data(tmp_path, default):
    store = YamlStateStore("absent.yaml", default=default, data_root=tmp_path)
    value = store.load()
    if isinstance(value, list):
        value[0]["items"].append(1)
    else:
        value["items"].append(1)
    assert store.load() == default


@pytest.mark.parametrize("sid", ["../escape", "/absolute", "a/b", "a\\b", ".."])
def test_session_identifiers_cannot_escape_storage(tmp_path, monkeypatch, sid):
    monkeypatch.setattr(session_state, "STATE_DIR", str(tmp_path / "sessions"))
    with pytest.raises(ValueError):
        session_state.update_session(sid, value=True)
    assert not list(tmp_path.rglob("*.json"))


def test_session_updates_preserve_unreadable_state(tmp_path, monkeypatch):
    monkeypatch.setattr(session_state, "STATE_DIR", str(tmp_path))
    path = tmp_path / "session.json"
    original = b'{"unfinished": ['
    path.write_bytes(original)
    with pytest.raises(json.JSONDecodeError):
        session_state.update_session("session", value=True)
    assert path.read_bytes() == original


def test_session_concurrent_updates_preserve_disjoint_fields(tmp_path):
    with ProcessPoolExecutor(max_workers=4) as pool:
        list(pool.map(_session_worker, [(str(tmp_path), i) for i in range(24)]))
    assert json.loads((tmp_path / "shared.json").read_text()) == {f"field{i}": i for i in range(24)}
