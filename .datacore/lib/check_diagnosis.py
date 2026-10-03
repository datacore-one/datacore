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
