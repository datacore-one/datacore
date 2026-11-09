"""A laptop night, as a test the suite audit runs.

laptop_night_drill.py builds scratch artifacts with chosen ages, fakes `pmset`
with a chosen power log, and runs the RUNNER's job_verify.py under a cron-like
environment -- eight scenarios, about three seconds. Until 2026-09-22 it ran as
its own job contract (mac-drills) on a 04:45 clock, which is exactly what a
visitor may not carry. It is a test, so it lives with the tests: the suite
audit runs lib/tests on the mac, and a drill that fails still turns that
contract red.

It tests what is DEPLOYED (~/.datacore/v2-runner), not ~/Data: a fix committed
but not synced into the runner fails here, on purpose. Off macOS it skips --
there is no sleep to account for, and it fails 4/8 there, correctly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
RUNNER = Path.home() / ".datacore" / "v2-runner" / ".datacore" / "lib" / "job_verify.py"


@pytest.mark.skipif(sys.platform != "darwin", reason="models a macOS night; no sleep to account for here")
@pytest.mark.skipif(not RUNNER.is_file(), reason="no deployed runner on this machine")
def test_a_laptop_night_reads_as_eight_correct_verdicts():
    proc = subprocess.run([sys.executable, str(LIB / "laptop_night_drill.py")],
                          capture_output=True, text=True, timeout=300)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out[-3000:]
    # The count, not only the exit code: a drill that stopped collecting
    # scenarios still exits 0 and still says every one held.
    assert "8/8 as expected" in out, out[-3000:]
