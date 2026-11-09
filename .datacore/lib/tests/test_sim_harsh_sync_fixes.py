"""Overnight/sync breaks found by the harsh fleet simulation (2026-10-03).

Each test is the red half of one fix (report:
2-datacore/1-tracks/dev/datacore-upgrade/sim-fixes-overnight-sync-2026-10-03.md).

  #1  A conflict converge merged around is ONE file waiting for a person, not a
      stopped space: the result says the rest of the space went through (so a
      caller can keep working there), a task that could not be filed says why,
      and the phase-1 cycle keeps ingesting that space.
  #9  The delegation review's receipt is committed by the run that writes it,
      so the overnight run does not find it as "unsaved work" every morning.
  #11 A converge that crashes says what crashed, in the JSON every caller
      parses, and the box's sync names it instead of "unknown".
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import ledger_transport as lt
from tests.test_sync_conflict_org_and_alerts import _git, _note_conflict, fleet  # noqa: F401 -- fixture

LIB = Path(__file__).resolve().parents[1]
COS_SYNC = LIB.parent / "modules" / "chief-of-staff" / "server" / "lib" / "cos_sync.sh"


# ───────────── #1: a merged-around conflict does not stop the space ─────────────

def test_a_merged_around_conflict_says_the_rest_of_the_space_went_through(fleet):
    w = fleet
    _note_conflict(w)
    (w["b"] / "other.md").write_text("written here, unrelated to the conflict\n")

    res = lt.converge(w["b"], root=w["root"])

    assert not res.ok and "note.md" in res.reason, res          # still the first-time alert
    assert res.context.get("went_through") is True, res.context
    # and it is true: the unrelated work is on origin
    assert _git(w["origin"], "show", "main:other.md").returncode == 0


def test_a_conflict_whose_task_could_not_be_filed_says_why(fleet, monkeypatch):
    w = fleet
    _note_conflict(w)
    monkeypatch.setattr(lt, "file_conflict_tasks",
                        lambda space, kept: ["no task filed: PolicyError: creation allowance spent"])

    res = lt.converge(w["b"], root=w["root"])

    assert "no task filed: PolicyError: creation allowance spent" in res.reason, res.reason


def _cycle(tmp_path, transport_json: dict, rc: int):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    root = tmp_path / "data"
    space = root / "2-test"
    (space / ".datacore/events").mkdir(parents=True)
    (space / ".git").mkdir()
    (space / ".datacore/ledger-phase").write_text("1\n")
    state = tmp_path / "state"
    shutil.copyfile(LIB / "ledger_phase1_cycle.sh", scripts / "cycle.sh")
    shutil.copyfile(LIB / "runtime_shell.sh", scripts / "runtime_shell.sh")
    (scripts / "ledger_transport.py").write_text(
        f"import json, sys\nprint(json.dumps({transport_json!r}, indent=2))\nsys.exit({rc})\n")
    trace = tmp_path / "trace"
    for name in ("ledger_ingest_org", "ledger_project_org"):
        (scripts / f"{name}.py").write_text(
            "import os, sys\nopen(os.environ['AUDIT_TRACE'], 'a').write("
            f"'{name} ' + ' '.join(sys.argv[1:]) + '\\n')\nprint('ok')\n")
    env = dict(os.environ, DATACORE_ROOT=str(root), DATACORE_STATE=str(state),
               DATACORE_PYTHON=sys.executable, AUDIT_TRACE=str(trace))
    proc = subprocess.run(["bash", str(scripts / "cycle.sh")], env=env,
                          capture_output=True, text=True, timeout=60)
    return proc, state, (trace.read_text() if trace.exists() else "")


def test_the_phase1_cycle_keeps_ingesting_a_space_whose_conflict_was_merged_around(tmp_path):
    reason = ("conflict waiting for a person: org/shared.org (merged around it, nothing lost; "
              "task sync-conflict-c91076a52cb2); the rest of the space went through")
    proc, state, trace = _cycle(tmp_path, {"ok": False, "reason": reason,
                                           "context": {"went_through": True,
                                                       "conflicts": ["org/shared.org"]}}, 1)

    assert "ledger_ingest_org" in trace, proc.stdout + proc.stderr
    assert not (state / "phase1-converge-2-test.failed").exists(), \
        "the hourly ingest would skip the space for as long as the conflict waits"
    assert "org/shared.org" in proc.stdout, "the waiting file is still named"
    assert "skipping this space" not in proc.stdout, proc.stdout


def test_the_phase1_cycle_still_skips_a_space_that_did_not_go_through(tmp_path):
    proc, state, trace = _cycle(tmp_path, {"ok": False, "reason": "merge conflict — human needed",
                                           "context": {}}, 1)

    assert "ledger_ingest_org" not in trace or "2-test" not in trace, trace
    assert (state / "phase1-converge-2-test.failed").exists()


# ───────────── #9: the review's receipt is committed where it is written ─────────────

def test_the_delegation_receipt_is_committed_by_the_run_that_writes_it(tmp_path):
    from delegation_receipt import paths, publish
    space = tmp_path / "teams/control"
    (space / ".datacore").mkdir(parents=True)
    (space / ".datacore/config.yaml").write_text("space: {name: datacore, type: team}\n")
    (space / "unrelated.md").write_text("one\n")
    for args in (("init", "-q", "-b", "main"), ("config", "user.email", "t@t"),
                 ("config", "user.name", "t"), ("add", "-A"), ("commit", "-qm", "seed")):
        assert _git(space, *args).returncode == 0
    (space / "unrelated.md").write_text("someone else's staged edit\n")
    _git(space, "add", "unrelated.md")
    success, attempt = paths(tmp_path)
    record = {"version": 2, "status": "complete", "actor": "reviewer", "review_id": "r1",
              "ts": datetime.now(timezone.utc).isoformat()}

    publish(tmp_path, success, record)
    publish(tmp_path, attempt, record)

    status = _git(space, "status", "--porcelain").stdout
    assert ".datacore/reviews" not in status, status          # nothing left for a rescue
    assert _git(space, "diff", "--cached", "--name-only").stdout.strip() == "unrelated.md"
    shown = _git(space, "show", "HEAD:.datacore/reviews/cos-lastrun.json").stdout
    assert json.loads(shown)["review_id"] == "r1"


def test_a_receipt_outside_a_git_checkout_is_still_written(tmp_path):
    from delegation_receipt import fresh_hours, paths, publish
    config = tmp_path / "teams/control/.datacore/config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("space: {name: datacore, type: team}\n")
    success, attempt = paths(tmp_path)
    record = {"version": 2, "status": "complete", "actor": "reviewer", "review_id": "r1",
              "ts": datetime.now(timezone.utc).isoformat()}
    publish(tmp_path, success, record)
    publish(tmp_path, attempt, record)
    assert fresh_hours(tmp_path) is not None


# ───────────── #11: a crashed converge names its cause ─────────────

def test_a_converge_that_crashes_names_the_cause_in_its_json(monkeypatch, tmp_path):
    def crash(space, *, root=None):
        raise ValueError("runtime state must have an absolute unaliased path")
    monkeypatch.setattr(lt, "converge", crash)
    out = io.StringIO()
    with redirect_stdout(out):
        code = lt.main(["converge", "--space", str(tmp_path)])

    doc = json.loads(out.getvalue())
    assert code == 1 and doc["ok"] is False
    assert "ValueError: runtime state must have an absolute unaliased path" in doc["reason"], doc


def test_cos_sync_never_says_unknown_when_the_transport_could_not_run(tmp_path):
    root = tmp_path / "Data"
    space = root / "2-test"
    space.mkdir(parents=True)
    for args in (("init", "-q", "-b", "main"), ("config", "user.email", "t@t"),
                 ("config", "user.name", "t"), ("commit", "-q", "--allow-empty", "-m", "seed")):
        _git(space, *args)
    lib = root / ".datacore" / "lib"
    lib.mkdir(parents=True)
    (lib / "ledger_transport.py").write_text(
        "import sys\nprint('Traceback (most recent call last):', file=sys.stderr)\n"
        "print('ModuleNotFoundError: No module named yaml', file=sys.stderr)\nsys.exit(1)\n")
    (lib / "cos_alert.sh").write_text("#!/bin/sh\necho \"ALERT $*\"\n")
    (lib / "cos_alert.sh").chmod(0o755)
    (tmp_path / "home" / ".datacore" / "cos").mkdir(parents=True)
    env = {**os.environ, "HOME": str(tmp_path / "home"), "DATACORE_HOME": str(root),
           "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}"}

    r = subprocess.run(["bash", str(COS_SYNC)], env=env, capture_output=True, text=True, timeout=60)

    alert = next((l for l in r.stdout.splitlines() if l.startswith("ALERT") and "2-test" in l), "")
    assert alert, r.stdout + r.stderr
    assert "unknown" not in alert, alert
    assert "No module named yaml" in alert, alert
