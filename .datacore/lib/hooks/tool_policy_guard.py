#!/usr/bin/env python3
"""PreToolUse hook: the in-flight tool-call policy for executors (datacore#30).

Thin wrapper so the hook can be named by path in `claude -p --settings`;
every decision lives in ../tool_policy.py. Reads the hook payload on stdin,
prints a deny when the call would cause an effect the principal may never
cause or one that needs a grant this task does not carry, and records the
refusal on the ledger. Exit 0 either way: the JSON is the decision.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from tool_policy import hook_main
except Exception as e:  # noqa: BLE001 — a missing library must not block every call
    print(f"[tool-policy] guard unavailable ({type(e).__name__}: {e}); call allowed", file=sys.stderr)
    sys.exit(0)

if __name__ == "__main__":
    sys.exit(hook_main())
