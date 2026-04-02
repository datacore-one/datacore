#!/usr/bin/env python3
"""Cost Tracker Hook (Stop)

Logs session metrics (tool calls, timestamp) to ~/.claude/session-costs.jsonl.
Lightweight — just appends a line per Stop event.
"""
import json
import os
import sys
import time


LOG_FILE = os.path.expanduser("~/.claude/session-costs.jsonl")


def main():
    raw = sys.stdin.read(1024 * 1024)
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}

    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "session_id": os.environ.get("CLAUDE_SESSION_ID", "unknown"),
        "cwd": os.getcwd(),
        "stop_reason": data.get("stop_reason", ""),
    }

    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass

    sys.stdout.write(raw)


if __name__ == "__main__":
    main()
