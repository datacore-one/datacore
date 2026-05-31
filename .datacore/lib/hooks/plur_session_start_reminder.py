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
                "Before other work, start this session's memory with plur:\n"
                "1. Run ToolSearch 'select:mcp__plur__plur_session_start' on its "
                "own and wait for the result. It is a deferred tool — its schema "
                "must load before it can be called.\n"
                "2. Then call mcp__plur__plur_session_start exactly once with a "
                "short task description.\n"
                "Do not batch steps 1 and 2 in the same turn, and do not repeat "
                "the call. A 'task required' error only means the schema wasn't "
                "loaded yet — run ToolSearch, then call it once. A guard nudges "
                "you once if you start with another tool, but it never blocks "
                "your work."
            ),
        }
    }))


if __name__ == "__main__":
    main()
