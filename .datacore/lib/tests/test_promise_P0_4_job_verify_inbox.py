"""P0-4 (audit B-F2 root cause): job_verify never writes into a generated
next_actions.org; a recurring failure is filed through the single capture
point, the space's inbox.org.

Seeded failure: restore `--allow-any-file --file 2-datacore/org/next_actions.org`
in job_verify._file_task. The adapter then nests the heading under the first
level-1 section (level 2) while emitting item.create at level 1, and the next
projection meets a "concurrent edit" it can never resolve: nightshift's
2-datacore was skipped for 56 cycles from 2026-09-23 18:25Z.
"""
import subprocess
import sys
from types import SimpleNamespace

import job_verify

NEXT = "* Operations\n** TODO an existing projected task\n:PROPERTIES:\n:ID: p04-existing\n:END:\n"


def _space(tmp_path, monkeypatch):
    org = tmp_path / "2-datacore" / "org"
    org.mkdir(parents=True)
    (org / "next_actions.org").write_text(NEXT)
    (org / "inbox.org").write_text("#+TITLE: Inbox\n")
    monkeypatch.setenv("DATACORE_ROOT", str(tmp_path))
    monkeypatch.setattr(job_verify, "TASK_FILE", str(org / "next_actions.org"))
    return org


JOB = SimpleNamespace(name="box-ledger-verify", machine="box", cmd="ledger_cli.py verify",
                      schedule="20 7 * * *")
REC = {"consecutive": 3, "first_failed": "2026-09-24"}


def test_a_recurring_failure_is_filed_into_the_inbox_not_the_generated_file(tmp_path, monkeypatch):
    org = _space(tmp_path, monkeypatch)
    tid = job_verify._file_task(JOB, REC, ["ledger-verify.log: regex '^OK ' did not match"])
    assert (org / "next_actions.org").read_text() == NEXT, \
        "the generated next_actions.org must never be written by job_verify"
    inbox = (org / "inbox.org").read_text()
    assert "job-verify: box-ledger-verify is failing on box" in inbox, inbox
    assert tid, "the filed task's id is returned so the pass can close it"
    assert tid in inbox


def test_the_adapter_is_never_asked_to_bypass_the_capture_point(tmp_path, monkeypatch):
    _space(tmp_path, monkeypatch)
    seen = []

    def fake_run(cmd, **kw):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout='{"added": true, "id": "x"}\n', stderr="")

    monkeypatch.setattr(job_verify.subprocess, "run", fake_run)
    job_verify._file_task(JOB, REC, ["boom"])
    assert seen, "a task is filed"
    for cmd in seen:
        assert "--allow-any-file" not in cmd
        files = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--file"]
        assert files and all(not f.endswith("next_actions.org") for f in files), cmd
