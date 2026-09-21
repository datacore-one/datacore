"""In a dark wake, hold back only what a dark wake can cause.

A lid-closed maintenance wake can drop an ssh call; that reads "unreachable".
It cannot remove a hooks directory. So the previous log is kept only when the
machine is in a dark wake AND every finding is unreachable. Real drift is
always written. (2026-09-17: a 45-second maintenance wake at 08:57 produced a
false drift alert.)
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]

# The detector counts drift and could-not-tell SEPARATELY since 2026-09-21: a
# host it failed to reach is 0 drift, 1 unreachable. It used to report the same
# machine as both, so the summary contradicted its own body ("UNREACHABLE
# winston" above "2 with drift") and the contract believed the summary.
UNREACHABLE = "  UNREACHABLE hermes  x\n\nconfig-drift: 5 machine(s), 0 with drift, 1 unreachable\n"
DRIFT = "  MISSING-HOOKS hermes  x\n\nconfig-drift: 5 machine(s), 1 with drift, 0 unreachable\n"
CLEAN = "  ok  hermes  x\n\nconfig-drift: 5 machine(s), 0 with drift, 0 unreachable\n"


def _run(tmp_path, detector_output, rc, wake):
    installed = tmp_path / "installed"
    (installed / "detectors").mkdir(parents=True)
    shutil.copytree(LIB / "jobs", installed / "jobs", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("config_drift_run.sh", "runtime_shell.sh"):
        shutil.copyfile(LIB / name, installed / name)
    (installed / "detectors" / "config_drift.py").write_text(
        f"import sys\nsys.stdout.write({detector_output!r})\nraise SystemExit({rc})\n")
    state = tmp_path / "state"
    state.mkdir()
    (state / "config-drift.log").write_text("PREVIOUS GOOD LOG\n")
    env = dict(os.environ, DATACORE_STATE=str(state), DATACORE_ROOT=str(tmp_path),
               DATACORE_PYTHON=sys.executable, DATACORE_WAKE_STATE=wake)
    proc = subprocess.run(["bash", str(installed / "config_drift_run.sh")], env=env,
                          capture_output=True, text=True, timeout=60)
    return proc, (state / "config-drift.log").read_text()


def test_dark_wake_with_only_unreachable_keeps_the_previous_log(tmp_path):
    proc, log = _run(tmp_path, UNREACHABLE, 1, "dark")
    assert proc.returncode == 0, proc.stderr
    assert log == "PREVIOUS GOOD LOG\n"
    assert "kept previous log" in (tmp_path / "state" / "config-drift.skipped.log").read_text()


def test_full_wake_unreachable_also_keeps_the_previous_log(tmp_path):
    """Unreachable is could-not-tell, on any kind of wake, and this job does not
    own reachability.

    This used to assert the opposite: awake and unable to reach a host meant
    the finding was real and worth writing. On a server that reasoning holds.
    On a laptop it does not -- 2026-09-20 21:28, properly awake, no route to
    two hosts (a train, a captive portal, tailscale not yet up), both answering
    on the first try next morning. The contract paged for a fleet that was fine.

    And reachability already has an owner: box-fleet-probe, which runs from the
    always-on box that is always on the network, and which says "nightshift is
    DOWN" when a host is genuinely down. Two jobs alarming on one condition is
    how one of them becomes noise. This one reports DRIFT among the hosts it
    could reach; a run that reached nobody learned nothing and must not
    overwrite the last run that did.

    Nothing is hidden: the artifact keeps its old mtime, freshness is judged in
    AWAKE time, and a fleet this machine cannot check for 26 waking hours goes
    stale and fails on exactly those grounds.
    """
    proc, log = _run(tmp_path, UNREACHABLE, 1, "full")
    assert proc.returncode == 0, proc.stderr
    assert log == "PREVIOUS GOOD LOG\n"


def test_real_drift_is_written_even_in_a_dark_wake(tmp_path):
    proc, log = _run(tmp_path, DRIFT, 1, "dark")
    assert proc.returncode == 1 and "MISSING-HOOKS" in log


@pytest.mark.parametrize("wake", ["dark", "full"])
def test_a_clean_result_is_always_written(tmp_path, wake):
    proc, log = _run(tmp_path, CLEAN, 0, wake)
    assert proc.returncode == 0 and "0 with drift" in log


def test_current_capabilities_decide_not_the_log_order():
    from importlib import import_module
    sys.path.insert(0, str(LIB))
    awake = import_module("jobs.awake")
    os.environ.pop("DATACORE_WAKE_STATE", None)
    dark = "Current System Capabilities are: CPU Network \nCurrent Power State: 4\n"
    full = "Current System Capabilities are: CPU Graphics Audio Network \nCurrent Power State: 4\n"
    ends_in_sleep = "2026-09-17 14:01:49 +0200 Sleep               \tEntering Sleep state\n"
    assert awake.in_dark_wake(systemstate=dark, log="") is True
    assert awake.in_dark_wake(systemstate=full, log=ends_in_sleep) is False
