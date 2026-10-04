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
             declared-against-declared mismatch is refused by this diagnostic.
             An unresolved local identity separately refuses all tool execution.
             This textual diagnostic does not constrain arbitrary same-user code.

  policy     Every acting tool call goes through tool_policy.evaluate_hook —
             the same classifier, the same approvals_policy.yaml limits, the
             same `metric.attest policy.refusal` in the ledger as the Claude
             SDK hook. A refusal reaches the model as the block message.

  presses   Approve / Dismiss presses on approval questions (callback data
             `cosap:`) are passed on to the chief-of-staff press handler
             (`cos_approvals_poll.py --press`) before Hermes's own button
             handler sees them — owner decision 2026-09-27, "Hermes passes
             presses on". See install_press_forwarder.

Guard failures refuse tool execution. This plugin is a policy check, not an
OS sandbox: arbitrary code with the same filesystem credentials can bypass
it, and deployed runtimes must supply an independent execution boundary.

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


# These modules carry identity, authorization and data-preservation decisions.
# One interpreter must not combine them from different installed releases.
_CORE_MODULES = frozenset({"actor_identity", "tool_policy", "yaml_safety",
                           "file_utils", "process_run", "spaces", "ledger"})
_CORE_FILES = tuple(name + ".py" for name in sorted(_CORE_MODULES - {"ledger"})) + ("ledger/__init__.py",)


def lib_candidates() -> list[Path]:
    """Bind bundled plugins to their code, independently of the selected data.

    An explicit administrator binding is exclusive, including when invalid.
    Legacy standalone copies may still discover a complete data/runner checkout;
    managed deployments must supply DATACORE_LIB for those copies.
    """
    if "DATACORE_LIB" in os.environ:
        override = os.environ["DATACORE_LIB"]
        if not override or "\0" in override:
            return []
        path = Path(override)
        if not path.is_absolute() or ".." in path.parts:
            return []
        return [path]
    source = Path(__file__).resolve()
    if source.parent.name == "hermes_plugin" and source.parent.parent.name == "lib":
        # Return even an incomplete installation. A missing policy file must
        # refuse execution, not select an older copy from writable data.
        return [source.parent.parent]
    return [_root() / ".datacore" / "lib",
            Path.home() / ".datacore" / "v2-runner" / ".datacore" / "lib"]


def _lib() -> bool:
    """Select one complete library; refuse already-loaded code from another."""
    for candidate in lib_candidates():
        try:
            try:
                lib = candidate.resolve(strict=True)
            except FileNotFoundError:
                continue
            if not all((lib / name).is_file() and (lib / name).resolve().is_relative_to(lib)
                       for name in _CORE_FILES):
                continue
            for name, module in list(sys.modules.items()):
                if name.split(".", 1)[0] not in _CORE_MODULES:
                    continue
                origin = getattr(module, "__file__", None)
                if not origin or not Path(origin).resolve().is_relative_to(lib):
                    return False
            # Merely finding the path somewhere in sys.path does not give it
            # precedence over an earlier stale or caller-supplied library.
            sys.path[:] = [str(lib), *(entry for entry in sys.path if entry != str(lib))]
            return True
        except (OSError, ValueError, RuntimeError):
            return False
    return False


_IDENTITY: dict | None = None


def identity(refresh: bool = False) -> dict:
    """{actor, principal, display, role, permission_mode, ok, why}.

    `ok` is False whenever the plugin cannot bind this host to a declared
    principal; the pre-tool gate refuses execution in that state."""
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
    except Exception as exc:  # noqa: BLE001 — unresolved identity fails the tool gate
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
    p = Path(path or memory_file())
    if not _lib():
        return "unwritable: Datacore file transaction library unavailable"
    from file_utils import atomic_write_text, file_lock

    try:
        # Match Hermes MemoryStore._file_lock exactly. The default Datacore
        # .MEMORY.md.lock name would not coordinate with provider writes.
        with file_lock(p, lock_path=p.with_suffix(p.suffix + ".lock")):
            try:
                text = p.read_bytes().decode("utf-8")
            except FileNotFoundError:
                text = ""
            except (OSError, UnicodeError) as exc:
                return f"unreadable: {type(exc).__name__}"
            starts, ends = text.count(MARK_START), text.count(MARK_END)
            if starts or ends:
                if starts != 1 or ends != 1 or text.index(MARK_START) >= text.index(MARK_END):
                    return "unreadable: ambiguous identity markers; source preserved"
                head, _, rest = text.partition(MARK_START)
                _, _, tail = rest.partition(MARK_END)
                new = head + block + tail
                outcome = "unchanged" if new == text else "updated"
            else:
                # Preserve authored bytes, including trailing whitespace.
                new = text + ("\n\n" if text else "") + block + "\n"
                outcome = "added"
            if outcome != "unchanged":
                # Provider writes preserve configured symlinks. Publish via a
                # unique flushed temporary sibling of that same target.
                atomic_write_text(p.resolve(), new)
            return outcome
    except OSError as exc:
        return f"unwritable: {type(exc).__name__}"


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
            return "Datacore policy library is unavailable; restore it before executing tools"
        from tool_policy import evaluate_hook  # noqa: PLC0415
        out = evaluate_hook({"tool_name": tool_name, "tool_input": args or {}})
        if not out:
            return None
        spec = (out.get("hookSpecificOutput") or {})
        if spec.get("permissionDecision") not in ("deny", "ask"):
            return None
        return str(spec.get("permissionDecisionReason") or "refused by the Datacore tool policy")
    except Exception as exc:  # noqa: BLE001 — unavailable policy cannot authorize work
        logger.warning("datacore: policy unavailable (%s)", type(exc).__name__)
        return "Datacore policy unavailable; tool execution refused"


def pre_tool_call(tool_name: str = "", args=None, **_kw):
    """Hermes pre_tool_call: {"action": "block", "message": ...} or None."""
    try:
        if not identity()["ok"]:
            return {"action": "block", "message": "Datacore identity unresolved; restore the principal registry"}
        text = call_text(tool_name, args)
        reason = foreign_actor_write(text) or policy_block(tool_name, args)
        if reason:
            return {"action": "block", "message": reason}
    except Exception as exc:  # noqa: BLE001
        logger.warning("datacore: pre_tool_call failed (%s)", type(exc).__name__)
        return {"action": "block", "message": "Datacore policy check failed; tool execution refused"}
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



# ---- acting through tools, not shell strings -------------------------------
# GAP 1 of the Winston-as-Hermes note. The wrong-actor incident was possible
# because a ledger write was a shell string the model composed: the actor was
# a quoted literal in a prompt, and a skill file supplied the wrong one. These
# tools take no actor at all — the runtime fixes it from the registry — and an
# approval decision is likewise a tool call rather than a command line.

def _cos_questions() -> Path | None:
    for lib in lib_candidates():
        cq = lib / "cos_questions.py"
        if cq.exists():
            return cq
    return None


def _run(argv: list[str], timeout: int = 45) -> tuple[int, str]:
    import subprocess
    try:
        from process_run import run
        r = run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except ImportError:
        return 127, "Canonical process runner is unavailable."
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except OSError as exc:
        return 127, str(exc)


APPROVALS_PENDING_SCHEMA = {
    "type": "function",
    "function": {
        "name": "datacore_approvals_pending",
        "description": ("Approvals waiting on the principal, through the daemon. Use this "
                        "when asked what is pending, or to find the id for a decision "
                        "described in words rather than by id."),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

APPROVAL_DECIDE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "datacore_approval_decide",
        "description": ("Approve or dismiss one pending approval, by id. Only ever call this "
                        "for a decision the principal stated explicitly in this conversation; "
                        "never infer one, and never decide on their behalf."),
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "The approval id, from datacore_approvals_pending."},
                "decision": {"type": "string", "enum": ["approve", "dismiss"]},
            },
            "required": ["id", "decision"],
        },
    },
}

LEDGER_APPEND_SCHEMA = {
    "type": "function",
    "function": {
        "name": "datacore_ledger_append",
        "description": ("Append one event to a space's ledger. The actor is this agent's own, "
                        "fixed by the runtime — there is no actor argument, and no way to write "
                        "another principal's log."),
        "parameters": {
            "type": "object",
            "properties": {
                "space": {"type": "string", "description": "Discovered space directory relative to the data root, including nested spaces."},
                "type": {"type": "string", "description": "Event type, e.g. item.create, item.update, item.complete."},
                "payload": {"type": "object", "description": "Event payload. For item.create include id and title."},
            },
            "required": ["space", "type", "payload"],
        },
    },
}


def approvals_pending_handler(**_kw) -> str:
    cq = _cos_questions()
    if cq is None:
        return "Approvals are not available on this host (no cos_questions.py in the fleet lib)."
    rc, out = _run([sys.executable, str(cq), "pending"])
    return out or f"no output (rc={rc})"


def approval_decide_handler(id: str = "", decision: str = "", **_kw) -> str:  # noqa: A002
    ident = identity()
    if not ident["ok"]:
        return "Refused: this host has no declared principal, so a decision cannot be attributed."
    if decision not in ("approve", "dismiss"):
        return "Refused: decision must be 'approve' or 'dismiss'."
    if not id.strip():
        return "Refused: an approval id is required — list them with datacore_approvals_pending."
    cq = _cos_questions()
    if cq is None:
        return "Approvals are not available on this host (no cos_questions.py in the fleet lib)."
    # Model-produced arguments do not prove a human decision. In particular,
    # never manufacture a .telegram identity for an agent-initiated action.
    return "Refused: decisions require the authenticated human approval interface; this agent may only list proposals."



def ledger_append_handler(space: str = "", payload=None, **kw) -> str:
    # The event type arrives as `type` (the schema's property name). It is read
    # from kwargs rather than taken as a parameter, because binding the name
    # `type` would shadow the builtin used in the error paths below.
    ident = identity()
    if not ident["ok"]:
        return "Refused: this host has no declared principal, so it cannot write a ledger."
    if not _lib():
        return "Refused: the fleet lib is not on this host."
    try:
        from ledger.events import EVENT_TYPES  # noqa: PLC0415
        from ledger.log import EventLog  # noqa: PLC0415
        from ledger.policy import guarded_append  # noqa: PLC0415
        from spaces import discover_spaces  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return f"Refused: the ledger library did not import ({type(exc).__name__})."
    etype = str(kw.get("type") or "").strip()
    if etype not in EVENT_TYPES:
        return (f"Refused: {etype!r} is not a declared event type. "
                f"Known: {', '.join(sorted(EVENT_TYPES))}.")
    if not isinstance(payload, dict) or not payload:
        return "Refused: payload must be a non-empty object."
    if (not isinstance(space, str) or not space or "\0" in space
            or Path(space).is_absolute() or ".." in Path(space).parts
            or str(Path(space)) != space or space == "."):
        return "Refused: space must be a canonical relative space directory."
    try:
        root = _root().resolve(strict=True)
        space_dir = root / space
        # Resolve through the same discovery rules used by other automated
        # writers. Metadata directories alone are not space identity; aliases
        # must not provide another route to a canonical space's ledger.
        known = discover_spaces(root, reject_aliases=True, reject_invalid=True)
        if (space_dir.resolve(strict=True) != space_dir
                or space_dir not in {entry.path for entry in known}
                or not (space_dir / ".datacore").is_dir()):
            return "Refused: directory is not a space under the configured data root."
    except (OSError, RuntimeError, ValueError):
        return "Refused: directory is not a space under the configured data root, or discovery is invalid."
    try:
        # No actor argument by design: it comes from the registry, never the model.
        ev = guarded_append(EventLog(space_dir=space_dir, actor=ident["actor"]), etype, payload)
    except Exception as exc:  # noqa: BLE001
        return f"Ledger append failed: {type(exc).__name__}: {exc}"
    return (f"appended {etype} to {space} as {ident['actor']} "
            f"(seq={getattr(ev, 'seq', '?')} hash={str(getattr(ev, 'hash', ''))[:12]})")

# ── Approval button presses (owner decision 2026-09-27) ─────────────────────
#
# The gateway is the bot's one getUpdates consumer, and Hermes's Telegram
# adapter dispatches callback data by prefix: `cosap:` (the chief-of-staff
# Approve / Dismiss buttons) matched none of its prefixes and was dropped
# unanswered — the button spun and the approval never moved. Hermes has no
# plugin API for Telegram callbacks (it has one for Slack actions), so the
# forwarder attaches where the adapter registers its callback handler with
# python-telegram-bot: every CallbackQueryHandler added to an Application gets
# its callback wrapped, `cosap:` data goes to the press handler, and anything
# else reaches Hermes's handler unchanged.
#
# The press handler decides, authorises (only the principal), answers the
# button, rewrites the message and alerts decision errors to The Firm group.
# This side adds nothing to that: it answers the button only when the handler
# could not (so it stops spinning, with the typed reply as the way out) and
# alerts the group through the same cos_alert.sh. Never a 1:1 message.

COSAP_PREFIX = "cosap:"
PRESS_HANDLER = "cos_approvals_poll.py"


def _fleet_script(name: str) -> Path | None:
    for lib in lib_candidates():
        path = lib / name
        if path.is_file():
            return path
    return None


def callback_payload(query) -> dict:
    """A python-telegram-bot CallbackQuery as the Bot API dict the handler reads."""
    msg = getattr(query, "message", None)
    chat = getattr(msg, "chat", None)
    chat_id = getattr(chat, "id", None)
    if chat_id is None:
        chat_id = getattr(msg, "chat_id", None)
    out = {"id": str(getattr(query, "id", "") or ""),
           "data": getattr(query, "data", None),
           "from": {"id": getattr(getattr(query, "from_user", None), "id", None)}}
    if msg is not None:
        out["message"] = {"message_id": getattr(msg, "message_id", None),
                          "chat": {"id": chat_id},
                          "text": getattr(msg, "text", None) or ""}
    return out


def run_press(payload: dict, timeout: int = 60) -> dict:
    """Hand one press to `cos_approvals_poll.py --press` -> its JSON verdict.

    {"outcome": decided|already|missing|unauthorised|ignored|error,
     "answered": whether the handler answered the button}. Anything that is
    not the handler's own verdict is an unanswered error."""
    script = _fleet_script(PRESS_HANDLER)
    if script is None:
        return {"outcome": "error", "answered": False,
                "why": f"{PRESS_HANDLER} is not in the fleet lib on this host"}
    import subprocess  # noqa: PLC0415
    argv = [sys.executable, str(script), "--press"]
    try:
        try:
            from process_run import run  # noqa: PLC0415
        except ImportError:
            run = subprocess.run
        r = run(argv, input=json.dumps(payload), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"outcome": "error", "answered": False, "why": f"timed out after {timeout}s"}
    except (OSError, ValueError, RuntimeError) as exc:
        return {"outcome": "error", "answered": False, "why": f"{type(exc).__name__}: {exc}"}
    for line in reversed((r.stdout or "").strip().splitlines()):
        try:
            verdict = json.loads(line)
        except ValueError:
            continue
        if isinstance(verdict, dict) and "outcome" in verdict:
            verdict.setdefault("answered", False)
            if r.stderr:
                verdict.setdefault("log", r.stderr.strip()[-400:])
            return verdict
    return {"outcome": "error", "answered": False,
            "why": f"rc={r.returncode}: {((r.stderr or '') + (r.stdout or '')).strip()[-200:]}"}


def alert_group(text: str) -> None:
    """The fleet's operational alert: The Firm group, posted by Winston's bot."""
    script = _fleet_script("cos_alert.sh")
    if script is None:
        logger.error("datacore: approvals alert not sent (no cos_alert.sh): %s", text)
        return
    import subprocess  # noqa: PLC0415
    try:
        subprocess.run([str(script), text], timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.SubprocessError, OSError) as exc:
        logger.error("datacore: cos_alert.sh failed (%s): %s", exc, text)


async def forward_press(query) -> str:
    """Pass one `cosap:` press on. Never raises: the gateway must outlive it."""
    import asyncio  # noqa: PLC0415
    data = str(getattr(query, "data", "") or "")
    rid = data.rsplit(":", 1)[-1][:36]
    who = getattr(getattr(query, "from_user", None), "id", "?")
    try:
        verdict = await asyncio.to_thread(run_press, callback_payload(query))
    except Exception as exc:  # noqa: BLE001
        verdict = {"outcome": "error", "answered": False, "why": f"{type(exc).__name__}: {exc}"}
    outcome = str(verdict.get("outcome", "error"))
    if verdict.get("log"):
        logger.info("datacore: press handler said: %s", verdict["log"])
    if outcome == "unauthorised":
        logger.warning("datacore: approval press on %s refused — from %s, not the principal", rid, who)
    else:
        logger.info("datacore: approval press on %s from %s -> %s", rid, who, outcome)
    if outcome == "error" and not verdict.get("answered"):
        try:
            await query.answer(text=(f"Could not record this press. Reply approve {rid} "
                                     f"or dismiss {rid} instead.")[:200])
        except Exception as exc:  # noqa: BLE001
            logger.warning("datacore: could not answer the press: %s", exc)
        alert_group(f"approvals: a Telegram button press on {rid[:8]} did not reach the "
                    f"decision ({str(verdict.get('why', ''))[:160]}). The approval is still "
                    f"pending; the typed reply works.")
    elif outcome == "ignored" and not verdict.get("answered"):
        try:
            await query.answer()
        except Exception:  # noqa: BLE001
            pass
    return outcome


def wrap_callback(original):
    """`cosap:` presses to forward_press; every other button to `original`."""
    if getattr(original, "_datacore_press_forwarder", False):
        return original

    async def callback(update, context):
        query = getattr(update, "callback_query", None)
        data = getattr(query, "data", None)
        if isinstance(data, str) and data.startswith(COSAP_PREFIX):
            try:
                await forward_press(query)
            except Exception:  # noqa: BLE001 — forward_press never raises; belt and braces
                logger.exception("datacore: approval press forwarding failed")
            return None
        return await original(update, context)

    callback._datacore_press_forwarder = True
    callback.__wrapped__ = original
    return callback


def install_press_forwarder(tx=None) -> bool:
    """Wrap python-telegram-bot's Application.add_handler so every callback-query
    handler the gateway adds forwards `cosap:` presses. Installed at plugin
    registration, which the gateway runs before any adapter connects; the
    Telegram adapter itself loads lazily, so its class is not patched."""
    if tx is None:
        try:
            import telegram.ext as tx  # noqa: PLC0415
        except Exception:  # noqa: BLE001 — no Telegram on this host: nothing to forward
            return False
    app_cls = getattr(tx, "Application", None)
    cqh = getattr(tx, "CallbackQueryHandler", None)
    if app_cls is None or cqh is None:
        return False
    current = app_cls.add_handler
    if getattr(current, "_datacore_press_forwarder", False):
        return True

    def add_handler(self, handler, *args, **kwargs):
        if isinstance(handler, cqh):
            try:
                handler.callback = wrap_callback(handler.callback)
            except Exception as exc:  # noqa: BLE001 — never block Hermes's own handler
                logger.warning("datacore: could not attach the approval press forwarder: %s", exc)
        return current(self, handler, *args, **kwargs)

    add_handler._datacore_press_forwarder = True
    add_handler.__wrapped__ = current
    app_cls.add_handler = add_handler
    return True


def _from_hermes(handler):
    """Hermes calls a tool as handler(args_dict, **kwargs) (tools/registry.py
    dispatch); the handlers here take the tool's arguments as keywords."""
    def call(args=None, **_runtime):
        return handler(**(args if isinstance(args, dict) else {}))
    call.__wrapped__ = handler
    return call


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("on_session_start", on_session_start)
    for name, schema, handler, desc, emoji in (
        ("datacore_whoami", WHOAMI_SCHEMA, whoami_handler,
         "This agent's Datacore principal, actor and guard state.", "🪪"),
        ("datacore_approvals_pending", APPROVALS_PENDING_SCHEMA, approvals_pending_handler,
         "Approvals waiting on the principal.", "📋"),
        ("datacore_approval_decide", APPROVAL_DECIDE_SCHEMA, approval_decide_handler,
         "Approve or dismiss one approval the principal decided explicitly.", "✅"),
        ("datacore_ledger_append", LEDGER_APPEND_SCHEMA, ledger_append_handler,
         "Append an event to a space ledger as this agent's own actor.", "📒"),
    ):
        try:
            ctx.register_tool(name=name, toolset="datacore", schema=schema,
                              handler=_from_hermes(handler), description=desc, emoji=emoji)
        except Exception as exc:  # noqa: BLE001 — hooks matter more than any tool
            logger.warning("datacore: could not register %s (%s)", name, exc)
    try:
        if install_press_forwarder():
            logger.info("datacore plugin: approval button presses (cosap:) are passed on")
    except Exception as exc:  # noqa: BLE001 — hooks matter more than the forwarder
        logger.warning("datacore: approval press forwarder not installed (%s)", exc)
    d = identity()
    logger.info("datacore plugin: %s",
                f"{d['principal']} as {d['actor']} — guards in force" if d["ok"]
                else f"identity unavailable; tools refused ({d['why']})")
