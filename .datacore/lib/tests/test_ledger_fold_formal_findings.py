"""Defects the Lean model (.datacore/specs/datacore-lean) found in the fold.

Each test is the counterexample trace from the corresponding Lean theorem,
replayed against the real fold. The Lean model now proves the fixed
behaviour for every event sequence; these pin the Python to it.
"""

from ledger.events import Event
from ledger.fold import fold

_COND = {"version": 1, "status": "dismissed", "owner": None, "resolves": []}


def _ev(seq, actor, type_, payload):
    return Event(seq=seq, hlc=f"{seq:013d}.0000.{actor}", actor=actor, type=type_,
                 payload=payload, prev="", hash=f"h{seq}", sig="")


def _dismissed_item():
    return [_ev(0, "w", "item.create", {"id": "x", "title": "t"}),
            _ev(1, "w", "item.dismiss", {"id": "x"})]


# --- Finding 1: dismissal freezes the item; conflicts move only visibly --------
#
# Recording a conflict on a dismissed item, and reconciling it, are the
# designed route through ledger_resolve_conflict.py. The defect was that the
# docstring claimed nothing changes and the reconciling dismiss logged itself
# as a no-op while it deleted conflicts.


def test_conditional_update_on_dismissed_item_freezes_lifecycle_and_says_conflict():
    base = _dismissed_item()
    cond = {**_COND, "base": {"title": {"exists": True, "value": "t"}}, "terminal": False}
    before = fold(base).items["x"]
    after = fold(base + [_ev(2, "w", "item.update", {"id": "x", "title": "u", "_merge": cond})]).items["x"]
    assert (after.status, after.owner, after.payload, after.closed_at) == \
        (before.status, before.owner, before.payload, before.closed_at)
    assert list(after.edit_conflicts) == ["h2"]
    assert "conflict" in after.history[-1] and "no-op" not in after.history[-1]


def test_reconciling_dismiss_of_dismissed_item_is_logged_as_applied():
    live_cond = {"version": 1, "base": {"title": {"exists": True, "value": "stale"}},
                 "status": "created", "owner": None, "terminal": False, "resolves": []}
    events = [
        _ev(0, "w", "item.create", {"id": "x", "title": "t"}),
        _ev(1, "w", "item.update", {"id": "x", "title": "u", "_merge": live_cond}),
        _ev(2, "w", "item.dismiss", {"id": "x"}),
    ]
    assert fold(events).items["x"].edit_conflicts, "precondition: a live conflict was recorded"
    clear = {**_COND, "terminal": True, "resolves": ["h1"],
             "base": {"id": {"exists": True, "value": "x"},
                      "title": {"exists": True, "value": "t"}}}
    item = fold(events + [_ev(3, "w", "item.dismiss", {"id": "x", "_merge": clear})]).items["x"]
    assert item.edit_conflicts == {} and item.status == "dismissed"
    assert "applied (reconciled 1 conflict(s)" in item.history[-1]


def test_plain_dismiss_of_dismissed_item_is_still_a_noop():
    item = fold(_dismissed_item() + [_ev(2, "w", "item.dismiss", {"id": "x"})]).items["x"]
    assert item.history[-1].endswith("no-op (item dismissed)")


def test_fieldless_conditional_update_that_reconciles_is_logged_as_applied():
    live_cond = {"version": 1, "base": {"title": {"exists": True, "value": "stale"}},
                 "status": "created", "owner": None, "terminal": False, "resolves": []}
    events = [
        _ev(0, "w", "item.create", {"id": "x", "title": "t"}),
        _ev(1, "w", "item.update", {"id": "x", "title": "u", "_merge": live_cond}),
    ]
    clear = {"version": 1, "base": {}, "status": "created", "owner": None,
             "terminal": False, "resolves": ["h1"]}
    item = fold(events + [_ev(2, "w", "item.update", {"id": "x", "_merge": clear})]).items["x"]
    assert item.edit_conflicts == {}
    assert item.history[-1].endswith("applied (reconciled 1 conflict(s))")


# --- Finding 2: the completer cannot verify their own work -------------------


def test_completer_cannot_verify():
    events = [_ev(0, "w", "item.create", {"id": "y"}),
              _ev(1, "a", "item.claim", {"id": "y"}),
              _ev(2, "a", "item.complete", {"id": "y"}),
              _ev(3, "a", "item.verify", {"id": "y"})]
    item = fold(events).items["y"]
    assert item.status == "completed"
    assert "no-op" in item.history[-1]


def test_another_actor_verifies():
    events = [_ev(0, "w", "item.create", {"id": "y"}),
              _ev(1, "a", "item.claim", {"id": "y"}),
              _ev(2, "a", "item.complete", {"id": "y"}),
              _ev(3, "reviewer", "item.verify", {"id": "y"})]
    assert fold(events).items["y"].status == "verified"


# --- Finding 3: a grant belongs to the claim it was granted for ---------------


def test_release_clears_grant():
    events = [_ev(0, "w", "item.create", {"id": "z"}),
              _ev(1, "a", "item.claim", {"id": "z"}),
              _ev(2, "approver", "item.grant", {"id": "z"}),
              _ev(3, "a", "item.release", {"id": "z"}),
              _ev(4, "b", "item.claim", {"id": "z"})]
    item = fold(events).items["z"]
    assert (item.status, item.owner) == ("claimed", "b")
    assert item.granted_by is None and item.granted_at is None
    regrant = fold(events + [_ev(5, "approver", "item.grant", {"id": "z"})]).items["z"]
    assert regrant.granted_by == "approver"


def test_owner_override_clears_grant():
    events = [_ev(0, "w", "item.create", {"id": "z"}),
              _ev(1, "a", "item.claim", {"id": "z"}),
              _ev(2, "approver", "item.grant", {"id": "z"}),
              _ev(3, "admin", "owner.set", {"id": "z", "owner": "b"})]
    item = fold(events).items["z"]
    assert item.owner == "b"
    assert item.granted_by is None and item.granted_at is None


def test_owner_override_to_same_owner_keeps_grant():
    events = [_ev(0, "w", "item.create", {"id": "z"}),
              _ev(1, "a", "item.claim", {"id": "z"}),
              _ev(2, "approver", "item.grant", {"id": "z"}),
              _ev(3, "admin", "owner.set", {"id": "z", "owner": "a"})]
    assert fold(events).items["z"].granted_by == "approver"
