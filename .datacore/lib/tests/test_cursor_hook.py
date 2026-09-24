"""Cursor hook bridge: Datacore's guards must hold under Cursor's payload shapes.

Cursor names its shell tool "Shell" and also sends beforeShellExecution with a
bare `command`, while the host guard only recognises Claude's "Bash". Without
the bridge, the contractual host guard allowed everything in Cursor.

The bridge runs the REAL guard scripts. To exercise the host guard without
naming a real restricted range, each test copies the bridge and the guards
into a temporary tree whose restricted list is TEST-NET-3 (203.0.113.0/24,
reserved for documentation); the bridge finds its guards relative to itself.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
HOOK = LIB / "adapters" / "cursor" / "hook.py"
WRONG_DAY = "* TODO x\nSCHEDULED: <2026-09-24 " + "Mon>\n"   # 2026-09-24 is a Thursday


@pytest.fixture
def hook(tmp_path):
    dc = tmp_path / ".datacore"
    (dc / "lib" / "adapters" / "cursor").mkdir(parents=True)
    (dc / "lib" / "hooks").mkdir(parents=True)
    shutil.copy(HOOK, dc / "lib" / "adapters" / "cursor" / "hook.py")
    for name in ("org_date_prewrite.py", "restricted_hosts_guard.py"):
        shutil.copy(LIB / "hooks" / name, dc / "lib" / "hooks" / name)
    shutil.copy(LIB / "date_utils.py", dc / "lib" / "date_utils.py")
    (dc / "lib" / "hooks" / "restricted_hosts.json").write_text(json.dumps(
        {"hosts": [], "networks": ["203.0.113."], "note": "Test range is off-limits."}))

    def run(payload):
        data = payload if isinstance(payload, str) else json.dumps(payload)
        r = subprocess.run([sys.executable, str(dc / "lib" / "adapters" / "cursor" / "hook.py")],
                           input=data, capture_output=True, text=True, timeout=60)
        out = r.stdout.strip()
        return r.returncode, (json.loads(out) if out else None)
    return run


def test_shell_tool_to_restricted_host_is_denied(hook):
    rc, out = hook({"hook_event_name": "preToolUse", "tool_name": "Shell",
                    "tool_input": {"command": "ssh root@203.0.113.7 uptime"}})
    assert rc == 0 and out["permission"] == "deny"
    assert "off-limits" in out["agent_message"]


def test_before_shell_execution_is_guarded_too(hook):
    rc, out = hook({"hook_event_name": "beforeShellExecution", "command": "curl http://203.0.113.9/", "cwd": "/tmp"})
    assert out["permission"] == "deny"


def test_wrong_weekday_in_org_write_is_denied(hook):
    rc, out = hook({"hook_event_name": "preToolUse", "tool_name": "Write",
                    "tool_input": {"file_path": "/tmp/x/inbox.org", "content": WRONG_DAY}})
    assert out["permission"] == "deny"
    assert "Thu" in out["agent_message"]


def test_benign_calls_say_nothing(hook):
    assert hook({"hook_event_name": "preToolUse", "tool_name": "Shell", "tool_input": {"command": "ls -la"}}) == (0, None)
    assert hook({"hook_event_name": "preToolUse", "tool_name": "Write",
                 "tool_input": {"file_path": "/tmp/a.md", "content": "hello"}}) == (0, None)
    assert hook({"hook_event_name": "preToolUse", "tool_name": "MCP:datacore_status", "tool_input": {}}) == (0, None)


def test_malformed_input_never_blocks_or_crashes(hook):
    assert hook("not json") == (0, None)
    assert hook([1, 2]) == (0, None)


def test_unreadable_shell_event_is_refused(hook):
    rc, out = hook({"hook_event_name": "beforeShellExecution", "command": None})
    assert rc == 0 and out["permission"] == "deny"
