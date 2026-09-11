"""CLI invocation for text-only inference over untrusted source material.

Requires a Claude CLI supporting --safe-mode. An older CLI fails closed;
callers may use their existing API fallback, never retry with tools enabled.
Prompts are passed through stdin, not exposed in process-list arguments.
"""


def claude_text_command(executable="claude"):
    return [
        executable, "-p", "--safe-mode", "--tools", "",
        "--strict-mcp-config", "--disallowedTools", "mcp__*",
        "--disable-slash-commands", "--no-session-persistence",
        "--output-format", "text",
    ]


def claude_text_options():
    """Equivalent SDK boundary, without importing an optional SDK."""
    return {
        "tools": [], "permission_mode": "dontAsk", "setting_sources": [],
        "mcp_servers": {}, "disallowed_tools": ["mcp__*"],
        "extra_args": {"safe-mode": None, "strict-mcp-config": None,
                       "no-session-persistence": None, "disable-slash-commands": None},
    }
