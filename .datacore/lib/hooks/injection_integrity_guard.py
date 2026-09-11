#!/usr/bin/env python3
"""Make a TRUNCATED memory injection able to stop the session.

WHY THIS EXISTS
---------------
2026-09-07: plur_session_start returned 115,052 characters. That exceeded the
tool-result limit, so the harness spilled it to a file and handed back a
pointer. The model read 5,000 of those characters — 4% — and carried on as
though it had its memory. Four engrams forbidding what happened next were in
the 96% it skipped, including one literally titled DEMO MODE.

Nothing failed. PLUR recalled correctly, the harness truncated correctly, and
the model proceeded incorrectly — silently, because a pointer to a file reads
exactly like a result you already have.

This hook removes the silence. A truncated injection becomes a hard gate: no
tool runs except the ones needed to read the spilled file, until it is read.

MODES
-----
  mark   PostToolUse on mcp__plur__plur_session_start — detect the spill,
         record the path, arm the gate.
  check  PreToolUse '*' — refuse tools while the gate is armed.
  clear  PostToolUse on Read|Bash|Grep — disarm once the file is read.

FAIL-SAFE
---------
Every failure path exits 0 (allow). A guard that bricks a session on its own
bug is worse than the leak it prevents. Set DATACORE_SKIP_INJECTION_GUARD=1
to disable entirely.
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hook_state import state_path
from file_utils import atomic_write_json
# Tools that must stay available so the spilled file can actually be read.
READERS = {"Read", "Bash", "Grep", "Glob", "ToolSearch"}
SPILL_PAT = re.compile(
    r"(?:exceeds maximum allowed tokens|Output has been saved to)"
)
PATH_PAT = re.compile(r"(/[^\s\"']*tool-results/[^\s\"']+\.txt)")


def _state(sid: str) -> Path:
    return state_path("injection-gate", sid)


def _load() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def _text(obj) -> str:
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj)
    except Exception:
        return str(obj)


def mark(data: dict) -> None:
    resp = _text(data.get("tool_response", ""))
    if not SPILL_PAT.search(resp):
        return
    m = PATH_PAT.search(resp)
    if not m:
        return
    atomic_write_json(_state(data.get("session_id", "")), {"path": m.group(1), "armed": True})


def check(data: dict) -> None:
    p = _state(data.get("session_id", ""))
    if not p.exists():
        return
    try:
        st = json.loads(p.read_text())
    except Exception:
        return
    if not st.get("armed"):
        return
    if data.get("tool_name") in READERS:
        return
    path = st.get("path", "the spilled injection file")
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "MEMORY INJECTION TRUNCATED — you do not have your memory yet.\n\n"
                        f"plur_session_start spilled to:\n  {path}\n\n"
                        "You have read only the pointer, not the payload. Engrams that "
                        "constrain what you may SAY — client names, demo-mode redaction, "
                        "credential handling — live in the middle of that file, not at its "
                        "head or tail. Reading the first and last few KB is how a redaction "
                        "rule gets skipped (2026-09-07).\n\n"
                        "Read the complete file using the Read tool.\n\n"
                        "This gate lifts automatically once that file is read."
                    ),
                }
            }
        )
    )
    sys.exit(0)


def clear(data: dict) -> None:
    p = _state(data.get("session_id", ""))
    if not p.exists():
        return
    try:
        st = json.loads(p.read_text())
    except Exception:
        p.unlink(missing_ok=True)
        return
    target = st.get("path", "")
    if not target:
        p.unlink(missing_ok=True)
        return
    blob = _text(data.get("tool_input", "")) + _text(data.get("tool_response", ""))
    if target in blob or os.path.basename(target) in blob:
        p.unlink(missing_ok=True)


def main() -> None:
    if os.environ.get("DATACORE_SKIP_INJECTION_GUARD") == "1":
        sys.exit(0)
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    data = _load()
    try:
        {"mark": mark, "check": check, "clear": clear}.get(mode, check)(data)
    except SystemExit:
        raise
    except Exception:
        # Never brick a session on this guard's own bug.
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
