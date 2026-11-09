"""OPS-19: Tests never write into my real logs, activity feed, alerts or state.

Evidence (2026-10-28 fleet-logs report): 2,245 pytest events in the Mac's live
activity stream (~/.datacore/cos/agent-stream) and 160 on dayshift, 190 of them
fake failures. `agent_emit.py:52` fixes EVENT_LOG_DIR from `Path.home()` when
the module is IMPORTED, so a test that isolates HOME or the state root
afterwards -- which is what every pytest fixture does, since it runs after
collection imported the code -- still writes into the operator's real stream.
The chief-of-staff suite's conftest isolates only DATACORE_STATE, and
`tests/test_service.py` drives `lib/service.py:311,322,391`, which emit through
agent_emit into that real stream.

Kind: deterministic, hermetic. The real home is never written: every run is a
subprocess whose HOME is a directory under tmp_path ("the operator's home"),
and the assertion is about where the event lands relative to that stand-in.

Graded:
- with HOME pointed somewhere else AFTER agent_emit was imported, an emitted
  event lands under the new HOME, not the one seen at import (paths resolve
  when the event is written);
- running a real chief-of-staff test that emits (run_daily happy path) leaves
  the stand-in operator home's activity stream untouched.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
COS = LIB.parent / "modules" / "chief-of-staff"


def _env(home: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AGENT_STREAM_RELAY")}
    env.update({"HOME": str(home), "PYTHONPATH": str(LIB), **extra})
    return env


def _stream_files(home: Path) -> list[Path]:
    d = home / ".datacore" / "cos" / "agent-stream"
    return sorted(d.glob("events-*.jsonl")) if d.is_dir() else []


def test_an_event_lands_under_the_home_in_force_when_it_is_written(tmp_path):
    at_import = tmp_path / "operator-home"
    now = tmp_path / "test-home"
    at_import.mkdir()
    now.mkdir()
    code = (
        "import os, agent_emit\n"
        f"os.environ['HOME'] = {str(now)!r}\n"
        "ev = agent_emit.emit('test.event', 'pytest', 'OPS-19 probe')\n"
        "assert ev, 'emit returned nothing'\n"
    )
    r = subprocess.run([sys.executable, "-c", code], env=_env(at_import),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    leaked = _stream_files(at_import)
    assert not leaked, (
        "agent_emit wrote into the home it saw at import, not the one in force when "
        f"the event was written (EVENT_LOG_DIR is fixed at import): {leaked}")
    assert _stream_files(now), "the event did not land under the HOME in force at emit time"


def test_the_chief_of_staff_suite_leaves_the_operators_stream_alone(tmp_path):
    home = tmp_path / "operator-home"
    home.mkdir()
    env = _env(home, DATACORE_ROOT=str(LIB.parents[1]))
    env.pop("PROMISE_EVALS_ALL", None)
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(COS / "tests" / "test_service.py") + "::test_run_daily_happy_path"],
        cwd=str(COS), env=env, capture_output=True, text=True, timeout=300)
    assert "1 passed" in r.stdout, f"the chief-of-staff test itself did not run:\n{r.stdout[-1500:]}{r.stderr[-800:]}"
    leaked = _stream_files(home)
    rows = sum(len(p.read_text().splitlines()) for p in leaked)
    assert not leaked, (
        f"a chief-of-staff test wrote {rows} event(s) into the operator's real activity "
        f"stream ({leaked[0].parent})")
