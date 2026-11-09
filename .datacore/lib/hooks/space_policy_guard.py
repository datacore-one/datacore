#!/usr/bin/env python3
"""PreToolUse hook: deny or warn based on the space type a tool targets, and
keep a session inside the space it works in.

Policy is in .datacore/config/space-policy.yaml (tracked, no hostnames).
Space identity comes from spaces.find_space() keyed on the *target path*,
not the session cwd — so a git -C or absolute-path Edit into a client space
is caught even when the session started outside it.

Across spaces (SPC-5)
---------------------
A session whose cwd is inside a space may not read or write another space's
files (Read/Edit/Write targets, and paths named in a Bash command) unless the
target space lists the session's space under ``space.share_with`` in its
marker (``.datacore/config.yaml``; a list of space names or directory names,
or ``"*"``). A session started outside every space (the install root) is the
owner's cross-space view and is not restricted by this rule.

One customer per session (MEM-13)
---------------------------------
The first time a session writes in a ``client`` space (Edit/Write, a git
network call, or a shell command that changes something and names a client
path or runs there), that space is recorded for the session (private state,
keyed on session_id). From then on the session may not write inside the
install outside that client space, and may not run a git network command
outside it: other work goes to a new session. Reading is never recorded and
never stopped.

Person documents (MEM-15)
-------------------------
Writing a document about a named person's pay, equity or performance into a
space that is not ``personal`` asks first: such documents live only in the
personal space.

Client guards only (``--client``)
---------------------------------
Owner decision 2026-09-28: "Client guards should be in place." With
``--client`` the hook applies only the two client guards above (one customer
per session; person documents) and skips the cross-space rule and the
space-type policy. The installer wires this mode as the ``client`` guard.

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
import os
import re
import shlex
import sys
from pathlib import Path

_lib = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_lib))

POLICY_PATH = _lib.parent / "config" / "space-policy.yaml"
# The installation this guard ships with. Spaces are resolved in it unless
# DATACORE_ROOT says otherwise -- never from $HOME, which a sandboxed or
# probed session may have pointed elsewhere.
INSTALL_ROOT = _lib.parents[1]

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

    if tool in ("Edit", "Write", "MultiEdit"):
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


def _ask(reason: str) -> int:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
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
# Across spaces (SPC-5)
# ---------------------------------------------------------------------------

_SPACE_CACHE: dict = {}


def _space_of(path: Path):
    """find_space, memoised for this one hook call (several paths, one walk each)."""
    key = str(path)
    if key not in _SPACE_CACHE:
        try:
            _SPACE_CACHE[key] = find_space(path.resolve())
        except Exception:  # noqa: BLE001 — discovery failure → unknown
            _SPACE_CACHE[key] = None
    return _SPACE_CACHE[key]


_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n(.*?)(?:\n\2[ \t]*(?=\n|$)|\Z)", re.S)
_SEPARATORS = frozenset({"&&", "||", ";", "|", "&", "(", ")", ";;", "|&"})
_REDIRECTS = frozenset({">", ">>", "<", ">|", "&>", "&>>"})
_FILE_TOOLS = frozenset({"cp", "mv", "rm", "rmdir", "mkdir", "touch", "ln", "chmod",
                         "rsync", "truncate", "install", "tee", "sed"})


def _strip_heredocs(command: str) -> str:
    """The command without heredoc bodies: their text is data, never paths or writes."""
    return _HEREDOC.sub(lambda m: m.group(0)[: m.start(3) - m.start(0)].rstrip("\n"), command)


def _unquoted(command: str) -> str:
    """The command with quoted strings emptied (a search pattern or message is not a write)."""
    return re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", "''", command)


def _shell_tokens(command: str) -> list[str]:
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:
        return _unquoted(command).split()


def _shell_targets(command: str, cwd: str | None) -> tuple[list[Path], list[Path], list[Path]]:
    """(paths named, directories written in, paths named by writing segments).

    Each segment's relative paths resolve against the directory it runs in: a
    ``git -C <dir>`` for that segment, else the directory of the last ``cd``,
    else the session cwd. Heredoc bodies are skipped. A segment that only reads
    (a script it runs, a file it greps) adds nothing to the third list."""
    tokens = _shell_tokens(_strip_heredocs(command))
    here = Path(cwd) if cwd else None
    paths: list[Path] = []
    workdirs: list[Path] = []
    written: list[Path] = []

    def resolve(tok: str, base: Path | None) -> Path | None:
        p = Path(tok).expanduser()
        if p.is_absolute():
            return p
        return base / p if base is not None else None

    segments: list[list[str]] = [[]]
    for tok in tokens:
        if tok in _SEPARATORS:
            segments.append([])
        else:
            segments[-1].append(tok)

    for seg in filter(None, segments):
        if seg[0] == "cd":
            target = resolve(seg[1], here) if len(seg) > 1 else Path.home()
            if target is not None:
                here = target
            continue
        base = here
        tool = Path(seg[0]).name
        if tool == "git" and "-C" in seg[:-1]:
            base = resolve(seg[seg.index("-C") + 1], here) or here
        # The directory counts as a write target when the segment writes there:
        # git/gh act on the repo they run in; a write naming a bare file lands in it.
        # A bare file is a redirect target or a file-changing tool's argument, never
        # the text an echo prints.
        args, targets, after = [], [], ""
        for t in seg[1:]:
            if after:
                (targets if after == "redirect" else []).append(t)
                after = ""
            elif t in _REDIRECTS:
                after = "redirect"
            elif t in ("<<", "<<-", "<<<"):
                after = "tag"
            elif not t.startswith((">", "<", "&")):
                args.append(t)
        bare = any(not t.startswith("-") and "/" not in t and not t.isdigit()
                   for t in targets + (args if tool in _FILE_TOOLS else []))
        writes = _SHELL_WRITE.search(
            _HARMLESS_REDIRECT.sub(" ", " ".join(t for t in seg if " " not in t))) is not None
        if base is not None and writes and (tool in ("git", "gh") or bare):
            workdirs.append(base)
        after_redirect = False
        for tok in seg:
            if tok in _REDIRECTS:
                after_redirect = True
                continue
            if tok.startswith((">", "<", "&")) or tok.isdigit():
                after_redirect = False
                continue
            redirect_target, after_redirect = after_redirect, False
            if "=" in tok and not tok.startswith(("/", "~", ".")):
                tok = tok.split("=", 1)[1]
            if not tok or "://" in tok:
                continue
            if not redirect_target and "/" not in tok and tok not in ("..", "~"):
                continue
            p = resolve(tok, base)
            if p is not None:
                paths.append(p)
                if writes:
                    written.append(p)
    return paths, workdirs, written


def _command_paths(command: str, cwd: str | None) -> list[Path]:
    """Filesystem paths named in a shell command (absolute, ~, containing a /, or a
    redirect target), each resolved against the directory its segment runs in."""
    return _shell_targets(command, cwd)[0]


def _shares_with(target, session) -> bool:
    """True when the target space's marker lets the session's space in."""
    try:
        from spaces import read_marker
        block = read_marker(target.path) or {}
    except Exception:  # noqa: BLE001 — unreadable marker shares nothing
        return False
    allowed = block.get("share_with") or []
    if isinstance(allowed, str):
        allowed = [allowed]
    names = {session.name, session.path.name}
    return any(str(a) == "*" or str(a) in names for a in allowed)


def _cross_space(session, paths: list[Path]):
    """(target space, path) of the first path in another space that does not share."""
    if session is None:
        return None
    for path in paths:
        target = _space_of(path)
        if target is None or target.path.resolve() == session.path.resolve():
            continue
        if not _shares_with(target, session):
            return target, path
    return None


# ---------------------------------------------------------------------------
# One customer per session (MEM-13)
# ---------------------------------------------------------------------------

def _session_state(session_id: str) -> Path | None:
    if not session_id:
        return None
    try:
        import hashlib
        from file_utils import private_state_directory
        d = private_state_directory("space-guard")
    except Exception:  # noqa: BLE001 — no private state → no memory
        return None
    return d / (hashlib.sha256(session_id.encode()).hexdigest()[:32] + ".json")


def _session_client(state: Path | None) -> Path | None:
    try:
        return Path(json.loads(state.read_text())["client"]) if state else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _remember_client(state: Path | None, client: Path) -> None:
    if state is None:
        return
    try:
        state.write_text(json.dumps({"client": str(client)}))
        state.chmod(0o600)
    except OSError:
        pass


# A shell command that changes something: a redirect, a file-changing tool, a
# git write or a GitHub write. Reading commands (cat, grep, sed -n, ls) do not
# make a client session and are not stopped in one.
_HARMLESS_REDIRECT = re.compile(r"\d*>&\d+|&>\s*/dev/null|\d*>\s*/dev/null")
_SHELL_WRITE = re.compile(
    r">|\btee\b|\b(?:cp|mv|rm|rmdir|mkdir|touch|ln|chmod|rsync|truncate|install)\s"
    r"|\bsed\s+(?:-\w+\s+)*-i"
    r"|\bgit\s+(?:-C\s+\S+\s+)*(?:add|commit|mv|rm|checkout|switch|reset|restore|stash|merge|"
    r"rebase|apply|am|cherry-pick|revert|tag|push|pull|clone|init)\b"
    r"|\bgh\s+\w+\s+(?:create|edit|comment|close|reopen|merge|delete|upload)\b")


def _writes(tool: str, category: str, inp: dict) -> bool:
    """Whether this call changes something (Edit/Write, a git network call, a shell write)."""
    if tool in ("Edit", "Write", "MultiEdit"):
        return True
    if tool != "Bash":
        return False
    if category == "network":
        return True
    command = _unquoted(_strip_heredocs(str(inp.get("command") or "")))
    command = _HARMLESS_REDIRECT.sub(" ", command)
    return _SHELL_WRITE.search(command) is not None



def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _install_root() -> Path:
    return Path(os.environ.get("DATACORE_ROOT") or INSTALL_ROOT)


def _label(space) -> str:
    """A space's name for a message -- never a client's (it names the customer)."""
    return "a client space" if space.type == "client" else f"space {space.name!r}"


# ---------------------------------------------------------------------------
# Person documents (MEM-15)
# ---------------------------------------------------------------------------

_PERSON_TERMS = re.compile(
    r"\b(salary|salaries|remuneration|compensation|pay rise|bonus|equity|vesting|cliff|"
    r"stock options?|performance (?:notes|review|rating|issues?)|appraisal|disciplinary)\b", re.I)
_FULL_NAME = re.compile(r"\b[A-Z][a-z\u00C0-\u017F]+ [A-Z][a-z\u00C0-\u017F]+(?:-[A-Z][a-z]+)?\b")


def _written_text(inp: dict) -> str:
    parts = [inp.get("content"), inp.get("new_string")]
    parts += [e.get("new_string") for e in inp.get("edits") or [] if isinstance(e, dict)]
    return "\n".join(str(p) for p in parts if p)


def _is_person_document(text: str) -> bool:
    """Two or more pay/equity/performance terms and a full name."""
    terms = {m.group(1).lower() for m in _PERSON_TERMS.finditer(text)}
    return len(terms) >= 2 and _FULL_NAME.search(text) is not None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    client_only = "--client" in (sys.argv[1:] if argv is None else argv)
    added = "DATACORE_ROOT" not in os.environ
    if added:
        os.environ["DATACORE_ROOT"] = str(INSTALL_ROOT)
    restore = _discover_once()
    try:
        return _main(client_only)
    finally:
        restore()
        if added:
            os.environ.pop("DATACORE_ROOT", None)
        _SPACE_CACHE.clear()


def _discover_once():
    """Walk the install for spaces once per hook call, not once per path (~0.3 s each).
    Returns the function that puts spaces.discover_spaces back."""
    try:
        import spaces
    except Exception:  # noqa: BLE001
        return lambda: None
    real, seen = spaces.discover_spaces, {}

    def cached(*args, **kwargs):
        key = repr((args, sorted(kwargs.items())))
        if key not in seen:
            seen[key] = real(*args, **kwargs)
        return seen[key]

    spaces.discover_spaces = cached
    return lambda: setattr(spaces, "discover_spaces", real)


def _main(client_only: bool = False) -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    category, target = _category_and_path(payload)
    if category == "allow" or target is None:
        return 0

    if find_space is None:
        return 0

    tool = payload.get("tool_name") or payload.get("toolName") or ""
    inp = payload.get("tool_input") or payload.get("toolInput") or {}
    cwd = payload.get("cwd")
    paths = [target]
    if tool == "Bash":
        paths = _command_paths(str(inp.get("command") or ""), cwd)
        if category == "network":
            paths.append(target)

    # SPC-5: a session inside one space stays out of the others.
    session = _space_of(Path(cwd)) if cwd else None
    crossing = None if client_only else _cross_space(session, paths)
    if crossing:
        other, path = crossing
        return _deny(
            f"Space isolation: this session works in {_label(session)}; {path} belongs to "
            f"{_label(other)}, which does not share with it (space.share_with in its "
            ".datacore/config.yaml). Work on it from a session started in that space.")

    space = _space_of(target)

    # MEM-13: once a session has written in a client space, it writes nowhere else.
    if _writes(tool, category, inp):
        where = [target]
        if tool == "Bash":
            _, workdirs, written = _shell_targets(str(inp.get("command") or ""), cwd)
            where = written + workdirs
        state = _session_state(str(payload.get("session_id") or ""))
        client = _session_client(state)
        touched = [s for s in map(_space_of, where) if s is not None and s.type == "client"]
        if client is None and touched:
            client = touched[0].path
            _remember_client(state, client)
        outside = [p for p in where if client is not None
                   and not _inside(p, client) and _inside(p, _install_root())]
        if outside:
            return _deny(
                "Customer isolation: this session has written in a client space, so it may "
                f"change nothing outside it ({outside[0]} is outside). Start a separate "
                "session for other work.")

    # MEM-15: a person document belongs in the personal space.
    if tool in ("Edit", "Write", "MultiEdit") and space is not None and space.type != "personal":
        if _is_person_document(_written_text(inp)):
            return _ask(
                f"Person document: this names a person with pay, equity or performance detail "
                f"and would be written into {_label(space)} ({space.type}). Documents "
                "about a named person belong in the personal space (0-personal/1-active/"
                "<venture>/people/). Confirm only if the owner asked for this location.")

    if space is None or client_only:
        return 0  # path not in any space, or only the client guards are switched on

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
