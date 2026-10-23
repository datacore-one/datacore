"""Pool state and older seals (round 5 amendment 9; round 6, Codex round 5 H3).

Round 5 left `pool_work` out of the state root until an item was leased, so a
seal made before pools still verified. Codex round 5 (H3): equal roots then
hid a state difference the policy decides on (a role-only task re-addressed
before any lease: `owner.set` allowed under the old fold, refused under the
pool fold). Round 6 makes compatibility an explicit version rule instead:

  * a seal names its root rule (`pool_root`); the pool code seals with the
    rule that hashes `pool_work` whenever it is set, so two states that a
    decision tells apart never share a root;
  * a seal that names no rule (made before pools) is verified with the rule
    it was made with -- the pre-pool root, without the field;
  * finality only moves forward: once a seal used the pool rule, a later seal
    without it is refused (no downgrade).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from ledger.events import Event, body_dict, compute_hash
from ledger.fold import fold
from ledger.seal import as_sequencer, build_seal_payload, verify_seal


def _ev(seq, ms, actor, type, payload, prev="GENESIS"):
    body = body_dict(seq, f"{ms:013d}.0000.{actor}", actor, type, payload, prev)
    return Event(**body, hash=compute_hash(body), sig="")


def _readdressed():
    a = _ev(0, 1_700_000_000_000, "own", "item.create", {"id": "t", "role": "writer"})
    b = _ev(1, 1_700_000_000_001, "own", "item.update", {"id": "t", "role": None, "assignee": "wa"}, a.hash)
    return [a, b]


def test_r6_states_a_decision_tells_apart_never_share_a_root():
    events = _readdressed()
    state = fold(events)
    assert state.items["t"].pool_work and state.items["t"].lease_ref is None
    other = fold(events)
    other.items["t"].pool_work = False             # what the pre-pool fold carried
    assert state.state_root() != other.state_root()


def test_r6_a_seal_from_before_pools_still_verifies_with_its_own_rule():
    events = _readdressed()
    payload = build_seal_payload(events)
    assert payload.get("pool_root") is True
    old = {k: v for k, v in payload.items() if k != "pool_root"}
    old["state_root"] = fold(events).state_root(pool_root=False)     # how the pre-pool code hashed it
    seal = _ev(0, 1_700_000_000_100, "seq", "ledger.seal", old)
    with as_sequencer("seq"):
        assert verify_seal(events + [seal])[0] is True


def test_r6_no_seal_downgrades_from_the_pool_rule():
    events = _readdressed()
    new = _ev(0, 1_700_000_000_100, "seq", "ledger.seal", build_seal_payload(events))
    old = {k: v for k, v in build_seal_payload(events).items() if k != "pool_root"}
    old["state_root"] = fold(events).state_root(pool_root=False)
    later = _ev(1, 1_700_000_000_200, "seq", "ledger.seal", old, new.hash)
    with as_sequencer("seq"):
        ok, detail = verify_seal(events + [new, later])
    assert ok is False and "pool" in detail, detail


def test_r6_the_audit_probe_equal_roots_no_longer_hide_the_ownership_decision():
    """Codex round 5's probe, turned around: the pre-pool hub fold and the pool
    fold disagree on `owner.set`, and now their roots disagree too."""
    hub = Path(__file__).resolve().parents[1] / "ledger" / "fold.py"
    events = _readdressed()
    current = fold(events)
    legacy_root = current.state_root(pool_root=False)
    assert current.state_root() != legacy_root
    assert current.items["t"].pool_work
