"""TSK-3: One piece of work never shows up as two tasks, even when two machines
create it at the same time.

Kind: deterministic twin + production contract.
- Deterministic: the real GitHub triage writer (modules/github/lib/task_creator
  create_tasks_from_scan -> triage_utils -> org_workspace_adapter add) runs on
  two unsynced copies of the same space, as two hosts would before they sync.
  The two inboxes are then merged the way git merges them (union). The same
  repo#number must end as ONE identity (one :ID:), not two.
- Production (GT-10): triage_github.sh is scheduled exactly once in the fleet
  (all crontabs and systemd timers on nightshift and winston), read-only.

Seeded failure: the same scan run on two copies (2026-09-25/26: one task under
two :ID:s); a second scheduler entry for triage_github.sh (nightshift crontab
03:30 plus nightshift-github-triage.timer 04:30).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
ROOT = LIB.parent.parent
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(ROOT / ".datacore" / "modules" / "github" / "lib"))

HEADER = "#+SEQ_TODO: TODO(t) NEXT(n) WAITING(w@) REVIEW(r!) | DONE(d!) DEFERRED(f@) CANCELLED(c@)\n#+TITLE: Inbox\n"
SCAN = {"mentions": [{"repo": "plur-ai/plur", "number": 42, "title": "Recall misses scoped engrams",
                      "url": "https://github.com/plur-ai/plur/issues/42"}]}


def _space(root: Path) -> Path:
    org = root / "5-evals" / "org"
    org.mkdir(parents=True)
    (org / "inbox.org").write_text(HEADER, encoding="utf-8")
    (org / "next_actions.org").write_text(HEADER.replace("Inbox", "Next"), encoding="utf-8")
    return root


def _ids_for(text: str, triage_id: str) -> list[str]:
    """Every :ID: on any heading whose TRIAGE_ID is triage_id."""
    out = []
    for block in re.split(r"(?m)^(?=\*+ )", text):
        if re.search(rf"(?m)^\s*:TRIAGE_ID:\s+{re.escape(triage_id)}\s*$", block):
            # A union merge of two hosts' captures can fold both into one
            # heading carrying two :ID: lines: that is two identities too.
            out += re.findall(r"(?m)^\s*:ID:\s+(\S+)", block) or ["<none>"]
    return out


def test_two_machines_triaging_the_same_issue_make_one_task(tmp_path, monkeypatch):
    from task_creator import create_tasks_from_scan
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DATACORE_STATE", str(state))
    base = _space(tmp_path / "base")
    host_a = tmp_path / "a"
    host_b = tmp_path / "b"
    shutil.copytree(base, host_a)
    shutil.copytree(base, host_b)
    for host in (host_a, host_b):
        r = create_tasks_from_scan(SCAN, host, {"plur-ai": ["5-evals"]})
        assert r["errors"] == 0 and r["created"] == 1, r

    rel = Path("5-evals/org/inbox.org")
    merged = tmp_path / "merged.org"
    shutil.copy(host_a / rel, merged)
    subprocess.run(["git", "merge-file", "--union", str(merged), str(base / rel), str(host_b / rel)],
                   check=False, timeout=30)
    ids = set(_ids_for(merged.read_text(encoding="utf-8"), "gh-plur-42"))
    assert len(ids) == 1, (
        f"one GitHub issue became {len(ids)} tasks with different identities {sorted(ids)} "
        "after two hosts triaged it before syncing")


def test_the_same_scan_twice_on_one_host_is_one_task(tmp_path, monkeypatch):
    from task_creator import create_tasks_from_scan
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DATACORE_STATE", str(state))
    space = _space(tmp_path / "s")
    create_tasks_from_scan(SCAN, space, {"plur-ai": ["5-evals"]})
    r = create_tasks_from_scan(SCAN, space, {"plur-ai": ["5-evals"]})
    assert r["created"] == 0 and r["skipped"] == 1, r
    assert len(_ids_for((space / "5-evals/org/inbox.org").read_text(), "gh-plur-42")) == 1


def _ssh(host: str, cmd: str) -> str:
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, cmd],
                       capture_output=True, text=True, timeout=40)
    if r.returncode not in (0, 1):
        pytest.fail(f"could not read {host} (ssh exit {r.returncode}): could not tell is not a pass")
    return r.stdout


@pytest.mark.production
def test_github_triage_is_scheduled_exactly_once_in_the_fleet():
    read = ("crontab -l 2>/dev/null | grep -v '^\\s*#' | grep -c triage_github; "
            "grep -ls triage_github /etc/systemd/system/*.service ~/.config/systemd/user/*.service "
            "2>/dev/null | while read f; do n=$(basename $f .service); "
            "(systemctl is-enabled $n.timer 2>/dev/null; systemctl --user is-enabled $n.timer 2>/dev/null)"
            " | grep -q enabled && echo TIMER $n; done")
    schedulers = []
    for host in ("nightshift", "winston"):
        out = _ssh(host, read).split("\n")
        crons = int(out[0].strip() or 0) if out and out[0].strip().isdigit() else 0
        schedulers += [f"{host}:cron"] * crons
        schedulers += [f"{host}:{line}" for line in out[1:] if line.startswith("TIMER")]
    assert len(schedulers) == 1, f"triage_github.sh is scheduled {len(schedulers)} times: {schedulers}"
