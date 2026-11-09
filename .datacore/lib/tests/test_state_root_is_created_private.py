"""Whatever creates ~/.datacore/state must create it 0700.

file_utils.private_state_directory refuses a state directory that others can
read, and every ledger, journal and manifest write goes through it. So the
FIRST writer to touch a fresh state directory decides whether anything after
it works: created with the default 0755, every later private write raises
"runtime state directory must be private to its identity". nightshift's
preflight record did exactly that on 2026-09-21 (four suite failures, and a
fresh host's first night); these are the core writers with the same shape.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

STATE_WRITERS = ["visitor_join.py", "jobs/autofix.py", "jobs/recurrence.py", "delegation_canary.py",
                 "cadence_liveness.py", "detectors/actor_presence.py", "detectors/id_churn.py"]


@pytest.mark.parametrize("name", STATE_WRITERS)
def test_no_state_writer_creates_a_directory_with_the_default_mode(name):
    src = (LIB / name).read_text()
    bad = [l.strip() for l in src.splitlines()
           if ".mkdir(" in l and "exist_ok=True" in l and "mode=0o700" not in l]
    assert not bad, f"{name} creates a state directory readable by others:\n  " + "\n  ".join(bad)


def test_a_fresh_state_root_written_by_the_join_record_is_private(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("DATACORE_STATE", str(state))
    import visitor_join as vj
    importlib.reload(vj)
    vj._write_atomic(vj.RECORD, {"joined_at": 1})
    assert state.stat().st_mode & 0o777 == 0o700
    # ...and the strict helper now accepts it, which is the whole point.
    from file_utils import private_state_directory
    assert private_state_directory().is_relative_to(state)
