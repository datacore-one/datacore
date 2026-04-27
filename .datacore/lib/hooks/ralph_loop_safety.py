#!/usr/bin/env python3
"""Safety net for ralph-loop plugin cross-session trapping bug.

Two entry points controlled by argv[1]:

  patch-session-id  (PostToolUse on Skill|Bash)
    After /ralph-loop creates its state file, the session_id field is empty
    because CLAUDE_CODE_SESSION_ID is not in the env. This hook reads
    session_id from stdin JSON and patches it into the state file.

  stop-guard  (Stop hook)
    Backup defense: if the state file STILL has no session_id at exit time,
    delete it so the ralph-loop stop hook cannot trap arbitrary sessions.

Why both? patch-session-id fixes the root cause. stop-guard catches edge
cases (crash between creation and patch, plugin update resets the hook, etc.).
"""
import json
import os
import re
import sys
import tempfile

STATE_FILE = ".claude/ralph-loop.local.md"


def _read_stdin():
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return {}


def _read_state():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE) as f:
        return f.read()


def patch_session_id():
    """PostToolUse: inject session_id into state file if missing."""
    data = _read_stdin()
    session_id = data.get("session_id", "")
    if not session_id:
        return

    content = _read_state()
    if content is None:
        return

    # Check if session_id is empty (the bug: setup script writes "session_id: ")
    if not re.search(r"^session_id:\s*$", content, re.MULTILINE):
        return  # session_id already present and non-empty

    # Patch it in
    patched = re.sub(
        r"^session_id:\s*$",
        f"session_id: {session_id}",
        content,
        count=1,
        flags=re.MULTILINE,
    )

    # Atomic write
    tmp = STATE_FILE + f".tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        f.write(patched)
    os.rename(tmp, STATE_FILE)


def stop_guard():
    """Stop hook: delete state file if session_id is empty (prevent cross-session trap)."""
    data = _read_stdin()
    session_id = data.get("session_id", "")

    content = _read_state()
    if content is None:
        return

    if re.search(r"^session_id:\s*$", content, re.MULTILINE):
        # Dangerous state file with no session_id — remove it
        os.remove(STATE_FILE)
        print(
            "ralph_loop_safety: removed state file with empty session_id "
            "(prevents cross-session trapping)",
            file=sys.stderr,
        )
        return

    # Extra safety: if state file has a session_id that doesn't match this session,
    # the ralph-loop hook should handle it. We don't interfere.


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "patch-session-id":
        patch_session_id()
    elif mode == "stop-guard":
        stop_guard()
