#!/usr/bin/env python3
"""UserPromptSubmit hook — bootstrap datacore session state (DIP-0024).

Creates the per-session state file that other reactive hooks depend on
(session_time_guardian, active_memory, session_cleanup). ~1ms, sync —
must complete before session_time_guardian reads the state.

Engram injection and the periodic plur_learn reminder are NOT done here.
Both are owned by the PLUR CLI's own hook (plur_inject_wrapper.py →
hook-inject), which does prompt-aware hybrid injection, Enterprise
remote-first, .plur.yaml scopes, and the 10-minute reminder. This script
used to duplicate both — a second 20s+ CLI cold-start per session, and a
reminder timer sharing /tmp/plur-sessions/{ppid}.reminded with
hook-inject so the two fought over it. Consolidated 2026-07-06.

Input: JSON on stdin with {prompt, session_id, ...}
Output: nothing (exit 0)
"""
import json, sys, os
from pathlib import Path

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
sys.path.insert(0, str(DATACORE_ROOT / ".datacore" / "lib"))
import session_state
from session_state import session_exists, create_session, _debug


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        input_data = {}

    session_state.set_session_id(input_data.get("session_id", ""))

    # Hot path: session already started (~1ms)
    if session_exists():
        sys.exit(0)

    create_session(input_data.get("prompt", ""))
    _debug("first message: session state created")


if __name__ == "__main__":
    main()
