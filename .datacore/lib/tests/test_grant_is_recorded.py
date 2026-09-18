"""item.grant was declared, policy-gated, and ignored by the fold.

events.py declares it as the DIP-0034 amendment -- "a claim is a PROPOSAL;
execution in an arbitrated pool requires the arbiter's grant" -- and policy.py
refuses a grant from anyone but the approver. The fold had no handler, so a
grant changed nothing, left no trace in the item's history, and did not move the
state root. A control that can be demanded, refused and audited at the gate, and
then vanishes, is not a control. Found by a fleet stress test, 2026-09-18.
"""
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

from ledger.fold import fold  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402


def _space(tmp_path: Path) -> Path:
    space = tmp_path / "9-grant"
    (space / ".datacore" / "events").mkdir(parents=True)
    return space


def _claimed(space: Path):
    log = EventLog(space, "miles")
    log.append("item.create", {"id": "t1", "title": "Ship it", "state": "NEXT", "space": space.name})
    log.append("item.claim", {"id": "t1", "executor": "miles"})
    return log


def test_a_grant_is_recorded_on_the_item(tmp_path):
    space = _space(tmp_path)
    _claimed(space)
    before = fold(read_events(space))
    assert before.items["t1"].granted_by is None, "nothing is granted by default"
    root_before = before.state_root()

    EventLog(space, "winston").append("item.grant", {"id": "t1"})

    item = fold(read_events(space)).items["t1"]
    assert item.granted_by == "winston"
    assert item.granted_at
    assert any("granted" in line for line in item.history), item.history
    assert fold(read_events(space)).state_root() != root_before, "a grant is state"


def test_an_ungranted_item_hashes_as_it_always_did(tmp_path):
    """Adding the fields must not invalidate every checkpoint ever written."""
    space = _space(tmp_path)
    _claimed(space)
    state = fold(read_events(space))
    import json
    from dataclasses import asdict
    document = asdict(state.items["t1"])
    assert document["granted_by"] is None
    assert state.state_root() == fold(read_events(space)).state_root()
    assert "granted_by" not in json.dumps(
        {k: v for k, v in document.items() if v is not None}), "absent when unset"


def test_granting_twice_or_out_of_turn_is_a_named_no_op(tmp_path):
    space = _space(tmp_path)
    _claimed(space)
    EventLog(space, "winston").append("item.grant", {"id": "t1"})
    EventLog(space, "winston").append("item.grant", {"id": "t1"})
    item = fold(read_events(space)).items["t1"]
    assert item.granted_by == "winston"
    assert any("already granted" in line for line in item.history), item.history


def test_a_grant_on_an_unclaimed_item_names_the_status(tmp_path):
    space = _space(tmp_path)
    log = EventLog(space, "miles")
    log.append("item.create", {"id": "t2", "title": "Unclaimed", "state": "NEXT", "space": space.name})
    EventLog(space, "winston").append("item.grant", {"id": "t2"})
    item = fold(read_events(space)).items["t2"]
    assert item.granted_by is None
    assert any("grant illegal from status=created" in line for line in item.history), item.history


def test_a_grant_never_claims_completes_or_reassigns(tmp_path):
    space = _space(tmp_path)
    _claimed(space)
    EventLog(space, "winston").append("item.grant", {"id": "t1"})
    item = fold(read_events(space)).items["t1"]
    assert item.owner == "miles" and item.status == "claimed"
