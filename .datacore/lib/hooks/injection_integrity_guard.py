#!/usr/bin/env python3
"""Make a TRUNCATED memory injection able to stop the session.

WHY THIS EXISTS
---------------
2026-09-07: plur_session_start returned 115,052 characters. That exceeded the
tool-result limit, so the harness spilled it to a file and handed back a
pointer. The model read 5,000 of those characters — 4% — and carried on as
though it had its memory. Four engrams forbidding what happened next were in
the 96% it skipped, including one literally titled DEMO MODE.

Nothing failed. PLUR recalled correctly, the harness truncated correctly, and
the model proceeded incorrectly — silently, because a pointer to a file reads
exactly like a result you already have.

This hook removes the silence. A truncated injection becomes a hard gate: no
tool runs except the ones needed to read the spilled file, until it is read.

MODES
-----
  mark   PostToolUse on mcp__plur__plur_session_start — detect the spill,
         record the path, arm the gate.
  check  PreToolUse '*' — refuse tools while the gate is armed.
  clear  PostToolUse on Read|Bash|Grep — disarm once the file is read.
         "Read" means every line of it, by the Read tool. Coverage is
         accumulated across Read calls (offset/limit ranges), because a spill
         file is usually too large for one Read. A mention of the path — an
         `ls`, a grep hit, a 10-line peek — does not count: until 2026-09-23
         any mention cleared the gate, which re-created the 4% read this hook
         exists to stop (DatacoreSpec/Guards.lean, `gate_clears_only_on_full_read`).

         While armed, Bash is allowed only for a read-only command whose
         ONLY file operand is the spill file: cat / head / tail / sed -n /
         grep / wc / less on that path, with no redirection, pipe, `;`, `&&`,
         `||`, `&`, substitution or expansion. Anything else is denied, and
         the denial says how the gate clears (decision S2, 2026-09-23;
         DatacoreSpec/Guards.lean `armed_bash_reads_only_the_spill`). Until
         then Bash was allowed outright, so `curl` or `git push` ran while
         memory was truncated. A Bash read does not clear the gate: only the
         Read tool does, because only its line ranges are known.

FAIL-SAFE
---------
Every failure path exits 0 (allow). A guard that bricks a session on its own
bug is worse than the leak it prevents. Set DATACORE_SKIP_INJECTION_GUARD=1
to disable entirely.
"""
import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hook_state import state_path
from file_utils import atomic_write_json
# Tools that must stay available so the spilled file can actually be read.
# Bash is not here: it passes only through `bash_reads_only(…)` below.
READERS = {"Read", "Grep", "Glob", "ToolSearch"}
SPILL_PAT = re.compile(
    r"(?:exceeds maximum allowed tokens|Output has been saved to)"
)
PATH_PAT = re.compile(r"(/[^\s\"']*tool-results/[^\s\"']+\.txt)")


def _state(sid: str) -> Path:
    return state_path("injection-gate", sid)


# ── Bash while armed (decision S2) ─────────────────────────────────────────
# Any of these anywhere in the command means it is not a plain single command:
# pipes, lists, background, redirection, substitution, expansion, newlines.
_SHELL_META = set("|;&<>`$\n\r(){}")
_NUM = re.compile(r"^[+-]?\d+$")
_SED_PRINT = re.compile(r"^(?:\d+|\$)(?:,(?:\d+|\$))?p$")


def _opts_ok(prog: str, args: list[str]) -> tuple[bool, list[str]]:
    """Consume the options `prog` may take; return (ok, the remaining operands)."""
    rest: list[str] = []
    i = 0
    need_pattern = prog == "grep"
    sed_script = False
    while i < len(args):
        a = args[i]
        if prog == "sed":
            if a == "-n":
                i += 1
                continue
            if a.startswith("-"):
                return False, []
            if not sed_script:
                if not _SED_PRINT.match(a) or "-n" not in args[:i]:
                    return False, []
                sed_script = True
                i += 1
                continue
        elif prog in ("head", "tail"):
            if a in ("-n", "-c"):
                if i + 1 >= len(args) or not _NUM.match(args[i + 1]):
                    return False, []
                i += 2
                continue
            if re.match(r"^-[nc][+-]?\d+$", a) or re.match(r"^-\d+$", a):
                i += 1
                continue
            if a.startswith("-"):
                return False, []
        elif prog == "grep":
            if a in ("-A", "-B", "-C", "-m"):
                if i + 1 >= len(args) or not args[i + 1].isdigit():
                    return False, []
                i += 2
                continue
            if re.match(r"^-[ABCm]\d+$", a) or re.match(r"^-[nicvwxFEoHhl]+$", a):
                i += 1
                continue
            if a.startswith("-"):
                return False, []
            if need_pattern:
                need_pattern = False
                i += 1
                continue
        elif prog == "cat":
            if re.match(r"^-[nbsAvetE]+$", a):
                i += 1
                continue
            if a.startswith("-"):
                return False, []
        elif prog == "wc":
            if re.match(r"^-[lwcm]+$", a):
                i += 1
                continue
            if a.startswith("-"):
                return False, []
        elif prog == "less":
            if re.match(r"^-[NRS]+$", a):
                i += 1
                continue
            if a.startswith("-"):
                return False, []
        rest.append(a)
        i += 1
    if need_pattern or (prog == "sed" and not sed_script):
        return False, []
    return True, rest


READ_ONLY_PROGS = ("cat", "head", "tail", "sed", "grep", "wc", "less")


def bash_reads_only(command: str, spill: str) -> bool:
    """True iff `command` is one read-only program whose only file operand is
    the spill file. Anything unparseable is False (denied)."""
    if not isinstance(command, str) or not spill or any(c in _SHELL_META for c in command):
        return False
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if not argv or argv[0] not in READ_ONLY_PROGS:
        return False
    ok, operands = _opts_ok(argv[0], argv[1:])
    if not ok or len(operands) != 1:
        return False
    try:
        return os.path.realpath(operands[0]) == os.path.realpath(spill)
    except (OSError, ValueError):
        return False


def _load() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def _text(obj) -> str:
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj)
    except Exception:
        return str(obj)


def mark(data: dict) -> None:
    resp = _text(data.get("tool_response", ""))
    if not SPILL_PAT.search(resp):
        return
    m = PATH_PAT.search(resp)
    if not m:
        return
    atomic_write_json(_state(data.get("session_id", "")), {"path": m.group(1), "armed": True})


def check(data: dict) -> None:
    p = _state(data.get("session_id", ""))
    if not p.exists():
        return
    try:
        st = json.loads(p.read_text())
    except Exception:
        return
    if not st.get("armed"):
        return
    tool = data.get("tool_name")
    if tool in READERS:
        return
    path = st.get("path", "the spilled injection file")
    if tool == "Bash":
        ti = data.get("tool_input")
        cmd = ti.get("command") if isinstance(ti, dict) else None
        if bash_reads_only(cmd, st.get("path", "")):
            return
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "MEMORY INJECTION TRUNCATED — you do not have your memory yet.\n\n"
                        f"plur_session_start spilled to:\n  {path}\n\n"
                        "You have read only the pointer, not the payload. Engrams that "
                        "constrain what you may SAY — client names, demo-mode redaction, "
                        "credential handling — live in the middle of that file, not at its "
                        "head or tail. Reading the first and last few KB is how a redaction "
                        "rule gets skipped (2026-09-07).\n\n"
                        "Read the complete file using the Read tool. If it is too large "
                        "for one Read, page through it with offset/limit.\n\n"
                        "This gate lifts automatically once every line of that file "
                        "has been read by the Read tool. Until then Bash runs only a "
                        "read-only command on that one file (cat, head, tail, sed -n "
                        "'N,Mp', grep, wc, less), with no pipe, redirection, `;`, `&&` "
                        "or `||`. A Bash read does not lift the gate."
                    ),
                }
            }
        )
    )
    sys.exit(0)


READ_DEFAULT_LIMIT = 2000   # lines the Read tool returns when no limit is given


def _covered(ranges: list, total: int) -> bool:
    """True when the union of 1-based inclusive [start, end] ranges covers
    every line 1..total."""
    reach = 0
    for start, end in sorted(ranges):
        if start > reach + 1:
            return False
        reach = max(reach, end)
    return reach >= total


def _line_count(path: str) -> int:
    with open(path, "rb") as fh:
        data = fh.read()
    return data.count(b"\n") + (0 if not data or data.endswith(b"\n") else 1)


def clear(data: dict) -> None:
    p = _state(data.get("session_id", ""))
    if not p.exists():
        return
    try:
        st = json.loads(p.read_text())
    except Exception:
        p.unlink(missing_ok=True)
        return
    target = st.get("path", "")
    if not target:
        p.unlink(missing_ok=True)
        return
    if data.get("tool_name") != "Read":
        return
    ti = data.get("tool_input")
    if not isinstance(ti, dict) or not isinstance(ti.get("file_path"), str):
        return
    if os.path.realpath(ti["file_path"]) != os.path.realpath(target):
        return
    if SPILL_PAT.search(_text(data.get("tool_response", ""))):
        return      # the read itself was refused as too large: nothing was read
    try:
        total = _line_count(target)
    except OSError:
        # The file is gone, so the gate can never be satisfied. Fail-safe:
        # a guard that bricks a session on its own state is worse (docstring).
        p.unlink(missing_ok=True)
        return
    try:
        start = max(1, int(ti.get("offset") or 1))
        limit = int(ti.get("limit") or READ_DEFAULT_LIMIT)
    except (TypeError, ValueError):
        return
    if limit < 1:
        return
    ranges = [r for r in st.get("covered", []) if isinstance(r, list) and len(r) == 2]
    ranges.append([start, start + limit - 1])
    if _covered(ranges, total):
        p.unlink(missing_ok=True)
        return
    st["covered"] = ranges
    atomic_write_json(p, st)


def main() -> None:
    if os.environ.get("DATACORE_SKIP_INJECTION_GUARD") == "1":
        sys.exit(0)
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    data = _load()
    try:
        {"mark": mark, "check": check, "clear": clear}.get(mode, check)(data)
    except SystemExit:
        raise
    except Exception:
        # Never brick a session on this guard's own bug.
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
