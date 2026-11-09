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
  persist), `-C`, `--git-dir` and `GIT_DIR` — with every value a variable in
  them can have, and every pass of a loop (see "shell structure" below) —
  plus where git's config sends it instead: `insteadOf` rewrites, ssh
  commands and proxies, including ones set in the same command

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
import os
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

# Non-shell tools that fetch a URL from this machine (Claude Code, Hermes).
URL_TOOLS = frozenset({"WebFetch", "web_extract"})
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


def _git_remote_urls(directory: Path) -> list[str] | None:
    """The fetch and push URLs of the repository at `directory`, as git will
    use them: `git remote -v` applies `insteadOf` and `pushInsteadOf`.

    None when it is not a repository (the directory is absent, or git says
    so). Any OTHER git failure — a dubious-ownership refusal, a corrupt
    config — is not evidence of that, so it is Unevaluable."""
    if not directory.is_dir():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), "remote", "-v"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unevaluable(f"could not resolve git remotes: {exc}") from exc
    if result.returncode != 0:
        if "not a git repository" in (result.stderr or "").lower():
            return None
        raise Unevaluable(f"git remote -v failed in {directory} (exit {result.returncode})")
    return [line.split()[1] for line in result.stdout.splitlines() if len(line.split()) > 1]


# Config that sends git somewhere other than the remote URL says: URL
# rewrites (their replacement base is where git goes), ssh commands (a jump
# host), proxies, and submodule URLs (a fetch or pull recurses into
# submodules by default). Read for every directory a network git could run in.
_CONFIG_KEYS = (r"^(url\..*\.(insteadof|pushinsteadof)|core\.(sshcommand|gitproxy)"
                r"|http\.(.*\.)?proxy|remote\..*\.proxy|submodule\..*\.url)$")


def _value_targets(value: str) -> list[str]:
    """Hosts a config or environment value can send git to: the value itself
    (`host:3128`) plus any URL, scp target or remote-tool destination in it
    (`ssh -J jumphost`)."""
    try:
        return [value] + _explicit_targets(value, _flatten(_program(value, _MAX_DEPTH - 1)))
    except Unevaluable:
        return [value]


def _config_targets(directory: Path) -> list[str]:
    """Rewrite bases, ssh commands and proxies git would use in `directory`
    (repository, global and system config). Outside a repository git still
    reads the global and system files."""
    where = directory if directory.is_dir() else Path("/")
    try:
        result = subprocess.run(
            ["git", "-C", str(where), "config", "--get-regexp", _CONFIG_KEYS],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unevaluable(f"could not read git config: {exc}") from exc
    if result.returncode == 1:          # nothing matched
        return []
    if result.returncode != 0:
        raise Unevaluable(f"git config failed in {where} (exit {result.returncode})")
    targets: list[str] = []
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        low = key.lower()
        if low.startswith("url.") and low.endswith(("insteadof", "pushinsteadof")):
            targets.append(key[4:key.rfind(".")])     # the base git rewrites TO
        else:
            targets += _value_targets(value)
    return targets


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
UNKNOWN_DIR = None      # a directory only known at run time
_MAX_DEPTH = 3          # nested `sh -c` levels unpacked before giving up

# Environment that changes which config git reads: with any of these set in
# the command, the remotes and rewrites read here are not the ones git uses.
GIT_CONFIG_ENV = frozenset({
    "HOME", "XDG_CONFIG_HOME", "GIT_CONFIG", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_EXEC_PATH",
})
# Environment that routes git's connection through another host.
GIT_ROUTE_ENV = frozenset({
    "GIT_SSH", "GIT_SSH_COMMAND", "GIT_PROXY_COMMAND", "http_proxy", "https_proxy",
    "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
})
# `-c key=value` keys that decide where git connects.
_C_TARGET_KEY = re.compile(r"^(remote\..*\.(url|pushurl|proxy)|core\.(sshcommand|gitproxy)"
                           r"|http\.(.*\.)?proxy)$", re.IGNORECASE)
_C_REWRITE_KEY = re.compile(r"^url\.(.*)\.(insteadof|pushinsteadof)$", re.IGNORECASE)
_C_INCLUDE_KEY = re.compile(r"^include(if\..*)?\.path$", re.IGNORECASE)


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


# ── shell structure ────────────────────────────────────────────────────────
#
# Which directory a git command runs in is often spelled through a variable:
# `for r in a b; do git -C $r push; done`, `W=/x && cd $W && git pull`. Until
# 2026-09-30 every such command was refused, 66 of them in two days. The
# guard now reads the command's structure — and-or lists, pipelines,
# subshells, loops, conditionals, here-documents — and works out every value
# a variable can have where it is used. A variable is known only where the
# command itself binds it with a literal value, on every path to the use; set
# outside the command, by `read`, `eval`, `export`, a function, arithmetic,
# a glob, a command substitution, or on a path that may be skipped, it is
# unknown and the command is refused exactly as before. It never guesses.
#
# Loops repeat their body. A `for` over n literal words is walked n times, so
# a relative `cd` in it is followed as far as the shell can take it; any
# other loop with a `cd` in it is refused (DatacoreSpec/Guards.lean, §1).

class _ParseError(Exception):
    """The command uses shell syntax this parser does not model."""


_DATA = "__restricted_hosts_guard_heredoc_{}__"
_HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)(-?)\s*(\\?)(['\"]?)([^\s'\"<>;&|()]+)\3")
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_EXPANSION_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_ALL = "*"              # every variable is unknown
_MAX_VALUES = 64        # combinations of variable values followed before giving up
_MAX_DIRS = 256         # candidate directories followed before giving up
# Whether a tilde may be literal (`cd '~/x'`): set per command by offending().
_LITERAL_TILDE = True
# Commands that can turn a directory into a repository by copying files, so
# a directory that is not a repository yet may be one when git runs there.
_COPY_TOOLS = frozenset({
    "cp", "mv", "rsync", "ln", "tar", "gtar", "bsdtar", "unzip", "ditto", "cpio", "pax",
    "install", "7z", "rclone", "scp", "sftp", "gunzip", "bunzip2", "unxz", "patch",
})

# Commands that can bind variables without naming them where this parser
# looks (`eval "$x"`, `source f`, namerefs, `setopt`), or change how words
# expand (IFS). Their presence makes every variable unknown.
_KILL_HEADS = frozenset({
    "eval", "source", ".", "declare", "typeset", "local", "export", "readonly", "let",
    "alias", "nameref", "integer", "float", "trap", "enable", "setopt", "unsetopt",
    "shopt", "emulate", "coproc", "function",
})


def _heredoc_mode(line: str, start: int, quoted: bool) -> str:
    """How a here-document body runs. Fed to a shell (`bash <<EOF`, `ssh
    host <<EOF`) it is a script; otherwise it is data, in which only `$( )`
    and backticks run — and not even those when the delimiter is quoted."""
    words = _tokens(line[:start])
    if any(Path(w).name in SHELLS | {"ssh", "eval", "source", "."} for w in words):
        return "script"
    return "data" if quoted else "expand"


def _mark_heredocs(command: str) -> tuple[str, dict[str, tuple[str, str]]]:
    """The command with each here-document body replaced by a placeholder
    line, and the bodies with how they run. A body is not commands of this
    shell: `W=/x` in one assigns nothing, so it is never read as one."""
    out: list[str] = []
    bodies: dict[str, tuple[str, str]] = {}
    pending: list[tuple[bool, str, str]] = []
    body: list[str] = []

    def close(mode: str) -> None:
        key = _DATA.format(len(bodies))
        bodies[key] = ("\n".join(body), mode)
        out.append(key)
        body.clear()

    for line in command.split("\n"):
        if pending:
            strip, delim, mode = pending[0]
            if (line.lstrip("\t") if strip else line) == delim:
                pending.pop(0)
                close(mode)
                continue
            body.append(line)
            continue
        out.append(line)
        pending = [(m.group(1) == "-", m.group(4),
                    _heredoc_mode(line, m.start(), bool(m.group(2) or m.group(3))))
                   for m in _HEREDOC_RE.finditer(line)]
    while pending:
        close(pending.pop(0)[2])
    return "\n".join(out), bodies


def _split_ops(token: str) -> list[str]:
    ops, i = [], 0
    while i < len(token):
        two = token[i:i + 2]
        if two in ("&&", "||", ";;", "|&"):
            ops.append(two)
            i += 2
        elif two in ("<(", ">("):
            ops.append("psub")
            i += 2
        elif token[i] in "<>":
            i += 1          # a redirection glued to an operator; its target follows
        else:
            ops.append(token[i])
            i += 1
    return ops


def _lex(text: str) -> list[tuple[str, str]]:
    """Words and operators. Raises ValueError on unbalanced quotes."""
    text = text.replace("\\\n", " ").replace("\n", " ; ")
    lex = shlex.shlex(text, posix=True, punctuation_chars=";&|()<>")
    lex.whitespace_split = True
    out: list[tuple[str, str]] = []
    for token in lex:
        chars = set(token)
        if token and chars <= set(";&|()<>") and (chars & set("()") or not chars & set("<>")):
            out.extend(("op", op) for op in _split_ops(token))
        else:
            out.append(("w", token))
    return out


class _Parser:
    """Recursive descent over the grammar the guard needs: lists, and-or
    lists, pipelines, `( )`, `{ }`, `for`, `while`/`until`, `if`, simple
    commands with `$( )` / `<( )` substitutions. Anything else (`case`,
    functions, zsh's `for x (…)`) raises _ParseError."""

    STOPS = frozenset({"do", "done", "then", "fi", "else", "elif", "}", "esac"})

    def __init__(self, toks):
        self.toks, self.i = toks, 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def is_op(self, *ops):
        kind, value = self.peek()
        return kind == "op" and value in ops

    def is_word(self, *words):
        kind, value = self.peek()
        return kind == "w" and value in words

    def expect_word(self, word):
        if not self.is_word(word):
            raise _ParseError(f"expected {word}")
        self.i += 1

    def expect_close(self):
        if not self.is_op(")"):
            raise _ParseError("expected )")
        self.i += 1

    def skip_newlines(self):
        while self.is_op(";"):
            self.i += 1

    def parse(self):
        body = self.parse_list(frozenset())
        if self.peek()[0] is not None:
            raise _ParseError(f"unexpected {self.peek()[1]}")
        return body

    def parse_list(self, stop_words, stop_op=None):
        items = []
        while True:
            self.skip_newlines()
            kind, value = self.peek()
            if kind is None or (kind == "op" and value == stop_op):
                return items
            if kind == "w" and (value in stop_words or value in self.STOPS):
                return items
            start = self.i
            and_or = self.parse_and_or()
            if self.i == start:
                raise _ParseError("no progress")
            term = ";"
            if self.is_op(";", "&"):
                term = self.peek()[1]
                self.i += 1
            elif self.is_op(";;"):
                raise _ParseError("case")
            items.append((and_or, term))

    def parse_and_or(self):
        parts = [(None, self.parse_pipeline())]
        while self.is_op("&&", "||"):
            op = self.peek()[1]
            self.i += 1
            self.skip_newlines()
            parts.append((op, self.parse_pipeline()))
        return parts

    def parse_pipeline(self):
        if self.is_word("!"):
            self.i += 1
        cmds = [self.parse_command()]
        while self.is_op("|", "|&"):
            self.i += 1
            self.skip_newlines()
            cmds.append(self.parse_command())
        return cmds

    def trailing(self):
        """Redirections after a compound command (`done < f`, `) 2>&1`)."""
        while self.peek()[0] == "w" and self.peek()[1] not in self.STOPS:
            self.i += 1

    def parse_command(self):
        kind, value = self.peek()
        if kind == "op" and value == "(":
            self.i += 1
            body = self.parse_list(frozenset(), ")")
            self.expect_close()
            self.trailing()
            return ("sub", body)
        if kind != "w":
            raise _ParseError(f"unexpected {value}")
        if value == "{":
            self.i += 1
            body = self.parse_list(frozenset({"}"}))
            self.expect_word("}")
            self.trailing()
            return ("brace", body)
        if value == "for":
            self.i += 1
            kind, var = self.peek()
            if kind != "w" or not _NAME_RE.fullmatch(var or ""):
                raise _ParseError("for")
            self.i += 1
            words, subs = None, []
            if self.is_word("in"):
                self.i += 1
                words, subs = self.collect_words()
            self.skip_newlines()
            self.expect_word("do")
            body = self.parse_list(frozenset({"done"}))
            self.expect_word("done")
            self.trailing()
            return ("for", var, words, subs, body)
        if value in ("while", "until"):
            self.i += 1
            cond = self.parse_list(frozenset({"do"}))
            self.expect_word("do")
            body = self.parse_list(frozenset({"done"}))
            self.expect_word("done")
            self.trailing()
            return ("while", cond, body)
        if value == "if":
            self.i += 1
            parts = [self.parse_list(frozenset({"then"}))]
            self.expect_word("then")
            parts.append(self.parse_list(frozenset({"elif", "else", "fi"})))
            while self.is_word("elif"):
                self.i += 1
                parts.append(self.parse_list(frozenset({"then"})))
                self.expect_word("then")
                parts.append(self.parse_list(frozenset({"elif", "else", "fi"})))
            if self.is_word("else"):
                self.i += 1
                parts.append(self.parse_list(frozenset({"fi"})))
            self.expect_word("fi")
            self.trailing()
            return ("if", parts)
        if value in ("case", "select", "function", "coproc", "repeat", "foreach"):
            raise _ParseError(value)
        if value.startswith(_DATA.format("")[:-2]):
            self.i += 1
            return ("data", value)
        words, subs = self.collect_words()
        return ("simple", words, subs)

    def collect_words(self):
        words, subs = [], []
        while True:
            kind, value = self.peek()
            if kind == "w":
                self.i += 1
                words.append(value)
                if value.endswith("$") and self.is_op("("):       # $( … )
                    self.i += 1
                    subs.append(self.parse_list(frozenset(), ")"))
                    self.expect_close()
                continue
            if kind == "op" and value == "psub":                  # <( … )
                self.i += 1
                subs.append(self.parse_list(frozenset(), ")"))
                self.expect_close()
                continue
            if kind == "op" and value == "(":
                raise _ParseError("function definition or array")
            return words, subs


def _walk_ast(lst, visit):
    """Call visit(node) on every command node in a parsed list, depth first."""
    for and_or, _ in lst:
        for _, pipeline in and_or:
            for node in pipeline:
                visit(node)
                kind = node[0]
                if kind in ("sub", "brace"):
                    _walk_ast(node[1], visit)
                elif kind == "for":
                    for sub in node[3]:
                        _walk_ast(sub, visit)
                    _walk_ast(node[4], visit)
                elif kind == "while":
                    _walk_ast(node[1], visit)
                    _walk_ast(node[2], visit)
                elif kind == "if":
                    for part in node[1]:
                        _walk_ast(part, visit)
                elif kind == "simple":
                    for sub in node[2]:
                        _walk_ast(sub, visit)


def _standalone(node) -> bool:
    """A simple command that only assigns (`W=/x`, `A=1 B=2`)."""
    return node[0] == "simple" and bool(node[1]) and all(_is_assignment(w) for w in node[1])


def _bound(lst) -> set[str]:
    """Every variable a parsed list can bind in the forms the parser reads."""
    names: set[str] = set()

    def visit(node):
        if _standalone(node):
            names.update(w.partition("=")[0] for w in node[1])
        elif node[0] == "for":
            names.add(node[1])
    _walk_ast(lst, visit)
    return names


def _clobbered(text: str, ast, inherited=()) -> set[str] | str:
    """Variables this command may bind in a way the parser does not follow.

    Counts every place the raw text could bind a name — `NAME=`, `NAME+=`,
    `NAME[`, or the bare name as a word (`read NAME`, `for NAME`, `printf -v
    NAME`) — and compares with the bindings the parser recognised. Any excess
    means an unrecognised binding: that name is unknown everywhere."""
    if re.search(r"\(\(|\$\[|\$\{[^A-Za-z_]|\bIFS\b", text):
        return _ALL
    assigned: dict[str, int] = {}
    looped: dict[str, int] = {}
    heads: set[str] = set()

    def visit(node):
        if _standalone(node):
            for w in node[1]:
                name = w.partition("=")[0]
                assigned[name] = assigned.get(name, 0) + 1
        elif node[0] == "for":
            looped[node[1]] = looped.get(node[1], 0) + 1
        elif node[0] == "simple":
            _, argv = _invocation(node[1])
            if argv:
                heads.add(argv[0] if argv[0] == "." else Path(argv[0]).name)
    _walk_ast(ast, visit)
    if heads & _KILL_HEADS:
        return _ALL
    quoted, outside = _single_quoted(text)
    out: set[str] = set()
    before = r"(?:^|(?<=[\s;&|(`'\"]))"
    for name in set(assigned) | set(looped) | set(inherited) | {"HOME"}:
        n = re.escape(name)
        binds = len(re.findall(before + n + r"(?:\+?=|\[)", text))
        # the bare name as a word (`read d`), or a whole quoted word (`read "d"`)
        words = len(re.findall(r"(?:^|(?<=[\s;&|(`]))" + n + r"(?=$|[\s;&|)`])", text)) \
            + len(re.findall(r"(?:^|(?<=[\s;&|(`]))(['\"])" + n + r"\1", text))
        ref = r"\$\{?" + n + r"(?![A-Za-z0-9_])"
        # `${NAME=x}` / `${NAME:=x}` bind it; `'$NAME'` and `\$NAME` are the
        # literal text, not the value, and parsing lost the quotes
        if binds > assigned.get(name, 0) or words > looped.get(name, 0) \
                or re.search(r"\$\{" + n + r":{0,2}=", text) \
                or re.search(r"\\" + ref, outside) \
                or any(re.search(ref, region) for region in quoted):
            out.add(name)
    return out


def _single_quoted(text: str) -> tuple[list[str], str]:
    """(the single-quoted regions of `text`, the text outside them). Inside
    double quotes a `'` is an ordinary character."""
    regions: list[str] = []
    outside: list[str] = []
    i, double = 0, False
    while i < len(text):
        c = text[i]
        if c == "\\":
            outside.append(text[i:i + 2])
            i += 2
            continue
        if c == '"':
            double = not double
        elif c == "'" and not double:
            end = text.find("'", i + 1)
            end = len(text) if end < 0 else end
            regions.append(text[i + 1:end])
            outside.append(" ")
            i = end + 1
            continue
        outside.append(c)
        i += 1
    return regions, "".join(outside)


def _expand(word: str, env: dict, clobbered) -> list[str] | None:
    """Every value `word` can take, given the variable values in `env`; None
    when any part is only known at run time. A value that the shell would
    split into words or glob is unknown too. (`'$W'` is the literal text; a
    name that appears single-quoted or escaped is never resolved, see
    `_clobbered`.)"""
    parts = _EXPANSION_RE.split(word)
    options: list[str] = [""]
    for i in range(0, len(parts), 3):
        literal = parts[i]
        if "$" in literal or "`" in literal:
            return None
        options = [o + literal for o in options]
        if i + 1 >= len(parts):
            break
        name = parts[i + 1] or parts[i + 2]
        if clobbered == _ALL or name in clobbered or name not in env:
            return None
        options = [o + v for o in options for v in env[name]]
        if len(options) > _MAX_VALUES:
            return None
    if any(re.search(r"[\s*?\[]", o) for o in options):
        return None
    return list(dict.fromkeys(options))


def _assign_values(value: str, env: dict, clobbered) -> tuple | None:
    """What `NAME=value` stores. A leading tilde expands in an assignment;
    when the command quotes a tilde somewhere, the literal reading is kept
    too, because parsing lost which one was quoted."""
    if "$" in value or "`" in value:
        values = _expand(value, env, clobbered)
    else:
        values = [value]
    if values is None:
        return None
    out = []
    for v in values:
        if not v.startswith("~") or _LITERAL_TILDE:
            out.append(v)
        if v.startswith("~"):
            try:
                out.append(str(Path(v).expanduser()))
            except RuntimeError:
                return None
    return tuple(dict.fromkeys(out))


def _join(a: dict, b: dict) -> dict:
    return {k: tuple(dict.fromkeys(a[k] + b[k])) for k in a if k in b}


def _havoc(env: dict, names: set[str]) -> dict:
    return {k: v for k, v in env.items() if k not in names}


class _Ctx:
    def __init__(self, depth: int, bodies: dict, clobbered):
        self.depth, self.bodies, self.clobbered = depth, bodies, clobbered


def _eval_list(lst, env, ctx):
    items: list = []
    for and_or, term in lst:
        its, after = _eval_and_or(and_or, env, ctx)
        items += its
        env = env if term == "&" else after        # a background list binds nothing here
    return items, env


def _eval_and_or(and_or, env, ctx):
    """Only the first pipeline of `a && b || c` surely runs. While every
    operator so far is `&&`, each later one runs only after all before it
    succeeded; after an `||`, any of them may have been skipped."""
    items: list = []
    strong = weak = env
    pure = True
    for index, (op, pipeline) in enumerate(and_or):
        if op == "||":
            pure = False
        entry = strong if pure else weak
        its, out = _eval_pipeline(pipeline, entry, ctx)
        items += its
        if index == 0:
            strong = weak = out
        else:
            strong = out if pure else strong
            weak = _join(weak, out)
    return items, weak


def _eval_pipeline(cmds, env, ctx):
    if len(cmds) == 1:
        return _eval_command(cmds[0], env, ctx)
    items: list = []
    out = env
    for node in cmds:
        its, out = _eval_command(node, env, ctx)
        items += its
    # bash runs every member in a subshell; zsh runs the last one here
    return items, _join(env, out)


def _eval_command(node, env, ctx):
    kind = node[0]
    if kind == "simple":
        _, words, subs = node
        items = []
        for sub in subs:
            items += _eval_list(sub, env, ctx)[0]
        if _standalone(node):
            for w in words:
                name, _, value = w.partition("=")
                values = None if ctx.clobbered == _ALL or name in ctx.clobbered \
                    else _assign_values(value, env, ctx.clobbered)
                env = {**env, name: values} if values is not None else _havoc(env, {name})
            return items + _nested_in_words(words, ctx, env), env
        return items + _simple_items(words, env, ctx), env
    if kind == "sub":
        return _eval_list(node[1], env, ctx)[0], env
    if kind == "brace":
        return _eval_list(node[1], env, ctx)
    if kind == "for":
        _, var, words, subs, body = node
        items = []
        for sub in subs:
            items += _eval_list(sub, env, ctx)[0]
        after = _havoc(env, _bound(body) | {var})
        entry = after
        count = len(words) if words is not None and not subs else None
        if count is not None:
            values: list[str] = []
            for w in words:
                expanded = _expand(w, env, ctx.clobbered) if ("$" in w or "`" in w) else \
                    (None if re.search(r"[*?\[]", w) else [w])
                if expanded is None:
                    values = None
                    break
                values += expanded
            if values is not None and ctx.clobbered != _ALL and var not in ctx.clobbered:
                entry = {**after, var: tuple(dict.fromkeys(values))}
        body_items = _eval_list(body, entry, ctx)[0]
        return items + [("loop", count, body_items)], after
    if kind == "while":
        _, cond, body = node
        entry = _havoc(env, _bound(cond) | _bound(body))
        items = _eval_list(cond, entry, ctx)[0] + _eval_list(body, entry, ctx)[0]
        return [("loop", None, items)], entry
    if kind == "if":
        names: set[str] = set()
        for part in node[1]:
            names |= _bound(part)
        entry = _havoc(env, names)
        items = []
        for part in node[1]:
            items += _eval_list(part, entry, ctx)[0]
        return items, entry
    if kind == "data":
        text, mode = ctx.bodies.get(node[1], ("", "data"))
        if mode == "script":           # a shell reads it: its lines are commands
            return _nested(text, ctx), env
        if mode == "expand":           # unquoted delimiter: substitutions run here
            return _nested_in_words([text], ctx, env), env
        return [], env
    raise _ParseError(kind)


def _nested(body: str, ctx, env=None) -> list:
    """Commands in a string that a shell runs: `sh -c`'s argument (a new
    process: env None, nothing inherited) or a substitution (`env`: it sees
    this shell's variables)."""
    if ctx.depth >= _MAX_DEPTH:
        raise Unevaluable("shell nesting too deep to evaluate")
    return _program(body, ctx.depth + 1, env)


def _nested_in_words(words, ctx, env=None) -> list:
    """A substitution inside a quoted word still runs: "$(git push)"."""
    items: list = []
    for token in words:
        if "$(" in token or "`" in token:
            items += _nested(token.replace("`", " ; ").replace("$(", " ; ").replace(")", " ; "),
                             ctx, env)
    return items


def _strip_redirects(words: list[str]) -> list[str]:
    """Drop `>`, `2>&`, `<<` … and their targets: they are not arguments."""
    out, skip = [], False
    for w in words:
        if skip:
            skip = False
            continue
        if w and set(w) <= set("<>&|") and set(w) & set("<>"):
            skip = True
            continue
        out.append(w)
    return out


def _simple_items(words, env, ctx) -> list:
    items = _nested_in_words(words, ctx, env)
    words = _strip_redirects(words)
    assigns, argv = _invocation([t.strip("`") for t in words if t.strip("`")])
    if not argv:
        return items
    head = Path(argv[0]).name
    if head in SHELLS and "-c" in argv[1:]:
        k = argv.index("-c", 1)
        if k + 1 < len(argv):
            items += _nested(argv[k + 1], ctx)
        return items
    if head == "eval":
        return items + _nested(" ".join(argv[1:]), ctx)
    items.append(("inv", assigns, argv, env))
    return items


def _flat_program(command: str, depth: int) -> list:
    """The pre-2026-09-30 reading, for syntax the parser does not model:
    every simple command in order, no variable known. A loop keyword anywhere
    makes the whole command a loop of unknown length, so a `cd` in it is
    never followed only once."""
    ctx = _Ctx(depth, {}, _ALL)
    items: list = []
    looped = False
    for raw in _simple_commands(command):
        if raw and raw[0] in ("for", "while", "until", "select", "repeat", "foreach"):
            looped = True
        items += _simple_items(raw, {}, ctx)
    return [("loop", None, items)] if looped else items


def _program(command: str, depth: int = 0, env: dict | None = None) -> list:
    """Every simple command in `command`, in order, as items:
    ("inv", assigns, argv, env) — env holds each variable's possible values —
    and ("loop", count, items) — count is the exact number of passes, or None
    when only the run can tell. `env` is what an enclosing shell passes in."""
    marked, bodies = _mark_heredocs(command)
    try:
        ast = _Parser(_lex(marked)).parse()
    except (ValueError, _ParseError):
        return _flat_program(command, depth)
    start = dict(env) if env is not None else {"HOME": (str(Path.home()),)}
    # here-document bodies bind nothing in this shell, so they are not scanned
    ctx = _Ctx(depth, bodies, _clobbered(marked, ast, start))
    start = {} if ctx.clobbered == _ALL else _havoc(start, ctx.clobbered)
    return _eval_list(ast, start, ctx)[0]


def _flatten(program: list) -> list[tuple[dict, list[str]]]:
    out: list[tuple[dict, list[str]]] = []
    for item in program:
        if item[0] == "inv":
            out.append((item[1], item[2]))
        else:
            out += _flatten(item[2])
    return out


def _invocations(command: str, depth: int = 0) -> list[tuple[dict, list[str]]]:
    """Every simple command in `command`, in order, with `sh -c '…'`,
    `eval '…'` and command-substitution bodies unpacked in place."""
    return _flatten(_program(command, depth))


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


def _resolve_dirs(base, arg: str, env: dict, clobbered, is_cd: bool) -> list:
    """Every directory `cd arg` (or `git -C arg`) from `base` can land in, or
    [UNKNOWN_DIR] when only the shell knows (`cd $REPO` with REPO set outside
    the command, `cd -`, a glob, a command substitution)."""
    if base is UNKNOWN_DIR or arg == "-" or re.search(r"[*?\[]", arg):
        return [UNKNOWN_DIR]
    values = _expand(arg, env, clobbered) if ("$" in arg or "`" in arg) else [arg]
    if values is None:
        return [UNKNOWN_DIR]
    out: list = []
    for value in values:
        if is_cd and os.environ.get("CDPATH") and not value.startswith(("/", "./", "../", "~")):
            return [UNKNOWN_DIR]            # `cd name` may search CDPATH
        readings = [value]
        if value.startswith("~"):
            try:
                readings = [str(Path(value).expanduser())] + ([value] if _LITERAL_TILDE else [])
            except RuntimeError:
                return [UNKNOWN_DIR]
        for r in readings:
            target = Path(r)
            out.append(target if target.is_absolute() else Path(base) / target)
    return out


def _git_subcommand(argv: list[str]):
    """(subcommand, `-C` paths, `--git-dir` paths, `-c` settings, rest) of a git argv."""
    sub, chdirs, gitdirs, settings, rest = None, [], [], [], []
    i = 1
    while i < len(argv):
        token = argv[i]
        if token in GIT_OPTS_WITH_ARG:
            value = argv[i + 1] if i + 1 < len(argv) else ""
            if token == "-C":
                chdirs.append(value)
            elif token == "--git-dir":
                gitdirs.append(value)
            elif token == "-c":
                settings.append(value)
            elif token == "--config-env":
                settings.append(value.split("=", 1)[0] + "=$")     # value from the environment
            i += 2
            continue
        if token.startswith("--git-dir="):
            gitdirs.append(token.split("=", 1)[1])
        if token.startswith("--config-env="):
            settings.append(token.split("=", 1)[1].split("=", 1)[0] + "=$")
        if token.startswith("-"):
            i += 1
            continue
        sub = token
        rest = argv[i + 1:]
        break
    return sub, chdirs, gitdirs, settings, rest


def _setting_targets(key: str, value: str) -> list[str]:
    """Where a git setting sends git, or Unevaluable when it cannot be known."""
    rewrite = _C_REWRITE_KEY.match(key)
    if rewrite:
        return [rewrite.group(1)]
    if _C_INCLUDE_KEY.match(key):
        raise Unevaluable("git reads more config from a file named in the command")
    if _C_TARGET_KEY.match(key):
        if "$" in value or "`" in value:
            raise Unevaluable(f"git's {key} is only known at run time")
        return _value_targets(value)
    return []


def _config_write_targets(rest: list[str]) -> list[str]:
    """Targets a `git config` write in the command stores for a later git."""
    args = [a for a in rest if not a.startswith("-")]
    if args[:1] == ["set"]:
        args = args[1:]
    if len(args) < 2 or args[0] in ("get", "unset", "list", "get-regexp", "rename-section",
                                    "remove-section", "edit"):
        return []
    return _setting_targets(args[0], args[1])


# Options of each network subcommand that take no value. Any other option
# before the first positional argument may have consumed it, so the
# repository could be a later argument.
_FLAGS = {
    "push": {"-u", "--set-upstream", "-f", "--force", "-q", "--quiet", "-v", "--verbose", "--all",
             "--tags", "--follow-tags", "--atomic", "-n", "--dry-run", "--porcelain", "-d",
             "--delete", "--mirror", "--prune", "--thin", "--no-thin",
             "--progress", "--force-with-lease", "--force-if-includes"},
    "fetch": {"-q", "--quiet", "-v", "--verbose", "--all", "--tags", "--no-tags", "-p", "--prune",
              "-P", "--prune-tags", "-f", "--force", "--progress", "--unshallow", "--update-shallow",
              "-n", "--dry-run", "-a", "--append", "-k", "--keep", "--recurse-submodules",
              "--no-recurse-submodules", "--atomic", "-t"},
    "pull": {"-q", "--quiet", "-v", "--verbose", "--all", "--tags", "--no-tags", "-p", "--prune",
             "-f", "--force", "--ff-only", "--ff", "--no-ff", "--rebase", "-r", "--no-rebase",
             "--no-edit", "--edit", "--autostash", "--no-autostash", "--progress", "--squash",
             "--no-squash", "--commit", "--no-commit", "--stat", "--no-stat", "-n",
             "--recurse-submodules", "--no-recurse-submodules", "--unshallow"},
    "ls-remote": {"-q", "--quiet", "-h", "--heads", "-t", "--tags", "-b", "--branches", "--refs",
                  "--get-url", "--exit-code", "--symref"},
    "clone": {"-q", "--quiet", "-v", "--verbose", "-n", "--no-checkout", "--bare", "--mirror",
              "-l", "--local", "--no-local", "--no-hardlinks", "-s", "--shared", "--recursive",
              "--recurse-submodules", "--single-branch", "--no-single-branch", "--no-tags",
              "--shallow-submodules", "--sparse", "--progress", "--dissociate"},
}


def _repository_args(sub: str, rest: list[str], env: dict) -> list[str]:
    """Every value an argument that can name a repository (a URL, `host:path`)
    can take. An argument only known at run time is Unevaluable, unless it is
    provably a refspec: after the repository, which is the first positional
    argument when every option before it takes no value (`git push origin
    HEAD:$BRANCH`)."""
    flags = _FLAGS.get(sub)
    targets: list[str] = []
    shifted, seen = False, False
    for arg in rest:
        if arg.startswith("-") and not arg.startswith(("--repo=", "--remote=")):
            if not seen and "=" not in arg and (flags is None or arg not in flags):
                shifted = True
            continue
        value = arg.split("=", 1)[1] if arg.startswith("--") else arg
        values = _expand(value, env, set()) if ("$" in value or "`" in value) else [value]
        if values is None:
            if flags is None or shifted or not seen or arg.startswith("--"):
                raise Unevaluable(f"git's repository argument `{arg}` is only known at run time")
        else:
            targets += values
        seen = True
    return targets


def _local_remote_command(sub: str, rest: list[str]) -> bool:
    """`git remote`, `-v`, `get-url`, `rename`, `remove`, `set-branches` and
    `show -n` only read or edit local config; they open no connection."""
    if sub != "remote":
        return False
    action = next((a for a in rest if not a.startswith("-")), None)
    if action in (None, "get-url", "rename", "remove", "rm", "set-branches"):
        return True
    return action == "show" and "-n" in rest


def _git_targets(program: list, cwd: str | None) -> list[str]:
    """Remote URLs and routes any network git subcommand in the command could
    contact.

    Which directory a git command runs in depends on which earlier `cd`s took
    effect, and a `cd` inside `( … )` or a pipeline does not persist. So this
    tracks EVERY directory the shell could be in — the start directory plus
    each `cd` applied or not, each with every value its variables can have —
    and checks the remotes of all of them. That over-approximates on purpose:
    checking one guess is how `cd repo && git push` walked past this guard
    (DatacoreSpec/Guards.lean, `git_candidates_sound`, `resolved_candidates_sound`)."""
    start = Path(cwd) if cwd else Path.cwd()
    urls: list[str] = []
    stored: list[str] = []          # set by `git config` or `git worktree add`, for a later git
    seen: dict[str, tuple] = {}
    state = {"network": False, "dynamic": None, "unknown": None}
    copies = any(Path(argv[0]).name in _COPY_TOOLS for _, argv in _flatten(program))

    def reach(directory: Path) -> list[str]:
        """What git contacts from `directory`. A directory that does not exist
        yet is made by the command, and git there uses the repository around
        it. When the command also copies files, a directory that is not a
        repository now may be one by then (`cp -R repo x && git -C x push`)."""
        base = directory
        while not base.is_dir() and base.parent != base:
            base = base.parent
        key = str(base)
        if key not in seen:
            seen[key] = (_git_remote_urls(base), _config_targets(base))
        remotes, config = seen[key]
        if copies and (remotes is None or base != directory):
            raise Unevaluable(f"{directory} is not a repository now but may be one when git runs: "
                              "the command copies files")
        return (remotes or []) + config

    def has_cd(items) -> bool:
        return any((it[0] == "inv" and Path(it[2][0]).name in ("cd", "pushd"))
                   or (it[0] == "loop" and has_cd(it[2])) for it in items)

    def resolve(dirs, arg, env, is_cd):
        out = list(dict.fromkeys(r for d in dirs for r in _resolve_dirs(d, arg, env, set(), is_cd)))
        if len(out) > _MAX_DIRS:
            out = [UNKNOWN_DIR]
            state["unknown"] = state["unknown"] or "too many possible directories"
        if UNKNOWN_DIR in out and state["unknown"] is None:
            state["unknown"] = "a command substitution" if arg.endswith("$") else arg
        return out

    def walk(items, dirs):
        for item in items:
            if item[0] == "loop":
                count, body = item[1], item[2]
                if count is None:
                    if has_cd(body):
                        # the loop may run any number of times, each pass
                        # starting where the last ended
                        dirs = list(dict.fromkeys(dirs + [UNKNOWN_DIR]))
                        state["unknown"] = state["unknown"] or "a loop of unknown length"
                    dirs = walk(body, dirs)
                else:
                    for _ in range(count if has_cd(body) else min(count, 1)):
                        dirs = walk(body, dirs)
                continue
            _, assigns, argv, env = item
            head = Path(argv[0]).name
            if head in ("cd", "pushd"):
                arg = next((t for t in argv[1:] if not t.startswith("-") or t == "-"), "~")
                dirs = list(dict.fromkeys(dirs + resolve(dirs, arg, env, True)))   # ordered union
                if len(dirs) > _MAX_DIRS:
                    dirs = [UNKNOWN_DIR]
                    state["unknown"] = state["unknown"] or "too many possible directories"
                continue
            if head != "git":
                continue
            sub, chdirs, gitdirs, settings, rest = _git_subcommand(argv)
            if sub == "config":
                try:
                    stored.extend(_config_write_targets(rest))
                except Unevaluable as exc:
                    state["dynamic"] = str(exc)
                continue
            if sub == "worktree" and rest[:1] == ["add"]:
                # a new worktree shares the config of the repository it came from
                source = dirs
                for c in chdirs:
                    source = resolve(source, c, env, False)
                for d in source:
                    if d is UNKNOWN_DIR:
                        state["dynamic"] = "a worktree is added from a directory only known at run time"
                        continue
                    try:
                        stored.extend(reach(Path(d)))
                    except Unevaluable as exc:
                        state["dynamic"] = str(exc)
                continue
            if sub not in GIT_NETWORK_SUBCOMMANDS or _local_remote_command(sub, rest):
                continue
            state["network"] = True
            for name in set(assigns) & GIT_CONFIG_ENV:
                raise Unevaluable(f"{name} is set for git, so its config is not the one read here")
            for name in set(assigns) & GIT_ROUTE_ENV:
                if "$" in assigns[name] or "`" in assigns[name]:
                    raise Unevaluable(f"{name} is only known at run time")
                urls.extend(_value_targets(assigns[name]))
            for setting in settings:
                key, _, value = setting.partition("=")
                urls.extend(_setting_targets(key, value))
            urls.extend(_repository_args(sub, rest, env))
            here = dirs
            for c in chdirs:
                here = resolve(here, c, env, False)
            extra = [assigns["GIT_DIR"]] if "GIT_DIR" in assigns else []
            for g in gitdirs + extra:
                here = here + resolve(here, g, env, False)
            for d in here:
                if d is UNKNOWN_DIR:
                    raise Unevaluable(
                        f"git runs in a directory only known at run time ({state['unknown']}); "
                        "name the directories in the command, e.g. "
                        "`for r in ~/a ~/b; do git -C \"$r\" pull; done`")
                urls.extend(reach(Path(d)))
        return dirs

    walk(program, [start])
    if not state["network"]:
        return urls
    if state["dynamic"]:
        raise Unevaluable(state["dynamic"])
    return urls + stored


def _environment_targets(command: str, invocations) -> list[str]:
    """Routes set for the whole shell: `export GIT_SSH_COMMAND=…` in the
    command, or already in the environment git inherits."""
    if not any(Path(argv[0]).name == "git" and _git_subcommand(argv)[0] in GIT_NETWORK_SUBCOMMANDS
               for _, argv in invocations):
        return []
    targets = [v for k, v in os.environ.items() if k in GIT_ROUTE_ENV and v]
    for _, argv in invocations:
        for token in argv[1:]:
            name, eq, value = token.partition("=")
            if not eq:
                continue
            if name in GIT_CONFIG_ENV:
                raise Unevaluable(f"{name} is set in the command, so git's config is not the one read here")
            if name in GIT_ROUTE_ENV:
                if "$" in value or "`" in value:
                    raise Unevaluable(f"{name} is only known at run time")
                targets.append(value)
    return [t for v in targets for t in _value_targets(v)]


def offending(command: str, config: dict, cwd: str | None = None) -> str | None:
    """The restricted target this command would reach, if any."""
    global _LITERAL_TILDE
    for network in config["networks"]:
        if network in command:
            return network.rstrip(".") + ".x"
    _LITERAL_TILDE = re.search(r"['\"\\]~", command) is not None

    program = _program(command)
    invocations = _flatten(program)
    targets = (_explicit_targets(command, invocations) + _environment_targets(command, invocations)
               + _git_targets(program, cwd))

    for target in targets:
        # a remote URL read from git config is matched like the command text
        for network in config["networks"]:
            if network in target:
                return network.rstrip(".") + ".x"
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
    if not isinstance(payload, dict):
        return 0
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if tool_name == "Bash":
        command = str(tool_input.get("command", "")) if isinstance(tool_input, dict) else ""
    elif tool_name in URL_TOOLS:
        # A tool that opens a connection from this machine reaches a host just
        # as ssh does (MEM-01). Until 2026-09-26 only Bash was inspected, so
        # WebFetch http://<restricted>/ was never seen. Its URL is the target.
        command = str(tool_input.get("url", "")) if isinstance(tool_input, dict) else ""
    else:
        return 0
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
