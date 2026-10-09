"""Say what a failed check IS: where it ran, what it ran, and why it failed.

Owner, 2026-10-02: "Errors should say what they are."

Every repair on the box failed with

    python3: can't open file '/tmp/check-xxxx/verify/.datacore/lib/jobs/fix_check.py':
    [Errno 2] No such file or directory

and a reliability report read it as "the checker runs from an old snapshot".
The real cause was that the claim loop ran the check in a temporary copy of the
2-datacore space, a separate repository that has no `.datacore/lib/` at all.
The raw line named neither the repository nor the commit nor the reason, so the
reader guessed. A guess that reads like a diagnosis costs more than silence.

`diagnose()` turns a failed command into one plain sentence:

    the check could not pass in a temporary copy of 2-datacore
    (git@github.com:datacore-one/2-datacore.git) at commit 1a2b3c4d5e:
    `python3 .datacore/lib/jobs/fix_check.py ...` -- this repository (2-datacore) has
    no .datacore/lib; checks must run from the installation's copy
    ($DATACORE_ROOT/.datacore/lib/jobs/fix_check.py) (raw: python3: can't open ...)

Causes it detects and says directly: a missing file (and whether the repository
lacks .datacore/lib, the commit predates the file, or the path belongs to the
installation rather than this repository), a missing program, a timeout, a
kill (most often out of memory), and an unreachable host. Anything else is
reported with its exit status and the last line the command printed. The raw
line is always kept at the end, in brackets, so nothing is lost.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_MISSING_FILE = (
    re.compile(r"can't open file '([^']+)'"),
    re.compile(r"No such file or directory: '([^']+)'"),
    re.compile(r"(?:^|\s)([^\s:]+): No such file or directory"),
    re.compile(r"cannot access '([^']+)': No such file"),
)
_NOT_FOUND = (
    re.compile(r"(?:^|\s)([^\s:]+): command not found"),
    re.compile(r"sh: (?:line )?\d+: ([^\s:]+): (?:command )?not found"),
    re.compile(r"(?:^|\s)([^\s:]+): not found"),
)
_UNREACHABLE = (
    re.compile(r"Could not resolve hostname ([^\s:]+)"),
    re.compile(r"Could not resolve host: ([^\s;,']+)"),
    re.compile(r"connect to host (\S+) port \d+: ([^\n]+)"),
    re.compile(r"Failed to connect to (\S+)"),
)
_UNREACHABLE_BARE = ("No route to host", "Network is unreachable", "Connection refused",
                     "Name or service not known", "Temporary failure in name resolution")
_KILLED = (-9, 137)
_ENOSPC = ("No space left on device", "Disk quota exceeded", "[Errno 28]")
#: Below this much free space a disk is full for every practical purpose: git
#: cannot write a pack, the ledger cannot append, python cannot write state.
FULL_FREE_BYTES = 64 * 2**20


def _this_host() -> str:
    import socket
    return ((os.environ.get("DATACORE_ACTOR") or "").strip()
            or socket.gethostname().split(".")[0].lower() or "this host")


def disk_full(path=None, said: str = "") -> str | None:
    """'disk full on <host>, N% used (<mount>)' when the disk is full, else None.

    Fleet sim 2026-10-03 (break 4): a full disk on the overnight host read as
    "fetch failed (offline?)" and "projection could not be verified
    (RuntimeError)". The owner was told the host was offline. The disk is
    measured, and ENOSPC text in `said` counts as full even when it cannot be.
    """
    path = Path(path or Path.home())
    while not path.exists() and path != path.parent:
        path = path.parent
    hit = any(marker in (said or "") for marker in _ENOSPC)
    try:
        import shutil
        usage = shutil.disk_usage(path)
    except OSError:
        return f"disk full on {_this_host()} ({path}: No space left on device)" if hit else None
    pct = round(100 * usage.used / usage.total) if usage.total else 100
    if not hit and usage.free >= FULL_FREE_BYTES:
        return None
    return f"disk full on {_this_host()}, {pct}% used ({path}, {usage.free // 2**20} MB free)"


def _text(v) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v or ""


def _git(repo, *args) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return p.returncode, (p.stdout or "").strip()


def describe_repo(repo, sha: str = "") -> str:
    """'2-datacore (git@github.com:o/2-datacore.git) at commit 1a2b3c4d5e'."""
    if repo is None:
        return "this host"
    rc, top = _git(repo, "rev-parse", "--show-toplevel")
    name = Path(top).name if rc == 0 and top else Path(str(repo)).name
    _, origin = _git(repo, "remote", "get-url", "origin")
    out = name + (f" ({origin})" if origin else " (no origin remote)")
    return out + (f" at commit {sha[:10]}" if sha else "")


def _relative(path: str, cwd) -> str:
    p = Path(path)
    if not p.is_absolute():
        return path
    for base in (Path(str(cwd)), Path(str(cwd)).resolve()):
        try:
            return p.relative_to(base).as_posix()
        except ValueError:
            pass
    try:
        return p.resolve().relative_to(Path(str(cwd)).resolve()).as_posix()
    except (ValueError, OSError):
        pass
    # The worktree is gone by now; the temp layout is <tmp>/check-*/verify/<rel>.
    m = re.search(r"/check-[^/]+/verify/(.+)$", path)
    return m.group(1) if m else path


def _missing_file_cause(rel: str, repo, sha: str, install_root: Path) -> str:
    name = describe_repo(repo).split(" (", 1)[0] if repo is not None else "this directory"
    in_install = (install_root / rel).exists()
    if repo is not None and rel.startswith(".datacore/lib"):
        has_lib = _git(repo, "cat-file", "-e", f"{sha}:.datacore/lib")[0] == 0 if sha else False
        if not has_lib:
            where = install_root / rel
            return (f"this repository ({name}) has no .datacore/lib; checks must run from the "
                    f"installation's copy ({where}{'' if in_install else ', also missing there'}), "
                    f"named through ${{DATACORE_ROOT:-$HOME/Data}}")
    if repo is not None and sha:
        rc, first = _git(repo, "log", "--all", "--diff-filter=A", "--format=%H", "--", rel)
        added = first.splitlines()[-1] if rc == 0 and first else ""
        if added and _git(repo, "merge-base", "--is-ancestor", added, sha)[0] != 0:
            return (f"commit {sha[:10]} predates {rel}; it was added in {added[:10]}, which this "
                    f"copy does not contain (pull, or check the commit the work was recorded on)")
    if in_install:
        return (f"{rel} is a path in the installation ({install_root}), not in {name}; the check "
                f"ran with {name} as its working directory, so it must name the installation's "
                f"copy through ${{DATACORE_ROOT:-$HOME/Data}}")
    if repo is None:
        return f"{rel} does not exist on this host"
    return f"{rel} does not exist in {name}{f' at {sha[:10]}' if sha else ''} (it was never committed there)"


def cause(*, cmd: str, rc, cwd, repo, sha: str, stderr="", stdout="",
          timed_out: float | None = None, install_root: Path | None = None) -> str:
    """The most likely cause, in plain words."""
    err, out = _text(stderr), _text(stdout)
    both = f"{err}\n{out}"
    install_root = Path(install_root or os.environ.get("DATACORE_ROOT") or Path.home() / "Data")
    if timed_out is not None:
        return (f"it timed out after {timed_out:g}s and was stopped (a hung command, or a host or "
                f"service that did not answer)")
    if rc in _KILLED or "MemoryError" in both or "Out of memory" in both:
        return ("it was killed (signal 9): most likely out of memory on this host, or stopped by "
                "the system")
    for rx in _UNREACHABLE:
        m = rx.search(both)
        if m:
            why = m.group(2).strip() if m.lastindex and m.lastindex > 1 else ""
            return f"could not reach {m.group(1)}" + (f" ({why})" if why else "") + \
                " -- the host is down, asleep, or unreachable from here"
    for bare in _UNREACHABLE_BARE:
        if bare in both:
            return f"could not reach the remote host ({bare})"
    if any(marker in both for marker in _ENOSPC):
        # rsync's receiver ran out of room: the full disk is the far end's, and
        # measuring this one would name the wrong host (2026-10-09).
        if "[receiver" in both or "receiver.c" in both:
            m = re.search(r"\bto (?:[^@\s]+@)?([^\s:]+)", cmd or "")
            return (f"disk full on the receiving host {m.group(1) if m else '(the remote end)'}"
                    " (No space left on device)")
        return (disk_full(cwd, both) or "the disk is full on this host") + " (No space left on device)"
    denied = _line_with(both, "Permission denied")
    if denied:
        return f"permission denied: {denied}"
    for rx in _MISSING_FILE:
        m = rx.search(err) or rx.search(out)
        if m:
            return _missing_file_cause(_relative(m.group(1), cwd), repo, sha, install_root)
    if rc == 127 or "command not found" in both:
        for rx in _NOT_FOUND:
            m = rx.search(both)
            if m:
                return f"the program `{m.group(1)}` is not installed on this host (or not on PATH)"
        return "a program it calls is not installed on this host (or not on PATH)"
    last = _last_line(err) or _last_line(out)
    return f"it exited {rc}" + (f"; the check said: {last}" if last else " and printed nothing")


def _last_line(s: str) -> str:
    lines = [ln.strip() for ln in (s or "").strip().splitlines() if ln.strip()]
    return lines[-1][:200] if lines else ""


def diagnose(*, cmd: str, rc, cwd, repo, sha: str, stderr="", stdout="",
             timed_out: float | None = None, install_root: Path | None = None,
             where: str = "") -> str:
    """One sentence: where it ran, the command, the cause, and the raw line."""
    place = where or (f"a temporary copy of {describe_repo(repo, sha)}" if repo is not None
                      else describe_repo(None))
    why = cause(cmd=cmd, rc=rc, cwd=cwd, repo=repo, sha=sha, stderr=stderr, stdout=stdout,
                timed_out=timed_out, install_root=install_root)
    c = cmd if len(cmd) <= 300 else cmd[:297] + "..."
    raw = _last_line(_text(stderr)) or _last_line(_text(stdout))
    already = raw and raw in why
    return (f"the check could not pass in {place}: `{c}` -- {why}"
            + (f" (raw: {raw})" if raw and not already else ""))


# ── Round 2 (2026-10-03): a model or agent run that failed ────────────────────
#
# Nightshift's "N Tasks Failed" alert carried `claude exited 1: <stderr>` cut to
# 160 characters, often the CLI's usage banner; the chief-of-staff jobs said
# "model exit 1" and the last line printed. Whether a PERSON was needed (a
# login, a usage limit, no credit) or the TASK was the problem (too big for its
# budget) was left to the reader. agent_failure() says which, in plain words.

_PREFIX = re.compile(r"^\s*claude exited -?\d+:\s*")


def _line_with(text: str, needle: str, *, case: bool = True) -> str:
    """The first line containing `needle`, stripped, at most 200 characters."""
    for ln in (text or "").splitlines():
        if (needle in ln) if case else (needle.lower() in ln.lower()):
            return _PREFIX.sub("", ln.strip())[:200]
    return ""


def raw_tail(text, n: int = 120) -> str:
    """The end of `text` in at most `n` characters, cut at a word, '…' in front."""
    words = " ".join(_text(text).split()).split(" ")
    if not words or words == [""]:
        return ""
    out: list[str] = []
    size = 0
    for w in reversed(words):
        if size + len(w) + (1 if out else 0) > n - 1:
            break
        out.append(w)
        size += len(w) + (1 if len(out) > 1 else 0)
    shown = " ".join(reversed(out))
    return shown if len(out) == len(words) else "…" + shown


_USAGE = ("usage limit", "hit your limit", "limit reached", "rate limit", "rate_limit",
          "weekly limit", "out of extra usage", "quota exceeded", "exceeded your quota",
          "quota exhausted", "http 429", "status 429")
_CREDIT = ("http 402", "insufficient credit", "credit balance", "no credits remaining",
           "billing", "payment required", "insufficient_quota")
_LOGIN = ("oauth", "token has been revoked", "session expired", "api_key overrides",
          "refused the session")
_GIT = ("fatal: ", "CONFLICT (", "non-fast-forward", "fast-forward", "merge conflict",
        "index.lock", "not a git repository", "pre-commit hook", "uncommitted changes",
        "pull request not opened", "would be overwritten by", "divergent branches",
        "[rejected]", "failed to push", "unmerged")
_EVALUATOR = (
    re.compile(r"(?:refused|rejected|vetoed) by (?:the )?(?:evaluator|reviewer)[: ]+[`'\"]?([\w.-]+)"),
    re.compile(r"(?:evaluator|reviewer)[: ]+[`'\"]?([\w.-]+)[`'\"]? (?:refused|rejected|vetoed)"),
)


def agent_failure(text, rc=None) -> tuple[str, str]:
    """(kind, plain sentence) for a model/agent run that failed.

    Kinds: too_big, usage_limit, credit, login, out_of_memory, timeout, refused,
    ledger_stop, git, provider, mcp, unreachable, write, internal, unknown_outcome,
    handoff, other. Ordered so the cause a person must act on wins over noise.
    """
    raw = _text(text)
    low = raw.lower()
    try:
        rc = int(rc) if rc is not None and str(rc).strip() != "" else None
    except ValueError:
        rc = None
    m = re.match(r"\s*(?:[\w-]+: )?claude exited (-?\d+)", raw)
    if rc is None and m:
        rc = int(m.group(1))

    if "stalelogerror" in low or "already wrote seq" in low:
        return "ledger_stop", ("the ledger refused a stale log, so the work stopped; the owner "
                               "repairs it (it is never retried)")
    m = re.search(r"timed out after (\d+) min", low)
    if "too big for its budget" in low or m or "timeoutexpired" in low:
        mins = f"{m.group(1)}-minute " if m else ""
        return "too_big", (f"too big for its {mins}time budget and was stopped; split it into "
                           f"smaller steps (partial changes are possible, check them first)")
    if any(k in low for k in _CREDIT):
        line = _line_with(raw, "402") or _line_with(raw, "credit", case=False) or _line_with(raw, "billing", case=False)
        return "credit", (f"the model provider account has no credit left ({line or 'payment required'}); "
                          f"a person must top it up")
    if any(k in low for k in _USAGE):
        return "usage_limit", ("the Claude usage limit was reached; nothing more runs until it "
                               "resets (waiting is the fix, not a retry)")
    try:
        from ops_markers import AUTH_FAILURE_MARKERS
    except ImportError:  # a host without the core markers still names a login
        AUTH_FAILURE_MARKERS = ("not logged in", "please run /login", "invalid api key",
                                "authentication_error", "unauthorized")
    hit = next((k for k in (*AUTH_FAILURE_MARKERS, *_LOGIN) if k in low), None)
    if hit or re.search(r"\b401\b", low):
        return "login", (f"the Claude login on this host stopped working ({hit or '401'}); someone "
                         f"must log in again -- retrying cannot help")
    if rc in (137, -9) or "memoryerror" in low or "out of memory" in low \
            or "cannot allocate memory" in low or re.search(r"^killed$", low, re.M):
        return "out_of_memory", ("it ran out of memory and was killed by the system "
                                 + (f"(exit {rc})" if rc is not None else "(signal 9)"))
    m = re.search(r"execution refused: (\w+)", low)
    if m:
        name = re.search(r"Execution refused: (\w+)", raw)
        return "refused", (f"the execution gate refused to start it ({name.group(1) if name else m.group(1)}): "
                           f"overnight work is paused, or the task is not cleared to run unattended")
    if "co-signed grant" in low or "never_effects" in low or "[tool-policy]" in low \
            or "policy.refusal" in low:
        line = _line_with(raw, "co-signed grant") or _line_with(raw, "never_effects") \
            or _line_with(raw, "[tool-policy]") or _line_with(raw, "policy.refusal")
        return "refused", f"the tool policy refused one of its steps: {line[:160]}"
    m = re.search(r"evaluation crashed \(([^)]*)\)?", raw)
    if m:
        return "refused", f"the evaluator panel crashed ({m.group(1)[:120]}); the output went to review"
    for rx in _EVALUATOR:
        m = rx.search(raw)
        if m:
            return "refused", f"the evaluator {m.group(1)} refused it: {_line_with(raw, m.group(1))[:160]}"
    if "review gate gave no verdict" in low:
        return "refused", "the review gate gave no verdict, so nothing was approved"
    if "background agent" in low:
        return "handoff", ("it handed the work to a background agent and returned no result; "
                           "the work must run in the foreground")
    for k in _GIT:
        line = _line_with(raw, k)
        if line:
            return "git", f"a git problem: {line}"
    m = re.search(r"API Error:? ?(5\d\d)|\b(529)\b|(overloaded)", raw, re.I)
    if m:
        code = m.group(1) or m.group(2) or ""
        return "provider", (f"the Claude service was overloaded or failed"
                            f"{f' (API Error {code})' if code else ''}; a later retry usually works")
    m = re.search(r"MCP server[s]? [`'\"]?([\w.@/-]+?)[`'\"]?:? (?:failed to connect|is not connected|"
                  r"not connected|disconnected|connection closed|failed)", raw, re.I)
    if m:
        return "mcp", f"a tool server it needs (MCP {m.group(1)}) was not connected"
    for rx in _UNREACHABLE:
        m = rx.search(raw)
        if m:
            why = m.group(2).strip() if m.lastindex and m.lastindex > 1 else ""
            return "unreachable", (f"could not reach {m.group(1)}" + (f" ({why})" if why else "")
                                   + " -- the host is down, asleep, or unreachable from here")
    for bare in _UNREACHABLE_BARE:
        if bare in raw:
            return "unreachable", f"could not reach the remote host ({bare})"
    if "timed out" in low or "timeout" in low:
        return "timeout", "it timed out and was stopped (a hung call, or a service that did not answer)"
    m = re.search(r"Output write failed: (\S+)", raw)
    if m:
        return "write", f"its result could not be saved ({m.group(1)})"
    m = re.search(r"task raised (.+)", raw)
    if m:
        return "internal", f"nightshift itself failed on this task ({m.group(1)[:120]}); the run went on"
    m = re.search(r"(?:outcome unknown|not acknowledged|acknowledgement failed)(?: \(([^)]*)\))?", raw)
    if m:
        return "unknown_outcome", ("the run ended without a clear result"
                                   + (f" ({m.group(1)})" if m.group(1) else "")
                                   + "; check what it changed before running it again")
    last = _last_line(_PREFIX.sub("", raw.strip()) if "\n" not in raw.strip() else raw)
    last = _PREFIX.sub("", last)
    if last.strip("() ") in ("no stderr/stdout captured", ""):
        last = ""
    status = f"it exited {rc}" if rc is not None else "it failed"
    return "other", status + (f"; the last thing it printed: {last}" if last else " and printed nothing")


def explain_agent(text, rc=None, tail: int = 120) -> str:
    """The plain cause, then the raw tail in brackets (cut at a word)."""
    _, sentence = agent_failure(text, rc)
    t = raw_tail(_PREFIX.sub("", _text(text).strip()), tail)
    return sentence + (f" (raw: {t})" if t and t.lstrip("…") not in sentence else "")


def send_failure(text) -> str:
    """Why a Telegram send (winston_send) did not go through, in plain words."""
    raw = _text(text)
    low = raw.lower()
    m = re.search(r"\b(\w+_(?:TOKEN|CHAT_ID))\b[^\n]*not set", raw)
    if m or "not set" in low:
        var = m.group(1) if m else "a setting"
        return f"{var} is not set on this host, so the sender has nowhere to post"
    m = re.search(r"http (\d{3})", low)
    code = m.group(1) if m else ""
    if code == "401":
        return "Telegram rejected the bot token (HTTP 401): the token is wrong or was revoked"
    if code == "400":
        return ("Telegram refused the message (HTTP 400): most often a wrong chat id (chat not "
                "found) or text it could not parse")
    if code == "403":
        return "the bot may not post in that chat (HTTP 403): it was blocked or removed from the group"
    if code == "429":
        return "Telegram rate-limited the bot (HTTP 429); a later send goes through"
    if code:
        return f"Telegram answered HTTP {code}"
    if "wrong weekday" in low:
        return "held back on purpose: a date in it names the wrong weekday"
    if "empty input" in low:
        return "there was nothing to send (the job produced no text)"
    for bare in (*_UNREACHABLE_BARE, "urlopen error", "timed out"):
        if bare.lower() in low:
            return f"could not reach Telegram ({_line_with(raw, bare, case=False)[:160]})"
    last = _last_line(raw)
    return f"the sender failed: {last}" if last else "the sender failed and printed nothing"


def _main(argv=None) -> int:
    """CLI for the shell jobs. stdin is the run's output; one line on stdout.

      check_diagnosis.py agent   --rc N [--tail 120]   a model/agent run
      check_diagnosis.py send                          a Telegram send (winston_send)
      check_diagnosis.py command --rc N --cmd CMD      any other command (tar, rsync, ...)
    """
    import argparse
    import sys
    ap = argparse.ArgumentParser(description=_main.__doc__)
    ap.add_argument("mode", choices=("agent", "send", "command"))
    ap.add_argument("--rc", default=None)
    ap.add_argument("--cmd", default="")
    ap.add_argument("--tail", type=int, default=120)
    a = ap.parse_args(argv)
    text = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        rc = int(a.rc) if a.rc not in (None, "") else None
    except ValueError:
        rc = None
    if a.mode == "agent":
        out = explain_agent(text, rc, a.tail)
    elif a.mode == "send":
        out = send_failure(text)
    else:
        why = cause(cmd=a.cmd, rc=rc, cwd=".", repo=None, sha="", stderr=text).replace(
            "; the check said: ", "; it printed: ")
        prog = (a.cmd.split() or ["it"])[0].rsplit("/", 1)[-1]
        if why.startswith("it exited"):
            why = prog + why[2:]
        t = raw_tail(text, a.tail)
        out = why + (f" (raw: {t})" if t and t.lstrip("…") not in why else "")
    print(" ".join(out.split()))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
