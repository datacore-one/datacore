"""fleet_status: each row must be measured where that machine's work runs.

2026-10-30: Tris's and Data's rows (roster names hermes and plur-claw) have
ssh_alias '-' because they now live in pods on the box. probe() read '-' as
"run it here" and printed the Mac's own versions under their names, so the
table showed two machines in step with the Mac that it had never asked.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import fleet_reach
import fleet_status as fs

ROSTER = """
servers:
  mac:
    ssh_alias: '-'
    access: {actor: mac}
  box:
    ssh_alias: winston
    access: {actor: winston, ssh_user: gregor, service_user: gregor}
  hermes:
    ssh_alias: '-'
    access:
      actor: tris
      k8s: {via: box, namespace: firm, deployment: tris, container: cron}
  nightshift:
    ssh_alias: dayshift
    access: {actor: miles, ssh_user: miles, run_as: miles, host_machine: dayshift, service_user: miles}
  dayshift:
    ssh_alias: dayshift
    access: {actor: owner}
"""


def _capture(monkeypatch, tmp_path: Path) -> list:
    reg = tmp_path / ".datacore/registry/infrastructure.yaml"
    reg.parent.mkdir(parents=True)
    reg.write_text(ROSTER)
    monkeypatch.setattr(fs, "ROOT", tmp_path)
    monkeypatch.setattr(fleet_reach.socket, "gethostname", lambda: "mac")
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "data_head=abc\n", "")

    monkeypatch.setattr(fs.subprocess, "run", fake_run)
    return seen


def test_a_pod_row_is_probed_in_its_pod_not_on_this_machine(monkeypatch, tmp_path):
    import yaml
    seen = _capture(monkeypatch, tmp_path)
    cfg = yaml.safe_load(ROSTER)["servers"]["hermes"]
    fs.probe("hermes", cfg)
    cmd = seen[-1]
    assert cmd[:2] != ["bash", "-lc"], "a pod row was measured on the local machine"
    assert "winston" in cmd and "deploy/tris" in " ".join(cmd)


def test_an_agent_user_on_a_shared_host_is_probed_as_that_user(monkeypatch, tmp_path):
    import yaml
    seen = _capture(monkeypatch, tmp_path)
    cfg = yaml.safe_load(ROSTER)["servers"]["nightshift"]
    fs.probe("nightshift", cfg)
    cmd = " ".join(seen[-1])
    assert seen[-1][:1] == ["ssh"] and "dayshift" in cmd and "sudo -n -u miles" in cmd


def test_the_local_machine_and_plain_hosts_keep_their_route(monkeypatch, tmp_path):
    import yaml
    seen = _capture(monkeypatch, tmp_path)
    s = yaml.safe_load(ROSTER)["servers"]
    fs.probe("mac", s["mac"])
    assert seen[-1][:2] == ["bash", "-lc"]
    fs.probe("box", s["box"])
    assert seen[-1][0] == "ssh" and "winston" in seen[-1]


# --- SYN-4 stale-main detection (issue #221) ---

def test_off_main_flags_runner_on_non_main_branch():
    row = {"runner_branch": "fix/something", "runner_behind": "0"}
    findings = fs.off_main(row)
    assert any("fix/something" in f for f in findings)


def test_off_main_silent_when_runner_on_main_and_current():
    row = {"runner_branch": "main", "runner_behind": "0"}
    assert fs.off_main(row) == []


def test_off_main_flags_runner_on_main_but_behind_origin():
    # Seeded failure from issue #221: runner on main, 29 commits behind origin/main.
    row = {"runner_branch": "main", "runner_behind": "29"}
    findings = fs.off_main(row)
    assert findings, "runner on main but 29 commits behind must be flagged"
    assert any("29" in f for f in findings)


def test_off_main_flags_data_root_on_main_but_behind_origin():
    row = {"data_branch": "main", "data_core": "1", "data_behind": "5"}
    findings = fs.off_main(row)
    assert findings, "data root on main but 5 commits behind must be flagged"
    assert any("5" in f for f in findings)


def test_off_main_silent_when_data_root_on_main_and_current():
    row = {"data_branch": "main", "data_core": "1", "data_behind": "0"}
    assert fs.off_main(row) == []


def test_off_main_silent_for_non_core_data_root_behind():
    # A space repo (not datacore core) on main behind origin is not flagged.
    row = {"data_branch": "main", "data_behind": "10"}
    # data_core absent → not a core checkout → no warning
    assert fs.off_main(row) == []


def test_probe_script_collects_behind_counts():
    # The PROBE shell script must ask for behind counts — verify the variables
    # are present so a future edit cannot silently drop them.
    assert "data_behind" in fs.PROBE
    assert "runner_behind" in fs.PROBE
    assert "rev-list --count HEAD..origin/main" in fs.PROBE
