"""AUD-8: Every record of something that ran says which session or job ran it;
when that is not known the record says 'unknown', never a value that looks real.

Evidence (2026-10-28 fleet-logs report): every command receipt carries the hash
of an empty string as its session. `hooks/command_receipt.py:92` hashes the
environment variable CLAUDE_SESSION_ID, which Claude Code does not set for
hooks, so every receipt says `session_sha256 = sha256('') = e3b0c442...b855` --
a value that looks like a real session and is the same for all of them. The
hook payload's own `session_id` field, which Claude Code does send, is ignored.

Kind: deterministic, hermetic. The REAL hook runs as Claude Code runs it (hook
JSON on stdin), with DATACORE_STATE in tmp_path and CLAUDE_SESSION_ID unset.

Graded:
- two invocations from two different sessions (payload `session_id`) leave two
  different session values on their receipts;
- an invocation with no session id anywhere says 'unknown' (or null), never the
  hash of an empty string.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "command_receipt.py"
EMPTY_HASH = hashlib.sha256(b"").hexdigest()


def _invoke(state: Path, payload: dict) -> None:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_SESSION_ID"}
    env["DATACORE_STATE"] = str(state)
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


def _receipts(state: Path) -> list[dict]:
    return [json.loads(l) for p in sorted((state / "command-runs").glob("*.jsonl"))
            for l in p.read_text().splitlines() if l.strip()]


def _session(row: dict):
    keys = [k for k in row if k.startswith("session")]
    assert keys, f"the receipt names no session at all: {row}"
    return row[keys[0]]


def test_two_sessions_leave_two_different_session_values(tmp_path):
    for sid in ("6f1c0a52-1111-4a6e-9c1e-000000000001", "6f1c0a52-2222-4a6e-9c1e-000000000002"):
        _invoke(tmp_path, {"session_id": sid, "hook_event_name": "PreToolUse",
                           "tool_name": "Skill", "tool_input": {"skill": "weekly-plan"}})
    rows = _receipts(tmp_path)
    assert len(rows) == 2, rows
    a, b = (_session(r) for r in rows)
    assert a != b, (
        f"two different sessions left the same session value on their receipts ({a!r}); "
        "the hook payload's session_id is ignored")


def test_an_unknown_session_says_unknown_not_a_believable_hash(tmp_path):
    _invoke(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Skill",
                       "tool_input": {"skill": "weekly-plan"}})
    rows = _receipts(tmp_path)
    assert len(rows) == 1, rows
    value = _session(rows[0])
    assert value != EMPTY_HASH, (
        "with no session id the receipt carries sha256('') -- a value that looks like a "
        "real session -- instead of 'unknown'")
    assert value in ("unknown", None), f"an unknown session must read 'unknown', got {value!r}"
