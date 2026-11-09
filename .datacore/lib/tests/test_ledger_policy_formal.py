"""Formal findings for the LedgerPolicy cluster (2026-09-23), pinned.

Each test replays a counterexample found while modelling the code in
`specs/datacore-lean/DatacoreSpec/LedgerPolicy.lean`; see
`specs/datacore-lean/findings/ledger-policy.md`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import actor_identity  # noqa: E402
import claim_gate  # noqa: E402
import ledger.policy as LP  # noqa: E402
import ledger_claim as lc  # noqa: E402
from ledger.edits import EditConflict, merge_values  # noqa: E402
from ledger.fold import fold  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ledger.policy import Policy, PolicyError, approval_payload_hash, guarded_append  # noqa: E402


@pytest.fixture
def roster(tmp_path, monkeypatch):
    p = tmp_path / "principals.yaml"
    p.write_text("principals:\n"
                 "  human: {kind: human, writes_as: [human]}\n"
                 "  miles: {kind: agent, writes_as: [miles, nightshift]}\n"
                 "  winston: {kind: agent, writes_as: [winston, bridge]}\n"
                 "  agent: {kind: agent, writes_as: [worker]}\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", p)
    return p


def _space(tmp_path, name="space"):
    space = tmp_path / name
    (space / ".datacore" / "events").mkdir(parents=True)
    return space


# --- 1. Delegation depth is derived, not self-declared --------------------

def _chain_policy(monkeypatch, max_hops=1):
    pol = Policy("human", frozenset(), principals={
        "miles": {"max_hops": max_hops, "may_delegate_to": ["winston"]},
        "winston": {"max_hops": max_hops, "may_delegate_to": ["miles"]}})
    monkeypatch.setattr(LP, "load_policy", lambda *a, **k: pol)
    return pol


def _nested(actors):
    then = None
    for n, who in reversed(list(enumerate(actors))):
        item = {"id": f"step{n}", "title": f"step {n}", "assignee": who, "check": "true"}
        if then:
            item["then"] = then
        then = item
    return then


def test_an_a_b_a_follow_up_chain_stops_at_max_hops(tmp_path, roster, monkeypatch):
    """Lean: `LedgerPolicy.Hops.old_chain_unbounded` (the bug) and
    `LedgerPolicy.Hops.chain_depth_bounded` (the fix)."""
    pol = _chain_policy(monkeypatch, max_hops=1)
    space = _space(tmp_path)
    root = _nested(["miles", "winston", "miles", "winston", "miles"])
    guarded_append(EventLog(space, "winston", sign=False), "item.create",
                   {**root, "requested_by": "winston"}, pol)
    results, cur, actor = [], "step0", "miles"
    for _ in range(4):
        payload = fold(read_events(space)).items[cur].payload
        ok, note = lc.chain_follow_up(space, actor, payload)
        results.append((ok, note))
        if not ok:
            break
        cur, actor = payload["then"]["id"], payload["then"]["assignee"]
    items = fold(read_events(space)).items
    assert items["step1"].payload["hops"] == 1
    assert "step2" not in items, results
    assert results[-1][0] is False and "hops" in results[-1][1]


def test_a_follow_up_cannot_understate_its_depth(tmp_path, roster, monkeypatch):
    pol = _chain_policy(monkeypatch, max_hops=3)
    space = _space(tmp_path)
    guarded_append(EventLog(space, "winston", sign=False), "item.create",
                   {"id": "p", "title": "parent", "assignee": "miles", "hops": 2}, pol)
    ok, why = claim_gate.check_create("miles", {"id": "c", "title": "c", "after": "p", "hops": 0},
                                      policy=pol, space_dir=space)
    assert not ok and "at least 3" in why
    ok, why = claim_gate.check_create("miles", {"id": "c", "title": "c", "after": "p", "hops": 3},
                                      policy=pol, space_dir=space)
    assert ok, why
    ok, why = claim_gate.check_create("miles", {"id": "c", "title": "c", "after": "nowhere"},
                                      policy=pol, space_dir=space)
    assert not ok and "nowhere" in why


def test_chain_follow_up_overrides_a_then_that_claims_zero_hops(tmp_path, roster, monkeypatch):
    pol = _chain_policy(monkeypatch, max_hops=3)
    space = _space(tmp_path)
    parent = {"id": "p", "title": "parent", "assignee": "miles", "hops": 1,
              "then": {"id": "c", "title": "c", "assignee": "winston", "check": "true", "hops": 0}}
    guarded_append(EventLog(space, "winston", sign=False), "item.create", parent, pol)
    ok, note = lc.chain_follow_up(space, "miles", parent)
    assert ok, note
    assert fold(read_events(space)).items["c"].payload["hops"] == 2


# --- 2. An approval granted by an update is retained ----------------------

def test_approval_added_by_update_survives_an_unguarded_effects_drop(tmp_path, roster):
    """Lean: `LedgerPolicy.Approval.old_update_approval_lost` and
    `LedgerPolicy.Approval.claim_keeps_approval`."""
    pol = Policy("human", frozenset({"payment"}), principals={"agent": {}})
    space = _space(tmp_path)
    w = EventLog(space, "worker", sign=False)
    guarded_append(w, "item.create", {"id": "one", "title": "t"}, pol)
    target = {**fold(read_events(space)).items["one"].payload, "effects": ["payment"]}
    grant = guarded_append(EventLog(space, "human", sign=False), "approval.grant",
                           {"item": "one", "payload_hash": approval_payload_hash(target)}, pol)
    guarded_append(w, "item.update", {"id": "one", "effects": ["payment"], "approval_ref": grant.hash}, pol)
    w.append("item.update", {"id": "one", "effects": [], "approval_ref": None})  # unguarded import
    with pytest.raises(PolicyError, match="approval|bind"):
        guarded_append(w, "item.claim", {"id": "one"}, pol)


def test_approval_added_by_update_survives_a_guarded_effects_drop(tmp_path, roster):
    pol = Policy("human", frozenset({"payment"}), principals={"agent": {}})
    space = _space(tmp_path)
    w = EventLog(space, "worker", sign=False)
    guarded_append(w, "item.create", {"id": "one", "title": "t"}, pol)
    target = {**fold(read_events(space)).items["one"].payload, "effects": ["payment"]}
    grant = guarded_append(EventLog(space, "human", sign=False), "approval.grant",
                           {"item": "one", "payload_hash": approval_payload_hash(target)}, pol)
    guarded_append(w, "item.update", {"id": "one", "effects": ["payment"], "approval_ref": grant.hash}, pol)
    with pytest.raises(PolicyError, match="approval|bind"):
        guarded_append(w, "item.update", {"id": "one", "effects": []}, pol)


def test_an_item_never_approved_still_needs_no_grant(tmp_path, roster):
    pol = Policy("human", frozenset({"payment"}), principals={"agent": {}})
    space = _space(tmp_path)
    w = EventLog(space, "worker", sign=False)
    guarded_append(w, "item.create", {"id": "one", "title": "t"}, pol)
    guarded_append(w, "item.update", {"id": "one", "title": "t2"}, pol)
    guarded_append(w, "item.claim", {"id": "one"}, pol)


# --- 3. merge_values laws (documented Python-equality caveat) --------------

@pytest.mark.parametrize("b,l,r", [
    ({"a": 1, "b": {"c": 2}}, {"a": 1, "b": {"c": 3}}, {"a": 5, "b": {"c": 2}}),
    ({"a": 1}, {"a": 2}, {"a": 3}),
    ({"a": 1}, {}, {"a": 2}),
    ({"a": 1}, {"a": 2}, {}),
    ({}, {"x": 1}, {"x": 2}),
    ({}, {"x": 1}, {"y": 2}),
    (1, 2, 1), (1, 1, 2), (1, 2, 3),
])
def test_merge_is_symmetric_including_conflicts(b, l, r):
    """Lean: `LedgerPolicy.Merge.merge_symm`."""
    def run(x, y):
        try:
            return ("ok", merge_values(b, x, y))
        except EditConflict:
            return ("conflict", None)
    assert run(l, r) == run(r, l)


def test_merge_identity_laws_hold_up_to_python_equality():
    """Lean: `merge_base_local` / `merge_base_remote`, and the representational
    counterexample `bool_one_edit_lost`: True == 1 in Python, so an edit from 1
    to True reads as no change and the remote's 1 is kept."""
    assert merge_values({"a": 1}, {"a": 1}, {"a": 2}) == {"a": 2}
    assert merge_values({"a": 1}, {"a": 2}, {"a": 1}) == {"a": 2}
    kept = merge_values(1, True, 1)
    assert kept == True and kept is not True  # noqa: E712 -- the point of the test


# --- 4. The dead-letter counts attempts, not writes -----------------------

def test_no_op_releases_by_a_stranger_do_not_dead_letter_real_work(tmp_path, roster, capsys, monkeypatch):
    """Lean: `LedgerPolicy.Dispatch.old_deadletter_griefable` and
    `LedgerPolicy.Dispatch.deadletter_counts_applied`."""
    pol = Policy("human", frozenset(), principals={"miles": {}, "winston": {}})
    monkeypatch.setattr(LP, "load_policy", lambda *a, **k: pol)
    space = _space(tmp_path)
    guarded_append(EventLog(space, "winston", sign=False), "item.create",
                   {"id": "y", "title": "real work", "assignee": "miles"}, pol)
    for _ in range(3):
        EventLog(space, "bridge", sign=False).append("item.release", {"id": "y", "owner": "bridge"})
    monkeypatch.setattr(sys, "argv", ["ledger_claim.py", "--space", str(space), "--actor", "miles"])
    lc.main()
    out = capsys.readouterr().out
    assert "deadletter" not in out.lower(), out
    assert "would claim" in out


def test_applied_releases_still_dead_letter(tmp_path, roster, capsys, monkeypatch):
    pol = Policy("human", frozenset(), principals={"miles": {}, "winston": {}})
    monkeypatch.setattr(LP, "load_policy", lambda *a, **k: pol)
    space = _space(tmp_path)
    guarded_append(EventLog(space, "winston", sign=False), "item.create",
                   {"id": "y", "title": "real work", "assignee": "miles"}, pol)
    m = EventLog(space, "miles", sign=False)
    for _ in range(3):
        guarded_append(m, "item.claim", {"id": "y"}, pol)
        m.append("item.release", {"id": "y", "owner": "miles"})
    monkeypatch.setattr(sys, "argv", ["ledger_claim.py", "--space", str(space), "--actor", "miles", "--execute"])
    lc.main()
    assert "DEADLETTER" in capsys.readouterr().out
    assert fold(read_events(space)).items["y"].status == "dismissed"
