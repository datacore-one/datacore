"""A scheduled job needs a contract with evidence (plan 2026-10-31, phase H;
processes page, "A scheduled job", what changes [0]).

  * The installer refuses to install a job whose manifest entry declares no
    evidence (no entry, or an entry with no artifact), when it is given the
    manifest and the machine; nothing is written.
  * The live-schedule scan flags any scheduled line that is not in the
    manifest -- including lines that run no .py/.sh script (a bare `git pull`,
    a `touch`, a systemd ExecStart), which it used to drop unseen.

Written before the code.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import cron_install as C
import process_inventory as P

LIB = Path(__file__).resolve().parents[1]
LINE = '25 * * * * DATACORE_ROOT=/data /runner/.datacore/lib/ledger_phase1_cycle.sh >> /state/cycle.log 2>&1'


def _manifest(tmp_path, jobs):
    p = tmp_path / "manifest.yaml"
    p.write_text(yaml.safe_dump({"jobs": jobs}))
    return p


JOB = {"name": "nightshift-phase1-cycle", "machine": "nightshift", "schedule": "25 * * * *",
       "cmd": "~/Data/.datacore/lib/ledger_phase1_cycle.sh", "on_fail": "telegram",
       "artifacts": [{"path": "~/.datacore/state/phase1-cycle.log", "check": "nonempty", "max_age_hours": 2}]}


def test_an_entry_with_a_contract_that_has_evidence_is_allowed(tmp_path):
    m = _manifest(tmp_path, [JOB])
    assert C.contract_problems({"phase1-cycle": LINE}, m, "nightshift") == []


def test_an_entry_matches_its_contract_by_what_it_runs_not_only_by_key(tmp_path):
    m = _manifest(tmp_path, [JOB])
    assert C.contract_problems({"cycle": LINE}, m, "nightshift") == []


def test_an_entry_with_no_manifest_entry_is_refused(tmp_path):
    m = _manifest(tmp_path, [JOB])
    bad = C.contract_problems({"bot-alive": "*/15 * * * * /r/.datacore/lib/unit_alive.sh x.service /s/a"},
                              m, "nightshift")
    assert len(bad) == 1 and "bot-alive" in bad[0] and "no manifest entry" in bad[0], bad


def test_a_contract_on_another_machine_does_not_count(tmp_path):
    m = _manifest(tmp_path, [JOB])
    bad = C.contract_problems({"phase1-cycle": LINE}, m, "hermes")
    assert len(bad) == 1 and "phase1-cycle" in bad[0], bad


def test_an_entry_whose_contract_declares_no_evidence_is_refused(tmp_path):
    m = _manifest(tmp_path, [{**JOB, "artifacts": []}])
    bad = C.contract_problems({"phase1-cycle": LINE}, m, "nightshift")
    assert len(bad) == 1 and "no evidence" in bad[0], bad


def test_the_cli_refuses_and_writes_nothing(tmp_path):
    m = _manifest(tmp_path, [JOB])
    fake = tmp_path / "bin"
    fake.mkdir()
    wrote = tmp_path / "wrote"
    (fake / "crontab").write_text(f"#!/bin/sh\nif [ \"$1\" = - ]; then cat > {wrote}; fi\nexit 0\n")
    (fake / "crontab").chmod(0o755)
    import os
    env = dict(os.environ, PATH=f"{fake}:{os.environ['PATH']}")
    proc = subprocess.run([sys.executable, str(LIB / "cron_install.py"), "--state", str(tmp_path / "st"),
                           "--manifest", str(m), "--machine", "nightshift",
                           "--entry", "gate-check", "40 8 * * * python3 /x/gate_check.py >> /s/g 2>&1"],
                          env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "gate-check" in proc.stdout and not wrote.exists(), proc.stdout


def test_without_the_manifest_the_installer_is_unchanged():
    assert C.reconcile("", {"phase1-cycle": LINE}).endswith("# datacore-job:phase1-cycle\n")


# ── the live-schedule scan ────────────────────────────────────────────────

def test_a_scheduled_line_that_runs_no_script_is_kept_not_dropped():
    assert P.identity("17 * * * * git -C /runner pull -q --ff-only origin main >> /s/r.log 2>&1") == "cmd:git"
    assert P.identity("ExecStart=/usr/bin/docker run --rm thing") == "cmd:docker"
    assert P.identity("MAILTO=someone") == ""
    assert P.identity("SHELL=/bin/bash") == ""


def test_an_unlisted_line_with_no_script_is_flagged_no_contract():
    procs = [{"name": "box-x", "machine": "box", "actor": None, "trigger": "", "entrypoints": ["x.py"],
              "writes": [], "checked_by": "", "commands": ["x"]}]
    flags = P.overlaps(procs, {"nightshift": ["cmd:touch"]})
    assert {"kind": "no-contract", "what": "cmd:touch", "by": ["nightshift"]} in flags, flags


def test_a_no_script_line_the_manifest_runs_on_that_machine_is_not_flagged():
    procs = P.manifest_processes(LIB / "jobs" / "manifest.yaml")
    rr = [p for p in procs if p["name"] == "runner-refresh"]
    assert rr and "git" in rr[0]["commands"], rr
    flags = P.overlaps(procs, {rr[0]["machine"]: ["cmd:git"]})
    assert not any(f["what"] == "cmd:git" for f in flags), flags
