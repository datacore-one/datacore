#!/usr/bin/env python3
"""Wrapper around plur hook-inject that adds forceful session start reminder
if the sentinel file doesn't exist yet."""
import json
import os
import subprocess
import sys
import tempfile


def main():
    # Read stdin for session_id
    try:
        stdin_data = sys.stdin.read()
        data = json.loads(stdin_data) if stdin_data.strip() else {}
    except (json.JSONDecodeError, EOFError):
        data = {}

    session_id = data.get("session_id", "")

    # Run the original hook-inject
    result = subprocess.run(
        ["npx", "@plur-ai/cli", "hook-inject"],
        capture_output=True, text=True, timeout=15,
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
