#!/usr/bin/env python3
"""PLUR Observation Hook (PreToolUse/PostToolUse)

Captures tool calls to a JSONL observation log for offline pattern extraction.
This creates raw material for learning without relying on the LLM calling plur_learn.

Inspired by ECC's continuous-learning-v2 observation hooks.

Observations are stored in ~/.plur/observations/YYYY-MM-DD.jsonl
Kept separate from engrams — these are raw data, not curated knowledge.

A separate analysis step (manual or scheduled) processes observations into engrams.
"""
import json
import os
import sys
import time

OBS_DIR = os.path.expanduser("~/.plur/observations")
MAX_STDIN = 512 * 1024  # 512KB — observations can be trimmed


def main():
    raw = sys.stdin.read(MAX_STDIN)
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        # Pass through silently — don't block on parse errors
        sys.stdout.write(raw)
        sys.exit(0)

    tool_name = data.get("tool_name", "") or data.get("name", "")
    event = os.environ.get("CLAUDE_HOOK_EVENT_NAME", "PreToolUse")

    # Skip observing ourselves and high-frequency low-value tools
    skip_tools = {"Read", "Glob", "Grep", "TaskCreate", "TaskUpdate", "TaskGet", "TaskList"}
    if tool_name in skip_tools:
        sys.stdout.write(raw)
        sys.exit(0)

    # Build observation record
    obs = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": event,
        "tool": tool_name,
        "session_id": os.environ.get("CLAUDE_SESSION_ID", "unknown"),
        "cwd": os.getcwd(),
    }

    # Capture tool input (trimmed) for PreToolUse
    if event == "PreToolUse":
        tool_input = data.get("tool_input", {})
        # Trim large inputs — we want the shape, not the content
        if isinstance(tool_input, dict):
            trimmed = {}
            for k, v in tool_input.items():
                if isinstance(v, str) and len(v) > 200:
                    trimmed[k] = v[:200] + "...[trimmed]"
                else:
                    trimmed[k] = v
            obs["input"] = trimmed
        else:
            obs["input"] = str(tool_input)[:500]

    # Capture success/failure for PostToolUse
    if event in ("PostToolUse", "PostToolUseFailure"):
        obs["success"] = event == "PostToolUse"
        error = data.get("error", "")
        if error:
            obs["error"] = str(error)[:500]

    # Write observation
    os.makedirs(OBS_DIR, exist_ok=True)
    date_str = time.strftime("%Y-%m-%d")
    obs_file = os.path.join(OBS_DIR, f"{date_str}.jsonl")
    try:
        with open(obs_file, "a") as f:
            f.write(json.dumps(obs) + "\n")
    except OSError:
        pass  # Never block on write failure

    sys.stdout.write(raw)
    sys.exit(0)


if __name__ == "__main__":
    main()
