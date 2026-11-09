"""AUD-1: the audit's GitHub issues reach the chief of operations' queue.

The weekly cross-model audit files each finding as an open issue labelled
`audit-finding`, assigned to the GitHub account of the principal whose role is
"chief of operations". His daily GitHub triage (triage_github.sh ->
github_scanner -> task_creator) must list those issues and capture each one,
once, as a task addressed to him.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE / "lib"))
sys.path.insert(0, str(MODULE.parents[1] / "lib"))

import github_scanner  # noqa: E402
import task_creator  # noqa: E402

ISSUE = {"repository": {"nameWithOwner": "example-org/datacore"}, "number": 7,
         "title": "[audit] TSK-2 at .datacore/lib/spaces.py:101", "url": "https://github.com/example-org/datacore/issues/7",
         "state": "open", "updatedAt": "2026-09-30T01:40:00Z", "labels": [{"name": "audit-finding"}]}


def test_the_scan_lists_open_audit_findings_assigned_to_the_chief_of_operations(monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, json.dumps([ISSUE]), "")
    monkeypatch.setattr(github_scanner.subprocess, "run", fake_run)
    items = github_scanner.scan_assigned_findings("ops-account")
    cmd = seen[0]
    assert cmd[:3] == ["gh", "search", "issues"]
    assert "--assignee=ops-account" in cmd and "--label=audit-finding" in cmd and "--state=open" in cmd
    assert not any(a.startswith("--updated") for a in cmd), "the whole open queue, not only today's"
    assert items == [{"repo": "example-org/datacore", "number": 7, "title": ISSUE["title"], "url": ISSUE["url"],
                      "state": "open", "updated_at": ISSUE["updatedAt"], "type": "issue",
                      "scan_type": "audit_finding"}]


def test_the_chief_of_operations_account_comes_from_the_principals(monkeypatch):
    import roster
    monkeypatch.setattr(roster, "by_role", lambda role, path=None: "ops" if role == "chief of operations" else None)
    monkeypatch.setattr(roster, "entries", lambda path=None: {"ops": {"github": "ops-account"}})
    assert github_scanner.chief_of_operations() == ("ops", "ops-account")
    monkeypatch.setattr(roster, "by_role", lambda role, path=None: None)
    assert github_scanner.chief_of_operations() == (None, None)


def test_the_full_scan_carries_the_audit_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(github_scanner, "scan_mentions", lambda u, s: [])
    monkeypatch.setattr(github_scanner, "scan_authored", lambda u, s: [])
    monkeypatch.setattr(github_scanner, "scan_org_activity", lambda o, s: {"org": o})
    monkeypatch.setattr(github_scanner, "chief_of_operations", lambda: ("ops", "ops-account"))
    monkeypatch.setattr(github_scanner, "scan_assigned_findings",
                        lambda account: [{"repo": "example-org/datacore", "number": 7, "account": account}])
    scan = github_scanner.run_full_scan("someone", ["example-org"], cache_path=tmp_path / "c.json")
    assert scan["audit_findings"] == [{"repo": "example-org/datacore", "number": 7, "account": "ops-account"}]
    assert scan["audit_assignee"] == "ops"


def test_each_open_finding_becomes_one_task_addressed_to_him(tmp_path):
    data = tmp_path / "Data"
    inbox = data / "2-datacore" / "org" / "inbox.org"
    inbox.parent.mkdir(parents=True)
    inbox.write_text("#+TITLE: Inbox\n\n")
    scan = {"audit_assignee": "ops", "audit_findings": [
        {"repo": "example-org/datacore", "number": 7, "title": ISSUE["title"], "url": ISSUE["url"],
         "scan_type": "audit_finding"}]}
    first = task_creator.create_tasks_from_scan(scan, data, {"example-org": ["2-datacore"]})
    again = task_creator.create_tasks_from_scan(scan, data, {"example-org": ["2-datacore"]})
    text = inbox.read_text()
    assert first["created"] == 1 and first["errors"] == 0, first
    assert again["created"] == 0, "the same issue is captured once"
    import re
    prop = lambda k, v: len(re.findall(rf"^\s*:{k}:\s+{re.escape(v)}\s*$", text, re.M))
    assert prop("TRIAGE_ID", "gh-datacore-7") == 1
    assert prop("ASSIGNEE", "ops") == 1 and prop("GITHUB_TYPE", "audit_finding") == 1 and ISSUE["url"] in text
    assert "example-org/datacore#7" in text
