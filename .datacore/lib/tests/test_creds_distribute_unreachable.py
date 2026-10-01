"""distribute.sh keeps going when a machine drops off mid-run (2026-10-01).

The Mac's run on 2026-10-01 22:56 lost the box between delivery and the drift
check. The drift check's ssh sits in `out="$(...)"` under `set -e`, so the
failed ssh ended the whole script with exit 255: no drift report, no
cross-machine comparison, and the reason was invisible in the log. An
unreachable machine must be reported by name and the run must still compare
the rest.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _creds_fleet_harness import Fleet  # noqa: E402


def test_a_machine_lost_before_the_drift_check_is_named_and_the_run_finishes(tmp_path):
    f = Fleet(tmp_path)
    f.central({"FIXTURE_API_KEY": "fixture-v1"})
    shutil.rmtree(tmp_path / "hosts" / "hostb")
    out = f.distribute()
    assert "hostb: could not audit" in out.stdout, out.stdout[-1500:] + out.stderr[-500:]
    assert "--- cross-machine check ---" in out.stdout, out.stdout[-1500:]
    assert out.returncode == 1, out.returncode
