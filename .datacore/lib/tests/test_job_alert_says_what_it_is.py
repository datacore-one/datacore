"""A job alert says which machine, what failed, and the job's own words.

Owner, 2026-10-02: "Errors should say what they are."

Survey of the alerts that reach the owner (errors-say-what-they-are-2026-10-02):

1. job_verify's direct alert -- the box and agent hosts, 94 of 95 jobs -- was
   only "job.verify FAILED: <job> (N failure(s))": no machine, no artifact, no
   reason. The failure strings went to stderr and a log nobody reads.
2. A job whose artifact is an append-only `exit=$rc $(date)` log (creds
   distribute, creds sync and many more) failed as "last line does not match
   '^exit=0 ' -- last line: 'exit=1 Fri Oct 3 ...'". What failed and why -- the
   lines the run printed just above its exit line -- were never quoted.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import job_verify as jv  # noqa: E402
from jobs import recurrence  # noqa: E402
from jobs.checks import run_check  # noqa: E402
from jobs.manifest import Artifact  # noqa: E402


def test_an_exit_line_failure_quotes_what_the_run_printed(tmp_path):
    log = tmp_path / "creds-distribute.log"
    log.write_text("exit=0 Thu Oct  2 09:00:00 2026\n"
                   "assembling for box\n"
                   "✗ box: transfer failed (ssh: connect to host box port 22: Connection refused)\n"
                   "exit=1 Fri Oct  3 09:00:00 2026\n")
    errors = run_check(Artifact(path=str(log), check="last_line_regex", arg="^exit=0 "))
    said = " ".join(errors)
    assert "exit=1" in said
    assert "transfer failed" in said and "Connection refused" in said, said
    # Yesterday's run is not this run's explanation.
    assert "exit=0 Thu" not in said.split("the run printed")[-1], said


def test_an_exit_line_failure_with_nothing_above_says_so(tmp_path):
    log = tmp_path / "x.log"
    log.write_text("exit=0 Thu\nexit=2 Fri\n")
    said = " ".join(run_check(Artifact(path=str(log), check="last_line_regex", arg="^exit=0 ")))
    assert "printed nothing" in said, said


@pytest.fixture
def sent(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "state"))
    monkeypatch.setattr(recurrence, "STATE", recurrence._DEFAULT_STATE)
    monkeypatch.setattr(jv, "_NO_EMIT", False)
    monkeypatch.setattr(jv, "_delegate_repair", lambda job, failures, rec: ("refused", "fixture"))
    monkeypatch.setattr(jv, "_file_task", lambda job, rec, failures: None)
    out = []
    monkeypatch.setattr(jv, "_send_telegram", lambda msg: out.append(msg) or True)
    monkeypatch.setattr(jv, "_send_command", lambda msg: out.append(msg) or True)
    return tmp_path, out


@pytest.mark.parametrize("mode", ["telegram", "command"])
def test_the_direct_alert_names_the_machine_and_the_failure(sent, mode):
    tmp, out = sent
    art = tmp / "ingest.log"
    art.write_text("x\n")
    job = types.SimpleNamespace(name="box-ledger-ingest", machine="box", delegate=True,
                                artifacts=[types.SimpleNamespace(path=str(art), max_age_hours=None)])
    failure = f"{art}: stale (age 30.0h exceeds max_age_hours=2)"
    jv._dispatch_alert(mode, "box-ledger-ingest", [failure], job)
    assert out, "nothing was sent"
    msg = out[0]
    assert msg.startswith("job.verify FAILED: box-ledger-ingest")
    assert "on box" in msg, msg
    assert "stale (age 30.0h" in msg, msg


def test_many_failures_are_bounded(sent):
    tmp, out = sent
    job = types.SimpleNamespace(name="j", machine="box", delegate=True, artifacts=[])
    jv._dispatch_alert("telegram", "j", [f"/p{i}: does not exist " + "z" * 500 for i in range(9)], job)
    msg = out[0]
    assert "/p0" in msg and "/p2" in msg and "/p5" not in msg
    assert "and 6 more" in msg
    assert len(msg) < 1500, len(msg)


def test_log_mode_is_unchanged(sent, capsys):
    """The mac relay reads `alert: ...` lines from log mode; the failure lines
    are already printed above them there, so log mode keeps one line."""
    tmp, out = sent
    job = types.SimpleNamespace(name="j", machine="mac", delegate=True, artifacts=[])
    jv._dispatch_alert("log", "j", ["/p: does not exist"], job)
    line = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("alert: ")]
    assert line == ["alert: job.verify FAILED: j (1 failure(s))"], line
