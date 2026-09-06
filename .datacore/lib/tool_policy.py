#!/usr/bin/env python3
"""In-flight tool-call policy for executors (datacore#30, roadmap D-005).

The policy layer bound an item at CLAIM time — approvals_policy.yaml names,
per principal, the effects it may never cause and the effects that need a
human's co-signed grant; claim_gate.py enforces both before work starts. But
a running task was bounded only by the outer controls: nightshift passed no
hooks or settings to `claude -p`, and Miles ran with bypassPermissions and no
hooks. Once a task was executing, "never payment" was a sentence in a YAML
file, not a wall.

This module is the wall. One classifier — config/tool_effects.yaml maps tool
calls onto the same effect vocabulary — and one decision, keyed by principal:

    never-effect hit      -> refused  (no grant can allow it)
    cosign effect, no     -> paused   (the model is told to leave a proposal;
      grant on this task               a human grants, the next run acts)
    anything else         -> allowed

Every refusal is recorded on the ledger as `metric.attest` with
metric=policy.refusal — the same "this machine observed this about itself"
vocabulary cos_ledger_event.sh uses — so a task that tried to pay someone
shows up in the morning instead of in a log nobody reads.

Two callers, one decision:
  * hooks/tool_policy_guard.py — the PreToolUse command hook nightshift passes
    to `claude -p --settings` (see `settings_json`)
  * the Miles bot — an SDK PreToolUse hook that calls `evaluate_hook` in-process

Context reaches the hook through the environment the executor sets:
  DATACORE_POLICY_PRINCIPAL  whose limits apply (default: this host's actor's
                             principal from registry/principals.yaml)
  DATACORE_POLICY_SPACE      the task's space; the refusal is recorded there
                             (default: <root>/2-datacore)
  DATACORE_POLICY_TASK       the task id, carried on the record
  DATACORE_POLICY_GRANTED    comma-separated effects a human already granted
                             for this task (the task's :GRANTED_EFFECTS:)

Fails OPEN when the policy or effects file cannot be read — a broken hook
that blocks every call is an outage of its own, and a run with no policy is
what every run was before this file existed — and says so on stderr. A
classification hit is decided even when the ledger cannot be written: the
record is evidence, not the gate.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

LIB = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

DEFAULT_EFFECTS_FILE = LIB.parent / "config" / "tool_effects.yaml"
DEFAULT_POLICY_FILE = LIB.parent / "config" / "approvals_policy.yaml"
GUARD = LIB / "hooks" / "tool_policy_guard.py"
REFUSAL_METRIC = "policy.refusal"

#: Which keys of a tool's input carry the text worth matching. Anything
#: else (an MCP tool's structured input) is matched as its JSON.
_TEXT_KEYS = ("command", "url", "file_path", "path", "query", "prompt", "content", "new_string")


@dataclass
class Decision:
    allow: bool
    effects: set[str] = field(default_factory=set)
    reason: str = ""
    kind: str = "allow"          # allow | never | cosign | granted

    @property
    def blocked(self) -> bool:
        return not self.allow


# ── effects ─────────────────────────────────────────────────────────────────
def load_effects(path: Path | None = None) -> dict[str, dict]:
    """{effect: {tools, tool_patterns, patterns}} from tool_effects.yaml.
    An unreadable file is an empty vocabulary: every call allowed, which is
    the pre-policy behaviour, reported on stderr by the caller."""
    import yaml
    p = Path(path or DEFAULT_EFFECTS_FILE)
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out: dict[str, dict] = {}
    for name, spec in (data.get("effects") or {}).items():
        spec = spec or {}
        out[str(name)] = {
            "tools": [str(t) for t in (spec.get("tools") or [])],
            "tool_patterns": [re.compile(str(r), re.I) for r in (spec.get("tool_patterns") or [])],
            "patterns": [re.compile(str(r), re.I) for r in (spec.get("patterns") or [])],
        }
    return out


def call_text(tool_input) -> str:
    """The matchable text of a call: its command/url/path fields, else its JSON."""
    if isinstance(tool_input, str):
        return tool_input
    if not isinstance(tool_input, dict):
        return ""
    parts = [str(tool_input[k]) for k in _TEXT_KEYS if isinstance(tool_input.get(k), str)]
    if not parts:
        try:
            return json.dumps(tool_input, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return str(tool_input)
    return "\n".join(parts)


def classify(tool_name: str, tool_input, effects: dict[str, dict] | None = None) -> set[str]:
    """The effects a call would cause, by the vocabulary in tool_effects.yaml."""
    effects = effects if effects is not None else load_effects()
    text = call_text(tool_input)
    hit: set[str] = set()
    for name, spec in effects.items():
        tools = spec.get("tools") or []
        if tools and not any(fnmatch.fnmatch(tool_name, t) for t in tools):
            continue
        if any(r.search(tool_name) for r in spec.get("tool_patterns") or []):
            hit.add(name)
            continue
        if text and any(r.search(text) for r in spec.get("patterns") or []):
            hit.add(name)
    return hit


# ── principals ──────────────────────────────────────────────────────────────
def limits_for(principal: str, policy_path: Path | None = None) -> tuple[set[str], set[str]]:
    """(never_effects, cosign_effects) for a principal from approvals_policy.yaml.
    An unlisted principal gets the global cosign set and no never-effects."""
    from ledger.policy import load_policy
    policy = load_policy(Path(policy_path or DEFAULT_POLICY_FILE))
    entry = (policy.principals or {}).get(principal) or {}
    never = {str(e) for e in (entry.get("never_effects") or [])}
    cosign = set(policy.cosign_effects) | {str(e) for e in (entry.get("cosign_effects") or [])}
    return never, cosign


def principal_for(actor: str | None = None) -> str:
    """The principal whose limits bind this executor: the writer's own entry,
    or the principal that lists it under writes_as (nightshift -> miles)."""
    from actor_identity import principal_of, this_actor
    actor = (actor or this_actor()).strip().lower()
    name, _ = principal_of(actor)
    return name or actor


def decide(principal: str, tool_name: str, tool_input, granted=(),
           effects: dict[str, dict] | None = None,
           policy_path: Path | None = None) -> Decision:
    hit = classify(tool_name, tool_input, effects)
    if not hit:
        return Decision(True, hit, "no policy effect", "allow")
    never, cosign = limits_for(principal, policy_path)
    forbidden = hit & never
    if forbidden:
        what = ", ".join(sorted(forbidden))
        return Decision(False, hit, f"{principal} may never cause {what} "
                                    f"(approvals_policy.yaml never_effects); the call is refused "
                                    f"and recorded — do not retry it another way", "never")
    needs = (hit & cosign) - {str(g).strip() for g in granted if str(g).strip()}
    if needs:
        what = ", ".join(sorted(needs))
        return Decision(False, hit, f"{what} needs a co-signed grant before it runs and this task "
                                    f"carries none; the call is paused and recorded — leave the "
                                    f"step as a proposal for a human to grant", "cosign")
    return Decision(True, hit, f"{', '.join(sorted(hit))} granted on this task", "granted")


# ── the record ──────────────────────────────────────────────────────────────
def record_refusal(decision: Decision, *, principal: str, tool_name: str,
                   space_dir: Path | None = None, task_id: str = "",
                   actor: str | None = None, detail: str = "") -> bool:
    """Append the refusal to the ledger of the task's space. Best-effort:
    returns False and says why on stderr when it cannot; never raises."""
    try:
        from actor_identity import this_actor
        from ledger.log import EventLog
        space = Path(space_dir) if space_dir else ROOT / "2-datacore"
        if not (space / ".datacore" / "events").is_dir():
            print(f"[tool-policy] no ledger at {space}; refusal not recorded", file=sys.stderr)
            return False
        log = EventLog(space, (actor or this_actor()).strip().lower())
        log.append("metric.attest", {
            "metric": REFUSAL_METRIC,
            "principal": principal,
            "task": task_id or "",
            "tool": tool_name,
            "effects": sorted(decision.effects),
            "kind": decision.kind,
            "reason": decision.reason[:300],
            "detail": detail[:200],
        })
        return True
    except Exception as e:  # noqa: BLE001 — the record is evidence, not the gate
        print(f"[tool-policy] refusal not recorded ({type(e).__name__}: {e})", file=sys.stderr)
        return False


# ── the hook ────────────────────────────────────────────────────────────────
def context_from_env(env=None) -> dict:
    env = os.environ if env is None else env
    principal = (env.get("DATACORE_POLICY_PRINCIPAL") or "").strip().lower()
    if not principal:
        try:
            principal = principal_for()
        except Exception:  # noqa: BLE001
            principal = "unknown"
    granted = [g.strip() for g in (env.get("DATACORE_POLICY_GRANTED") or "").split(",") if g.strip()]
    space = env.get("DATACORE_POLICY_SPACE") or ""
    return {"principal": principal, "granted": granted,
            "space": Path(space) if space else None,
            "task": (env.get("DATACORE_POLICY_TASK") or "").strip()}


def deny_output(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def evaluate_hook(payload: dict, env=None, *, record: bool = True,
                  effects: dict[str, dict] | None = None,
                  policy_path: Path | None = None) -> dict | None:
    """The PreToolUse decision for one call. Returns the hook's JSON output
    when the call is refused or paused, None when it may proceed."""
    tool_name = str((payload or {}).get("tool_name") or "")
    tool_input = (payload or {}).get("tool_input") or {}
    ctx = context_from_env(env)
    try:
        effects = effects if effects is not None else load_effects()
        decision = decide(ctx["principal"], tool_name, tool_input, ctx["granted"], effects, policy_path)
    except Exception as e:  # noqa: BLE001 — fail open, loudly
        print(f"[tool-policy] policy unavailable ({type(e).__name__}: {e}); call allowed", file=sys.stderr)
        return None
    if decision.allow:
        return None
    if record:
        record_refusal(decision, principal=ctx["principal"], tool_name=tool_name,
                       space_dir=ctx["space"], task_id=ctx["task"],
                       detail=call_text(tool_input)[:200])
    return deny_output(decision.reason)


def hook_main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError, ValueError):
        return 0
    out = evaluate_hook(payload)
    if out is not None:
        print(json.dumps(out))
    return 0


def settings_json(guard: Path | None = None, timeout: int = 8) -> str:
    """The `--settings` JSON that wires the guard as a PreToolUse hook on
    every tool, for `claude -p` runs that load no other settings."""
    cmd = f"python3 {guard or GUARD}"
    return json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "*", "hooks": [{"type": "command", "command": cmd, "timeout": timeout}]}]}})


if __name__ == "__main__":
    sys.exit(hook_main())
