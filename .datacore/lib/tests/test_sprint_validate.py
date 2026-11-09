"""sprint_validate.py: the three cases that used to fail open (#78210bf7).

Each test covers one hole:
  1. Out-of-vocabulary item state (schema was untyped → anything passed).
  2. Backlog item missing required state field (no required constraint).
  3. Closed sprint keeps an in-flight item not listed in carryover (JSON
     Schema cannot express "unless listed in carryover"; Python check needed).

What would have to break for these to fail:
 - item `state` goes back to untyped → hole-1 test passes the invalid file.
 - backlog items drop the required constraint → hole-2 test passes.
 - closed_sprint_errors() is removed or stops matching carryover entries →
   hole-3 test passes the invalid file, or the carried-item test rejects it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parent.parent
VALIDATOR = LIB / "sprint_validate.py"

BASE = {
    "sprint_id": "2026-W40-sprint1",
    "project": "5-test/2-projects/repo",
    "status": "active",
    "cadence": "1w",
    "dates": {"start": "2026-10-01", "end": "2026-10-07"},
    "goal": "test sprint",
    "okr_links": [],
    "facilitator": {"operational": "miles", "strategic": "gregor"},
    "miles_routing": {
        "primary_repo": "test/repo",
        "overflow_repo": "test/overflow",
        "sprint_tag_filter": "sprint-1",
    },
    "backlog": [{"id": "B1", "state": "todo"}],
    "stretch": [],
    "hitl_log": [],
    "claims": [],
    "done_when": "all items done",
}


def _run(doc: dict, tmp_path: Path) -> subprocess.CompletedProcess:
    p = tmp_path / "sprint.yaml"
    p.write_text(yaml.safe_dump(doc))
    return subprocess.run([sys.executable, str(VALIDATOR), str(p)],
                          capture_output=True, text=True, timeout=30)


def _sprint(**overrides) -> dict:
    import copy
    d = copy.deepcopy(BASE)
    d.update(overrides)
    return d


def test_control_valid_sprint_passes(tmp_path):
    """Baseline: if this fails the negatives prove nothing."""
    assert _run(_sprint(), tmp_path).returncode == 0


# ── Hole 1: item state vocabulary ────────────────────────────────────────────

@pytest.mark.parametrize("state", ["finished", "Done", "merged", ""])
def test_out_of_vocabulary_item_state_is_rejected(state, tmp_path):
    """item.state must be one of the declared vocabulary; any other value fails."""
    doc = _sprint(backlog=[{"id": "B1", "state": state}])
    r = _run(doc, tmp_path)
    assert r.returncode != 0, f"item state {state!r} was accepted (hole 1 open)"


# ── Hole 2: backlog item requires id + state ─────────────────────────────────

def test_backlog_item_without_state_is_rejected(tmp_path):
    """A backlog item with no state field must fail; it was previously accepted."""
    doc = _sprint(backlog=[{"id": "B1"}])
    r = _run(doc, tmp_path)
    assert r.returncode != 0, "backlog item without state was accepted (hole 2 open)"


# ── Hole 3: closed sprint may not keep an in-flight item ─────────────────────

@pytest.mark.parametrize("state", ["claimed", "in-progress", "review"])
def test_closed_sprint_cannot_keep_an_item_in_flight(state, tmp_path):
    """A closed sprint with an in-flight item not in carryover must fail."""
    doc = _sprint(
        status="closed",
        dates={"start": "2026-10-01", "end": "2026-10-07", "retro": "2026-10-08"},
        carryover=[],
        backlog=[{"id": "B1", "state": state}],
    )
    r = _run(doc, tmp_path)
    assert r.returncode != 0, (
        f"closed sprint with {state!r} item accepted (hole 3 open)"
    )
    assert "B1" in (r.stdout + r.stderr)


def test_carried_in_flight_item_is_accepted(tmp_path):
    """An in-flight item listed in carryover is fine — explicitly deferred."""
    doc = _sprint(
        status="closed",
        dates={"start": "2026-10-01", "end": "2026-10-07", "retro": "2026-10-08"},
        carryover=["B1"],
        backlog=[{"id": "B1", "state": "review"}],
    )
    assert _run(doc, tmp_path).returncode == 0


def test_active_sprint_may_have_items_in_flight(tmp_path):
    """The check must not apply to open sprints."""
    doc = _sprint(backlog=[{"id": "B1", "state": "in-progress"}])
    assert _run(doc, tmp_path).returncode == 0
