#!/usr/bin/env python3
"""PreToolUse hook: an interactive session asks before it changes a promise eval (MEM-30).

"Every Datacore upgrade starts with failing tests written first ... and its builder
can't edit them." Unattended agents are refused by the tool policy (effect
`eval.edit` in config/tool_effects.yaml, a never-effect of every agent principal).
An interactive session has no principal, so this hook applies the same pattern
set and asks the owner instead: an edit, overwrite, move or delete of an existing
eval (test_promise_*.py, agent_eval.py, _audit_contract.py, _inbox_job_harness.py)
needs his yes. Writing a NEW eval file stays open -- that is how evals get written.

One vocabulary: the patterns live in tool_effects.yaml, read through
tool_policy.classify; nothing is duplicated here.

Wire with: python3 .datacore/lib/hooks/install_redaction_guards.py --only evals
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FILE_TOOLS = {"Write", "write_file"}


def changes_an_eval(tool: str, inp: dict) -> bool:
    from tool_policy import classify
    if "eval.edit" not in classify(tool, inp):
        return False
    if tool in FILE_TOOLS:
        path = str(inp.get("file_path") or inp.get("path") or "")
        return bool(path) and Path(path).expanduser().exists()   # a new eval may be written
    return True


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    tool = str(payload.get("tool_name") or "")
    inp = payload.get("tool_input") or {}
    if not isinstance(inp, dict) or not changes_an_eval(tool, inp):
        return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": (
            "Eval guard: this changes an existing promise eval. The builder never edits its own "
            "judge -- if the eval looks wrong, report it; change it only with the owner's yes."),
    }}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — guard failure must not block tool use
        sys.exit(0)
