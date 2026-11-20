"""The simulated week does not run the ten-minute test-suite audit.

Since October the workstation's join runs its arrival duties itself, and one
of them runs every module's test suite (about ten minutes). The simulator cuts
any job at 300 s, so in the week runs of 2026-11-20 the join was killed on
every simulated day, and the audit, scheduled a second time on its own, was
killed as well: about 80 minutes of a 2 h 40 min run, and the join's other
duties were never reached. The audit says nothing about a fleet week, so each
simulated machine's own (untracked) job list now replaces it with a line that
says so; the tracked list is not touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import fleet_week_sim as sim  # noqa: E402
from jobs.manifest import overlay  # noqa: E402

TRACKED = {"jobs": [
    {"name": "mac-join", "machine": "mac", "cmd": "python3 ~/Data/.datacore/lib/visitor_join.py --tick"},
    {"name": "mac-suite-audit", "machine": "mac", "trigger": "arrival",
     "cmd": "~/Data/.datacore/lib/audit_trio_run.sh suites", "artifacts": [{"path": "~/x.log"}]},
]}


def _effective(local):
    return {j["name"]: j for j in overlay(TRACKED, local)["jobs"]}


def test_the_suite_audit_is_replaced_in_the_machines_own_list():
    local = sim.without_slow_jobs(TRACKED, None)
    job = _effective(local)["mac-suite-audit"]
    assert "audit_trio_run" not in job["cmd"] and sim.NOT_MODELLED_MARK in job["cmd"]
    assert job["trigger"] == "arrival" and job["artifacts"] == [{"path": "~/x.log"}]   # nothing else changes
    assert _effective(local)["mac-join"] == TRACKED["jobs"][0]


def test_the_planner_lists_it_as_not_modelled():
    import re
    job = _effective(sim.without_slow_jobs(TRACKED, None))["mac-suite-audit"]
    assert any(re.search(pat, job["cmd"]) for pat in sim.NOT_MODELLED)


def test_an_existing_own_list_keeps_its_jobs_and_keys():
    local = {"note": "mine", "jobs": [{"name": "my-job", "machine": "mac", "cmd": "true"},
                                      {"name": "mac-suite-audit", "machine": "mac",
                                       "cmd": "~/Data/.datacore/lib/audit_trio_run.sh suites --fast"}]}
    out = sim.without_slow_jobs(TRACKED, local)
    assert out["note"] == "mine"
    assert [j["name"] for j in out["jobs"]] == ["my-job", "mac-suite-audit"]
    assert sim.NOT_MODELLED_MARK in out["jobs"][1]["cmd"]
    assert local["jobs"][1]["cmd"].endswith("--fast")          # the input is not changed


def test_a_list_without_slow_jobs_is_returned_as_it_was():
    tracked = {"jobs": [TRACKED["jobs"][0]]}
    assert sim.without_slow_jobs(tracked, None) is None
    assert sim.without_slow_jobs(tracked, {"jobs": []}) == {"jobs": []}
