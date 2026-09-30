#!/usr/bin/env python3
"""The Datacore tool policy for an OpenClaw tool call (Data on plur-claw).

index.js (the OpenClaw plugin) pipes each `before_tool_call` event here as
JSON — `{"toolName", "params", "derivedPaths"?, "toolKind"?}` — and reads one
line back: `{"block": false}` or `{"block": true, "blockReason": "..."}`.

The decision is tool_policy.evaluate_hook, the same one the Claude hook and the
Hermes plugin use. This file only translates OpenClaw's tool names into the
ones tool_effects.yaml already classifies, so the vocabulary stays in one
place:

    exec / exec_command / bash / shell   -> Bash     (params.command, or cmd)
    code-mode exec (no command, code)    -> execute_code
    apply_patch                          -> patch, then Edit per touched path
    write / edit / read                  -> Write / Edit / Read (file_path)
    web_fetch                            -> WebFetch
    cron / spawn_agent / browser         -> cronjob / delegate_task / browser_openclaw
    message (action A; none = send)      -> openclaw_message.A
    gateway (action A)                   -> openclaw_gateway.A
    <server>__<tool>                     -> mcp__<server>__<tool>
    anything else                        -> unchanged

Anything that goes wrong refuses the call: an unreadable request, a shell
call whose command cannot be read, a policy library that will not load.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

SHELL = {"exec", "exec_command", "bash", "shell", "shell_command"}
RENAME = {"write": "Write", "edit": "Edit", "read": "Read", "web_fetch": "WebFetch",
          "cron": "cronjob", "spawn_agent": "delegate_task", "browser": "browser_openclaw"}
#: OpenClaw tools whose verb is their `action` param -> the action assumed when none is given.
ACTION_QUALIFIED = {"message": "send", "gateway": ""}
_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$|^\*\*\* Move to: (.+?)\s*$", re.M)


def block(reason: str) -> dict:
    return {"block": True, "blockReason": reason}


def _path(p: str, cwd: str | None = None) -> str:
    """Absolute when it can be; else `./rel`, so the policy's `/.datacore/...`
    and `/.env` patterns still see a slash before the name."""
    p = str(p)
    if p.startswith(("/", "~")):
        return p
    if cwd and str(cwd).startswith("/"):
        return str(cwd).rstrip("/") + "/" + p
    return p if p.startswith(("./", "../")) else "./" + p


def _command(params: dict) -> str | None:
    for key in ("command", "cmd"):
        v = params.get(key)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
            import shlex
            return " ".join(shlex.quote(x) for x in v)
    return None


def translate(event: dict) -> list[tuple[str, dict]]:
    """The (tool_name, tool_input) calls the policy must allow for this event.
    Raises ValueError when the call cannot be read."""
    name = event.get("toolName")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("tool call without a tool name")
    params = event.get("params")
    params = dict(params) if isinstance(params, dict) else {}
    cwd = params.get("workdir") or params.get("cwd")
    low = name.strip().lower()

    if low in SHELL:
        cmd = _command(params)
        if cmd is None:
            if event.get("toolKind") == "code_mode_exec" or isinstance(params.get("code"), str):
                return [("execute_code", params)]
            raise ValueError(f"cannot read the command of this {name} call")
        return [("Bash", {**params, "command": cmd})]
    if low == "apply_patch":
        calls = [("patch", params)]
        text = "\n".join(str(v) for v in params.values() if isinstance(v, str))
        paths = [a or b for a, b in _PATCH_FILE.findall(text)]
        paths += [p for p in (event.get("derivedPaths") or []) if isinstance(p, str)]
        for key in ("path", "file_path"):
            if isinstance(params.get(key), str):
                paths.append(params[key])
        calls += [("Edit", {"file_path": _path(p, cwd)}) for p in dict.fromkeys(paths)]
        return calls
    if low in ("write", "edit", "read"):
        p = params.get("file_path") or params.get("path")
        if isinstance(p, str):
            params = {**params, "file_path": _path(p, cwd)}
            params.pop("path", None)
        return [(RENAME[low], params)]
    if low in RENAME:
        return [(RENAME[low], params)]
    if low in ACTION_QUALIFIED:
        # The verb of these OpenClaw tools is their `action`; like an MCP
        # tool's name, the qualified name carries it, so tool_effects.yaml can
        # tell `message send` from `message read` (no action = a send).
        action = params.get("action")
        action = action.strip() if isinstance(action, str) and action.strip() else ACTION_QUALIFIED[low]
        return [(f"openclaw_{low}.{action}", params)]
    if "__" in name and not name.startswith("mcp__"):
        return [("mcp__" + name, params)]
    return [(name, params)]


def decide_event(event, env=None, *, policy_path=None, record: bool = True) -> dict:
    try:
        if not isinstance(event, dict):
            return block("invalid tool-policy request")
        try:
            calls = translate(event)
        except ValueError as e:
            return block(f"Datacore tool policy: {e}; call refused")
        from tool_policy import evaluate_hook
        for tool_name, tool_input in calls:
            out = evaluate_hook({"tool_name": tool_name, "tool_input": tool_input}, env,
                                record=record, policy_path=policy_path)
            if out is None:
                continue
            spec = out.get("hookSpecificOutput") or {}
            return block(str(spec.get("permissionDecisionReason") or "refused by the Datacore tool policy"))
        return {"block": False}
    except Exception as e:  # noqa: BLE001 -- an unavailable policy cannot authorize work
        print(f"[openclaw-gate] policy unavailable ({type(e).__name__}); call refused", file=sys.stderr)
        return block("Datacore execution policy unavailable; call refused")


def main() -> int:
    try:
        event = json.loads(sys.stdin.read())
    except (ValueError, OSError):
        print(json.dumps(block("invalid tool-policy JSON request")))
        return 0
    if not isinstance(event, dict) or not isinstance(event.get("toolName"), str):
        print(json.dumps(block("invalid tool-policy request")))
        return 0
    print(json.dumps(decide_event(event)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
