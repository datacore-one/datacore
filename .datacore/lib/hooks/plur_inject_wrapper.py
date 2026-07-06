#!/usr/bin/env python3
"""Wrapper around plur hook-inject that adds forceful session start reminder
if the sentinel file doesn't exist yet.

Runs async (settings.json: async: true, timeout: 90) — the CLI cold-start
loads the BGE embedder (~20s with a 4k-engram store), too slow for a
blocking hook. Inner subprocess timeout stays under the hook's 90s budget.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Prefer the plur-init-installed shim (direct node invocation, no npx
# resolution overhead); fall back to npx when it isn't installed.
_SHIM = Path.home() / ".plur" / "bin" / "plur-hook"


def _hook_cmd():
    if _SHIM.exists():
        return [str(_SHIM), "hook-inject"]
    return ["npx", "@plur-ai/cli", "hook-inject"]


def main():
    # Read stdin for session_id
    try:
        stdin_data = sys.stdin.read()
        data = json.loads(stdin_data) if stdin_data.strip() else {}
    except (json.JSONDecodeError, EOFError):
        stdin_data = ""
        data = {}

    session_id = data.get("session_id", "")

    # Run the original hook-inject, FORWARDING the hook payload on stdin —
    # hook-inject reads {prompt} from stdin for prompt-aware injection.
    # Swallowing it here (pre-2026-07-06 bug) degraded every injection to
    # a generic 'general session' query.
    result = subprocess.run(
        _hook_cmd(),
        input=stdin_data,
        capture_output=True, text=True, timeout=85,
    )
    try:
        output = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError:
        output = {}

    # If session not started, prepend forceful reminder. Skip reminder in
    # headless contexts (chat-sidecar bootstraps the session itself); the
    # injection itself still runs.
    sentinel = f"{tempfile.gettempdir()}/plur-session-{session_id}" if session_id else ""
    if sentinel and not os.path.exists(sentinel) and not os.environ.get("DATACORE_HEADLESS"):
        existing = output.get("additionalContext", "")
        reminder = (
            ">>> MANDATORY: Call plur_session_start IMMEDIATELY before any other action. "
            "A PreToolUse guard will block all tools until you do. <<<"
        )
        output["additionalContext"] = f"{reminder}\n\n{existing}" if existing else reminder

    print(json.dumps(output))


if __name__ == "__main__":
    main()
