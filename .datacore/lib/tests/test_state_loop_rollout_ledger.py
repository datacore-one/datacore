"""Tests for state_loop_rollout.migrate_ledger — the ledger half of v2.0.

Org files were migrated to the v2.0 loop; the same state stored inside each
live item's ledger payload was not, and projector.py renders that value
verbatim. The result was a projection emitting 164 `WORKING` and 3 `QUEUED`
headings under a `#+SEQ_TODO` that no longer declares them, so a standalone
parse read them as untyped headings rather than tasks.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import state_loop_rollout as roll  # noqa: E402
from ledger.fold import fold  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ledger.projector import SEQ_TODO, project  # noqa: E402


@pytest.fixture
def space(tmp_path):
    space = tmp_path / "0-testspace"
    (space / "org").mkdir(parents=True)
    log = EventLog(space, "test")
    log.append("item.create", {"id": "a", "title": "legacy working", "state": "WORKING"})
    log.append("item.create", {"id": "b", "title": "legacy queued", "state": "QUEUED"})
    log.append("item.create", {"id": "c", "title": "already canon", "state": "NEXT"})
    log.append("item.create", {"id": "d", "title": "closed one", "state": "WORKING"})
    log.append("item.dismiss", {"id": "d", "kind": "done", "reason": "finished"})
    return space


def _states(space):
    return {i: (it.payload or {}).get("state")
            for i, it in fold(read_events(space)).items.items()}


def test_dry_run_counts_without_appending(space):
    before = len(read_events(space))
    res = roll.migrate_ledger(space, execute=False)

    assert res["swaps"] == {"WORKING": 1, "QUEUED": 1}, "closed 'd' excluded"
    assert len(read_events(space)) == before
    assert _states(space)["a"] == "WORKING"


def test_execute_migrates_live_items_only(space):
    roll.migrate_ledger(space, execute=True)
    states = _states(space)

    assert states["a"] == "NEXT"
    assert states["b"] == "NEXT"
    assert states["c"] == "NEXT"
    assert states["d"] == "WORKING", (
        "a closed item's stored state is history — the projector renders it "
        "from status, never from the payload, so migrating it would edit the "
        "record of what actually happened"
    )


def test_migration_is_append_only(space):
    before = read_events(space)
    roll.migrate_ledger(space, execute=True)
    after = read_events(space)

    assert len(after) > len(before)
    assert [e.hash for e in after[:len(before)]] == [e.hash for e in before], (
        "rewriting history would break every hash chain behind it (DIP-0046)"
    )


def test_running_twice_is_a_no_op(space):
    roll.migrate_ledger(space, execute=True)
    n = len(read_events(space))

    res = roll.migrate_ledger(space, execute=True)

    assert res["swaps"] == {}
    assert len(read_events(space)) == n


def test_projection_emits_only_declared_keywords_after_migration(space):
    """The property the whole exercise is for."""
    import re

    roll.migrate_ledger(space, execute=True)
    text = project(fold(read_events(space)), space=space.name).text

    declared = set(re.findall(r"[A-Z]{3,}", SEQ_TODO.split(":", 1)[1]))
    emitted = {m.group(1) for m in re.finditer(r"^\*+ ([A-Z]{3,}) ", text, re.M)}

    assert emitted <= declared, f"undeclared keywords emitted: {emitted - declared}"
