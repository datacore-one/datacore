"""Promise CAP-4 (catalogue GT-10):

    Capturing the same thing twice (the same tab, email or GitHub issue)
    gives me one inbox entry, not two.

Kind: deterministic (real writers against tmp inboxes) plus a production
contract (read-only: GitHub triage is scheduled exactly once in the fleet).
  - tab: the same URL captured in two saves, and twice within one save
    (two open tabs on the same page), gives one entry;
  - email: the mail task creator run on the same email on two different days
    gives one entry (its TRIAGE_ID used to carry the date);
  - GitHub: the same issue triaged on two unsynced copies of a space (two
    hosts before they sync) carries ONE :ID:, so the copies are one task;
    and triage_github.sh has exactly one live scheduler across the fleet.

Seeded failure: drop the SOURCE dedupe in host.capture_tabs; put the date back
into a TRIAGE_ID; add a second scheduler entry for triage_github.sh.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
from datetime import date
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
DC = LIB.parent
HOST = DC / "modules" / "tab-capture" / "lib" / "host.py"
INBOX = "#+TITLE: Inbox\n\n* Inbox\n"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _org(tmp_path: Path, name: str = "0-personal") -> Path:
    org = tmp_path / name / "org"
    org.mkdir(parents=True)
    (org / "inbox.org").write_text(INBOX, encoding="utf-8")
    (org / "next_actions.org").write_text("* Work\n", encoding="utf-8")
    return org


def test_the_same_tab_twice_is_one_entry(tmp_path):
    host = _load("tab_host_cap4", HOST)
    inbox = _org(tmp_path) / "inbox.org"
    cfg = {"inbox_path": str(inbox), "filtered_prefixes": ["chrome://"]}
    url = "https://example.org/article"
    host.capture_tabs([{"title": "Article", "url": url}], cfg)
    host.capture_tabs([{"title": "Article (reloaded)", "url": url}], cfg)
    assert inbox.read_text(encoding="utf-8").count(f":SOURCE: {url}") == 1, "two saves, two entries"
    # two open tabs on one page, one save
    other = "https://example.org/other"
    host.capture_tabs([{"title": "Other", "url": other}, {"title": "Other", "url": other}], cfg)
    assert inbox.read_text(encoding="utf-8").count(f":SOURCE: {other}") == 1, \
        "the same page open in two tabs became two entries in one save"


class _Day(date):
    today_value = date(2026, 9, 25)

    @classmethod
    def today(cls):
        return cls.today_value


def test_the_same_email_on_two_days_is_one_entry(tmp_path, monkeypatch):
    org = _org(tmp_path)
    tc = _load("mail_task_creator_cap4", DC / "modules" / "mail" / "lib" / "task_creator.py")
    monkeypatch.setattr(tc, "date", _Day)
    scan = {"categories": {"actionable": [{
        "id": "18c0ffee12345678", "sender": "alice@example.com", "sender_name": "Alice",
        "subject": "Contract signature needed",
        "gmail_url": "https://mail.google.com/mail/u/0/#inbox/18c0ffee12345678"}]}}
    target = str(org / "inbox.org")   # CAP-2 covers where it goes; this is about how often
    first = tc.create_tasks_from_scan(scan, tmp_path, target_org=target)
    assert first["created"] == 1, first
    _Day.today_value = date(2026, 9, 26)
    tc.create_tasks_from_scan(scan, tmp_path, target_org=target)
    _Day.today_value = date(2026, 9, 25)
    n = (org / "inbox.org").read_text(encoding="utf-8").count("Contract signature needed")
    assert n == 1, f"the same email became {n} inbox entries on two days"


def test_the_same_issue_on_two_unsynced_hosts_is_one_task(tmp_path):
    tc = _load("gh_task_creator_cap4", DC / "modules" / "github" / "lib" / "task_creator.py")
    scan = {"mentions": [{"repo": "team-org/widget", "number": 42, "title": "Please review",
                          "url": "https://github.com/team-org/widget/issues/42"}]}
    ids = []
    for hostname in ("hostA", "hostB"):
        data = tmp_path / hostname
        _org(data, "5-team")
        assert tc.create_tasks_from_scan(scan, data, {"team-org": ["5-team"]})["created"] == 1
        # a second run on the same host is a no-op
        assert tc.create_tasks_from_scan(scan, data, {"team-org": ["5-team"]})["created"] == 0
        text = (data / "5-team/org/inbox.org").read_text(encoding="utf-8")
        ids.append(re.findall(r"^\s*:ID:\s*(\S+)", text, re.M))
    assert len(ids[0]) == 1 and len(ids[1]) == 1
    assert ids[0] == ids[1], f"one issue, two task identities across hosts: {ids[0]} vs {ids[1]}"


def _ssh(host: str, cmd: str) -> str:
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, cmd],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"could not read {host}: {r.stderr.strip()[:200]}"
    return r.stdout


@pytest.mark.production
def test_github_triage_is_scheduled_once_in_the_fleet():
    probe = ("n=$(crontab -l 2>/dev/null | grep -v '^\\s*#' | grep -c triage_github); "
             "for t in $(systemctl list-unit-files --type=timer --no-legend 2>/dev/null | awk '{print $1}'); do "
             "  s=${t%.timer}.service; "
             "  if systemctl cat \"$s\" 2>/dev/null | grep -q triage_github && systemctl is-enabled \"$t\" >/dev/null 2>&1; "
             "  then n=$((n+1)); fi; done; echo $n")
    counts = {h: int(_ssh(h, probe).strip() or 0) for h in ("nightshift", "winston", "hermes")}
    local = subprocess.run(["bash", "-c", "crontab -l 2>/dev/null | grep -v '^\\s*#' | grep -c triage_github; "
                            "grep -ls triage_github ~/Library/LaunchAgents/*.plist 2>/dev/null | wc -l"],
                           capture_output=True, text=True, timeout=30).stdout.split()
    counts["mac"] = sum(int(x) for x in local if x.isdigit())
    assert sum(counts.values()) == 1, f"triage_github.sh schedulers per host: {counts}"
