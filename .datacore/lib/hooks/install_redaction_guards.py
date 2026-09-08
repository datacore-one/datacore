#!/usr/bin/env python3
"""Wire the redaction + injection-integrity guards into ~/.claude/settings.json.

Written 2026-09-07 after a live demo disclosed a customer name, their invoice
timing, and infrastructure health to an audience. The rules forbidding all
three were already engrams and were already injected — inside a 115,052-char
session_start payload that exceeded the tool-result limit, spilled to a file,
and was read at 4%.

Two guards close that:

  redaction_guard.py            UserPromptSubmit. Re-injects a compact
                                (~700 char) redaction block EVERY turn, so the
                                client-name rule can never be truncated away.
                                Escalates to the full DEMO MODE block when the
                                session looks like a demo, and stays escalated.

  injection_integrity_guard.py  Turns a truncated injection into a hard gate.
                                No tool runs except readers until the spilled
                                file is actually read.

Run:   python3 .datacore/lib/hooks/install_redaction_guards.py [--dry-run]

Idempotent. Backs up settings.json before writing. Never removes a hook.
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"
HOOKS = Path.home() / "Data" / ".datacore" / "lib" / "hooks"
RG = f"python3 {HOOKS / 'redaction_guard.py'}"
IG = f"python3 {HOOKS / 'injection_integrity_guard.py'}"

# (event, matcher, command, timeout). matcher None => no matcher key.
WIRING = [
    ("UserPromptSubmit", None, RG, 5),
    ("PostToolUse", "mcp__plur__plur_session_start", f"{IG} mark", 5),
    ("PreToolUse", "*", f"{IG} check", 5),
    ("PostToolUse", "Read|Bash|Grep|Glob", f"{IG} clear", 5),
]


def already(groups, cmd):
    return any(cmd in h.get("command", "") for g in groups for h in g.get("hooks", []))


def add(hooks, event, matcher, cmd, timeout):
    groups = hooks.setdefault(event, [])
    if already(groups, cmd):
        return f"  = {event}[{matcher}] already wired"
    entry = {"type": "command", "command": cmd, "timeout": timeout}
    for g in groups:
        if g.get("matcher") == matcher:
            g["hooks"].append(entry)
            return f"  + {event}[{matcher}] appended to existing group"
    grp = {"hooks": [entry]}
    if matcher is not None:
        grp["matcher"] = matcher
    groups.append(grp)
    return f"  + {event}[{matcher}] new group created"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    for f in ("redaction_guard.py", "injection_integrity_guard.py"):
        if not (HOOKS / f).exists():
            sys.exit(f"missing hook: {HOOKS / f}")

    if not SETTINGS.exists():
        sys.exit(f"not found: {SETTINGS}")

    data = json.loads(SETTINGS.read_text())
    hooks = data.setdefault("hooks", {})

    print(f"settings: {SETTINGS}")
    for event, matcher, cmd, timeout in WIRING:
        print(add(hooks, event, matcher, cmd, timeout))

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    backup = SETTINGS.with_suffix(f".json.bak-{stamp}")
    shutil.copy2(SETTINGS, backup)
    SETTINGS.write_text(json.dumps(data, indent=2) + "\n")
    json.loads(SETTINGS.read_text())  # parse-check what we just wrote
    print(f"\nbackup:  {backup}")
    print("written. Restart Claude Code for the hooks to load.")


if __name__ == "__main__":
    main()
