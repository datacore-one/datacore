#!/usr/bin/env python3
"""Move a running Claude Code session from the Mac to dayshift.

Two halves, because the agent is not allowed to copy its own transcript (the
permission check refuses it as transcript tampering):

    handover.py checkpoint --topic T --mode M --inflight FILE   # the /handover command, in the session
    handover.py go <session-id>                                 # the owner, after exiting Claude

`checkpoint` commits and pushes the session's files through wrap_up_mechanics
(same rules as /wrap-up), stops on anything dayshift would be missing, and
writes ~/.datacore/state/handover/<id>.json. `go` checks the Mac session has
exited and dayshift holds no live or newer copy, checks every repo on dayshift
before changing any, fast-forwards them to the pushed commits, copies the
transcript and checkpoint, and resumes the conversation in tmux there with the
Mac session's permissions — never wider.

    handover.py ds [name]        # Mac: attach to a dayshift tmux session, with previews
    handover.py mac-open <path>  # dayshift: show a file to the owner on the Mac
    handover.py listen <group>   # Mac: the preview listener ds runs

Spec: 2-datacore/1-tracks/dev/superpowers/specs/2026-10-28-handover-design.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

LIB = Path(__file__).resolve().parent
DATA = Path.home() / "Data"
STATE = Path.home() / ".datacore" / "state" / "handover"
HOST = "dayshift"
REMOTE_CLAUDE = "~/.local/bin/claude"   # non-interactive ssh on dayshift has no ~/.local/bin on PATH
# One shared connection: dayshift's fail2ban bans bursts of fresh logins.
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
       "-o", "ControlMaster=auto", "-o", "ControlPath=~/.ssh/cm-handover-%r@%h:%p",
       "-o", "ControlPersist=60", HOST]

# --- permissions: the resumed session is never wider than the Mac one --------

SKIP_FLAGS = ("--allow-dangerously-skip-permissions", "--dangerously-skip-permissions")
MODES = {"acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan", "default"}
# Dropping these would loosen what the session may do, so they cannot be dropped.
NARROWING_FLAGS = {"--settings", "--disallowedTools", "--disallowed-tools"}


def permission_args(argv: list[str], mode: str | None) -> tuple[list[str], list[str]]:
    """The permission flags to start dayshift's session with, and the flags left behind.

    Only the skip-permissions flags and the permission mode travel; everything
    else on the Mac command line (--chrome, --resume, a first prompt, Mac paths)
    is left behind and named. `ps` loses the original quoting, so values are
    never carried — only these known flags are recognised. The mode is the one
    the session was in (an in-session switch is not in argv), else the launch
    mode. Raises ValueError for anything that would widen permissions.
    """
    skips: list[str] = []
    dropped: list[str] = []
    launch_mode = None
    args = argv[1:]
    i = 0
    while i < len(args):
        t = args[i]
        if t in SKIP_FLAGS:
            if t not in skips:
                skips.append(t)
        elif t == "--permission-mode":
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                launch_mode = args[i + 1]
                i += 1
        elif t.startswith("--permission-mode="):
            launch_mode = t.split("=", 1)[1]
        elif t.split("=", 1)[0] in NARROWING_FLAGS:
            raise ValueError(f"{t.split('=', 1)[0]} restricts this session and cannot be carried "
                             "to dayshift; start the session there by hand")
        elif t.startswith("-") and t not in dropped:
            dropped.append(t)
        i += 1
    mode = mode or launch_mode
    keep = list(skips)
    if mode:
        if mode not in MODES:
            raise ValueError(f"unknown permission mode {mode!r}")
        if mode == "bypassPermissions" and not skips:
            raise ValueError("bypassPermissions was not available to the Mac session")
        keep += ["--permission-mode", mode]
    return keep, dropped


# --- the Mac session, dayshift's repos and its copy of the conversation -------

def session_is_live(pid: int, cmdline_of: Callable[[int], str | None]) -> bool:
    """True while the Mac claude process for this session still runs."""
    cmd = cmdline_of(pid)
    return bool(cmd) and "claude" in cmd


def repo_action(exists: bool, contains_sha: bool, on_branch: bool, can_ff: bool) -> str:
    """What `go` may do with one repo on dayshift.

    Only a fast-forward of the branch the session was on is done unasked;
    a missing repo, another checked-out branch or a diverged one stops the
    handover (no stash, reset, checkout or clone on anyone's behalf).
    Uncommitted edits there do not block it: `git merge --ff-only` itself
    refuses when an incoming commit touches a locally changed file and never
    overwrites one. Observed 2026-10-28: dayshift's root repo carried a stray
    package-lock.json edit that a clean-tree rule would have let block every
    handover.
    """
    if not exists:
        return "stop:missing"
    if not on_branch:
        return "stop:other-branch"
    if contains_sha:
        return "ok"
    if not can_ff:
        return "stop:diverged"
    return "fast-forward"


def remote_copy_action(live_there: bool, remote_hash: str | None, local_hash: str) -> str:
    """Whether `go` may put the Mac transcript on dayshift.

    A rerun (the attach failed, the terminal closed) must not overwrite a
    conversation that has carried on there, nor start a second copy of it.
    """
    if live_there:
        return "stop:live"
    if remote_hash is None:
        return "copy"
    return "skip" if remote_hash == local_hash else "stop:diverged"


def tmux_name(topic: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:30].strip("-")
    return name or "handover"


def blocking_issues(report: dict, data_root: Path) -> list[str]:
    """What in a wrap_up_mechanics finalize report would be missing on dayshift.

    Code repos under 2-projects are checked separately (finalize lists their
    files whether or not they are committed). Unversioned files outside the
    Data tree (~/.zshrc) are reported, not blocking; inside it they are module
    or DIP repos finalize does not push, so the session's work would stay here.
    """
    issues: list[str] = []
    if report.get("error"):
        issues.append(f"finalize: {report['error']}")
    for p in report.get("pushes", []):
        if p.get("ok") is False:
            issues.append(f"{p.get('repo')}: {p.get('note') or 'not pushed'}")
        for f in p.get("deleted_not_staged") or []:
            issues.append(f"{p.get('repo')}/{f}: deleted this session but not committed — "
                          "commit the deletion or restore it, then rerun")
    root = str(data_root) + os.sep
    for f in report.get("unversioned") or []:
        if str(f).startswith(root):
            issues.append(f"{f}: in a repo finalize does not push (a module or DIP repo) — "
                          "commit and push it there, then rerun")
    return issues


def write_checkpoint(session_id: str, claude_pid: int, mode: str | None, topic: str,
                     inflight: list[dict], repos: list[dict], state_dir: Path,
                     argv: list[str] | None = None, cwd: str | None = None) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"{session_id}.json"
    path.write_text(json.dumps({
        "session_id": session_id,
        "claude_pid": claude_pid,
        "argv": argv or ["claude"],
        "mode": mode,
        "topic": topic,
        "cwd": cwd,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "inflight": inflight,
        "repos": repos,
    }, indent=2) + "\n")
    return path


# --- previews: a dayshift session shows the owner a file on the Mac ----------
#
# Pull only: the Mac holds one ssh to dayshift and reads a queue file there.
# Nothing on dayshift can reach into the Mac (its sshd is off and stays off).

PREVIEW_SUFFIXES = (".md", ".markdown", ".html", ".htm", ".pdf", ".png", ".jpg", ".jpeg", ".svg")
TYPORA_SUFFIXES = {".md", ".markdown"}
QUEUES = ".datacore/state/previews"                 # relative to home, on dayshift
CURSORS = Path.home() / ".datacore" / "state" / "previews"
PREVIEW_CACHE = Path.home() / "Library" / "Caches" / "dayshift-previews"
TITLE_ICON = "☀️"


def safe_group(name: str) -> bool:
    """A tmux name usable in a queue file name and a remote command as-is."""
    return bool(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]*", name or ""))


def valid_preview(path: str, data_root: Path) -> Path | None:
    """The existing file under data_root that `path` names, if it may be shown."""
    if "\n" in path or "\r" in path:
        return None
    p = Path(path).resolve()
    root = data_root.resolve()
    if root not in p.parents or p.suffix.lower() not in PREVIEW_SUFFIXES or not p.is_file():
        return None
    return p


def local_preview_path(remote: Path, remote_root: Path, cache: Path) -> Path:
    """Where the Mac keeps its read-only copy; refuses anything outside the root."""
    norm = Path(os.path.normpath(remote))
    if remote_root not in norm.parents or norm.suffix.lower() not in PREVIEW_SUFFIXES:
        raise ValueError(f"not a previewable path under {remote_root}: {remote}")
    return cache / norm.relative_to(remote_root)


def remote_preview_command(path: str) -> str:
    """Shell for dayshift: print the file only if, symlinks resolved, it is a
    previewable file under ~/Data. Checked there, at read time, because the
    queue line was checked when it was written and the file may have changed.
    """
    pats = "|".join(f"*{s}" for s in PREVIEW_SUFFIXES)
    return (f'r=$(readlink -f -- "$HOME/Data") || exit 3; '
            f'p=$(readlink -f -- {shlex.quote(path)}) || exit 3; [ -f "$p" ] || exit 3; '
            f'case "$p" in "$r"/*) ;; *) exit 3;; esac; '
            f'case "$p" in {pats}) cat -- "$p";; *) exit 4;; esac')


def opener(local: Path) -> list[str]:
    if local.suffix.lower() in TYPORA_SUFFIXES:
        return ["open", "-a", "Typora", str(local)]
    return ["open", str(local)]


def new_entries(lines: list[str], cursor: int) -> tuple[list[str], int]:
    """Queue lines not yet opened. A cursor past the end means the queue was reset."""
    if cursor > len(lines):
        cursor = 0
    return lines[cursor:], len(lines)


def group_of(list_sessions_output: str, name: str) -> str | None:
    """The tmux group (or session name) `tmux attach -t name` would land in.

    Sessions here are grouped clones (plur-new-7 in group plur-new); the queue is
    per group so every clone of one session shares its previews.
    """
    rows = [line.split(" ", 1) for line in list_sessions_output.splitlines() if line.strip()]
    rows = [(r[0], r[1].strip() if len(r) > 1 else "") for r in rows]
    for match in (lambda s: s == name, lambda s: s.startswith(name)):
        for sess, group in rows:
            if match(sess):
                return group or sess
    return None


def title_sequence(name: str | None) -> str:
    """The escape that sets the terminal tab title (empty name: clear it)."""
    return f"\033]0;{TITLE_ICON} ds: {name}\007" if name else "\033]0;\007"


# --- side effects -----------------------------------------------------------

def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _cmdline(pid: int) -> str | None:
    r = _run(["ps", "-o", "command=", "-p", str(pid)])
    return r.stdout.strip() or None


def _remote(script: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return _run(SSH + [script], timeout=timeout)


def _git(repo: Path, *args: str) -> str:
    return _run(["git", *args], cwd=repo).stdout.strip()


def _die(lines: list[str]) -> None:
    print("handover stopped:", file=sys.stderr)
    for line in lines:
        print(f"  - {line}", file=sys.stderr)
    sys.exit(1)


def _transcript(session_id: str) -> Path | None:
    hits = sorted((Path.home() / ".claude" / "projects").glob(f"*/{session_id}.jsonl"))
    return hits[0] if hits else None


def _session_cwd(transcript: Path) -> str | None:
    """The directory the session was started in, as the transcript records it."""
    with transcript.open() as f:
        for line in f:
            try:
                cwd = json.loads(line).get("cwd")
            except ValueError:
                continue
            if cwd:
                return cwd
    return None


def _repo_entry(repo: Path, issues: list[str]) -> dict | None:
    rel = "." if repo == DATA else str(repo.relative_to(DATA))
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    upstream = _run(["git", "rev-parse", "@{u}"], cwd=repo)
    if upstream.returncode != 0:
        issues.append(f"{rel}: branch '{branch}' has no upstream, so dayshift cannot get it")
        return None
    ahead = _git(repo, "rev-list", "--count", "@{u}..HEAD")
    if ahead not in ("", "0"):
        issues.append(f"{rel}: {ahead} commit(s) not pushed — push them, then rerun")
        return None
    return {"path": rel, "branch": branch, "sha": upstream.stdout.strip()}


def cmd_checkpoint(a: argparse.Namespace) -> None:
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    pid = os.environ.get("CLAUDE_PID")
    if not sid or not pid:
        _die(["run this from inside the Claude Code session (/handover): no CLAUDE_CODE_SESSION_ID/CLAUDE_PID"])
    argv = (_cmdline(int(pid)) or "claude").split()
    try:
        permission_args(argv, a.mode)
    except ValueError as e:
        _die([str(e)])
    inflight = json.loads(Path(a.inflight).read_text()) if a.inflight else []

    fin = _run([sys.executable, str(LIB / "wrap_up_mechanics.py"), "finalize"], cwd=DATA, timeout=900)
    try:
        report = json.loads(fin.stdout)
    except ValueError:
        _die([f"finalize gave no report: {(fin.stderr or fin.stdout)[-400:]}"])
    issues = blocking_issues(report, DATA)

    repos: dict[str, dict] = {}
    for p in report.get("pushes", []):
        e = _repo_entry(DATA / p["repo"], issues)
        if e:
            repos[e["path"]] = e
    # Code repos: finalize lists their files whether committed or not. Block only
    # on what is really not pushed, and bring the repo along on dayshift.
    for f in report.get("skipped_project_repos") or []:
        top = _git(Path(f).parent, "rev-parse", "--show-toplevel")
        if not top:
            issues.append(f"{f}: not in a git repo")
            continue
        if _git(Path(top), "status", "--porcelain", "--", f):
            issues.append(f"{f}: uncommitted in a code repo — commit and push it (or open the PR), then rerun")
            continue
        e = _repo_entry(Path(top), issues)
        if e:
            repos[e["path"]] = e
    if issues:
        _die(issues)

    t = _transcript(sid)
    path = write_checkpoint(sid, int(pid), a.mode, a.topic, inflight, list(repos.values()), STATE,
                            argv, _session_cwd(t) if t else None)
    others = {p["repo"]: p["left_for_other_sessions"] for p in report.get("pushes", [])
              if p.get("left_for_other_sessions")}
    print(json.dumps({"checkpoint": str(path), "repos": list(repos.values()),
                      "left_for_other_sessions": others,
                      "unversioned_outside_data": report.get("unversioned") or []}, indent=2))
    print(f"\nExit Claude, then run:\n\n    handover {sid}\n")


def _probe(r: dict) -> str:
    """repo_action for one dayshift repo; changes nothing."""
    q = shlex.quote
    script = (f"cd ~/Data/{q(r['path'])} 2>/dev/null || {{ echo missing; exit 0; }}; "
              f"git fetch -q origin; "
              f"b=0; [ \"$(git rev-parse --abbrev-ref HEAD)\" = {q(r['branch'])} ] && b=1; "
              f"c=0; git merge-base --is-ancestor {q(r['sha'])} HEAD 2>/dev/null && c=1; "
              f"f=0; git merge-base --is-ancestor HEAD {q(r['sha'])} 2>/dev/null && f=1; "
              f"echo $c $b $f")
    out = _remote(script).stdout.strip()
    if out == "missing":
        return repo_action(False, False, False, False)
    try:
        c, b, f = (x == "1" for x in out.split())
    except ValueError:
        return f"stop:unreadable ({out!r})"
    return repo_action(True, c, b, f)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_go(a: argparse.Namespace) -> None:
    try:
        _go(a.session_id)
    except (subprocess.TimeoutExpired, OSError) as e:
        _die([f"{type(e).__name__}: {e} — nothing after this point was done; rerun when dayshift answers"])


def _go(sid: str) -> None:
    cp_path = STATE / f"{sid}.json"
    if not cp_path.exists():
        done = STATE / f"{sid}.done.json"
        _die([f"already handed over ({done.name}); attach with ds" if done.exists()
              else f"no checkpoint at {cp_path} — run /handover in the session first"])
    cp = json.loads(cp_path.read_text())
    if session_is_live(cp["claude_pid"], _cmdline):
        _die([f"the Mac session is still running (pid {cp['claude_pid']}) — exit Claude first"])
    try:
        keep, dropped = permission_args(cp["argv"], cp.get("mode"))
    except ValueError as e:
        _die([str(e)])

    transcript = _transcript(sid)
    if not transcript:
        _die([f"no transcript for {sid} under ~/.claude/projects"])
    home = _remote('printf %s "$HOME"').stdout.strip()
    if not home:
        _die([f"{HOST} did not answer"])
    local_home_dashed = str(Path.home()).replace("/", "-")
    folder = transcript.parent.name
    if not folder.startswith(local_home_dashed):
        _die([f"session folder {folder} is not under the home folder; move it by hand"])
    remote_project = f"{home}/.claude/projects/{home.replace('/', '-')}{folder[len(local_home_dashed):]}"
    remote_file = f"{remote_project}/{sid}.jsonl"
    cwd = cp.get("cwd") or str(DATA)
    remote_cwd = home + cwd[len(str(Path.home())):] if cwd.startswith(str(Path.home())) else f"{home}/Data"

    q = shlex.quote
    live = _remote(f"pgrep -f -- {q('--resume ' + sid)} >/dev/null").returncode == 0
    rh = _remote(f"sha256sum -- {q(remote_file)} 2>/dev/null | cut -d' ' -f1").stdout.strip() or None
    copy = remote_copy_action(live, rh, _sha256(transcript))
    if copy == "stop:live":
        _die([f"this conversation is already running on {HOST} — attach with ds"])
    if copy == "stop:diverged":
        _die([f"{HOST} already has a different copy of this conversation ({remote_file}); "
              "it may have carried on there — not overwriting it"])

    # Check every repo before changing any, so a stop leaves dayshift as it was.
    actions = [(r, _probe(r)) for r in cp["repos"]]
    stops = [f"{r['path']} on {HOST}: {act.split(':', 1)[1]} — fix it there, then rerun"
             for r, act in actions if act.startswith("stop:")]
    if stops:
        _die(stops)
    for r, act in actions:
        if act == "fast-forward":
            ff = _remote(f"cd ~/Data/{q(r['path'])} && git merge -q --ff-only {q(r['sha'])}")
            if ff.returncode != 0:
                why = "local edits in the way" if "overwritten" in ff.stderr else "fast-forward failed"
                _die([f"{r['path']} on {HOST}: {why}: {ff.stderr.strip()[-200:]}"])

    _remote(f"mkdir -p {q(remote_project)} ~/.datacore/state/handover")
    copies = [["scp", "-q", str(cp_path), f"{HOST}:.datacore/state/handover/"]]
    if copy == "copy":
        copies.insert(0, ["scp", "-q", str(transcript), f"{HOST}:{remote_project}/"])
        if (transcript.parent / sid).is_dir():                # subagent transcripts
            copies.append(["scp", "-qr", str(transcript.parent / sid), f"{HOST}:{remote_project}/"])
    for c in copies:
        if _run(c, timeout=600).returncode != 0:
            _die([f"copy failed: {' '.join(c)}"])

    name = tmux_name(cp["topic"])
    if _remote(f"tmux has-session -t ={q(name)}").returncode == 0:
        name = f"{name}-{time.strftime('%H%M')}"
    note = f" Left behind on the Mac (not available here): {', '.join(dropped)}." if dropped else ""
    message = (f"You were moved from the Mac to dayshift by /handover. Read "
               f"~/.datacore/state/handover/{sid}.json, relaunch every in-flight item it "
               f"lists, then continue from where you stopped.{note}")
    claude = " ".join([REMOTE_CLAUDE, "--resume", q(sid)] + [q(k) for k in keep] + [q(message)])
    start = _remote(f"tmux new -d -s {q(name)} -c {q(remote_cwd)} {q(claude)}")
    if start.returncode != 0:
        _die([f"tmux start failed: {start.stderr[-300:]}"])
    time.sleep(4)
    alive = _remote(f"tmux list-panes -t ={q(name)} -F '#{{pane_dead}}' 2>/dev/null").stdout.strip()
    if alive != "0":
        _die([f"claude exited right after starting on {HOST} (tmux session '{name}'); "
              f"try by hand: ssh -t {HOST} {q(claude)}"])
    cp_path.rename(STATE / f"{sid}.done.json")
    print(f"resumed on {HOST} as tmux session '{name}'"
          + (f" (left behind: {', '.join(dropped)})" if dropped else ""))
    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), "ds", name])


def cmd_mac_open(a: argparse.Namespace) -> None:
    """On dayshift: queue a file for the owner's Mac."""
    p = valid_preview(a.path, DATA)
    if not p:
        _die([f"not shown: {a.path} (must be an existing {', '.join(PREVIEW_SUFFIXES)} file under {DATA})"])
    group = "default"
    if os.environ.get("TMUX"):
        r = _run(["tmux", "display", "-p", "#{?session_group,#{session_group},#{session_name}}"])
        if safe_group(r.stdout.strip()):
            group = r.stdout.strip()
    q = Path.home() / QUEUES / f"{group}.queue"
    q.parent.mkdir(parents=True, exist_ok=True)
    with q.open("a") as f:
        f.write(f"{p}\n")
    print(f"queued for the Mac: {p} — opens there while `ds {group}` is attached")


def _show(line: str, remote_root: Path) -> None:
    try:
        local = local_preview_path(Path(line), remote_root, PREVIEW_CACHE)
    except ValueError as e:
        print(f"skipped: {e}", file=sys.stderr)
        return
    local.parent.mkdir(parents=True, exist_ok=True)
    tmp = local.with_name(local.name + ".part")
    with tmp.open("wb") as out:
        r = subprocess.run(SSH + [remote_preview_command(line)], stdout=out, timeout=120)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        print(f"skipped: {line} (refused on {HOST}, exit {r.returncode})", file=sys.stderr)
        return
    tmp.replace(local)
    subprocess.run(opener(local))


def cmd_listen(a: argparse.Namespace) -> None:
    """On the Mac: open what a dayshift tmux group queues, until stopped."""
    if not safe_group(a.group):
        _die([f"not a usable session name: {a.group!r}"])
    queue = shlex.quote(f"{QUEUES}/{a.group}.queue")
    home = _remote('printf %s "$HOME"').stdout.strip()
    remote_root = Path(home) / "Data"
    cur_file = CURSORS / f"{a.group}.cursor"
    cur_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        cursor = int(cur_file.read_text())
    except (OSError, ValueError):
        cursor = 0
    lines = _remote(f"cat {queue} 2>/dev/null").stdout.splitlines()
    todo, cursor = new_entries(lines, cursor)
    for line in todo:                                  # queued while no terminal was attached
        _show(line, remote_root)
    cur_file.write_text(str(cursor))
    tail = subprocess.Popen(SSH + [f"mkdir -p {QUEUES} && touch {queue} && tail -n +{cursor + 1} -F {queue}"],
                            stdout=subprocess.PIPE, text=True)

    # The ssh child outlives a stopped listener unless closed here: observed
    # 2026-10-28, a terminated listener left `ssh … tail -F` running on both
    # machines. The parent check is the backstop for a terminal closed so hard
    # that ds never got to stop us.
    def _stop(*_):
        tail.terminate()
        os._exit(0)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGHUP, _stop)
    parent = os.getppid()

    def _watch_parent():
        while True:
            time.sleep(5)
            if os.getppid() != parent:
                _stop()
    threading.Thread(target=_watch_parent, daemon=True).start()
    try:
        for line in tail.stdout:
            _show(line.rstrip("\n"), remote_root)
            cursor += 1
            cur_file.write_text(str(cursor))
    finally:
        tail.terminate()


def cmd_ds(a: argparse.Namespace) -> None:
    """Attach to (or create) a dayshift tmux session, with its preview listener."""
    if not safe_group(a.name):
        _die([f"use letters, digits, '.', '_' or '-' for the session name: {a.name!r}"])
    sessions = _remote("tmux list-sessions -F '#{session_name} #{session_group}' 2>/dev/null").stdout
    group = group_of(sessions, a.name) or a.name
    if not safe_group(group):
        group = a.name
    log = CURSORS / f"{group}.listener.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as lf:
        listener = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "listen", group],
                                    stdout=lf, stderr=lf, start_new_session=True)

    def _cleanup(*_):
        try:
            os.killpg(listener.pid, signal.SIGTERM)    # the listener and its ssh child
        except ProcessLookupError:
            pass
        sys.stdout.write(title_sequence(None))
        sys.stdout.flush()
    # Closing the window sends SIGHUP; Python's default would skip `finally`.
    signal.signal(signal.SIGHUP, lambda *_: (_cleanup(), os._exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: (_cleanup(), os._exit(0)))
    sys.stdout.write(title_sequence(group))
    sys.stdout.flush()
    try:
        q = shlex.quote(a.name)
        subprocess.run(["ssh", "-t", HOST, f"tmux attach -t {q} 2>/dev/null || tmux new -s {q} -c ~/Data"])
    finally:
        _cleanup()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("checkpoint", help="in the session: commit, push, write the checkpoint")
    c.add_argument("--topic", required=True)
    c.add_argument("--mode", help="the session's current permission mode, e.g. auto")
    c.add_argument("--inflight", help="JSON file: [{what, progress, relaunch}]")
    g = sub.add_parser("go", help="owner, after exiting Claude: move the session to dayshift")
    g.add_argument("session_id")
    m = sub.add_parser("mac-open", help="on dayshift: show a file to the owner on the Mac")
    m.add_argument("path")
    li = sub.add_parser("listen", help="on the Mac: open files a dayshift tmux group queues")
    li.add_argument("group")
    d = sub.add_parser("ds", help="on the Mac: attach to a dayshift tmux session, with previews")
    d.add_argument("name", nargs="?", default="dev")
    a = ap.parse_args()
    {"checkpoint": cmd_checkpoint, "go": cmd_go, "mac-open": cmd_mac_open,
     "listen": cmd_listen, "ds": cmd_ds}[a.cmd](a)


if __name__ == "__main__":
    main()
