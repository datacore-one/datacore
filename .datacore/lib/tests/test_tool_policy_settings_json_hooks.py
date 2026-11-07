"""Test that settings_with_safety_guards.settings_json() includes all required safety hooks.

This test ensures that the generated settings for unattended Claude Code runs
(like Winston's cron jobs) include:
- org_date_prewrite.py (PreToolUse, Edit|Write|MultiEdit)
- redaction_guard.py (UserPromptSubmit)

Without these hooks, safety promises MEM-09, MEM-12, MEM-15, SPC-4, SPC-6,
SPC-7, SPC-8, AUD-8 cannot be met.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import settings_with_safety_guards  # noqa: E402

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"


def test_settings_json_includes_org_date_prewrite():
    """org_date_prewrite.py must be wired as PreToolUse for Edit|Write|MultiEdit."""
    settings = json.loads(settings_with_safety_guards.settings_json())
    hooks = settings.get("hooks", {})
    pre_tool_use = hooks.get("PreToolUse", [])
    
    # Find the org_date_prewrite hook
    found = False
    for group in pre_tool_use:
        matcher = group.get("matcher", "")
        if matcher != "Edit|Write|MultiEdit":
            continue
        for hook in group.get("hooks", []):
            cmd = hook.get("command", "")
            if "org_date_prewrite.py" in cmd:
                found = True
                break
    
    assert found, (
        "org_date_prewrite.py is not wired in settings_json() output -- "
        "date safety check will not run for Winston's Claude Code sessions"
    )


def test_settings_json_includes_redaction_guard():
    """redaction_guard.py must be wired as UserPromptSubmit."""
    settings = json.loads(settings_with_safety_guards.settings_json())
    hooks = settings.get("hooks", {})
    user_prompt_submit = hooks.get("UserPromptSubmit", [])
    
    found = False
    for group in user_prompt_submit:
        for hook in group.get("hooks", []):
            cmd = hook.get("command", "")
            if "redaction_guard.py" in cmd:
                found = True
                break
    
    assert found, (
        "redaction_guard.py is not wired in settings_json() output -- "
        "client-name redaction rule will not be injected for Winston's Claude Code sessions"
    )


if __name__ == "__main__":
    test_settings_json_includes_org_date_prewrite()
    test_settings_json_includes_redaction_guard()
    print("All tests passed with settings_with_safety_guards!")
