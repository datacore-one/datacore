"""MEM-43: Dates and weekdays are never typed from memory. A file or message
with the wrong weekday is refused before it is saved.

Kind: deterministic. The guards as they are wired:
  * files: every PreToolUse hook .claude/settings.json registers for Write/Edit
    is run on a payload carrying a wrong weekday; the write must be refused
    (exit 2 / deny). Covered forms: the org stamp "<2026-09-26 Mon>", the
    journal heading "2026-09-26 Monday", "Monday, 2026-09-26" and
    "Mon 2026-09-26" in a .md file. A correct weekday passes.
  * messages: the shared Telegram sender (winston_send.send_chunked) must not
    post a message whose date carries the wrong weekday (Telegram stubbed).

2026-09-26 is a Saturday.

Seeded failure: a guard that recognises only the "YYYY-MM-DD Ddd" form
(today's date_utils.DATE_DOW_RE) -- the long and leading forms get through.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
LIB = TESTS.parent
ROOT = TESTS.parents[2]
sys.path.insert(0, str(LIB))


def _hooks(tool: str) -> list[str]:
    cfg = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    cmds = []
    for e in cfg.get("hooks", {}).get("PreToolUse", []):
        if re.fullmatch(e.get("matcher", ""), tool):
            cmds += [h["command"] for h in e.get("hooks", []) if "date" in h.get("command", "")]
    return cmds


def _refused(path: str, content: str) -> bool:
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": path, "content": content},
                          "hook_event_name": "PreToolUse", "session_id": "mem43"})
    cmds = _hooks("Write")
    assert cmds, "no date guard registered for Write"
    for cmd in cmds:
        p = subprocess.run(["bash", "-c", cmd.replace("~", str(Path.home()))], input=payload,
                           capture_output=True, text=True, timeout=30)
        if p.returncode == 2 or '"deny"' in p.stdout:
            return True
    return False


J = str(ROOT / "0-personal/notes/journals/2026-09-26.md")


@pytest.mark.parametrize("path,content", [
    (str(ROOT / "0-personal/org/inbox.org"), "** TODO Call\nSCHEDULED: <2026-09-26 Mon>\n"),
    (J, "# 2026-09-26 Monday\n"),
    (J, "Briefing for Monday, 2026-09-26\n"),
    (J, "Mon 2026-09-26: standup\n"),
])
def test_a_file_with_the_wrong_weekday_is_refused(path, content):
    assert _refused(path, content), f"write with a wrong weekday was not refused: {content!r}"


def test_the_right_weekday_passes():
    assert not _refused(J, "# 2026-09-26 Saturday\nSCHEDULED: <2026-09-26 Sat>\n")


def test_a_message_with_the_wrong_weekday_is_not_sent(monkeypatch):
    import winston_send as W
    sent = []
    monkeypatch.setattr(W, "_post", lambda text: sent.append(text) or 200)
    try:
        W.send_chunked("Good morning. Your plan for Monday 2026-09-26:\n- call the bank")
    except Exception:
        pass   # a refusal may raise
    assert not sent, f"a message with a wrong weekday was sent: {sent[0][:80]!r}"
