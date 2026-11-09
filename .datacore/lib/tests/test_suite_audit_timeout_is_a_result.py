"""One suite that hangs is a red suite, not a crashed audit.

`run_suite` let `subprocess.TimeoutExpired` escape the thread pool, so one
suite over 1800 s killed the whole run with a traceback: no per-suite lines,
no summary line, nothing to say WHICH suite hung. mac-suite-audit read a
traceback as its last line on 26 of its failing runs between 2026-09-26 and
09-30, and the owner could not tell a hang from a failure from a crash.
"""
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import suite_audit  # noqa: E402


def test_a_suite_that_times_out_is_reported_as_a_failing_suite(monkeypatch):
    def hang(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"), output="3 passed so far\n")

    monkeypatch.setattr(suite_audit.subprocess, "run", hang)
    suite = suite_audit.DATACORE / "lib" / "tests"

    res = suite_audit.run_suite(suite, sys.executable)

    assert not res.ok
    assert res.errors >= 1
    assert any("timed out" in f for f in res.failures), res.failures
    assert res.suite == "lib/tests"


def test_the_core_suite_gets_its_own_longer_limit(monkeypatch):
    """lib/tests is slow, not hung: run alone it passed 81% in 45 minutes with no
    test over the 240 s hang detector, so 30 minutes always cut it off and the Mac
    suite audit could never go green (owner 2026-10-01: give it 90 minutes)."""
    seen = []

    def run(cmd, **kw):
        seen.append(kw.get("timeout"))
        return subprocess.CompletedProcess(cmd, 0, "1 passed\n", "")

    monkeypatch.setattr(suite_audit.subprocess, "run", run)
    suite_audit.run_suite(suite_audit.DATACORE / "lib" / "tests", sys.executable)
    suite_audit.run_suite(suite_audit.DATACORE / "modules" / "mail" / "tests", sys.executable)
    assert seen == [5400, 1800], f"core suite 90 min, others 30 min; got {seen}"
