#!/usr/bin/env python3
"""Generate Claude Code settings with all safety guards (tool_policy wrapper).

Extends tool_policy.settings_json() to include UserPromptSubmit hooks that
tool_policy doesn't currently wire, such as redaction_guard.py.

This module is used by cos_llm.sh instead of calling tool_policy.settings_json()
directly, ensuring Winston's Claude Code sessions get all required safety hooks.
"""
import json
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tool_policy  # noqa: E402


def settings_json(guard: Path | None = None, timeout: int = 8) -> str:
    """Generate settings JSON with all safety hooks.

    Calls tool_policy.settings_json() for PreToolUse hooks, then adds
    UserPromptSubmit hooks for redaction_guard.py.
    """
    guard = Path(guard or tool_policy.GUARD)

    # Get the base settings from tool_policy (PreToolUse hooks)
    base_settings = json.loads(tool_policy.settings_json(guard, timeout))
    hooks = base_settings.get("hooks", {})

    # Add UserPromptSubmit hooks for redaction safety
    user_prompt_submit = hooks.get("UserPromptSubmit", [])
    redaction_guard_path = guard.parent / "redaction_guard.py"
    if redaction_guard_path.is_file():
        # Check if it's already wired
        found = any("redaction_guard.py" in h.get("command", "")
                   for g in user_prompt_submit
                   for h in g.get("hooks", []))
        if not found:
            user_prompt_submit.append({
                "hooks": [{
                    "type": "command",
                    "command": f"python3 {shlex.quote(str(redaction_guard_path))}",
                    "timeout": 5
                }]
            })

    hooks["UserPromptSubmit"] = user_prompt_submit
    return json.dumps({"hooks": hooks})


if __name__ == "__main__":
    print(settings_json())
