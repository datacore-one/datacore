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

    # Skip guard for non-interactive sessions (bots, Agent SDK, subagents,
    # nightshift, chat-sidecar/headless). DATACORE_HEADLESS covers the
    # datacore-app chat panel which bootstraps plur_session_start itself.
    if (
        os.environ.get("CLAUDE_AGENT_SDK")
        or os.environ.get("OPENCLAW_SESSION")
        or os.environ.get("NIGHTSHIFT_RUN")
        or os.environ.get("DATACORE_HEADLESS")
    ):
        return

    tmp = tempfile.gettempdir()
    sentinel = f"{tmp}/plur-session-{session_id}"
    if os.path.exists(sentinel):
        return

    # Fail-open after a single nudge. We deny at most ONE tool call per session
    # to steer the first action toward session_start; after that we allow
    # everything through even if the call never landed. This is deliberate and
    # model-agnostic: a hard deny-everything guard collapses the model's action
    # space to one allowed call, so any hiccup on that call (e.g. the deferred
    # tool's "task required" race) can spiral into repeated identical calls or an
    # indefinite block. Bounding the guard to one nudge makes that impossible —
    # engram injection is best-effort, never worth trapping the session.
    nudged = f"{tmp}/plur-session-nudged-{session_id}"
    if os.path.exists(nudged):
        return  # already nudged once this session -> allow work to proceed

    try:
        open(nudged, "w").close()
    except OSError:
        return  # can't record the nudge -> fail open rather than risk re-denying

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Reminder: call mcp__plur__plur_session_start once before other "
                "tools so this session has memory. It is a deferred tool — first "
                "run ToolSearch 'select:mcp__plur__plur_session_start' as its own "
                "step, then call it a single time with a short task description. "
                "Don't batch the ToolSearch with the call, and don't repeat it. "
                "This reminder fires only once; your next tool call proceeds "
                "normally whether or not session_start succeeded."
            ),
        }
    }))


if __name__ == "__main__":
    main()
