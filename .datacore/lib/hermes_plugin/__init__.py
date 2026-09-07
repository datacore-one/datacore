"""Datacore principal plugin for Hermes (DIP-0044, datacore#30).

WHY THIS EXISTS. A Hermes agent knew who it was only if a skill file said so,
and nothing checked. On 2026-09-06 and again on 2026-09-07 the geo cadence on
hermes followed a skill that said `EventLog(actor='winston', ...)`, wrote a
task into 5-plur's winston log, and the fleet verifier reported
"5-plur/winston by tris" the next morning — twice, after the fact. Meanwhile
the tool-call policy that guards nightshift and Miles (datacore#30) had no
Hermes equivalent at all: Winston, the principal whose registry entry says
`permission_mode: propose`, was the least governed agent in the fleet.

WHAT IT DOES. Three things, all from the registry the fleet already keeps:

  identity   The host's actor and principal (registry/principals.yaml) are
             written into Hermes's own always-injected memory file at session
             start, inside a marked block that is rewritten, never appended.
             `datacore_whoami` returns the same record as a tool.

  authorship A pre_tool_call gate refuses a call that would write the ledger
             under a DIFFERENT declared principal's name. Only a
             declared-against-declared mismatch is refused: an undeclared
             writer, or a host whose own identity cannot be resolved, is left
             alone — the guard exists for the one case the registry can decide.

  policy     Every acting tool call goes through tool_policy.evaluate_hook —
             the same classifier, the same approvals_policy.yaml limits, the
             same `metric.attest policy.refusal` in the ledger as the Claude
             SDK hook. A refusal reaches the model as the block message.

FAILS OPEN, ALWAYS. Every entry point is wrapped: a missing DATACORE_ROOT, an
unreadable registry, an import error in the fleet lib — the agent keeps
working and the plugin is simply not in force. A guard that can take the
gateway down would be worse than the exposure it closes. `datacore_whoami`
says whether it is actually in force, so "inert" is visible rather than
assumed.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

MARK_START = "<!-- datacore:principal (generated — edits are overwritten) -->"
MARK_END = "<!-- /datacore:principal -->"

# `EventLog(space_dir=..., actor='winston')`, `EventLog(dir, "winston")`,
# `--actor winston`, `DATACORE_ACTOR=winston` — the shapes a shell or python
# call actually takes. Anchored on the ledger API so ordinary prose about a
# principal ("ask winston to review") is never mistaken for a write.
# `actor=` ANYWHERE in a ledger-context call, not just before the first ')'.
# The first version anchored on `EventLog\([^)]*?actor=` and missed the exact
# shape the incident took — `EventLog(space_dir=Path('~/Data/2-plur'),
# actor='winston')` — because Path(...) closes a paren first. The
# _LEDGER_CONTEXT gate is what keeps prose out, so this can be permissive.
_EVENTLOG_KW = re.compile(r"\bactor\s*=\s*['\"]([A-Za-z0-9_-]+)['\"]")
_EVENTLOG_POS = re.compile(r"EventLog\s*\(\s*[^,)]+,\s*['\"]([A-Za-z0-9_-]+)['\"]", re.S)
_ACTOR_FLAG = re.compile(r"--actor[= ]+['\"]?([A-Za-z0-9_-]+)")
_ACTOR_ENV = re.compile(r"DATACORE_ACTOR=['\"]?([A-Za-z0-9_-]+)")
_LEDGER_CONTEXT = re.compile(r"EventLog|ledger|\.datacore/events|append\s*\(\s*['\"]item\.")


def _root() -> Path:
    return Path(os.environ.get("DATACORE_ROOT") or (Path.home() / "Data"))


def lib_candidates() -> list[Path]:
    """Where the fleet lib can be, most specific first.

    A host does not always keep its code under its data root. hermes keeps
    spaces in ~/Data and the code in the v2-runner clone, which is exactly
    why `ledger_transport._registry` grew the same fallback on 2026-09-07
    (datacore#139) after failing twice a day for a fortnight. The plugin
    deployed there reported INERT for the same reason, five minutes after
    that fix landed."""
    out = []
    override = os.environ.get("DATACORE_LIB")
    if override:
        out.append(Path(override))
    out.append(_root() / ".datacore" / "lib")
    out.append(Path.home() / ".datacore" / "v2-runner" / ".datacore" / "lib")
    return out


def _lib() -> bool:
    """Put the fleet lib on the path. False when this host has no Datacore."""
    for lib in lib_candidates():
        if not (lib / "actor_identity.py").exists():
            continue
        if str(lib) not in sys.path:
            sys.path.insert(0, str(lib))
        return True
    return False


_IDENTITY: dict | None = None


def identity(refresh: bool = False) -> dict:
    """{actor, principal, display, role, permission_mode, ok, why}.

    `ok` is False whenever the plugin cannot bind this host to a declared
    principal — the guards stand down in that state and say so."""
    global _IDENTITY
    if _IDENTITY is not None and not refresh:
        return _IDENTITY
    out = {"actor": "", "principal": "", "display": "", "role": "",
           "permission_mode": "", "ok": False, "why": ""}
    try:
        if not _lib():
            tried = ", ".join(str(p) for p in lib_candidates())
            out["why"] = f"no .datacore/lib found (tried {tried})"
            _IDENTITY = out
            return out
        from actor_identity import principal_of, this_actor  # noqa: PLC0415
        actor = (this_actor() or "").strip().lower()
        name, rec = principal_of(actor)
        out.update(actor=actor, principal=name or "",
                   display=str(rec.get("display") or ""),
                   role=str(rec.get("role") or ""),
                   permission_mode=str(rec.get("permission_mode") or ""))
        if not name:
            out["why"] = f"actor {actor!r} is not a declared principal"
        else:
            out["ok"] = True
    except Exception as exc:  # noqa: BLE001 — identity is advisory, never fatal
        out["why"] = f"{type(exc).__name__}: {exc}"
    _IDENTITY = out
    return out


# ---- identity in memory ----------------------------------------------------

def identity_block(ident: dict | None = None) -> str:
    """The block written into Hermes's memory file. Short by design: it is
    injected into every turn, and its job is to make one fact unmissable."""
    d = ident or identity()
    if not d["ok"]:
        return ""
    who = d["display"] or d["principal"]
    role = f", {d['role']}" if d["role"] else ""
    return (
        f"{MARK_START}\n"
        f"## Who I am (Datacore registry, DIP-0044)\n"
        f"I am **{who}**{role} — principal `{d['principal']}`, "
        f"writing as actor `{d['actor']}`.\n"
        f"- My ledger log is `<space>/.datacore/events/{d['actor']}.jsonl`. "
        f"I write NO other principal's log, whatever an older note or skill says.\n"
        f"- Permission mode: `{d['permission_mode'] or 'unset'}`. "
        f"Effects the fleet policy reserves are refused before the call runs.\n"
        f"{MARK_END}"
    )


def memory_file() -> Path:
    home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
    return home / "memories" / "MEMORY.md"


def sync_memory_block(path: Path | None = None, ident: dict | None = None) -> str:
    """Write the identity block into MEMORY.md, replacing any earlier one.

    Idempotent and bounded: the block sits between markers, so a hand-written
    memory around it survives and the file cannot grow a copy per session."""
    block = identity_block(ident)
    if not block:
        return "skipped"
    p = path or memory_file()
    try:
        text = p.read_text(encoding="utf-8") if p.exists() else ""
    except OSError as exc:
        return f"unreadable: {exc}"
    if MARK_START in text and MARK_END in text:
        head, _, rest = text.partition(MARK_START)
        _, _, tail = rest.partition(MARK_END)
        new = head + block + tail
        outcome = "unchanged" if new == text else "updated"
    else:
        new = (text.rstrip("\n") + "\n\n" + block + "\n") if text.strip() else block + "\n"
        outcome = "added"
    if outcome == "unchanged":
        return outcome
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(new, encoding="utf-8")
        tmp.replace(p)
    except OSError as exc:
        return f"unwritable: {exc}"
    return outcome


def on_session_start(**_kw):
    try:
        outcome = sync_memory_block()
        logger.debug("datacore: identity block %s", outcome)
    except Exception as exc:  # noqa: BLE001
        logger.debug("datacore: identity sync skipped (%s)", exc)
    return None


# ---- authorship gate -------------------------------------------------------

def call_text(tool_name: str, args) -> str:
    try:
        if not _lib():
            return json.dumps(args, default=str) if args else ""
        from tool_policy import call_text as _ct  # noqa: PLC0415
        return _ct(args)
    except Exception:  # noqa: BLE001
        try:
            return json.dumps(args, default=str)
        except Exception:  # noqa: BLE001
            return str(args)


def foreign_actor_write(text: str, ident: dict | None = None) -> str | None:
    """The reason to refuse, or None.

    Refuses only when the named actor belongs to a DIFFERENT declared
    principal. An unknown writer is not refused: it may be a hostname-derived
    log from before DIP-0044, and refusing it would break more than it
    protects."""
    d = ident or identity()
    if not d["ok"] or not text or not _LEDGER_CONTEXT.search(text):
        return None
    try:
        from actor_identity import principal_of  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    for rx in (_EVENTLOG_KW, _EVENTLOG_POS, _ACTOR_FLAG, _ACTOR_ENV):
        for named in rx.findall(text):
            actor = str(named).strip().lower()
            if not actor or actor == d["actor"]:
                continue
            try:
                owner, _ = principal_of(actor)
            except Exception:  # noqa: BLE001
                continue
            if owner and owner != d["principal"]:
                return (
                    f"Refused: this call writes the ledger as actor '{actor}', which belongs to "
                    f"principal '{owner}'. You are '{d['principal']}' and write only as "
                    f"'{d['actor']}' (DIP-0044). A principal's log is its own word; writing "
                    f"another's is a misattribution the fleet verifier reports the next morning. "
                    f"Re-run with actor='{d['actor']}', or ask {owner} to record it."
                )
    return None


# ---- fleet tool policy -----------------------------------------------------

def policy_block(tool_name: str, args) -> str | None:
    """The fleet's tool-effect decision for this call, as a block reason."""
    try:
        if not _lib():
            return None
        from tool_policy import evaluate_hook  # noqa: PLC0415
        out = evaluate_hook({"tool_name": tool_name, "tool_input": args or {}})
        if not out:
            return None
        spec = (out.get("hookSpecificOutput") or {})
        if spec.get("permissionDecision") not in ("deny", "ask"):
            return None
        return str(spec.get("permissionDecisionReason") or "refused by the Datacore tool policy")
    except Exception as exc:  # noqa: BLE001 — policy failure must not stop the agent
        logger.debug("datacore: policy check skipped (%s)", exc)
        return None


def pre_tool_call(tool_name: str = "", args=None, **_kw):
    """Hermes pre_tool_call: {"action": "block", "message": ...} or None."""
    try:
        text = call_text(tool_name, args)
        reason = foreign_actor_write(text) or policy_block(tool_name, args)
        if reason:
            return {"action": "block", "message": reason}
    except Exception as exc:  # noqa: BLE001
        logger.debug("datacore: pre_tool_call skipped (%s)", exc)
    return None


# ---- tool -------------------------------------------------------------------

WHOAMI_SCHEMA = {
    "type": "function",
    "function": {
        "name": "datacore_whoami",
        "description": ("This agent's Datacore principal and ledger actor, from the fleet "
                        "registry, plus whether the identity and policy guards are in force."),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def whoami_handler(**_kw) -> str:
    d = identity(refresh=True)
    if not d["ok"]:
        return json.dumps({"in_force": False, "why": d["why"] or "identity unresolved",
                           "actor": d["actor"]}, indent=2)
    return json.dumps({"in_force": True, "principal": d["principal"], "actor": d["actor"],
                       "display": d["display"], "role": d["role"],
                       "permission_mode": d["permission_mode"],
                       "ledger_log": f"<space>/.datacore/events/{d['actor']}.jsonl",
                       "root": str(_root())}, indent=2)


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("on_session_start", on_session_start)
    try:
        ctx.register_tool(
            name="datacore_whoami", toolset="datacore", schema=WHOAMI_SCHEMA,
            handler=whoami_handler,
            description="This agent's Datacore principal, actor and guard state.",
            emoji="🪪",
        )
    except Exception as exc:  # noqa: BLE001 — hooks matter more than the tool
        logger.warning("datacore: could not register datacore_whoami (%s)", exc)
    d = identity()
    logger.info("datacore plugin: %s",
                f"{d['principal']} as {d['actor']} — guards in force" if d["ok"]
                else f"inert ({d['why']})")
