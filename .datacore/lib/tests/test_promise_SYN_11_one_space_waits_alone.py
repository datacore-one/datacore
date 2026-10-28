"""SYN-11: A problem in one space never stops work in any other space on that machine;
that space is named and waits alone.

Evidence (2026-10-28 fleet-logs bug-classes report, class 3): one EditConflict in
9-practice made the Mac's phase-1 cycle exit 1. The cycle itself already isolates the
space -- ledger_phase1_cycle.sh skips the failed space's projection and projects the
rest ("ingest failed for: 9-practice - projecting the rest") -- but at the end it folds
that one space's ingest verdict into the exit code (`[ "$irc" -eq 0 ] || rc=$irc`,
:214). visitor_join.py:338 then counts the join as converged only on `rc == 0`, so the
whole machine's join stayed unconverged from 10-23 13:14 UTC for 5 days, and all 15 of
its duties -- the job checker among them -- stopped, for every space.

Kind: deterministic, through the real join. `visitor_join._join` runs with its real
`converge()`, `run_duties()` and `run_duty()`; only the processes they launch are
replaced: the phase-1 cycle by a stand-in that prints what the real script prints for
this case (and leaves the same status and ingest-log files in the state dir), and the
duty envelope (jobs/run.py) by a recorder. The data root is an empty tmp dir, so
`measure()` finds nothing pending. Nothing touches the network or a real space.

Kept when, for a cycle where one space failed and the others converged, the join runs
the machine's duties and its record names the failed space.
Boundary: a cycle that failed WITHOUT naming a space (the sweep itself did not run)
still runs no duty -- nothing is known to be healthy then.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import visitor_join as vj
from jobs import awake

NOW = 1_800_000_000.0

#: What ledger_phase1_cycle.sh prints when 9-practice's ingest fails and the other
#: spaces converge, ingest and project (lines as the script writes them).
ONE_SPACE_FAILED = """\
=== 2026-10-23 13:14Z phase-1 cycle ===
ingest rc=1
ingest 9-practice     FAILED: EditConflict: org/next_actions.org changed under the ingest
ingest failed for: 9-practice - projecting the rest
project 9-practice: skipped, its ingest failed
project rc=0
"""
ONE_SPACE_INGEST_LOG = (
    "0-personal     ok\n2-datacore     ok\n5-plur         ok\n"
    "9-practice     FAILED: EditConflict: org/next_actions.org changed under the ingest\n")

#: The sweep did not run at all: no space is named.
WHOLE_CYCLE_FAILED = """\
=== 2026-10-23 13:14Z phase-1 cycle ===
ingest rc=2
ingest FAILED without naming a space: ModuleNotFoundError: No module named 'yaml'
"""


@pytest.fixture
def machine(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    root = tmp_path / "Data"
    root.mkdir()
    monkeypatch.setenv("DATACORE_STATE", str(state))
    monkeypatch.setattr(vj, "ROOT", root)
    monkeypatch.setattr(vj, "STATE", state)
    monkeypatch.setattr(vj, "RECORD", state / "join.json")
    monkeypatch.setattr(vj, "ATTEMPT", state / "join-attempt.json")
    monkeypatch.setattr(vj, "LOG", state / "join.log")
    monkeypatch.setattr(vj, "DUTIES", state / "join-duties.json")
    monkeypatch.setattr(awake.sys, "platform", "darwin")
    monkeypatch.setattr(awake, "always_on", lambda machine, roster=None: False)
    monkeypatch.setattr(awake, "in_dark_wake", lambda **kw: False)
    # This machine's duties, as the manifest would list them for a join.
    monkeypatch.setattr(vj, "duties", lambda triggers, **kw: ["mac-job-verify", "mac-lens-push"])
    monkeypatch.setattr(vj, "gates", lambda **kw: {})

    ran: list[str] = []
    cycle = {"stdout": "", "rc": 0, "ingest_log": "", "status": ""}
    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        argv = [str(x) for x in cmd]
        if any(x.endswith("ledger_phase1_cycle.sh") for x in argv):
            (state / "phase1-ingest.log").write_text(cycle["ingest_log"])
            (state / "phase1-cycle-status.txt").write_text(cycle["status"])
            return subprocess.CompletedProcess(argv, cycle["rc"], cycle["stdout"], "")
        if any(x.endswith("run.py") for x in argv):
            ran.append(argv[-1])
            return subprocess.CompletedProcess(argv, 0, f"{argv[-1]}: ok\n", "")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(vj.subprocess, "run", fake_run)
    return {"ran": ran, "cycle": cycle, "state": state}


def test_one_failed_space_does_not_stop_the_other_spaces_duties(machine):
    machine["cycle"].update(
        stdout=ONE_SPACE_FAILED, rc=1, ingest_log=ONE_SPACE_INGEST_LOG,
        status="FAIL phase1-cycle 2026-10-23T13:14:00Z rc=1\n")
    rec = vj.join(now=NOW, log="")
    assert machine["ran"], (
        "one space (9-practice) failed its ingest and every other space converged, yet the "
        f"join ran none of this machine's duties: converged={rec.get('converged')} "
        f"cycle_rc={rec.get('cycle_rc')} cycle={rec.get('cycle')!r}")
    on_record = json.dumps(rec)
    assert "9-practice" in on_record, (
        f"the join ran on, but its record does not name the space that is waiting: {rec}")


def test_a_cycle_that_failed_without_naming_a_space_runs_no_duty(machine):
    machine["cycle"].update(
        stdout=WHOLE_CYCLE_FAILED, rc=2, ingest_log="Traceback ...\n",
        status="FAIL phase1-cycle 2026-10-23T13:14:00Z rc=2\n")
    rec = vj.join(now=NOW, log="")
    assert machine["ran"] == [], rec
    assert rec["converged"] is False
