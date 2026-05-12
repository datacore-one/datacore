#!/usr/bin/env python3
"""Configure Claude Code hooks for PLUR session lifecycle enforcement.

Merges required hooks into ~/.claude/settings.json idempotently.
Safe to run multiple times — skips hooks that already exist.

Usage:
    python3 configure-hooks.py [--datacore-root /path/to/Data]
"""
import json
import os
import sys
from pathlib import Path

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def detect_datacore_root():
    """Find the Datacore root directory."""
    # CLI override
    for i, arg in enumerate(sys.argv):
        if arg == "--datacore-root" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    # Default
    default = Path.home() / "Data"
    if default.exists():
        return str(default)
    # Fallback: parent of this script
    return str(Path(__file__).resolve().parent.parent.parent)


def build_required_hooks(datacore_root: str) -> dict:
    """Define the hooks that must exist in settings.json."""
    hooks_dir = f"{datacore_root}/.datacore/lib/hooks"
    return {
        "SessionStart": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f"python3 {hooks_dir}/plur_session_start_reminder.py",
                        "timeout": 3,
                    }
                ]
            }
        ],
        "PreToolUse": [
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"python3 {hooks_dir}/plur_session_guard.py",
                        "timeout": 3,
                    }
                ],
            },
            {
                # DIP-0029: inject command-scoped engrams on Skill/SlashCommand/Agent invocation.
                # Fallback layer for harnesses that don't parse `recall:` frontmatter on commands.
                "matcher": "Skill|SlashCommand|Agent",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"python3 {hooks_dir}/command_recall_inject.py",
                        "timeout": 3,
                    }
                ],
            },
        ],
        "PostToolUse": [
            {
                "matcher": "mcp__plur__plur_session_start",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"python3 {hooks_dir}/plur_session_mark.py",
                        "timeout": 3,
                    }
                ],
            }
        ],
        "UserPromptSubmit": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f"python3 {hooks_dir}/plur_inject_wrapper.py",
                        "timeout": 15,
                    }
                ]
            }
        ],
        "PostCompact": [
            {
                "matcher": "auto|manual",
                "hooks": [
                    {
                        "type": "command",
                        "command": "npx @plur-ai/cli hook-inject --rehydrate",
                        "timeout": 15,
                    }
                ],
            }
        ],
    }


def hook_already_exists(existing_entries: list, new_entry: dict) -> bool:
    """Check if a hook entry (by matcher + command) already exists."""
    new_matcher = new_entry.get("matcher", "")
    new_commands = {h.get("command", "") for h in new_entry.get("hooks", [])}

    for entry in existing_entries:
        if entry.get("matcher", "") != new_matcher:
            continue
        existing_commands = {h.get("command", "") for h in entry.get("hooks", [])}
        if new_commands & existing_commands:
            return True
    return False


def merge_hooks(settings: dict, required: dict) -> tuple[dict, list[str]]:
    """Merge required hooks into settings, returning (updated, added_list)."""
    if "hooks" not in settings:
        settings["hooks"] = {}

    added = []
    for event, entries in required.items():
        if event not in settings["hooks"]:
            settings["hooks"][event] = []

        for entry in entries:
            if not hook_already_exists(settings["hooks"][event], entry):
                # PreToolUse guard must be first to block before other hooks run
                if event == "PreToolUse" and entry.get("matcher") == "*":
                    settings["hooks"][event].insert(0, entry)
                else:
                    settings["hooks"][event].append(entry)
                desc = entry.get("matcher", "default")
                added.append(f"  {event} ({desc})")

    return settings, added


def main():
    datacore_root = detect_datacore_root()

    # Verify hook scripts exist
    hooks_dir = Path(datacore_root) / ".datacore" / "lib" / "hooks"
    required_scripts = [
        "plur_session_start_reminder.py",
        "plur_session_guard.py",
        "plur_session_mark.py",
        "plur_inject_wrapper.py",
        "command_recall_inject.py",  # DIP-0029
    ]
    missing = [s for s in required_scripts if not (hooks_dir / s).exists()]
    if missing:
        print(f"ERROR: Missing hook scripts in {hooks_dir}:")
        for m in missing:
            print(f"  - {m}")
        print("Run 'git pull' in your Datacore root first.")
        sys.exit(1)

    # Ensure ~/.claude/ exists
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Read existing settings
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH) as f:
            settings = json.load(f)
    else:
        settings = {}

    required = build_required_hooks(datacore_root)
    settings, added = merge_hooks(settings, required)

    if not added:
        print("✓ All PLUR session hooks already configured")
        return

    # Write back
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")

    print("✓ Configured PLUR session hooks in ~/.claude/settings.json:")
    for a in added:
        print(a)
    print()
    print("These hooks enforce plur_session_start at the beginning of every session.")
    print("Restart Claude Code for changes to take effect.")


if __name__ == "__main__":
    main()
