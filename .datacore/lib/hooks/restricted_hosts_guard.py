#!/usr/bin/env python3
"""Block agent access to hosts that are off-limits to AI.

Some infrastructure is covered by agreements that forbid AI systems touching it
at all. That is not a preference an agent should be trusted to remember across
sessions — a fresh context, a plausible-sounding reason, and the rule is gone.
So it is enforced mechanically, before the command runs, rather than written
down and hoped for.

Configuration is split, because the host names themselves are confidential:

* ``restricted_hosts.json`` beside this file — **tracked, public**. Generic
  entries only: private network ranges, the default message. Never a customer
  host name; this repository is public.
* ``~/.datacore/private/customer-denylist.yaml`` under ``restricted_hosts:`` —
  **never in any repository**. The actual host names live here and are unioned
  with the public config at load time.

Naming a customer in the public file is itself the disclosure the agreement
exists to prevent, and it happened once already.

What counts as reaching a host
------------------------------
Matching the raw command text is both too weak and too strong. Too weak because
``git push origin main`` names no host at all — the target is in ``.git/config``.
Too strong because ``grep acme notes.md`` mentions a name while touching
nothing, and a guard that cries wolf gets switched off.

So targets are *extracted* rather than pattern-matched:

* URLs anywhere in the command (``https://git.example.com/x``)
* ``scp``-style targets (``user@host:/path``)
* the destination argument of a remote tool (``ssh myalias``)
* for a git subcommand that talks to a network, the resolved URLs of the
  remotes in every repository it could operate on: the start directory, each
  `cd` before it (applied or not, since a `cd` in a subshell does not
  persist), `-C`, `--git-dir` and `GIT_DIR`

"The command" is every simple command in it, not its first word: segments
split on shell operators (quote-aware), with leading `VAR=value`, wrappers
(`env`, `sudo`, `timeout 5`, …), `sh -c` / `eval` bodies and command
substitutions unpacked. Until 2026-09-23 git was recognised only as the first
token of the whole command, so `cd repo && git push`, `FOO=1 git push`,
`env git push` and `git -c k=v push` all passed (DatacoreSpec/Guards.lean).

Host names match on **domain-label** boundaries, so an entry ``acme`` catches
``git.acme.si`` and ``acme`` but not ``acmecorp``.

Failure behaviour
-----------------
Fails **open** for commands that cannot reach anywhere — a broken guard that
blocks every command is its own outage. Fails **closed** when it cannot finish
evaluating a command that demonstrably *can* reach a network. An error while
resolving a git remote must not be the reason a prohibited push succeeds.

Exit 2 blocks the call and returns the message to the agent. Exit 0 allows it.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

CONFIG = Path(__file__).with_name("restricted_hosts.json")
PRIVATE_CONFIG = Path.home() / ".datacore" / "private" / "customer-denylist.yaml"

DEFAULTS = {
    "hosts": [],
    "networks": [],
    "note": "This host is off-limits to AI agents. A human must run this manually.",
}

# Commands that reach another machine. `ssh` covers tunnels (-L/-R/-D) because
# the binary is the same; there is no separate tunnel command to enumerate.
REMOTE_TOOLS = (
    "ssh", "scp", "sftp", "rsync", "nc", "ncat", "netcat", "telnet",
    "curl", "wget", "ftp", "socat", "mosh", "ping", "traceroute", "nmap",
)

# git subcommands that open a connection. `git push` was previously invisible
# to this guard, which is the hole that let a prohibited push through.
GIT_NETWORK_SUBCOMMANDS = frozenset({
    "push", "pull", "fetch", "clone", "ls-remote", "remote", "submodule",
    "request-pull", "send-email", "svn", "archive",
})

URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://([^/\s'\"]+)", re.IGNORECASE)
SCP_RE = re.compile(r"(?:^|[\s'\"])(?:[\w.-]+@)([\w.-]+):", re.IGNORECASE)


class Unevaluable(Exception):
    """Evaluation failed for a command that can reach a network — fail closed."""


def _host_list(value, where: str) -> list[str]:
    """A list of host/network strings, or Unevaluable. A bare string would be
    iterated character by character; any other shape is a config we cannot
    read, which fails closed like an unreadable file."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise Unevaluable(f"{where} must be a list of strings")
    return [v for v in value if v]


def load() -> dict:
    """Public config unioned with the private host list.

    Any config that exists but cannot be read as the expected shape raises
    Unevaluable: silently dropping it would let a network command through
    with no list to check it against (2026-09-23, Lean model
    DatacoreSpec/Guards.lean: a private overlay written as a YAML list raised
    AttributeError, which the entry point turned into exit 0)."""
    config = dict(DEFAULTS)
    if CONFIG.exists():
        try:
            public = json.loads(CONFIG.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeError) as exc:
            raise Unevaluable(f"public host list unreadable: {exc}") from exc
        if not isinstance(public, dict):
            raise Unevaluable("public host list is not a mapping")
        config.update(public)
        config["hosts"] = _host_list(config.get("hosts"), "public hosts")
        config["networks"] = _host_list(config.get("networks"), "public networks")

    if not PRIVATE_CONFIG.exists():
        return config
    try:
        import yaml
        loaded = yaml.safe_load(PRIVATE_CONFIG.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — unreadable overlay is fail-closed
        raise Unevaluable(f"private host list unreadable: {exc}") from exc
    if not isinstance(loaded, dict):
        raise Unevaluable("private host list is not a mapping")
    private = loaded.get("restricted_hosts") or {}
    if not isinstance(private, dict):
        raise Unevaluable("private restricted_hosts is not a mapping")
    config["hosts"] = list(config["hosts"]) + _host_list(private.get("hosts"), "private hosts")
    config["networks"] = list(config["networks"]) + _host_list(private.get("networks"), "private networks")
    if isinstance(private.get("note"), str) and private["note"]:
        config["note"] = private["note"]
    return config


def _matches(target: str, host: str) -> bool:
    """True when `host` appears in `target` as a whole domain label."""
    return re.search(
        rf"(?<![A-Za-z0-9-]){re.escape(host)}(?![A-Za-z0-9-])", target, re.IGNORECASE
    ) is not None


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _git_remote_urls(directory: Path) -> list[str]:
    """The fetch and push URLs of the repository at `directory`.

    "Not a repository" (the directory is absent, or git says so) means there
    is nothing to reach. Any OTHER git failure — a dubious-ownership refusal,
    a corrupt config — is not evidence of that, so it is Unevaluable."""
    if not directory.is_dir():
        return []
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), "remote", "-v"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unevaluable(f"could not resolve git remotes: {exc}") from exc
    if result.returncode != 0:
        if "not a git repository" in (result.stderr or "").lower():
            return []
        raise Unevaluable(f"git remote -v failed in {directory} (exit {result.returncode})")
    return [line.split()[1] for line in result.stdout.splitlines() if len(line.split()) > 1]


# Fallback splitter, used only when the command has unbalanced quotes. Shell
# operators that start a new simple command; `$(`, backticks and parentheses
# start one too. Splitting a quoted operator by mistake only creates an extra
# segment to inspect.
SEGMENT_RE = re.compile(r"&&|\|\||\$\(|[;\n|&()`{}]")

# Words that run the command after them. Their own options are skipped by the
# scan in `_invocation`, which looks for the first recognised command name.
WRAPPERS = frozenset({
    "env", "command", "exec", "nohup", "time", "nice", "sudo", "doas",
    "builtin", "stdbuf", "timeout", "xargs", "caffeinate", "torsocks",
    "proxychains", "proxychains4", "unbuffer", "chronic", "ionice",
})
# Shell keywords that may lead a simple command.
KEYWORDS = frozenset({"!", "if", "then", "else", "elif", "do", "while", "until"})
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
# git's global options that take their value as the NEXT token.
GIT_OPTS_WITH_ARG = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env",
    "--super-prefix", "--exec-path",
})
UNKNOWN_DIR = None      # a `cd` whose target is only known at run time
_MAX_DEPTH = 3          # nested `sh -c` levels unpacked before giving up


def _is_assignment(token: str) -> bool:
    return re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token) is not None


def _invocation(tokens: list[str]) -> tuple[dict, list[str]]:
    """(env assignments, argv) of the command a segment actually runs.

    Strips leading keywords and `VAR=value` assignments, then — when the head
    is a wrapper (`env`, `sudo`, `timeout 5`, …) — skips to the first token
    that names a remote tool, git or a shell. A wrapper followed by nothing
    recognisable returns the wrapper itself, which matches nothing."""
    assigns: dict[str, str] = {}
    i = 0
    while i < len(tokens) and (tokens[i] in KEYWORDS or _is_assignment(tokens[i])):
        if _is_assignment(tokens[i]):
            key, _, value = tokens[i].partition("=")
            assigns[key] = value
        i += 1
    argv = tokens[i:]
    if argv and Path(argv[0]).name in WRAPPERS:
        known = set(REMOTE_TOOLS) | {"git"} | SHELLS | {"eval"}
        for j in range(1, len(argv)):
            if _is_assignment(argv[j]):
                key, _, value = argv[j].partition("=")
                assigns[key] = value
                continue
            if Path(argv[j]).name in known:
                return assigns, argv[j:]
    return assigns, argv


def _simple_commands(command: str) -> list[list[str]]:
    """The command split into simple commands, quote-aware.

    shlex with punctuation_chars keeps a quoted `&&` inside its string (so
    `sh -c "cd x && git pull"` stays one argument) while separating unquoted
    operators even without spaces around them. Unbalanced quotes fall back to
    splitting on the raw operator text, which over-splits: harmless, since an
    extra segment is only an extra thing to inspect."""
    text = command.replace("\\\n", " ").replace("\n", " ; ")
    try:
        lex = shlex.shlex(text, posix=True, punctuation_chars=";&|()<>")
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return [_tokens(part) for part in SEGMENT_RE.split(command) if part.strip()]
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in ("{", "}") or (token and set(token) <= set(";&|()")):
            if current:
                segments.append(current)
            current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _invocations(command: str, depth: int = 0) -> list[tuple[dict, list[str]]]:
    """Every simple command in `command`, in order, with `sh -c '…'`,
    `eval '…'` and command-substitution bodies unpacked in place."""
    out: list[tuple[dict, list[str]]] = []

    def nested(body: str) -> None:
        if depth >= _MAX_DEPTH:
            raise Unevaluable("shell nesting too deep to evaluate")
        out.extend(_invocations(body, depth + 1))

    for raw in _simple_commands(command):
        # A substitution inside a quoted word still runs: "$(git push)".
        for token in raw:
            if "$(" in token or "`" in token:
                nested(token.replace("`", " ; ").replace("$(", " ; ").replace(")", " ; "))
        tokens = [t.strip("`") for t in raw]
        assigns, argv = _invocation([t for t in tokens if t])
        if not argv:
            continue
        head = Path(argv[0]).name
        if head in SHELLS and "-c" in argv[1:]:
            k = argv.index("-c", 1)
            if k + 1 < len(argv):
                nested(argv[k + 1])
            continue
        if head == "eval":
            nested(" ".join(argv[1:]))
            continue
        out.append((assigns, argv))
    return out


def _segments(command: str) -> list[list[str]]:
    """The argv of each simple command (kept for callers and tests)."""
    return [argv for _, argv in _invocations(command)]


def _explicit_targets(command: str, invocations: list[tuple[dict, list[str]]]) -> list[str]:
    """Hosts named directly in the command."""
    # URLs and user@host: forms are unambiguous wherever they appear.
    targets = [match.group(1) for match in URL_RE.finditer(command)]
    targets += [match.group(1) for match in SCP_RE.finditer(command)]

    # A bare destination (`ssh myalias`) is read only from the argv of the
    # command that runs the remote tool. Scanning the whole command instead
    # means the word "scp-style" in a commit message switches on host-matching
    # against every token of every other segment — a false positive seen on a
    # `grep` in an `&&`-chained command.
    for _, segment in invocations:
        if Path(segment[0]).name not in REMOTE_TOOLS:
            continue
        for token in segment[1:]:
            if token.startswith("-"):
                continue
            candidate = token.split("@")[-1]
            if ":" in candidate:
                # `host:/path` — the colon comes before any slash, so this is a
                # destination rather than a local path.
                candidate = candidate.split(":", 1)[0]
            elif "/" in candidate:
                continue  # a local path, not a host
            if candidate and Path(candidate).name not in REMOTE_TOOLS:
                targets.append(candidate)
    return targets


def _resolve_dir(base, arg: str):
    """Where `cd arg` from `base` lands, or UNKNOWN_DIR when only the shell
    knows (`cd $REPO`, `cd -`, a command substitution)."""
    if base is UNKNOWN_DIR or arg == "-" or any(c in arg for c in "$`*?["):
        return UNKNOWN_DIR
    target = Path(arg).expanduser()
    return target if target.is_absolute() else Path(base) / target


def _git_subcommand(argv: list[str]) -> tuple[str | None, list[str], list[str]]:
    """(subcommand, `-C` paths, `--git-dir` paths) of a git argv."""
    sub, chdirs, gitdirs = None, [], []
    i = 1
    while i < len(argv):
        token = argv[i]
        if token in GIT_OPTS_WITH_ARG:
            value = argv[i + 1] if i + 1 < len(argv) else ""
            if token == "-C":
                chdirs.append(value)
            elif token == "--git-dir":
                gitdirs.append(value)
            i += 2
            continue
        if token.startswith("--git-dir="):
            gitdirs.append(token.split("=", 1)[1])
        if token.startswith("-"):
            i += 1
            continue
        sub = token
        break
    return sub, chdirs, gitdirs


def _git_targets(invocations: list[tuple[dict, list[str]]], cwd: str | None) -> list[str]:
    """Remote URLs any network git subcommand in the command could contact.

    Which directory a git command runs in depends on which earlier `cd`s took
    effect, and a `cd` inside `( … )` or a pipeline does not persist. So this
    tracks EVERY directory the shell could be in — the start directory plus
    each `cd` applied or not — and checks the remotes of all of them. That
    over-approximates on purpose: checking one guess is how `cd repo && git
    push` walked past this guard (DatacoreSpec/Guards.lean,
    `git_candidates_sound`)."""
    start = Path(cwd) if cwd else Path.cwd()
    dirs: list = [start]
    urls: list[str] = []
    for assigns, argv in invocations:
        head = Path(argv[0]).name
        if head in ("cd", "pushd"):
            arg = next((t for t in argv[1:] if not t.startswith("-") or t == "-"), "~")
            moved = [_resolve_dir(d, arg) for d in dirs]
            dirs = list(dict.fromkeys(dirs + moved))  # ordered union
            continue
        if head != "git":
            continue
        sub, chdirs, gitdirs = _git_subcommand(argv)
        if sub not in GIT_NETWORK_SUBCOMMANDS:
            continue
        here = dirs
        for c in chdirs:
            here = [_resolve_dir(d, c) for d in here]
        extra = [assigns["GIT_DIR"]] if "GIT_DIR" in assigns else []
        for g in gitdirs + extra:
            here = here + [_resolve_dir(d, g) for d in here]
        for d in here:
            if d is UNKNOWN_DIR:
                raise Unevaluable("git runs in a directory only known at run time")
            urls += _git_remote_urls(Path(d))
    return urls


def offending(command: str, config: dict, cwd: str | None = None) -> str | None:
    """The restricted target this command would reach, if any."""
    for network in config["networks"]:
        if network in command:
            return network.rstrip(".") + ".x"

    invocations = _invocations(command)
    targets = _explicit_targets(command, invocations) + _git_targets(invocations, cwd)

    for target in targets:
        try:
            host_part = urlsplit(target).hostname or target
        except ValueError:
            host_part = target
        for host in config["hosts"]:
            if _matches(host_part, host) or _matches(target, host):
                return host
    return None


def _can_reach_network(command: str, _tokens=None) -> bool:
    """Whether this command is capable of contacting a host at all.

    Deliberately textual and generous: it decides only what happens when
    evaluation FAILED, so it must not depend on the parser that just failed.
    The second argument is accepted for the historical `(command, tokens)`
    call shape and deliberately ignored."""
    try:
        lowered = command.lower()
        if any(re.search(rf"\b{re.escape(tool)}\b", lowered) for tool in REMOTE_TOOLS):
            return True
        if re.search(r"\bgit\b", lowered) and any(
                re.search(rf"\b{re.escape(sub)}\b", lowered) for sub in GIT_NETWORK_SUBCOMMANDS):
            return True
        return bool(URL_RE.search(command))
    except Exception:  # noqa: BLE001 — unknown reachability is treated as reachable
        return True


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0

    tool_input = payload.get("tool_input")
    command = str(tool_input.get("command", "")) if isinstance(tool_input, dict) else ""
    if not command:
        return 0
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None

    try:
        config = load()
        target = offending(command, config, cwd)
    except Exception as exc:  # noqa: BLE001 — see module docstring on failure behaviour
        # Not only Unevaluable: ANY failure to finish evaluating a command that
        # can reach a network is the fail-closed case. The entry point's
        # catch-all below used to turn an unexpected exception here into exit 0.
        if not _can_reach_network(command):
            return 0  # cannot reach anywhere — no reason to block
        print(
            f"BLOCKED: could not verify this command's target ({exc}).\n"
            "A command that can reach a network is refused when the restricted-host "
            "list cannot be read or the command cannot be evaluated, rather than "
            "allowed by default.",
            file=sys.stderr,
        )
        return 2

    if target is None:
        return 0

    print(
        f"BLOCKED: this command reaches {target}, which is off-limits to AI agents.\n"
        f"{config['note']}\n"
        "Do not attempt a workaround — no tunnel, proxy, alternate hostname, or "
        "asking another agent to run it. Tell the operator what needs doing and "
        "let them run it themselves.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - only payload handling can reach here;
        # every network-capable evaluation failure is decided inside main().
        sys.exit(0)
