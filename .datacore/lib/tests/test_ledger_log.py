"""Tests for ledger.log - locked append, per-writer files, merged read."""

import os

import pytest

from ledger.events import from_line
from ledger.fold import fold
from ledger.log import CorruptLogError, EventLog, read_events


def _mk_log(tmp_path, actor, sign=True):
    return EventLog(
        tmp_path / "space",
        actor,
        keys_dir=tmp_path / "keys",
        registry_path=tmp_path / "registry.yaml",
        sign=sign,
    )


def test_append_read_roundtrip(tmp_path):
    log = _mk_log(tmp_path, "mac")
    e1 = log.append("item.create", {"id": "t1"})
    e2 = log.append("item.claim", {"id": "t1"})

    assert e1.seq == 0 and e1.prev == "GENESIS"
    assert e2.seq == 1 and e2.prev == e1.hash

    events = read_events(tmp_path / "space")
    assert [e.type for e in events] == ["item.create", "item.claim"]
    assert events[0].seq == 0 and events[1].seq == 1

    path = tmp_path / "space" / ".datacore" / "events" / "mac.jsonl"
    assert path.exists()


def test_append_creates_parent_dirs(tmp_path):
    log = _mk_log(tmp_path, "mac")
    assert not (tmp_path / "space" / ".datacore" / "events").exists()
    log.append("item.create", {"id": "t1"})
    assert (tmp_path / "space" / ".datacore" / "events" / "mac.jsonl").exists()


def test_two_actors_merge_hlc_sorted(tmp_path):
    log_a = _mk_log(tmp_path, "mac")
    log_b = _mk_log(tmp_path, "box")

    log_a.append("item.create", {"id": "t1"})
    log_b.append("item.claim", {"id": "t1"})
    log_a.append("item.complete", {"id": "t1"})

    events = read_events(tmp_path / "space")
    assert len(events) == 3
    hlcs = [e.hlc for e in events]
    assert hlcs == sorted(hlcs)
    # both actors' files were actually merged in
    assert {e.actor for e in events} == {"mac", "box"}


def test_unknown_event_type_raises(tmp_path):
    log = _mk_log(tmp_path, "mac")
    with pytest.raises(ValueError):
        log.append("not.a.real.type", {})


def test_read_events_empty_dir(tmp_path):
    space_dir = tmp_path / "space"
    (space_dir / ".datacore" / "events").mkdir(parents=True)
    assert read_events(space_dir) == []


# --- torn vs. corrupt line handling ------------------------------------------


def _write_torn_tail(path):
    """Simulate a crash mid-write: raw garbage bytes with no trailing
    newline and no valid JSON, appended directly to the file (bypassing
    EventLog, which always writes a complete `to_line(event) + "\\n"`)."""
    with open(path, "ab") as f:
        f.write(b'{"seq": 99, "hlc": "garbage-torn-write-no-close')


def test_append_recovers_from_torn_tail(tmp_path):
    log = _mk_log(tmp_path, "mac")
    e1 = log.append("item.create", {"id": "t1"})

    _write_torn_tail(log.path)

    e2 = log.append("item.claim", {"id": "t1"})

    assert e2.seq == 1
    assert e2.prev == e1.hash  # chained to the last VALID event, not the torn tail

    lines = log.path.read_text().splitlines()
    assert len(lines) == 2
    assert from_line(lines[0]).hash == e1.hash
    assert from_line(lines[1]).hash == e2.hash


def test_read_events_skips_torn_final_line(tmp_path):
    log = _mk_log(tmp_path, "mac")
    e1 = log.append("item.create", {"id": "t1"})

    _write_torn_tail(log.path)

    events = read_events(tmp_path / "space")
    assert len(events) == 1
    assert events[0].hash == e1.hash


def test_read_events_raises_on_malformed_middle_line(tmp_path):
    log = _mk_log(tmp_path, "mac")
    log.append("item.create", {"id": "t1"})
    log.append("item.claim", {"id": "t1"})

    valid_lines = log.path.read_text().splitlines()
    assert len(valid_lines) == 2

    # Rewrite as: valid line, garbage line, valid line -- the garbage is
    # NOT the final line, so it cannot be explained by an in-flight write.
    corrupted = valid_lines[0] + "\n" + "not valid json {{{" + "\n" + valid_lines[1] + "\n"
    log.path.write_text(corrupted)

    with pytest.raises(CorruptLogError) as exc_info:
        read_events(tmp_path / "space")

    msg = str(exc_info.value)
    assert str(log.path) in msg
    assert "line 2" in msg


# --- signing opt-in (Task 1.4b) ----------------------------------------------


def test_default_no_signing_touches_no_key_material(tmp_path, monkeypatch):
    """Default EventLog (no `sign=` arg, no env var) must not sign events and
    must not touch key material at all -- no ensure_keypair call, so neither
    the keys_dir nor the registry file should ever be created."""
    monkeypatch.delenv("DATACORE_LEDGER_SIGN", raising=False)
    keys_dir = tmp_path / "keys"
    registry_path = tmp_path / "registry.yaml"

    log = EventLog(tmp_path / "space", "mac", keys_dir=keys_dir, registry_path=registry_path)
    event = log.append("item.create", {"id": "t1"})

    assert event.sig == ""
    assert not keys_dir.exists()
    assert not registry_path.exists()


def test_env_var_enables_signing(tmp_path, monkeypatch):
    """DATACORE_LEDGER_SIGN=1 turns signing on when `sign` is not passed."""
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "1")
    keys_dir = tmp_path / "keys"
    registry_path = tmp_path / "registry.yaml"

    log = EventLog(tmp_path / "space", "mac", keys_dir=keys_dir, registry_path=registry_path)
    event = log.append("item.create", {"id": "t1"})

    assert event.sig != ""
    assert keys_dir.exists()
    assert registry_path.exists()


def test_explicit_sign_false_overrides_env_var(tmp_path, monkeypatch):
    """An explicit `sign=False` wins over DATACORE_LEDGER_SIGN=1."""
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "1")
    keys_dir = tmp_path / "keys"
    registry_path = tmp_path / "registry.yaml"

    log = EventLog(
        tmp_path / "space", "mac", keys_dir=keys_dir, registry_path=registry_path, sign=False
    )
    event = log.append("item.create", {"id": "t1"})

    assert event.sig == ""
    assert not keys_dir.exists()
    assert not registry_path.exists()


def test_signed_and_unsigned_events_coexist_in_one_file(tmp_path, monkeypatch):
    """A single writer file can contain both signed and unsigned events (e.g.
    signing gets enabled partway through), and `read_events` handles both."""
    monkeypatch.delenv("DATACORE_LEDGER_SIGN", raising=False)

    signed_log = _mk_log(tmp_path, "mac", sign=True)
    e1 = signed_log.append("item.create", {"id": "t1"})

    unsigned_log = EventLog(
        tmp_path / "space",
        "mac",
        keys_dir=tmp_path / "keys",
        registry_path=tmp_path / "registry.yaml",
    )
    e2 = unsigned_log.append("item.claim", {"id": "t1"})

    assert e1.sig != ""
    assert e2.sig == ""

    events = read_events(tmp_path / "space")
    assert [e.type for e in events] == ["item.create", "item.claim"]
    assert events[0].sig == e1.sig
    assert events[1].sig == ""
    # chain integrity holds across the signed/unsigned boundary
    assert events[1].prev == events[0].hash


# --- multiprocessing concurrency test ---------------------------------------

import multiprocessing


def _mp_worker(space_dir_str, keys_dir_str, registry_path_str, actor, n):
    """Module-level worker (must be picklable) -- each process builds its own
    EventLog against the shared tmp_path space/keys/registry and appends n times."""
    from pathlib import Path

    from ledger.log import EventLog

    log = EventLog(
        Path(space_dir_str),
        actor,
        keys_dir=Path(keys_dir_str),
        registry_path=Path(registry_path_str),
        sign=True,
    )
    for i in range(n):
        log.append("item.create", {"i": i})


def test_concurrent_appends_multiprocess_same_actor(tmp_path):
    """Multiple processes append to the SAME actor's file concurrently.

    This is the actual point of the fcntl.flock: without a lock held across
    the read-tail + write critical section, concurrent processes could read
    the same "last event" and both compute the same next seq/prev, corrupting
    the chain. With the lock, appends serialize and the merged result is a
    single unbroken chain of exactly workers*per_worker events.
    """
    space_dir = tmp_path / "space"
    keys_dir = tmp_path / "keys"
    registry_path = tmp_path / "registry.yaml"
    actor = "shared"
    workers = 4
    per_worker = 25
    total = workers * per_worker

    # Pre-create the keypair before spawning workers: ensure_keypair is
    # itself concurrency-safe now (see test_ledger_keys.py's cold-start
    # test), but creating it once up front keeps this test focused purely
    # on log.py's own append-locking, rather than exercising both locks at
    # once.
    from ledger.keys import ensure_keypair

    ensure_keypair(actor, keys_dir=keys_dir, registry_path=registry_path)

    args = [
        (str(space_dir), str(keys_dir), str(registry_path), actor, per_worker)
        for _ in range(workers)
    ]

    with multiprocessing.Pool(workers) as pool:
        pool.starmap(_mp_worker, args)

    events = read_events(space_dir)
    assert len(events) == total

    events.sort(key=lambda e: e.seq)
    seqs = [e.seq for e in events]
    assert seqs == list(range(total)), f"gaps/dupes: {seqs}"

    assert events[0].prev == "GENESIS"
    for prev_e, e in zip(events, events[1:]):
        assert e.prev == prev_e.hash


# --- HLC cross-actor causal floor (Task 5.2b) --------------------------------
#
# Regression: two actors appending in the same millisecond used to tie-break
# by actor name in read_events' hlc sort (both actors' first tick() gets
# counter 0, so the hlc strings differ only in the trailing actor name).
# That let a dismiss by a lexically-earlier actor sort BEFORE the create it
# targets -- an orphaned dismiss, silently violating "dismiss is terminal /
# never resurfaces" because fold() never even sees an item to dismiss.


def _freeze_hlc_clock(monkeypatch, seconds=1_700_000_000.0):
    """Pin ledger.hlc's wall-clock read so every tick() in this test lands in
    the same millisecond unless the append-causal floor bumps the counter."""
    monkeypatch.setattr("ledger.hlc.time.time", lambda: seconds)


def test_cross_actor_same_ms_zzz_creates_aaa_dismisses(tmp_path, monkeypatch):
    """The actual regression shape: creator's name sorts AFTER dismisser's
    name lexically ("aaa" < "zzz"), so the old actor-name tie-break would
    put the dismiss first."""
    _freeze_hlc_clock(monkeypatch)
    creator = _mk_log(tmp_path, "zzz", sign=False)
    dismisser = _mk_log(tmp_path, "aaa", sign=False)

    creator.append("item.create", {"id": "t1", "title": "T1"})
    dismisser.append("item.dismiss", {"id": "t1"})

    events = read_events(tmp_path / "space")
    assert [e.type for e in events] == ["item.create", "item.dismiss"]

    state = fold(events)
    assert state.items["t1"].status == "dismissed"
    assert state.orphans == []


def test_cross_actor_same_ms_aaa_creates_zzz_dismisses(tmp_path, monkeypatch):
    """Same scenario with actor names swapped -- must produce the same
    outcome. (Under the old buggy code this ordering happened to come out
    right by coincidence, since "aaa" < "zzz" lexically matches the real
    create-before-dismiss order; it must keep passing under the fix too.)"""
    _freeze_hlc_clock(monkeypatch)
    creator = _mk_log(tmp_path, "aaa", sign=False)
    dismisser = _mk_log(tmp_path, "zzz", sign=False)

    creator.append("item.create", {"id": "t1", "title": "T1"})
    dismisser.append("item.dismiss", {"id": "t1"})

    events = read_events(tmp_path / "space")
    assert [e.type for e in events] == ["item.create", "item.dismiss"]

    state = fold(events)
    assert state.items["t1"].status == "dismissed"
    assert state.orphans == []


def test_sibling_torn_tail_tolerated_floor_still_advances(tmp_path, monkeypatch):
    """A sibling actor's file with a torn (in-flight/crashed) final line must
    not break the floor read -- the floor comes from that sibling's last
    VALID event, and the torn tail is tolerated silently (same posture as
    _parse_log_bytes uses everywhere else)."""
    _freeze_hlc_clock(monkeypatch)
    alice = _mk_log(tmp_path, "alice", sign=False)
    bob = _mk_log(tmp_path, "bob", sign=False)

    e1 = alice.append("item.create", {"id": "t1", "title": "T1"})
    _write_torn_tail(alice.path)

    e2 = bob.append("item.dismiss", {"id": "t1"})

    # bob's append succeeded despite alice's torn tail, and its hlc is
    # strictly after alice's last VALID event -- proof the floor read alice's
    # file and advanced past it rather than erroring or ignoring it.
    assert e2.hlc > e1.hlc

    events = read_events(tmp_path / "space")
    assert [e.type for e in events] == ["item.create", "item.dismiss"]


def test_sibling_corrupt_middle_line_is_best_effort_skipped(tmp_path, monkeypatch):
    """A sibling with REAL corruption (a malformed line that is NOT the
    final line -- cannot be explained by an in-flight write) must NEVER
    block another actor's append. Ruling: the floor read is best-effort --
    that whole sibling contributes nothing to the floor, and the current
    actor's own append proceeds and stays internally monotonic. The
    corruption itself still surfaces loudly, just via read_events (where
    diagnosis belongs), not through bob's write path."""
    _freeze_hlc_clock(monkeypatch)
    alice = _mk_log(tmp_path, "alice", sign=False)
    bob = _mk_log(tmp_path, "bob", sign=False)

    alice.append("item.create", {"id": "t1", "title": "T1"})
    alice.append("item.claim", {"id": "t1"})

    valid_lines = alice.path.read_text().splitlines()
    assert len(valid_lines) == 2
    # Corrupt the MIDDLE line (not the last) -- real corruption, not a torn
    # tail; _parse_log_bytes would raise CorruptLogError reading this file.
    corrupted = valid_lines[0] + "\n" + "not valid json {{{" + "\n" + valid_lines[1] + "\n"
    alice.path.write_text(corrupted)

    # bob's append must succeed despite alice's mid-file corruption, and his
    # own chain stays correctly monotonic across it.
    b1 = bob.append("item.dismiss", {"id": "t1"})
    b2 = bob.append("item.dismiss", {"id": "t1"})
    assert b1.seq == 0 and b2.seq == 1
    assert b2.prev == b1.hash
    assert b2.hlc > b1.hlc

    # The corruption itself is not swallowed system-wide -- read_events (and
    # verify_chain) still raise on it loudly.
    with pytest.raises(CorruptLogError):
        read_events(tmp_path / "space")


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_sibling_unreadable_permissions_is_best_effort_skipped(tmp_path, monkeypatch):
    """An unreadable sibling file (permissions denied, or any other OSError
    from read_bytes()) must not block another actor's append either -- same
    best-effort posture as the corrupt-sibling case."""
    _freeze_hlc_clock(monkeypatch)
    alice = _mk_log(tmp_path, "alice", sign=False)
    bob = _mk_log(tmp_path, "bob", sign=False)

    bob.append("item.create", {"id": "t0", "title": "T0"})
    bob.path.chmod(0o000)
    try:
        a1 = alice.append("item.create", {"id": "t1", "title": "T1"})
    finally:
        bob.path.chmod(0o644)

    assert a1.seq == 0 and a1.prev == "GENESIS"  # alice's own chain unaffected


def test_solo_actor_no_siblings_unaffected(tmp_path, monkeypatch):
    """Guard: a lone actor with no sibling files (or an otherwise-empty
    events dir) must behave exactly as before -- floor reduces to "own last
    hlc only"."""
    _freeze_hlc_clock(monkeypatch)
    log = _mk_log(tmp_path, "solo", sign=False)

    e1 = log.append("item.create", {"id": "t1", "title": "T1"})
    e2 = log.append("item.claim", {"id": "t1"})

    assert e2.seq == 1 and e2.prev == e1.hash
    assert e2.hlc > e1.hlc


def test_round_robin_three_actors_same_ms_preserves_append_order(tmp_path, monkeypatch):
    """Multi-sibling case: three actors appending round-robin, all frozen to
    the same millisecond -- read_events order must equal append order (the
    append-causal floor keeps advancing the counter across all three
    writers, not just each writer's own file).

    Actor names are DELIBERATELY non-alphabetical ("zed", "mac", "alice")
    and in that exact append order -- alphabetical-by-actor-name sort would
    reorder to alice/mac/zed, which does NOT match append order, so this
    fails without the cross-actor floor fix."""
    _freeze_hlc_clock(monkeypatch)
    logs = [_mk_log(tmp_path, name, sign=False) for name in ("zed", "mac", "alice")]

    appended_hashes = []
    for i in range(6):
        log = logs[i % 3]
        event = log.append("item.create", {"id": f"t{i}", "title": f"T{i}"})
        appended_hashes.append(event.hash)

    events = read_events(tmp_path / "space")
    assert [e.hash for e in events] == appended_hashes
