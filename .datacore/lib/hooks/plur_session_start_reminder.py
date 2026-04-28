#!/usr/bin/env python3
"""SessionStart hook: forceful reminder to call plur_session_start immediately."""
import json
import os
import sys

# Headless guard: chat-sidecar bootstraps plur_session_start programmatically,
# so the reminder is noise. Emit empty JSON and exit.
if os.environ.get("DATACORE_HEADLESS"):
    print(json.dumps({}))
    sys.exit(0)


def main():
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                "MANDATORY FIRST ACTION — DO THIS NOW BEFORE ANYTHING ELSE:\n"
                "1. Call ToolSearch with query 'select:mcp__plur__plur_session_start'\n"
                "2. Call mcp__plur__plur_session_start with the user's task description\n"
                "3. Only THEN proceed with any other work\n\n"
                "A PreToolUse guard will BLOCK all other tool calls until this is done. "
                "This is not optional. This is not a suggestion. Do it now."
            ),
        }
    }))


if __name__ == "__main__":
    main()
