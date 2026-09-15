#!/usr/bin/env python3
"""PreToolUse hook: deny or warn based on the space type a tool targets.

Policy is in .datacore/config/space-policy.yaml (tracked, no hostnames).
Space identity comes from spaces.find_space() keyed on the *target path*,
not the session cwd — so a git -C or absolute-path Edit into a client space
is caught even when the session started outside it.

Fail behaviour
--------------
* ``deny`` rules: JSON deny (block), even when the space cannot be resolved
  (path in no space → allow; policy file missing or unreadable → allow,
   because the policy cannot be evaluated → no enumeration gap).
* ``ask``  rules: exit 0 with additionalContext warning (non-blocking).
* Unknown space type / missing category key: apply ``default``; if absent → allow.

Registration in ~/.claude/settings.json:

    {
      "PreToolUse": [
        {
          "matcher": "Bash|Edit|Write|Read",
          "hooks": [
            {
              "type": "command",
              "command": "python3 ~/Data/.datacore/lib/hooks/space_policy_guard.py",
              "timeout": 5
            }
          ]
        }
      ]
    }
"""
from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_lib))

POLICY_PATH = _lib.parent / "config" / "space-policy.yaml"

try:
    from spaces import find_space
except Exception:  # noqa: BLE001 — spaces unavailable → guard is a no-op
    find_space = None  # type: ignore[assignment]

GIT_NETWORK_SUBCOMMANDS = frozenset({
    "push", "pull", "fetch", "clone", "ls-remote", "submodule",
    "request-pull", "send-email", "svn",
})


# ---------------------------------------------------------------------------
# Policy loading
# ---------------------------------------------------------------------------

def _load_policy() -> dict:
    try:
        import yaml
        raw = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8")) or {}
        return raw.get("policies") or {}
    except Exception:  # noqa: BLE001
        return {}


def _rule(policies: dict, space_type: str, category: str) -> str:
    """Resolve the effective rule for (space_type, category). Default: allow."""
    type_policy = policies.get(space_type) or {}
    return str(type_policy.get(category) or type_policy.get("default") or "allow")


# ---------------------------------------------------------------------------
# Target path extraction
# ---------------------------------------------------------------------------

def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _git_effective_dir(tokens: list[str], cwd: str | None) -> Path:
    """Directory a git command operates in: -C <path> if given, else cwd."""
    for i, tok in enumerate(tokens):
        if tok == "-C" and i + 1 < len(tokens):
            return Path(tokens[i + 1]).expanduser()
    return Path(cwd) if cwd else Path.cwd()


def _is_git_network(tokens: list[str]) -> bool:
    if not tokens or Path(tokens[0]).name != "git":
        return False
    skip_next = False
    for tok in tokens[1:]:
        if skip_next:
            skip_next = False
            continue
        if tok == "-C":
            skip_next = True
            continue
        if not tok.startswith("-"):
            return tok in GIT_NETWORK_SUBCOMMANDS
    return False


def _category_and_path(payload: dict) -> tuple[str, Path | None]:
    """Return (category, target_path) for the tool call."""
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    inp = payload.get("tool_input") or payload.get("toolInput") or {}
    cwd = payload.get("cwd")

    if tool in ("Edit", "Write"):
        fp = inp.get("file_path") or inp.get("filePath") or ""
        return "write", Path(fp).expanduser() if fp else None

    if tool == "Read":
        fp = inp.get("file_path") or inp.get("filePath") or ""
        return "read", Path(fp).expanduser() if fp else None

    if tool == "Bash":
        command = str(inp.get("command") or "")
        tokens = _tokens(command)
        if _is_git_network(tokens):
            return "network", _git_effective_dir(tokens, cwd)
        # Non-git Bash — use cwd as a conservative target
        return "write", Path(cwd) if cwd else None

    return "allow", None  # unknown tool — let through


# ---------------------------------------------------------------------------
# Hook output helpers
# ---------------------------------------------------------------------------

def _deny(reason: str) -> int:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    return 0


def _warn(message: str) -> int:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": message,
        }
    }))
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    category, target = _category_and_path(payload)
    if category == "allow" or target is None:
        return 0

    if find_space is None:
        return 0
    try:
        space = find_space(target.resolve())
    except Exception:  # noqa: BLE001 — discovery failure → allow
        return 0

    if space is None:
        return 0  # path not in any space

    policies = _load_policy()
    if not policies:
        return 0  # policy file missing or unreadable → allow

    rule = _rule(policies, space.type, category)

    if rule == "deny":
        return _deny(
            f"Space-policy: {category} is denied in {space.type!r} spaces "
            f"(space: {space.name!r}, path: {target}). "
            "This action must be performed by a human."
        )

    if rule == "ask":
        return _warn(
            f"Space-policy warning: you are about to {category} inside "
            f"{space.type!r} space {space.name!r}. "
            "Confirm this is intentional before proceeding."
        )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — guard failure must not block tool use
        sys.exit(0)
