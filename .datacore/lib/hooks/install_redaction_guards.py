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

Privacy guards (2026-09-26, promises SPC-4/5, MEM-13/15/17/19), wired the same way:

  space_policy_guard.py   PreToolUse Bash|Edit|Write|Read|MultiEdit. Space type
                          policy; a space session stays out of other spaces;
                          a session that touched a client space writes nothing
                          outside it; a person document outside the personal
                          space asks first.
  memory_guard.py         PreToolUse Edit|Write|MultiEdit. No name, amount, host
                          or secret on a line of always-loaded auto-memory.
  publish_guard.py        PreToolUse Artifact|Bash. An Artifact publish, gist,
                          surge, netlify or vercel deploy asks first.
  context_merge.py check  SessionStart. Rebuilds a stale composed CLAUDE.md,
                          reports a hand-edited one.

Eval guard (2026-09-27, promise MEM-30):

  eval_guard.py           PreToolUse Edit|Write|MultiEdit|Bash. Changing an
                          existing promise eval asks the owner first.

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
SPG = f"python3 {HOOKS / 'space_policy_guard.py'}"
MG = f"python3 {HOOKS / 'memory_guard.py'}"
PG = f"python3 {HOOKS / 'publish_guard.py'}"
EG = f"python3 {HOOKS / 'eval_guard.py'}"
CC = f"python3 {HOOKS.parent / 'context_merge.py'} check --fix --quiet"

# (guard, event, matcher, command, timeout). matcher None => no matcher key.
# Each guard is switched on by the owner, not by an agent: `--only` names them
# (owner, 2026-09-26: redaction, publish and context; not space or memory yet).
WIRING = [
    ("redaction", "UserPromptSubmit", None, RG, 5),
    ("redaction", "PostToolUse", "mcp__plur__plur_session_start", f"{IG} mark", 5),
    ("redaction", "PreToolUse", "*", f"{IG} check", 5),
    ("redaction", "PostToolUse", "Read|Bash|Grep|Glob", f"{IG} clear", 5),
    ("space", "PreToolUse", "Bash|Edit|Write|Read|MultiEdit", SPG, 5),
    ("memory", "PreToolUse", "Edit|Write|MultiEdit", MG, 5),
    ("publish", "PreToolUse", "Artifact|Bash", PG, 5),
    ("context", "SessionStart", None, CC, 20),
    ("evals", "PreToolUse", "Edit|Write|MultiEdit|Bash", EG, 5),
]
GUARDS = sorted({w[0] for w in WIRING})


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
    ap.add_argument("--only", default=",".join(GUARDS),
                    help=f"comma-separated guards to wire (default all): {', '.join(GUARDS)}")
    args = ap.parse_args()
    wanted = {g.strip() for g in args.only.split(",") if g.strip()}
    unknown = wanted - set(GUARDS)
    if unknown:
        sys.exit(f"unknown guard(s): {', '.join(sorted(unknown))}; choose from {', '.join(GUARDS)}")
    wiring = [w[1:] for w in WIRING if w[0] in wanted]

    for _event, _matcher, cmd, _timeout in wiring:
        script = Path(cmd.split()[1])
        if not script.exists():
            sys.exit(f"missing hook: {script}")

    if not SETTINGS.exists():
        sys.exit(f"not found: {SETTINGS}")

    data = json.loads(SETTINGS.read_text())
    hooks = data.setdefault("hooks", {})

    print(f"settings: {SETTINGS}")
    for event, matcher, cmd, timeout in wiring:
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
