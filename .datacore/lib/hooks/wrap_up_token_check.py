#!/usr/bin/env python3
"""PreToolUse hook for plur_session_end: verify Token Cost was written to journal.

Blocks plur_session_end if today's journal is missing a "Token Cost" section,
which means the wrap-up skipped the token accounting step.
"""
import json
import os
import sys
from datetime import date
from pathlib import Path


def main():
    raw = sys.stdin.read(1024 * 1024)
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        sys.stdout.write(raw)
        return

    tool_name = data.get("tool_name", "")
    if tool_name != "mcp__plur__plur_session_end":
        sys.stdout.write(raw)
        return

    # Check today's journal for Token Cost section
    today = date.today().isoformat()
    journal_paths = [
        Path.home() / "Data" / "0-personal" / "notes" / "journals" / f"{today}.md",
        Path.home() / "Data" / "0-personal" / "journal" / f"{today}.md",
    ]

    for jp in journal_paths:
        if jp.exists():
            content = jp.read_text()
            if "Token Cost" in content or "token cost" in content.lower():
                # Token cost section found — allow session end
                sys.stdout.write(raw)
                return

    # Token cost missing — block with feedback
    result = {
        "decision": "block",
        "reason": "WRAP-UP INCOMPLETE: Today's journal is missing a '### Token Cost' section. "
                  "Write the token cost table to the journal BEFORE calling plur_session_end. "
                  "Calculate subagent tokens from their usage blocks, estimate main conversation, show total."
    }
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
