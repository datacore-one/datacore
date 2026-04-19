#!/usr/bin/env python3
"""PreToolUse guard: blocks ALL tool calls until plur_session_start has been called.

Sentinel file: /tmp/plur-session-{session_id}
Created by: plur_session_mark.py (PostToolUse on plur_session_start)
"""
import json
import os
import sys
import tempfile

EXEMPT_TOOLS = {
    "mcp__plur__plur_session_start",
    "ToolSearch",
}


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    tool_name = data.get("tool_name", "")
    session_id = data.get("session_id", "")

    # Always allow exempt tools through
    if tool_name in EXEMPT_TOOLS:
        return

    if not session_id:
        return

    sentinel = f"{tempfile.gettempdir()}/plur-session-{session_id}"
    if os.path.exists(sentinel):
        return

    # Block the tool call
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "BLOCKED: plur_session_start has not been called yet. "
                "You MUST call mcp__plur__plur_session_start before using any other tool. "
                "Use ToolSearch to load it first if needed."
            ),
        }
    }))


if __name__ == "__main__":
    main()
