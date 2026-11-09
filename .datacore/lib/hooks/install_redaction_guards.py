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

Client guards (2026-09-28, promises MEM-13/15, owner: "Client guards should be in place"):

  space_policy_guard.py --client   PreToolUse Bash|Edit|Write|Read|MultiEdit. Only
                          the two client rules of the space guard: a session that
                          touched a client space changes nothing outside it, and a
                          person document outside the personal space asks first.
                          The space-type policy and the cross-space rule stay off
                          (they are the separate "space" guard).

Eval guard (2026-09-27, promise MEM-30):

  eval_guard.py           PreToolUse Edit|Write|MultiEdit|Bash. Changing an
                          existing promise eval asks the owner first.

Guard guard (2026-09-27, promise MEM-09, owner approved):

  config_protection.py    PreToolUse Bash|Edit|Write|MultiEdit. A guard cannot be
                          unplugged: dropping its hook, stubbing it, or the git
                          bypasses (--no-verify, SKIP_PRE_PUSH, core.hooksPath).

Policy guard (2026-10-01, promises AGT-11 and INS-7, owner approved AGT-11 2026-09-30):

  tool_policy_guard.py    PreToolUse *. Every Claude Code run on an agent's machine,
                          whatever script starts it, passes the tool-policy guard
                          (no stash, reset --hard, autostash, another writer's log).
                          Was hand-added to each agent machine's user settings;
                          agent_host_setup.sh now wires and verifies it with
                          `--only policy --hooks-dir <runner lib>/hooks --create`.

Per machine (2026-10-01): the roster's servers.<host>.guards names the guards a
machine needs; agent_host_setup.sh --host X wires them with --only <those> and
its --verify fails naming each one missing. Which guards a machine gets is the
owner's entry in the roster, never the installer's default.

Run:   python3 .datacore/lib/hooks/install_redaction_guards.py [--dry-run]
       ... --only policy --hooks-dir DIR [--create] [--verify]

--hooks-dir   where the guard scripts live (default ~/Data/.datacore/lib/hooks)
--create      start from an empty settings file when there is none (a new machine)
--verify      change nothing; exit 1 unless every chosen guard is wired, before the
              tools it covers, to a script that exists (a hand-wired copy at another
              path counts)

Idempotent. Backs up settings.json before writing, and writes only when something
was added. Never removes a hook.
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"
HOOKS = Path.home() / "Data" / ".datacore" / "lib" / "hooks"


def wiring_for(hooks: Path) -> list[tuple]:
    """(guard, event, matcher, command, timeout). matcher None => no matcher key.
    Each guard is switched on by the owner, not by an agent: `--only` names them
    (owner, 2026-09-26: redaction, publish and context; not space or memory yet)."""
    rg = f"python3 {hooks / 'redaction_guard.py'}"
    ig = f"python3 {hooks / 'injection_integrity_guard.py'}"
    spg = f"python3 {hooks / 'space_policy_guard.py'}"
    return [
        ("redaction", "UserPromptSubmit", None, rg, 5),
        ("redaction", "PostToolUse", "mcp__plur__plur_session_start", f"{ig} mark", 5),
        ("redaction", "PreToolUse", "*", f"{ig} check", 5),
        ("redaction", "PostToolUse", "Read|Bash|Grep|Glob", f"{ig} clear", 5),
        ("space", "PreToolUse", "Bash|Edit|Write|Read|MultiEdit", spg, 5),
        ("client", "PreToolUse", "Bash|Edit|Write|Read|MultiEdit", f"{spg} --client", 5),
        ("memory", "PreToolUse", "Edit|Write|MultiEdit", f"python3 {hooks / 'memory_guard.py'}", 5),
        ("publish", "PreToolUse", "Artifact|Bash", f"python3 {hooks / 'publish_guard.py'}", 5),
        ("context", "SessionStart", None, f"python3 {hooks.parent / 'context_merge.py'} check --fix --quiet", 20),
        ("evals", "PreToolUse", "Edit|Write|MultiEdit|Bash", f"python3 {hooks / 'eval_guard.py'}", 5),
        ("guards", "PreToolUse", "Bash|Edit|Write|MultiEdit", f"python3 {hooks / 'config_protection.py'}", 5),
        # Same matcher and timeout as tool_policy.settings_json(), which the box's
        # unattended runs already pass as --settings.
        ("policy", "PreToolUse", "*", f"python3 {hooks / 'tool_policy_guard.py'}", 8),
    ]


WIRING = wiring_for(HOOKS)
GUARDS = sorted({w[0] for w in WIRING})


def _script_form(cmd: str) -> str:
    """The command with its .py path reduced to the file name, so the same guard
    wired from another checkout (by hand, or from the runner) is recognised."""
    return " ".join(Path(t).name if t.endswith(".py") else t for t in cmd.split())


def already(groups, cmd):
    # endswith, not "in": the client guard's command contains the space guard's.
    want = _script_form(cmd)
    return any(_script_form(h.get("command", "").strip()).endswith(want)
               for g in groups for h in g.get("hooks", []))


def _covers(group_matcher, matcher) -> bool:
    if matcher is None:
        return True
    have = str(group_matcher or "")
    if have in ("", "*"):
        return True
    return matcher != "*" and set(matcher.split("|")) <= set(have.split("|"))


def wired(hooks, event, matcher, cmd) -> bool:
    """Wired before the tools it covers, to a script that exists."""
    want = _script_form(cmd)
    for g in hooks.get(event, []):
        if not _covers(g.get("matcher"), matcher):
            continue
        for h in g.get("hooks", []):
            have = h.get("command", "").strip()
            if _script_form(have).endswith(want) and all(
                    Path(t).is_file() for t in have.split() if t.endswith(".py")):
                return True
    return False


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
    ap.add_argument("--hooks-dir", type=Path, default=HOOKS,
                    help="where the guard scripts live (default: ~/Data/.datacore/lib/hooks)")
    ap.add_argument("--create", action="store_true",
                    help="start from an empty settings file when there is none (a new machine)")
    ap.add_argument("--verify", action="store_true",
                    help="change nothing; exit 1 unless every chosen guard is wired")
    args = ap.parse_args()
    wanted = {g.strip() for g in args.only.split(",") if g.strip()}
    unknown = wanted - set(GUARDS)
    if unknown:
        sys.exit(f"unknown guard(s): {', '.join(sorted(unknown))}; choose from {', '.join(GUARDS)}")
    wiring = [w[1:] for w in wiring_for(args.hooks_dir.expanduser().resolve()) if w[0] in wanted]

    if args.verify:
        try:
            hooks = json.loads(SETTINGS.read_text()).get("hooks", {}) if SETTINGS.exists() else {}
        except ValueError:
            print(f"FAIL {SETTINGS} is not valid JSON")
            return 1
        missing = [f"{event}[{matcher}] {Path(cmd.split()[1]).name}" for event, matcher, cmd, _t in wiring
                   if not wired(hooks, event, matcher, cmd)]
        for m in missing:
            print(f"FAIL not wired in {SETTINGS}: {m}")
        return 1 if missing else 0

    for _event, _matcher, cmd, _timeout in wiring:
        script = Path(cmd.split()[1])
        if not script.exists():
            sys.exit(f"missing hook: {script}")

    if not SETTINGS.exists():
        if not args.create:
            sys.exit(f"not found: {SETTINGS}")
        data = {}
    else:
        data = json.loads(SETTINGS.read_text())
    hooks = data.setdefault("hooks", {})

    print(f"settings: {SETTINGS}")
    changed = False
    for event, matcher, cmd, timeout in wiring:
        line = add(hooks, event, matcher, cmd, timeout)
        changed = changed or line.lstrip().startswith("+")
        print(line)

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0
    if not changed and SETTINGS.exists():
        print("\nnothing to add: settings left as they are.")
        return 0

    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    if SETTINGS.exists():
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        backup = SETTINGS.with_suffix(f".json.bak-{stamp}")
        shutil.copy2(SETTINGS, backup)
        print(f"\nbackup:  {backup}")
    SETTINGS.write_text(json.dumps(data, indent=2) + "\n")
    json.loads(SETTINGS.read_text())  # parse-check what we just wrote
    print("written. Restart Claude Code for the hooks to load.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
