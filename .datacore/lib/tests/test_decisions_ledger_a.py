"""Owner decisions L1, L2, L4, L5 (2026-09-23, specs/datacore-lean/DECISIONS.md), pinned.

  L1  readers accept only seals by the designated sequencer
  L2  a sweep whose only findings are could-not-tell prints UNKNOWN, exits 2
  L4  dispatch claims addressed work only as the exact writer named
  L5  an executed task exports DATACORE_HOPS = its recorded hops + 1

Lean: DatacoreSpec/LedgerSeal.lean (`latest_is_sequencer`, `foreign_seal_inert`,
`exit_zero_iff_all_sound`) and DatacoreSpec/LedgerPolicy.lean
(`Dispatch.exact_no_race`).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import actor_identity  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ledger.seal import (  # noqa: E402
    build_seal_payload, latest_seal, settled_events, verify_seal,
)


# --- L1: only the sequencer's seals count ------------------------------------

@pytest.fixture
def sealed(tmp_path, monkeypatch):
    monkeypatch.delenv("DATACORE_SEQUENCER", raising=False)
    sp = tmp_path / "s"
    log = EventLog(sp, "mac", sign=False)
    for i in range(3):
        log.append("item.create", {"id": f"i{i}", "title": "t"})
    EventLog(sp, "winston", sign=False).append("ledger.seal", build_seal_payload(read_events(sp)))
    return sp


def test_a_seal_by_another_writer_is_ignored_not_fatal(sealed):
    """A regressing seal by a non-sequencer used to be latest (and, after the
    regression fix, made the whole ledger unverifiable). Now it is inert."""
    sub = [e for e in read_events(sealed) if e.type != "ledger.seal" and e.seq == 0]
    EventLog(sealed, "mallory", sign=False).append("ledger.seal", build_seal_payload(sub))
    events = read_events(sealed)
    assert latest_seal(events).sequencer == "winston"
    ok, detail = verify_seal(events)
    assert ok is True, detail
    assert "ignored 1 seal(s) by mallory" in detail
    assert len(settled_events(events)) == 3


def test_a_forward_seal_by_another_writer_does_not_advance_settlement(sealed):
    EventLog(sealed, "mac", sign=False).append("item.create", {"id": "late", "title": "t"})
    EventLog(sealed, "mallory", sign=False).append("ledger.seal", build_seal_payload(read_events(sealed)))
    events = read_events(sealed)
    assert latest_seal(events).sequencer == "winston"
    assert len(settled_events(events)) == 3


def test_only_foreign_seals_means_nothing_is_settled(tmp_path, monkeypatch):
    monkeypatch.delenv("DATACORE_SEQUENCER", raising=False)
    sp = tmp_path / "s"
    EventLog(sp, "mac", sign=False).append("item.create", {"id": "a", "title": "t"})
    EventLog(sp, "mac", sign=False).append("ledger.seal", build_seal_payload(read_events(sp)))
    events = read_events(sp)
    assert latest_seal(events) is None
    ok, detail = verify_seal(events)
    assert ok is None and "no seal yet" in detail and "mac" in detail
    assert settled_events(events) == []


def test_the_sequencer_is_configurable(sealed, monkeypatch):
    monkeypatch.setenv("DATACORE_SEQUENCER", "mallory")
    assert latest_seal(read_events(sealed)) is None
    EventLog(sealed, "mallory", sign=False).append("ledger.seal", build_seal_payload(read_events(sealed)))
    ok, detail = verify_seal(read_events(sealed))
    assert ok is True and "seal by mallory" in detail and "ignored 1 seal(s) by winston" in detail


def test_the_cli_and_the_readers_name_the_same_sequencer(monkeypatch):
    import ledger.seal as seal
    import ledger_seal
    monkeypatch.delenv("DATACORE_SEQUENCER", raising=False)
    assert seal.sequencer() == "winston"
    monkeypatch.setenv("DATACORE_SEQUENCER", "other")
    assert seal.sequencer() == "other" and ledger_seal.sequencer() == "other"


def test_a_forced_seal_says_readers_will_ignore_it(sealed, monkeypatch, capsys):
    import ledger_seal
    monkeypatch.setattr(ledger_seal, "_actor", lambda: "mac")
    EventLog(sealed, "mac", sign=False).append("item.create", {"id": "late", "title": "t"})
    assert ledger_seal.cmd_emit(sealed, force=True) == 0
    assert "readers ignore" in capsys.readouterr().out
    assert len(settled_events(read_events(sealed))) == 3


# --- L2: could-not-tell is not SOUND -----------------------------------------

@pytest.fixture
def fleet(tmp_path, monkeypatch):
    registry = tmp_path / ".datacore/registry/principals.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text("principals:\n  worker: {kind: agent, writes_as: [worker]}\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", registry)
    actor_identity._PRINCIPALS_CACHE.clear()
    space = tmp_path / "1-work"
    EventLog(space, "worker", sign=False).append("item.create", {"id": "i", "title": "t"})
    return tmp_path


def test_unknown_only_prints_unknown_and_exits_2(fleet, capsys):
    import ledger_invariants as inv
    # A tmp space is not a git repository, so `unforked` is could-not-tell.
    rc = inv.main(["--root", str(fleet), "--quick", "--baseline", str(fleet / "none.yaml")])
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert rc == 2
    assert last.startswith("ledger-invariants: UNKNOWN"), last
    assert "SOUND" not in last


def test_broken_still_wins_over_unknown(fleet, capsys):
    import ledger_invariants as inv
    EventLog(fleet / "1-work", "stranger", sign=False).append("item.create", {"id": "s", "title": "t"})
    rc = inv.main(["--root", str(fleet), "--quick", "--baseline", str(fleet / "none.yaml")])
    assert rc == 1
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("ledger-invariants: BROKEN")


def test_sound_needs_every_finding_known_and_accepted():
    import ledger_invariants as inv
    F = inv.Finding
    assert inv.verdict([], []) == ("SOUND", 0)
    assert inv.verdict([], [F("unforked", "s", "x", True)]) == ("UNKNOWN", 2)
    assert inv.verdict([F("hashes", "s", "x")], [F("unforked", "s", "x", True)]) == ("BROKEN", 1)


# --- L4: dispatch on the exact writer ----------------------------------------

@pytest.fixture
def roster(tmp_path, monkeypatch):
    p = tmp_path / "principals.yaml"
    p.write_text("principals:\n"
                 "  gregor: {kind: human, writes_as: [mac]}\n"
                 "  miles: {kind: agent, writes_as: [miles, nightshift]}\n"
                 "  winston: {kind: agent, writes_as: [winston, bridge]}\n"
                 "  team: {kind: agent, writes_as: [left, right]}\n"
                 "  john: {kind: agent, writes_as: []}\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", p)
    return p


@pytest.mark.parametrize("actor,assignee,ok", [
    ("miles", "miles", True),
    ("nightshift", "nightshift", True),
    ("miles", "nightshift", False),       # sibling writer: exact name required
    ("nightshift", "miles", False),       # `miles` is itself a declared writer
    ("bridge", "winston", False),
    ("mac", "gregor", True),              # principal-only name, exactly one writer
    ("left", "team", False),              # principal-only name, two writers: ambiguous
    ("right", "team", False),
    ("john", "john", True),               # no writes_as: the name is its own writer
    ("winston", "miles", False),
    ("stranger", "stranger", True),       # unregistered: string match, as before
    ("miles", "", True),                  # open item (filtered elsewhere as unaddressed)
])
def test_exact_writer_rule(roster, actor, assignee, ok):
    assert actor_identity.dispatchable_by(actor, assignee) is ok


def test_ambiguous_principal_is_explained(roster):
    why = actor_identity.dispatch_ambiguity("team")
    assert why and "left" in why and "right" in why
    assert actor_identity.dispatch_ambiguity("gregor") is None
    assert actor_identity.dispatch_ambiguity("miles") is None


def _space_with(tmp_path, assignee):
    from ledger.policy import Policy, guarded_append
    space = tmp_path / "space"
    guarded_append(EventLog(space, "winston", sign=False), "item.create",
                   {"id": "item-1", "title": "Reconcile the ledger", "assignee": assignee},
                   Policy("gregor", frozenset()))
    return space


def _plan(space, actor, capsys, monkeypatch):
    import ledger_claim
    monkeypatch.setattr(sys, "argv", ["ledger_claim.py", "--space", str(space), "--actor", actor])
    assert ledger_claim.main() == 0
    return capsys.readouterr().out


def test_dispatcher_skips_and_reports_a_sibling_writers_item(tmp_path, roster, capsys, monkeypatch):
    out = _plan(_space_with(tmp_path, "nightshift"), "miles", capsys, monkeypatch)
    assert "would claim" not in out
    assert "addressed to nightshift, a sibling writer" in out, out


def test_dispatcher_skips_and_reports_an_ambiguous_principal(tmp_path, roster, capsys, monkeypatch):
    out = _plan(_space_with(tmp_path, "team"), "left", capsys, monkeypatch)
    assert "would claim" not in out
    assert "AMBIGUOUS" in out and "Reconcile the ledger" in out, out


def test_dispatcher_still_claims_for_a_single_writer_principal(tmp_path, roster, capsys, monkeypatch):
    out = _plan(_space_with(tmp_path, "gregor"), "mac", capsys, monkeypatch)
    assert "would claim" in out, out


# --- L5: the executor exports the depth of what it runs -----------------------

def _claimed(tmp_path, monkeypatch, payload):
    from ledger.policy import Policy, guarded_append
    registry = tmp_path / "principals.yaml"
    registry.write_text("principals:\n  human: {kind: human, writes_as: [human]}\n"
                        "  agent: {kind: agent, writes_as: [worker]}\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", registry)
    policy = Policy("human", frozenset(), principals={"agent": {}})
    log = EventLog(tmp_path, "worker", sign=False)
    guarded_append(log, "item.create", {"id": "one", "title": "t", **payload}, policy)
    guarded_append(log, "item.claim", {"id": "one"}, policy)
    from executors.claude_code import ClaudeCodeExecutor
    ex = ClaudeCodeExecutor()
    ex._actor, ex._space, ex._item = "worker", tmp_path, "one"
    return ex


@pytest.mark.parametrize("payload,want", [({}, "1"), ({"hops": 2}, "3")])
def test_execution_env_exports_parent_hops_plus_one(tmp_path, monkeypatch, payload, want):
    monkeypatch.setenv("DATACORE_HOPS", "0")      # ambient value must not leak through
    ex = _claimed(tmp_path, monkeypatch, payload)
    assert ex._execution_env()["DATACORE_HOPS"] == want
