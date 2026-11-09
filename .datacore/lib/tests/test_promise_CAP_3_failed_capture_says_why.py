"""Promise CAP-3:

    If a capture can't be saved, I'm told why right away. It never reports
    success after saving nothing.

Kind: deterministic. Each capture writer is pointed at an inbox it cannot
write (a read-only file in a read-only directory) and must answer with a failure that carries a reason,
never with success:
  - the typed/agent path (`org_workspace_adapter.py add`);
  - the triage path (`triage_utils.create_triage_task`) when the adapter
    refuses;
  - the browser-tab native host (`tab-capture/lib/host.py main`): the
    extension shows the host's `error` text, so the host must send
    `{"success": false, "error": <why>}` rather than die without a reply
    (Chrome then only says "Native host has exited").

Seeded failure: the tab host swallows the write error and replies
success with count 0, or the adapter reports added:true for a write that
did not land.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
HOST = LIB.parent / "modules" / "tab-capture" / "lib" / "host.py"

INBOX = "#+TITLE: Inbox\n\n* Inbox\n"

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")


@pytest.fixture
def ro_inbox(tmp_path):
    """An inbox nobody can write: the file AND its directory are read-only, so
    neither an in-place write nor an atomic replace can land."""
    org = tmp_path / "0-personal" / "org"
    org.mkdir(parents=True)
    inbox = org / "inbox.org"
    inbox.write_text(INBOX, encoding="utf-8")
    inbox.chmod(stat.S_IRUSR | stat.S_IRGRP)
    org.chmod(stat.S_IRUSR | stat.S_IXUSR)
    yield inbox
    org.chmod(stat.S_IRWXU)
    inbox.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_adapter_add_to_an_unwritable_inbox_fails_with_a_reason(ro_inbox):
    inbox = ro_inbox
    r = subprocess.run([sys.executable, str(LIB / "org_workspace_adapter.py"), "add",
                        "--file", str(inbox), "--heading", "Lost capture"],
                       capture_output=True, text=True, timeout=60, cwd=str(LIB))
    try:
        out = json.loads(r.stdout)
    except ValueError:
        out = {}
    assert out.get("added") is not True, "reported success after saving nothing"
    reason = out.get("error") or r.stderr.strip()
    assert reason, "the failure carries no reason"
    assert inbox.read_text(encoding="utf-8") == INBOX


def test_triage_capture_refusal_carries_the_reason(tmp_path):
    import triage_utils as T
    org = tmp_path / "org"
    org.mkdir()
    (org / "inbox.org").write_text(INBOX, encoding="utf-8")
    (org / "next_actions.org").write_text("* Work\n", encoding="utf-8")
    r = T.create_triage_task(org_file=org / "next_actions.org", heading="Refused one",
                             tags=["github"], properties={"TRIAGE_ID": "gh-x-1"})
    assert r["success"] is False
    assert (r.get("error") or "").strip(), "a refused capture must say why"


def test_tab_host_replies_with_the_reason_when_the_inbox_cannot_be_written(ro_inbox, monkeypatch):
    inbox = ro_inbox
    spec = importlib.util.spec_from_file_location("tab_host_cap3", HOST)
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    sent: list[dict] = []
    monkeypatch.setattr(host, "load_config", lambda: {"inbox_path": str(inbox),
                                                      "filtered_prefixes": ["chrome://"]})
    monkeypatch.setattr(host, "read_message", lambda: {
        "action": "capture", "tabs": [{"title": "A page", "url": "https://example.org/p"}]})
    monkeypatch.setattr(host, "send_message", sent.append)
    try:
        host.main()
    except Exception as exc:  # noqa: BLE001 — a crash means the extension hears no reason
        pytest.fail(f"host died without replying ({type(exc).__name__}: {exc}); "
                    "the extension would show only 'Native host has exited'")
    assert sent, "the host sent no reply"
    reply = sent[-1]
    assert reply.get("success") is False, f"reported success after saving nothing: {reply}"
    assert (reply.get("error") or "").strip(), "the failure reply carries no reason"
    assert inbox.read_text(encoding="utf-8") == INBOX
