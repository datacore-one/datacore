"""Run the hooks REALLY wired in ~/.claude/settings.json against a probe event.

Promises such as "nothing is published without asking" or "memory never holds
machine names" are kept, in an interactive session, only by hooks that are
actually registered -- a guard that exists on disk but is not wired keeps
nothing. ``probe(event, tool, payload)`` finds every hook registered for the
event whose matcher selects the tool, runs each with the probe on stdin, and
reports the combined decision.

Isolation: hooks run with HOME, TMPDIR and DATACORE_STATE pointed at a
throwaway directory, stdin is the probe only, and every hook has a timeout, so
a probe cannot write the owner's session state. Read-only on settings.json.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

SETTINGS = Path(os.environ.get("CLAUDE_SETTINGS", Path.home() / ".claude" / "settings.json"))


@dataclass
class Outcome:
    decision: str = "allow"                 # allow | ask | deny (the strictest seen)
    context: str = ""                       # additionalContext / stdout gathered
    ran: list = field(default_factory=list)
    reasons: list = field(default_factory=list)


def _matches(matcher, tool: str) -> bool:
    if matcher in (None, "", "*"):
        return True
    try:
        return re.fullmatch(matcher, tool) is not None
    except re.error:
        return matcher == tool


def hooks_for(event: str, tool: str = "", settings: Path = SETTINGS) -> list[str]:
    data = json.loads(settings.read_text()) if settings.is_file() else {}
    out = []
    for group in (data.get("hooks") or {}).get(event, []) or []:
        if event in ("PreToolUse", "PostToolUse") and not _matches(group.get("matcher"), tool):
            continue
        for h in group.get("hooks") or []:
            if h.get("type") == "command" and h.get("command"):
                out.append(h["command"])
    return out


RANK = {"allow": 0, "ask": 1, "deny": 2}


def probe(event: str, payload: dict, tool: str = "", settings: Path = SETTINGS,
          timeout: int = 20, state_dir=None) -> Outcome:
    """``state_dir``: reuse one throwaway HOME/state across several probes, so a hook that
    remembers earlier calls in the same session sees them (a sequence, not one call)."""
    res = Outcome()
    with tempfile.TemporaryDirectory(prefix="hook-probe-") as tmp:
        tmp = os.path.realpath(state_dir or tmp)   # DATACORE_STATE must be an unaliased path
        env = {**os.environ, "HOME": tmp, "TMPDIR": tmp, "DATACORE_STATE": tmp,
               "CLAUDE_HOOK_EVENT_NAME": event}
        body = {"hook_event_name": event, "session_id": "promise-eval-probe",
                "cwd": str(Path.home() / "Data"), **payload}
        if tool:
            body.setdefault("tool_name", tool)
        for cmd in hooks_for(event, tool, settings):
            try:
                p = subprocess.run(cmd, shell=True, input=json.dumps(body), capture_output=True,
                                   text=True, timeout=timeout, env=env, cwd=tmp)
            except subprocess.TimeoutExpired:
                res.ran.append((cmd, "timeout"))
                continue
            res.ran.append((cmd, p.returncode))
            dec = "deny" if p.returncode == 2 else "allow"
            try:
                out = json.loads(p.stdout) if p.stdout.strip() else {}
            except ValueError:
                out = {}
                res.context += p.stdout
            hso = out.get("hookSpecificOutput") or {}
            d = hso.get("permissionDecision") or out.get("decision")
            if d in ("deny", "block"):
                dec = "deny"
            elif d == "ask":
                dec = max(dec, "ask", key=RANK.get)
            res.context += str(hso.get("additionalContext") or "")
            if dec != "allow":
                res.reasons.append(f"{cmd}: {hso.get('permissionDecisionReason') or p.stderr.strip()[:200]}")
            res.decision = max(res.decision, dec, key=RANK.get)
    return res
