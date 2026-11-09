"""DAY-8: Ending a work session writes a journal entry in every space I worked
in and puts follow-ups and continuation tasks straight into that space's
inbox, never into my task lists.

Kind: deterministic part only. .datacore/lib/tests/agent_eval.py does not exist,
so the agent run of /wrap-up is pending; what is graded here is what an agent
running /wrap-up and /continue relies on:
- the task tool (org_workspace_adapter `add`) writes a follow-up to inbox.org
  and refuses next_actions.org;
- the wrap-up audit (wrap_up_mechanics.cmd_audit) fails when a space this
  session wrote to has no journal entry for today;
- the /continue and /wrap-up instructions send every continuation to the inbox
  of the space the work was in (owner decision), and never to a task list.

Seeded failure: a session that changed files in 2-datacore and wrote only the
personal journal; a follow-up aimed at next_actions.org; /continue's inline
save that files every continuation in 0-personal. Verified red on today's code
for the audit (its "space journals" check passes unconditionally) and the
inline save (hard-coded 0-personal inbox).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
ROOT = LIB.parent.parent
sys.path.insert(0, str(LIB))

ADAPTER = LIB / "org_workspace_adapter.py"
HEADER = "#+SEQ_TODO: TODO(t) NEXT(n) WAITING(w@) REVIEW(r!) | DONE(d!) DEFERRED(f@) CANCELLED(c@)\n"


def test_a_follow_up_goes_to_the_inbox_and_never_to_a_task_list(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DATACORE_STATE", str(state))
    org = tmp_path / "2-datacore" / "org"
    org.mkdir(parents=True)
    (org / "inbox.org").write_text(HEADER)
    (org / "next_actions.org").write_text(HEADER)
    before = (org / "next_actions.org").read_bytes()
    run = lambda f: subprocess.run(  # noqa: E731
        [sys.executable, str(ADAPTER), "add", "--file", str(f), "--heading", "Continue: ledger audit",
         "--tags", ":continuation:"], capture_output=True, text=True, timeout=60)
    r = run(org / "next_actions.org")
    assert r.returncode != 0 or json.loads(r.stdout or "{}").get("error"), "a task list accepted a new task"
    assert (org / "next_actions.org").read_bytes() == before
    r = run(org / "inbox.org")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Continue: ledger audit" in (org / "inbox.org").read_text()


def test_the_wrap_up_audit_fails_when_a_worked_space_has_no_journal(tmp_path, monkeypatch):
    import wrap_up_mechanics as W
    today = date.today().isoformat()
    for s in ("0-personal", "2-datacore"):
        (tmp_path / s / "journal").mkdir(parents=True)
    (tmp_path / "0-personal" / "journal" / f"{today}.md").write_text("# today\n")
    worked = str(tmp_path / "2-datacore" / "1-tracks" / "dev" / "notes.md")
    monkeypatch.setattr(W, "DATACORE_ROOT", tmp_path)
    monkeypatch.setattr(W, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(W, "session_files", lambda: ([worked], None))
    monkeypatch.setattr(W, "repo_status", lambda: [])
    monkeypatch.setattr(W, "session_scope_rows", lambda mine, repos: ({"ok": True, "detail": ""}, ""))
    monkeypatch.setattr(W, "journal_sections_lost", lambda: [])
    monkeypatch.setattr(W, "context_sync_check", lambda: {"registry_changed": False, "action": ""})
    out = W.cmd_audit()
    failed = " ".join(c["check"] + " " + c["detail"] for c in out["failed"])
    assert "2-datacore" in failed, (
        "the session wrote to 2-datacore, which has no journal entry today, and the wrap-up "
        f"audit passed that: {[(c['check'], c['pass']) for c in out['checks']]}")


def _continuation_targets(text: str) -> list[str]:
    return re.findall(r"[Cc]reate continuation task\*{0,2} in `([^`]+)`", text)


def test_continue_files_each_continuation_in_the_worked_spaces_inbox():
    text = (ROOT / ".datacore" / "commands" / "continue.md").read_text()
    targets = _continuation_targets(text)
    assert targets, "continue.md no longer says where a continuation task goes"
    wrong = [t for t in targets if not t.endswith("inbox.org") or t.startswith("0-personal/")]
    assert not wrong, (f"/continue files continuations in {wrong}: a fixed personal inbox, not the "
                       "inbox of the space the work was in")


def test_wrap_up_writes_follow_ups_only_to_inbox():
    text = (ROOT / ".datacore" / "commands" / "wrap-up.md").read_text()
    assert "Everything this command writes goes to inbox.org" in text
    writes = re.findall(r"(?:[Cc]reate|[Ww]rite|[Aa]dd)[^.\n]{0,60}\bto `?[\w/{}.-]*next_actions\.org", text)
    assert not writes, f"/wrap-up tells the agent to write a task list: {writes}"
