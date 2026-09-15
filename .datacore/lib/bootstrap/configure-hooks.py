#!/usr/bin/env python3
"""Configure Claude Code hooks for PLUR session lifecycle enforcement.

Merges required hooks into ~/.claude/settings.json idempotently.
Safe to run multiple times — skips hooks that already exist.

Usage:
    python3 configure-hooks.py [--datacore-root /path/to/Data]
"""
import json
import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from file_utils import atomic_write_json, file_lock

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
    return str(Path(__file__).resolve().parents[3])


def build_required_hooks(datacore_root: str) -> dict:
    """Define the hooks that must exist in settings.json."""
    hooks_dir = f"{datacore_root}/.datacore/lib/hooks"
    def hook_command(name, *args):
        return shlex.join(["python3", str(Path(hooks_dir) / name), *args])
    return {
        # plur_session_start_reminder.py / plur_session_guard.py retired in
        # 800fd31 ("wave 1 hygiene: one set of hooks") and #133. Do not wire
        # them: the PreToolUse "*" guard denied every tool call once its script
        # was deleted, which silently broke every `claude -p` gate subprocess.
        "PreToolUse": [
            {
                # DIP-0029: inject command-scoped engrams on Skill/SlashCommand/Agent invocation.
                # Fallback layer for harnesses that don't parse `recall:` frontmatter on commands.
                "matcher": "Skill|SlashCommand|Agent",
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_command("command_recall_inject.py"),
                        "timeout": 3,
                    }
                ],
            },
        ],
        # PostToolUse plur_session_mark.py also retired in 800fd31 / #133.
        "UserPromptSubmit": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_command("plur_inject_wrapper.py"),
                        # async+90s (2026-07-06, matches PLUR PR #502): the CLI cold-start
                        # loads the BGE embedder for hybrid search (~20s once the store
                        # passes a few thousand engrams) — a sync 15s timeout gets killed
                        # before it finishes, discarding the injection. plur_inject_wrapper.py
                        # itself already documents this contract; this entry just wasn't
                        # updated to match when the wrapper was fixed.
                        "timeout": 90,
                        "async": True,
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
                        "command": hook_command("plur_inject_wrapper.py", "--rehydrate"),
                        "timeout": 90,
                        "async": True,
                    }
                ],
            }
        ],
    }


def _find_matching_entry(existing_entries: list, new_entry: dict) -> dict | None:
    """Find an existing entry with the same matcher + overlapping command(s), if any."""
    new_matcher = new_entry.get("matcher", "")
    new_commands = {h.get("command", "") for h in new_entry.get("hooks", [])}

    for entry in existing_entries:
        if entry.get("matcher", "") != new_matcher:
            continue
        existing_commands = {h.get("command", "") for h in entry.get("hooks", [])}
        if new_commands & existing_commands:
            return entry
    return None


def _observation_replacement(command, required):
    """Migrate an existing known PLUR observation command without adding one."""
    if not isinstance(command, str):
        return None
    try:
        parts = shlex.split(command)
        if not parts:
            return None
        if parts[0] == "npx":
            parts = parts[1:]
            if parts and parts[0] in {"-y", "--yes"}:
                parts = parts[1:]
            if not parts or not (parts[0] == "@plur-ai/cli" or parts[0].startswith("@plur-ai/cli@")):
                return None
        elif Path(parts[0]).name not in {"plur", "plur-hook"}:
            return None
        arguments = parts[1:]
        if not arguments or arguments.pop(0) != "hook-observe":
            return None
        redirect = ""
        if arguments[-1:] == [">/dev/null"]:
            arguments.pop(); redirect = " >/dev/null"
        elif arguments[-2:] == [">", "/dev/null"]:
            arguments = arguments[:-2]; redirect = " >/dev/null"
        if arguments not in ([], ["--post"], ["--failure"]):
            return None
        wrapper = required["UserPromptSubmit"][0]["hooks"][0]["command"]
        installed_hook = Path(shlex.split(wrapper)[1]).with_name("plur_observe.py")
        return shlex.join(["python3", str(installed_hook), *arguments]) + redirect
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def merge_hooks(settings: dict, required: dict) -> tuple[dict, list[str], list[str]]:
    """Merge required hooks into settings, returning (updated, added_list, upgraded_list).

    A matching entry (same matcher + command) whose config (timeout/async/type)
    differs from what's required gets REPLACED, not skipped — this was the bug
    (2026-07-06): the old command-only equality check treated an entry as
    "already exists" even when its timeout/async fields were stale, so fixing
    build_required_hooks() alone never propagated to an already-configured
    settings.json. PLUR's own `plur init` hit and fixed this identical class
    of bug earlier (strip-then-reinstall, not skip-if-exists) — same fix here.
    """
    if "hooks" not in settings:
        settings["hooks"] = {}

    added = []
    upgraded = []
    for event, entries in settings["hooks"].items():
        for existing_entry in entries:
            for hook in existing_entry.get("hooks", []):
                replacement = _observation_replacement(hook.get("command", ""), required)
                if replacement is not None:
                    hook["command"] = replacement
                    upgraded.append(f"  {event} (private observation metadata)")
    # Retire only the exact legacy command this installer owned. Preserve
    # other commands, including those grouped into the same hook entry.
    if "PostCompact" in required:
        for entry in settings["hooks"].get("PostCompact", []):
            retained = [h for h in entry.get("hooks", [])
                        if h.get("command") != "npx @plur-ai/cli hook-inject --rehydrate"]
            if len(retained) != len(entry.get("hooks", [])):
                entry["hooks"] = retained
                upgraded.append("  PostCompact (installed CLI migration)")
    for event, entries in required.items():
        if event not in settings["hooks"]:
            settings["hooks"][event] = []

        for entry in entries:
            existing = _find_matching_entry(settings["hooks"][event], entry)
            desc = entry.get("matcher", "default")
            if existing is None:
                # PreToolUse guard must be first to block before other hooks run
                if event == "PreToolUse" and entry.get("matcher") == "*":
                    settings["hooks"][event].insert(0, entry)
                else:
                    settings["hooks"][event].append(entry)
                added.append(f"  {event} ({desc})")
            else:
                replacements = {h["command"]: h for h in entry["hooks"]}
                merged_hooks = [replacements.get(h.get("command"), h)
                                for h in existing.get("hooks", [])]
                present = {h.get("command") for h in merged_hooks}
                merged_hooks.extend(h for h in entry["hooks"] if h["command"] not in present)
                if merged_hooks != existing.get("hooks"):
                    existing["hooks"] = merged_hooks
                    upgraded.append(f"  {event} ({desc})")

    return settings, added, upgraded


def main():
    datacore_root = detect_datacore_root()

    # Verify hook scripts exist
    hooks_dir = Path(datacore_root) / ".datacore" / "lib" / "hooks"
    required_scripts = [
        # plur_session_{start_reminder,guard,mark}.py retired in 800fd31
        # ("wave 1 hygiene: one set of hooks") / #133 — no longer required.
        "plur_inject_wrapper.py",
        "command_recall_inject.py",  # DIP-0029
        "plur_observe.py",
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

    required = build_required_hooks(datacore_root)
    with file_lock(SETTINGS_PATH):
        # A read or parse failure must preserve the prior settings. Publish
        # once, after the complete merge, without a truncate/write window.
        if SETTINGS_PATH.is_symlink():
            raise ValueError("settings must be a regular local file")
        if SETTINGS_PATH.exists():
            settings = json.loads(SETTINGS_PATH.read_text())
        else:
            settings = {}
        settings, added, upgraded = merge_hooks(settings, required)
        if not added and not upgraded:
            print("✓ All PLUR session hooks already configured")
            return
        atomic_write_json(SETTINGS_PATH, settings)

    if added:
        print("✓ Configured PLUR session hooks in ~/.claude/settings.json:")
        for a in added:
            print(a)
    if upgraded:
        print("✓ Upgraded stale PLUR session hooks in ~/.claude/settings.json:")
        for u in upgraded:
            print(u)
    print()
    print("These hooks enforce plur_session_start at the beginning of every session.")
    print("Restart Claude Code for changes to take effect.")


if __name__ == "__main__":
    main()
