"""A contract's artifact must never be readable in a half-written state.

box-ledger-ingest reads ~/.datacore/state/ledger-ingest.log, winston's verifier
runs on the hour, and the hourly ingest truncated that same file on the hour and
then took minutes to fill it. On 2026-09-18 at 10:00 the verifier read the empty
file and paged -- "regex '0 space(s) failed' did not match -- file is empty" --
about an ingest that was working.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]


@pytest.fixture
def staged(tmp_path):
    """The wrapper, its runtime shim, and a deliberately SLOW ingest."""
    scripts = tmp_path / "lib"
    scripts.mkdir()
    for name in ("ledger_ingest_hourly.sh", "runtime_shell.sh"):
        (scripts / name).write_bytes((LIB / name).read_bytes())
        (scripts / name).chmod(0o755)
    (scripts / "ledger_ingest_org.py").write_text(
        "import sys, time\ntime.sleep(2)\nprint('imported 0 task(s) across 9 space(s); 0 space(s) failed')\n")
    state = tmp_path / "state"
    state.mkdir()
    (state / "ledger-ingest.log").write_text("imported 0 task(s) across 9 space(s); 0 space(s) failed\n")
    return scripts, state


def test_the_previous_log_stands_until_the_new_one_is_complete(staged, tmp_path):
    scripts, state = staged
    log = state / "ledger-ingest.log"
    env = dict(os.environ, DATACORE_ROOT=str(tmp_path), DATACORE_STATE=str(state),
               DATACORE_PYTHON=sys.executable)
    run = subprocess.Popen(["bash", str(scripts / "ledger_ingest_hourly.sh")], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)   # mid-ingest: the old log must still be whole
        assert "0 space(s) failed" in log.read_text(), \
            "the artifact was truncated while its producer was still running"
    finally:
        run.wait(timeout=60)

    assert "0 space(s) failed" in log.read_text()
    assert not list(state.glob("ledger-ingest.log.*")), "the temporary file is renamed, not left behind"
