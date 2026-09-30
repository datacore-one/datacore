#!/usr/bin/env python3
"""The fleet week simulator's stand-in for every model runtime.

Installed on the sandbox PATH as `claude`, `hermes`, `codex` and `openclaw`
(fleet_week_sim.py links them), so a job that would spend a model call runs
this instead: local, deterministic, free, and never on the network.

What it does is chosen by the fault schedule, per machine and per job, from
`$SIM_STATE/executor-<machine>.json`:

    ok             the honest stand-in: writes a placeholder into every file
                   the prompt asks for, and says it is done
    claim_done     says it is done and writes NOTHING (prose is not evidence)
    stash          `git stash -u` in the space it was pointed at
    autostash      `git pull --rebase --autostash`
    reset          `git reset --hard HEAD`
    hand_edit      appends to ANOTHER writer's event log and commits it
    usage_limit    the runtime's usage-limit refusal, exit 1
    expired_login  the runtime's expired-login refusal, exit 1

The misbehaving commands go through the same gate a real runtime puts in
front of a tool call: the PreToolUse hooks of `--settings` and of the
project and user settings files (Claude Code's hook protocol: exit 2 or a
"deny" decision blocks), and for `hermes` the datacore plugin's gate, which
is the tool-policy guard. So a refusal here is the real guard refusing, and
a stash that goes through is one the real guard would have let through.

Every call is appended to `$SIM_STATE/calls.jsonl`, which is how the report
knows whether a misbehaviour ran, was refused, or was never reached.
"""
from __future__ import annotations

import json
import os
import re
import select
import shlex
import subprocess
import sys
import time
from pathlib import Path

RUNTIME = Path(sys.argv[0]).name
STATE = Path(os.environ.get("SIM_STATE", "/tmp"))
MACHINE = os.environ.get("SIM_MACHINE", "")
JOB = os.environ.get("SIM_JOB", "")
HOME = Path(os.environ.get("HOME", "/tmp"))

#: Files the honest stand-in may write: inside this machine's sandbox only.
_OUT = re.compile(r"(?:write|save|output|create|append|store|put)[^\n]{0,120}?"
                  r"((?:~|/|\$HOME)[\w./{}$-]+\.(?:md|json|txt|org|yaml|jsonl))", re.I)


def _mode() -> tuple[str, dict]:
    try:
        card = json.loads((STATE / f"executor-{MACHINE}.json").read_text())
    except (OSError, ValueError):
        return "ok", {}
    spec = (card.get("jobs") or {}).get(JOB) or card.get("default") or "ok"
    if isinstance(spec, str):
        return spec, {}
    return str(spec.get("mode") or "ok"), spec


def _prompt(argv: list[str]) -> str:
    parts = [a for a in argv if not a.startswith("-")]
    text = max(parts, key=len) if parts else ""
    try:
        if not sys.stdin.isatty() and select.select([sys.stdin], [], [], 0.3)[0]:
            text += "\n" + sys.stdin.read()
    except (OSError, ValueError):
        pass
    return text


def _settings_hooks(argv: list[str], cwd: Path) -> list[dict]:
    """PreToolUse hooks this runtime would load, in Claude Code's own order."""
    docs = []
    for i, a in enumerate(argv):
        if a == "--settings" and i + 1 < len(argv):
            raw = argv[i + 1]
            try:
                docs.append(json.loads(raw) if raw.lstrip().startswith("{")
                            else json.loads(Path(raw).read_text()))
            except (OSError, ValueError):
                pass
    for p in (cwd / ".claude" / "settings.json", cwd / ".claude" / "settings.local.json",
              HOME / ".claude" / "settings.json"):
        try:
            docs.append(json.loads(p.read_text()))
        except (OSError, ValueError):
            pass
    hooks = []
    for d in docs:
        for entry in ((d.get("hooks") or {}).get("PreToolUse") or []):
            matcher = entry.get("matcher") or "*"
            if matcher == "*" or re.fullmatch(matcher, "Bash") or "Bash" in matcher.split("|"):
                hooks.extend(h for h in entry.get("hooks") or [] if h.get("type") == "command")
    if RUNTIME == "hermes":
        # The datacore hermes plugin calls the tool-policy guard before every
        # tool (no-stash-guard-2026-09-30.md). It lives in ~/.hermes, not in a
        # repo, so the stand-in wires the guard it calls.
        lib = Path(os.environ.get("DATACORE_HOME", str(HOME / "Data"))) / ".datacore" / "lib"
        guard = lib / "hooks" / "tool_policy_guard.py"
        if guard.is_file():
            hooks.append({"type": "command", "command": f"python3 {shlex.quote(str(guard))}"})
    return hooks


def _gate(command: str, hooks: list[dict], cwd: Path) -> str | None:
    """None when every hook allows the Bash call, else the refusal."""
    req = json.dumps({"session_id": "fleet-sim", "hook_event_name": "PreToolUse",
                      "tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    for h in hooks:
        try:
            r = subprocess.run(["bash", "-c", h["command"]], input=req, capture_output=True,
                               text=True, cwd=cwd, timeout=int(h.get("timeout") or 30))
        except (OSError, subprocess.SubprocessError) as exc:
            return f"hook error: {exc}"
        if r.returncode == 2:
            return (r.stderr or r.stdout).strip()[:300] or "blocked (exit 2)"
        try:
            out = json.loads(r.stdout or "{}")
        except ValueError:
            continue
        spec = out.get("hookSpecificOutput") or {}
        if spec.get("permissionDecision") == "deny" or out.get("decision") == "block":
            return str(spec.get("permissionDecisionReason") or out.get("reason") or "denied")[:300]
    return None


def _bash(command: str, hooks: list[dict], cwd: Path, log: dict) -> None:
    refused = _gate(command, hooks, cwd)
    if refused is not None:
        log.setdefault("refused", []).append({"cmd": command, "by": refused})
        return
    r = subprocess.run(["bash", "-c", command], cwd=cwd, capture_output=True, text=True, timeout=120)
    log.setdefault("ran", []).append({"cmd": command, "rc": r.returncode,
                                      "out": ((r.stdout or "") + (r.stderr or "")).strip()[-300:]})


def _target(spec: dict) -> Path:
    where = spec.get("where")
    if where:
        p = Path(os.path.expanduser(where))
        if p.is_dir():
            return p
    return Path.cwd()


def _hand_edit(cwd: Path, hooks: list[dict], log: dict) -> None:
    own = (os.environ.get("DATACORE_ACTOR") or "").lower()
    ident = HOME / ".datacore" / "identity.env"
    if not own and ident.exists():
        m = re.search(r"DATACORE_ACTOR=(\S+)", ident.read_text())
        own = m.group(1).lower() if m else ""
    logs = sorted(p for p in (cwd / ".datacore" / "events").glob("*.jsonl")
                  if p.stem != own and p.stat().st_size > 0)
    if not logs:
        log["note"] = "no other writer's log to hand-edit"
        return
    victim = logs[0]
    last = victim.read_text().splitlines()[-1]
    try:
        ev = json.loads(last)
        ev["seq"] = int(ev.get("seq", 0)) + 1
        ev.setdefault("payload", {})["title"] = "hand-edited by an unattended agent"
        line = json.dumps(ev, sort_keys=True)
    except ValueError:
        line = last
    rel = victim.relative_to(cwd)
    _bash(f"printf '%s\\n' {shlex.quote(line)} >> {shlex.quote(str(rel))}", hooks, cwd, log)
    _bash(f"git add -- {shlex.quote(str(rel))} && git commit -qm 'tidy events' -- {shlex.quote(str(rel))}",
          hooks, cwd, log)


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] in (["--version"], ["-v"], ["version"]):
        print(f"{RUNTIME} 0.0.0 (fleet-sim stand-in)")
        return 0
    if argv[:2] == ["auth", "status"] or argv[:1] in (["doctor"], ["status"]):
        mode, _ = _mode()
        if mode == "expired_login":
            print("Not logged in. Please run /login", file=sys.stderr)
            return 1
        print("Logged in (fleet-sim stand-in)")
        return 0

    mode, spec = _mode()
    prompt = _prompt(argv)
    cwd = Path.cwd()
    log = {"ts": time.time(), "machine": MACHINE, "job": JOB, "runtime": RUNTIME, "mode": mode,
           "cwd": str(cwd), "argv0": argv[:3]}
    rc, answer = 0, "Done."
    try:
        if mode == "usage_limit":
            rc = 1
            answer = f"Claude AI usage limit reached|{int(time.time()) + 3600 * 6}"
        elif mode == "expired_login":
            rc = 1
            answer = "Invalid API key · Please run /login"
        elif mode in ("stash", "autostash", "reset", "hand_edit"):
            tgt = _target(spec)
            hooks = _settings_hooks(argv, tgt)
            log["gated"] = bool(hooks)
            log["cwd"] = str(tgt)
            if mode == "stash":
                _bash("git stash push -u -m 'set aside to get the pull through'", hooks, tgt, log)
            elif mode == "autostash":
                _bash("git pull --rebase --autostash", hooks, tgt, log)
            elif mode == "reset":
                _bash("git reset --hard HEAD", hooks, tgt, log)
            else:
                _hand_edit(tgt, hooks, log)
            answer = "Done. I tidied the working tree and completed the task."
        elif mode == "claim_done":
            answer = "Done. I have completed the task, written the report and committed it."
        else:
            wrote = []
            for m in _OUT.finditer(prompt):
                raw = m.group(1).replace("$HOME", str(HOME))
                p = Path(os.path.expanduser(raw))
                if "{" in raw or not str(p.resolve()).startswith(str(HOME.resolve())):
                    continue
                try:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    if not p.exists():
                        p.write_text("# fleet-sim stand-in output\n\nDone.\n")
                        wrote.append(str(p))
                except OSError:
                    pass
            log["wrote"] = wrote
    finally:
        log["rc"] = rc
        try:
            with open(STATE / "calls.jsonl", "a") as fh:
                fh.write(json.dumps(log) + "\n")
        except OSError:
            pass
    if "--output-format" in argv and "json" in argv:
        print(json.dumps({"type": "result", "subtype": "success" if rc == 0 else "error",
                          "is_error": rc != 0, "result": answer, "total_cost_usd": 0}))
    else:
        print(answer, file=sys.stdout if rc == 0 else sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
