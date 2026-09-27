"""Promise CAP-2:

    Agents and automations add new tasks only to an inbox. The one exception
    is a scheduled duty that is fully specified (a clear done-when): it goes
    straight to its agent's queue. (Owner decision 2026-09-27.)

Kind: deterministic (real adapter and real task creators against a tmp Data
tree) plus a source contract over the automation code.
  1. The shared choke point (`org_workspace_adapter.py add`) refuses a new
     task aimed at next_actions.org.
  2. The mail task creator (`modules/mail/lib/task_creator.py`) puts an
     actionable email's task in the inbox, not next_actions.org.
  3. The GitHub task creator puts its task in the inbox.
  4. No automation passes the adapter's bypass (`--allow-any-file` /
     `allow_any_file=True`): the bypass exists for manual migrations and
     repairs, and an automation using it writes straight into a task list.

Seeded failure: the adapter's inbox-only rule is dropped (the add to
next_actions.org succeeds), or the mail creator targets next_actions.org
(today's state: its default is `<space>/org/next_actions.org`).
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
DC = LIB.parent
ADAPTER = LIB / "org_workspace_adapter.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _space(tmp_path: Path, name: str = "0-personal") -> Path:
    org = tmp_path / "Data" / name / "org"
    org.mkdir(parents=True)
    (org / "inbox.org").write_text("#+TITLE: Inbox\n\n* Inbox\n", encoding="utf-8")
    (org / "next_actions.org").write_text("#+TITLE: Next Actions\n\n* Work\n", encoding="utf-8")
    return org


def test_the_adapter_refuses_a_new_task_in_next_actions(tmp_path):
    org = _space(tmp_path)
    na = org / "next_actions.org"
    before = na.read_text(encoding="utf-8")
    r = subprocess.run([sys.executable, str(ADAPTER), "add", "--file", str(na),
                        "--heading", "Agent-made task"], capture_output=True, text=True,
                       timeout=60, cwd=str(LIB))
    out = json.loads(r.stdout or "{}")
    assert out.get("added") is not True and "error" in out, out
    assert na.read_text(encoding="utf-8") == before


def test_mail_automation_captures_into_the_inbox(tmp_path):
    org = _space(tmp_path)
    tc = _load("mail_task_creator_cap2", DC / "modules" / "mail" / "lib" / "task_creator.py")
    scan = {"categories": {"actionable": [{
        "id": "18c0ffee12345678", "sender": "alice@example.com", "sender_name": "Alice",
        "subject": "Contract signature needed", "gmail_url": "https://mail.google.com/mail/u/0/#inbox/18c0ffee12345678",
        "snippet": "Please sign", "priority": "HIGH", "reason": "actionable sender"}]}}
    res = tc.create_tasks_from_scan(scan, tmp_path / "Data")
    na = (org / "next_actions.org").read_text(encoding="utf-8")
    inbox = (org / "inbox.org").read_text(encoding="utf-8")
    assert "Contract signature needed" not in na, "an automation wrote straight into next_actions.org"
    assert res["created"] == 1 and "Contract signature needed" in inbox, \
        f"the actionable email's task did not reach the inbox: {res}"


def test_github_automation_captures_into_the_inbox(tmp_path):
    org = _space(tmp_path, "5-team")
    tc = _load("gh_task_creator_cap2", DC / "modules" / "github" / "lib" / "task_creator.py")
    scan = {"authored": [{"repo": "team-org/widget", "number": 7, "title": "Bug",
                          "url": "https://github.com/team-org/widget/issues/7",
                          "latest_commenter": "bob", "latest_comment_body": "any news?"}]}
    res = tc.create_tasks_from_scan(scan, tmp_path / "Data", {"team-org": ["5-team"]})
    assert res["created"] == 1, res
    assert "team-org/widget#7" in (org / "inbox.org").read_text(encoding="utf-8")
    assert "team-org/widget#7" not in (org / "next_actions.org").read_text(encoding="utf-8")


def _code_files():
    for p in DC.rglob("*"):
        if p.suffix not in (".py", ".sh") or not p.is_file():
            continue
        s = str(p)
        if "/tests/" in s or "/node_modules/" in s or "/.venv/" in s or "/_seed_" in s \
                or p.name.startswith("test_") or p == ADAPTER:
            continue
        yield p


#: The only automation allowed the bypass, and only for a fully specified duty
#: (owner decision 2026-09-27). It must decide on DONE_WHEN itself.
BYPASS_ALLOWED = {"modules/ventures/lib/cadence_capture.py"}


def test_a_cadence_duty_without_done_when_goes_to_the_inbox():
    """The exception is narrow: a duty with no DONE_WHEN is not fully specified,
    so cadence_capture must not bypass the inbox for it."""
    import importlib.util
    import sys as _sys
    _sys.path.insert(0, str(DC / "modules" / "ventures" / "lib"))
    spec = importlib.util.spec_from_file_location(
        "cadence_capture", DC / "modules" / "ventures" / "lib" / "cadence_capture.py")
    cc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cc)
    assert hasattr(cc, "fully_specified"), "cadence_capture must decide the bypass in one place: fully_specified()"
    assert cc.fully_specified({"DONE_WHEN": "the weekly report exists"}) is True
    assert cc.fully_specified({"DONE_WHEN": "  "}) is False
    assert cc.fully_specified({}) is False


def test_no_automation_uses_the_inbox_bypass():
    offenders = []
    pat = re.compile(r"--allow-any-file|allow_any_file\s*=\s*True")
    for p in _code_files():
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(lines, 1):
            m = pat.search(line)
            if m and "#" not in line[:m.start()]:
                if str(p.relative_to(DC)) in BYPASS_ALLOWED and "fully_specified" in p.read_text(encoding="utf-8"):
                    continue
                offenders.append(f"{p.relative_to(DC)}:{n}")
    assert not offenders, f"automations bypass the inbox-only rule: {offenders}"
