#!/usr/bin/env python3
"""Probe whether SDK options.env propagates to hook subprocesses.

Background: chat-sidecar.mjs sets DATACORE_HEADLESS=1 (and friends) via the
SDK's options.env. We KNOW that env reaches the SDK's MCP child processes —
the question is whether the same env reaches HOOK subprocesses spawned by
the Claude Code harness inside the SDK (PreToolUse, UserPromptSubmit, Stop,
etc).

If env does propagate, the headless guards added to
~/Data/.datacore/lib/hooks/{desktop_notify,plur_session_start_reminder,
plur_session_guard,plur_inject_wrapper}.py will work as designed.

If env does NOT propagate, those guards never fire and we need a different
mechanism (wrapper script per hook, or a sentinel file like
/tmp/datacore-headless).

Usage:
  1. Run the probe via chat-sidecar:
       cd ~/Data/2-datacore/2-projects/datacore-app/shell
       echo '{"type":"query","id":"probe-1","prompt":"read /tmp/probe-test.txt then write /tmp/probe-test.out","cwd":"/tmp"}' \
         | timeout 30 node chat-sidecar.mjs

  2. Inspect the resulting file: cat /tmp/datacore-hook-env-probe.json
     Expected: {"DATACORE_HEADLESS": "1", ...}
     If empty/missing keys → env did NOT propagate.

This script is intended to be wired as a PreToolUse hook on a temp
settings.json the user can opt into. For a quick check, you can also wire it
in ~/.claude/settings.json under hooks.PreToolUse and trigger any tool from
the chat-sidecar.
"""
import json
import os
import pathlib
import sys

OUT = pathlib.Path("/tmp/datacore-hook-env-probe.json")

env_snapshot = {k: v for k, v in os.environ.items() if k.startswith("DATACORE_") or k == "CLAUDE_AGENT_SDK"}
OUT.write_text(json.dumps(env_snapshot, sort_keys=True, indent=2))

# Hook contract: PreToolUse hooks read JSON from stdin. Pass it through so we
# don't break tool execution.
try:
    payload = sys.stdin.read()
    sys.stdout.write(payload)
except Exception:
    pass
