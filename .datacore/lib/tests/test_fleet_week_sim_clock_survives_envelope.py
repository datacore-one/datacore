"""The simulated clock reaches a job that was started with a cleaned environment.

The job envelope (jobs/run.py) gives every job one normalized environment and
drops everything else, on purpose. In the simulator that also dropped the fake
clock: a duty started through the envelope (the workstation's job verifier,
after a join) wrote ledger events stamped with the REAL date, weeks after the
simulated one. The owner's nightly task, written next in the same log, sat on
that timestamp, so the overnight agent's "I take this" was always older than
the task and never counted (two-night runs of 2026-11-18 and 2026-11-19: the
task adopted every hour, nothing done overnight).

The simulator now leaves the clock settings in each simulated home, and a
python started without them picks them up and starts again with them.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import fleet_week_sim as sim  # noqa: E402


def _run(home: Path, hook_dir: Path, extra: dict | None = None) -> str:
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(hook_dir), **(extra or {})}
    r = subprocess.run([sys.executable, "-c",
                        f"import {sim.CLOCK_HOOK}, os; print(os.environ.get('FAKETIME', 'none'), "
                        "os.environ.get('FAKETIME_DONT_FAKE_MONOTONIC', 'none'))"],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _hook(tmp_path: Path) -> Path:
    d = tmp_path / "hook"
    d.mkdir()
    (d / f"{sim.CLOCK_HOOK}.py").write_text(sim.CLOCK_HOOK_SOURCE)
    return d


def test_a_python_started_without_the_clock_gets_it_from_its_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    sim.leave_clock(home, {"LD_PRELOAD": "", "FAKETIME": "-4200000", "FAKETIME_DONT_FAKE_MONOTONIC": "0",
                           "PATH": "/not/kept", "HOME": "/not/kept"})
    assert _run(home, _hook(tmp_path)) == "-4200000 0"


def test_only_the_clock_settings_are_left_in_the_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    sim.leave_clock(home, {"FAKETIME": "+5", "PATH": "/x", "DATACORE_ACTOR": "someone"})
    text = (home / sim.CLOCK_FILE).read_text()
    assert "FAKETIME=+5" in text and "PATH" not in text and "DATACORE_ACTOR" not in text


def test_a_python_that_already_has_the_clock_keeps_its_own(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    sim.leave_clock(home, {"FAKETIME": "-1"})
    assert _run(home, _hook(tmp_path), {"FAKETIME": "-999"}).split()[0] == "-999"


def test_a_home_without_clock_settings_changes_nothing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert _run(home, _hook(tmp_path)) == "none none"


def test_simulated_machines_carry_the_repository_registry():
    """The registry is install-private (not in git). Without it a simulated
    machine refuses to sync its own installation root: 'repository not in
    registry' on every overnight run of 2026-11-18 and 2026-11-19."""
    assert sim.CONFIG_FILES.get("repositories.yaml") == ".datacore/registry/repositories.yaml"
