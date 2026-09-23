"""Defects the Lean model DatacoreSpec/LedgerSeal.lean found in the seal cluster.

Each test replays the counterexample of a Lean theorem against the real Python
(tmp spaces only). The model now proves the fixed behaviour; these pin the
Python to it.
"""
import fcntl
import shutil
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from ledger.fold import fold  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ledger.seal import build_seal_payload, settled_events, verify_seal  # noqa: E402


# --- seal finality: settled(t) ⊆ settled(t') (Lean: seal_settled_monotone) ----

@pytest.fixture(autouse=True)
def _default_sequencer(monkeypatch):
    monkeypatch.delenv("DATACORE_SEQUENCER", raising=False)


def _sealed_space(tmp_path):
    sp = tmp_path / "s"
    log = EventLog(sp, "mac", sign=False)
    for i in range(3):
        log.append("item.create", {"id": f"i{i}", "title": "t"})
    EventLog(sp, "winston", sign=False).append("ledger.seal", build_seal_payload(read_events(sp)))
    return sp


def test_a_later_seal_cannot_unsettle_what_an_earlier_seal_settled(tmp_path):
    """Lean counterexample `seal_regression_cex`: a later seal with lower
    watermarks used to verify, shrinking settled 3 -> 1.

    Written by the sequencer itself since decision L1 (2026-09-23): a seal by
    another writer is now ignored outright (test_decisions_ledger_a.py), so the
    regression gate matters for the sequencer's own lagging or rewound seals."""
    sp = _sealed_space(tmp_path)
    assert len(settled_events(read_events(sp))) == 3
    sub = [e for e in read_events(sp) if e.type != "ledger.seal" and e.seq == 0]
    EventLog(sp, "winston", sign=False).append("ledger.seal", build_seal_payload(sub))
    ok, detail = verify_seal(read_events(sp))
    assert ok is False and "SEAL REGRESSION" in detail
    with pytest.raises(ValueError):
        settled_events(read_events(sp))


def test_a_forward_seal_still_verifies(tmp_path):
    sp = _sealed_space(tmp_path)
    EventLog(sp, "mac", sign=False).append("item.create", {"id": "late", "title": "t"})
    EventLog(sp, "winston", sign=False).append("ledger.seal", build_seal_payload(read_events(sp)))
    ok, detail = verify_seal(read_events(sp))
    assert ok is True, detail
    assert len(settled_events(read_events(sp))) == 4


def test_emit_refuses_a_regressing_seal(tmp_path, monkeypatch, capsys):
    import ledger_seal
    sp = _sealed_space(tmp_path)
    # A checkout that lost mac's tail (rewound) must not seal over it.
    mac = sp / ".datacore/events/mac.jsonl"
    mac.write_text("".join(mac.read_text().splitlines(keepends=True)[:1]))
    monkeypatch.setattr(ledger_seal, "_actor", ledger_seal.sequencer)
    assert ledger_seal.cmd_emit(sp, force=False) == 2
    assert "behind an earlier seal" in capsys.readouterr().out
    assert sum(e.type == "ledger.seal" for e in read_events(sp)) == 1


# --- ledger_ingest_org: unreadable live file (Lean: ingest_unreadable_*) ------

def _archived_space(tmp_path):
    sp = tmp_path / "s"
    (sp / "org").mkdir(parents=True)
    EventLog(sp, "w", sign=False).append("item.create", {"id": "X", "title": "t"})
    heading = "* TODO t\n:PROPERTIES:\n:ID: X\n:END:\n"
    (sp / "org" / "next_actions_archive.org").write_text(heading)
    return sp, heading


@pytest.mark.skipif(hasattr(sys, "getuid") and __import__("os").getuid() == 0,
                    reason="root reads mode-000 files")
def test_unreadable_live_file_never_lets_its_ids_be_dismissed(tmp_path):
    import ledger_ingest_org as ingest
    sp, heading = _archived_space(tmp_path)
    live = sp / "org" / "next_actions.org"
    live.write_text(heading)
    state = fold(read_events(sp))
    live.chmod(0)
    try:
        assert ingest._dismiss_archived(sp, None, state, None, "w", True) == 0
    finally:
        live.chmod(0o644)


def test_a_nested_live_file_keeps_its_id_live(tmp_path):
    """'An id still present in ANY live org file is left alone' -- including
    org/inboxes/*.org, which the top-level glob never saw."""
    import ledger_ingest_org as ingest
    sp, heading = _archived_space(tmp_path)
    (sp / "org" / "inboxes").mkdir()
    (sp / "org" / "inboxes" / "tex.org").write_text(heading)
    assert ingest._dismiss_archived(sp, None, fold(read_events(sp)), None, "w", True) == 0


def test_archive_evidence_still_dismisses(tmp_path):
    import ledger_ingest_org as ingest
    sp, _ = _archived_space(tmp_path)
    (sp / "org" / "next_actions.org").write_text("")
    # A nested archive dir is neither evidence nor liveness.
    (sp / "org" / ".archive").mkdir()
    (sp / "org" / ".archive" / "old.org").write_text(":ID: X\n")
    assert ingest._dismiss_archived(sp, None, fold(read_events(sp)), None, "w", True) == 1


# --- ledger_restore_prefix: read/check/write under the log lock (restore_*) ---

def _parked(tmp_path):
    sp = tmp_path / "s"
    log = EventLog(sp, "nightshift", sign=False)
    for i in range(2):
        log.append("item.create", {"id": f"a{i}", "title": "t"})
    path = sp / ".datacore/events/nightshift.jsonl"
    other = tmp_path / "o"
    (other / ".datacore/events").mkdir(parents=True)
    shutil.copy(path, other / ".datacore/events/nightshift.jsonl")
    ol = EventLog(other, "nightshift", sign=False)
    ol.append("item.create", {"id": "p0", "title": "parked"})
    ol.append("item.create", {"id": "p1", "title": "parked"})
    return sp, path, (other / ".datacore/events/nightshift.jsonl").read_text()


def test_an_append_racing_the_restore_is_never_overwritten(tmp_path, monkeypatch):
    """Lean counterexample `restore_race_loses_append`: an append between the
    read and the write used to vanish, its seq reused by a parked event."""
    import ledger_restore_prefix as rp
    sp, path, recovered = _parked(tmp_path)

    def racing_git(repo, *args):
        EventLog(sp, "nightshift", sign=False).append(
            "item.create", {"id": "LIVE", "title": "concurrent"})
        return recovered
    monkeypatch.setattr(rp, "git", racing_git)

    assert rp.restore(sp, "nightshift", "rev", True) == 1
    assert '"LIVE"' in path.read_text()


def test_restore_holds_the_append_lock_while_it_writes(tmp_path, monkeypatch):
    import ledger_restore_prefix as rp
    import ledger.seal as seal
    sp, path, recovered = _parked(tmp_path)
    monkeypatch.setattr(rp, "git", lambda repo, *a: recovered)
    held = []
    real = seal._chain_issue

    def probe(events):
        with open(path, "a+b") as g:     # what EventLog.append opens
            try:
                fcntl.flock(g, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(g, fcntl.LOCK_UN)
                held.append(False)
            except BlockingIOError:
                held.append(True)
        return real(events)
    monkeypatch.setattr(seal, "_chain_issue", probe)

    assert rp.restore(sp, "nightshift", "rev", True) == 0
    assert held and all(held)
    assert path.read_text() == recovered
    assert (path.parent / "nightshift.jsonl.pre-restore").exists()


# --- ledger_dismiss_orphans: two real observations (orphans_*) -----------------

def test_a_non_finite_watch_time_is_not_a_first_observation(tmp_path, monkeypatch):
    import ledger_dismiss_orphans as od
    monkeypatch.setattr(od, "WATCH_DIR", tmp_path / "watch")
    monkeypatch.setattr(od, "_actor", lambda: "w")
    sp = tmp_path / "s"
    (sp / "org").mkdir(parents=True)
    log = EventLog(sp, "w", sign=False)
    for i in range(20):
        log.append("item.create", {"id": f"k{i}", "title": "t"})
    (sp / "org" / "a.org").write_text(
        "".join(f"* t\n:PROPERTIES:\n:ID: k{i}\n:END:\n" for i in range(1, 20)))
    od.WATCH_DIR.mkdir()
    od._watch_path(sp).write_text('{"at": -Infinity, "orphans": ["k0"]}')
    r = od.confirm_and_dismiss(sp, now=10_000.0, execute=True)
    assert r["dismissed"] == 0
    assert fold(read_events(sp)).items["k0"].status == "created"


# --- ledger_invariants: allowlist, not mute button ------------------------------

def test_baseline_prefix_is_a_token_prefix_and_never_empty():
    import ledger_invariants as inv
    f50 = inv.Finding("hashes", "5-plur", "tris seq 50: payload no longer hashes to its recorded hash")
    f5 = inv.Finding("hashes", "5-plur", "tris seq 5: payload no longer hashes to its recorded hash")
    entry = {"invariant": "hashes", "space": "5-plur", "detail_startswith": "tris seq 5"}
    assert inv._accepted(f5, [entry])
    assert not inv._accepted(f50, [entry])
    assert not inv._accepted(f50, [{"invariant": "hashes", "space": "5-plur"}])
    assert not inv._accepted(f50, [{**entry, "detail_startswith": ""}])
    ceo = inv.Finding("declared", "2-datacore", "ceo: belongs to no declared principal")
    assert inv._accepted(ceo, [{"invariant": "declared", "space": "2-datacore",
                                "detail_startswith": "ceo: belongs to no declared principal"}])
