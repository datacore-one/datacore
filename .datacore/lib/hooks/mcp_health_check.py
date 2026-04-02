#!/usr/bin/env python3
"""MCP Health Check Hook (PreToolUse: mcp__*)

Tracks MCP server health state. When an MCP tool is about to be called,
checks if the server was recently marked unhealthy and blocks the call
with an informative message so Claude falls back to non-MCP alternatives.

On PostToolUseFailure, marks the server unhealthy with exponential backoff.

State persisted in ~/.claude/mcp-health-cache.json
"""
import json
import os
import sys
import time

STATE_FILE = os.path.expanduser("~/.claude/mcp-health-cache.json")
TTL_SECONDS = 120  # healthy cache TTL
BACKOFF_BASE = 30  # seconds
MAX_BACKOFF = 600  # 10 minutes


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"servers": {}}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass


def extract_server(data):
    """Extract MCP server name from tool_name like mcp__servername__toolname."""
    tool_name = data.get("tool_name", "") or data.get("name", "")
    if not tool_name.startswith("mcp__"):
        return None
    parts = tool_name[5:].split("__", 1)
    return parts[0] if parts[0] else None


def main():
    raw = sys.stdin.read(1024 * 1024)
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}

    server = extract_server(data)
    if not server:
        sys.stdout.write(raw)
        sys.exit(0)

    event = os.environ.get("CLAUDE_HOOK_EVENT_NAME", "PreToolUse")
    now = time.time()
    state = load_state()
    servers = state.setdefault("servers", {})

    if event == "PostToolUseFailure":
        # Mark unhealthy on failure
        prev = servers.get(server, {})
        fails = prev.get("failure_count", 0) + 1
        backoff = min(BACKOFF_BASE * (2 ** max(fails - 1, 0)), MAX_BACKOFF)
        servers[server] = {
            "status": "unhealthy",
            "checked_at": now,
            "failure_count": fails,
            "retry_after": now + backoff,
            "last_error": str(data.get("error", ""))[:500],
        }
        save_state(state)
        tool_name = data.get("tool_name", server)
        sys.stderr.write(
            f"[MCPHealth] {server} marked unhealthy after failure "
            f"(attempt {fails}, retry in {int(backoff)}s)\n"
        )
        sys.stdout.write(raw)
        sys.exit(0)

    # PreToolUse — check health
    info = servers.get(server, {})

    # Healthy and cache still valid
    if info.get("status") == "healthy" and info.get("expires_at", 0) > now:
        sys.stdout.write(raw)
        sys.exit(0)

    # Unhealthy and still in backoff
    if info.get("status") == "unhealthy" and info.get("retry_after", 0) > now:
        wait = int(info["retry_after"] - now)
        sys.stderr.write(
            f"[MCPHealth] {server} is unhealthy (retry in {wait}s). "
            f"Blocking MCP call so Claude uses fallback tools.\n"
        )
        sys.exit(2)

    # Unknown or expired — allow and mark healthy optimistically
    servers[server] = {
        "status": "healthy",
        "checked_at": now,
        "expires_at": now + TTL_SECONDS,
        "failure_count": 0,
    }
    save_state(state)
    sys.stdout.write(raw)
    sys.exit(0)


if __name__ == "__main__":
    main()
