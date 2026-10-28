#!/usr/bin/env python3
"""Move a running Claude Code session from the Mac to dayshift.

Two halves, because the agent is not allowed to copy its own transcript (the
permission check refuses it as transcript tampering):

    handover.py checkpoint --topic T --mode M --inflight FILE   # the /handover command, in the session
    handover.py go <session-id>                                 # the owner, after exiting Claude

`checkpoint` commits and pushes the session's files through wrap_up_mechanics
(same rules as /wrap-up), stops on anything it could not push, and writes
~/.datacore/state/handover/<id>.json. `go` checks the Mac session has exited,
brings every repo on dayshift up to the pushed commit (or stops), copies the
transcript and checkpoint, and resumes the conversation in tmux there with the
same permission flags the Mac session had.

Spec: 2-datacore/1-tracks/dev/superpowers/specs/2026-10-28-handover-design.md
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

LIB = Path(__file__).resolve().parent
DATA = Path.home() / "Data"
STATE = Path.home() / ".datacore" / "state" / "handover"
HOST = "dayshift"
REMOTE_CLAUDE = "~/.local/bin/claude"   # non-interactive ssh on dayshift has no ~/.local/bin on PATH
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", HOST]

# Flags that mean nothing on dayshift. Named to the owner, never silently lost.
DROPPED_FLAGS = {"--chrome"}
# Flags that identify the Mac run, not its permissions; dayshift gets its own --resume.
SESSION_FLAGS_WITH_VALUE = {"--resume", "-r", "--session-id"}
SESSION_FLAGS = {"--continue", "-c", "--fork-session"}


def permission_args(argv: list[str], mode: str | None) -> tuple[list[str], list[str]]:
    """The Mac session's flags to pass on dayshift, and the ones dropped.

    A handover never widens what the session may do: the resumed session gets
    exactly the Mac launch flags plus the permission mode the session was in
    (an in-session switch such as auto mode is not visible in argv).
    """
    keep: list[str] = []
    dropped: list[str] = []
    args = argv[1:]
    i = 0
    has_mode = False
    while i < len(args):
        a = args[i]
        if a in SESSION_FLAGS_WITH_VALUE:
            i += 2
            continue
        if a in SESSION_FLAGS:
            i += 1
            continue
        if a in DROPPED_FLAGS:
            dropped.append(a)
        else:
            keep.append(a)
            if a == "--permission-mode":
                has_mode = True
                if mode and i + 1 < len(args):
                    keep.append(mode)       # the live mode wins over the launch mode
                    i += 2
                    continue
        i += 1
    if mode and not has_mode:
        keep += ["--permission-mode", mode]
    return keep, dropped


def session_is_live(pid: int, cmdline_of: Callable[[int], str | None]) -> bool:
    """True while the Mac claude process for this session still runs.

    Two live copies of one conversation would both append to it; `go` refuses.
    """
    cmd = cmdline_of(pid)
    return bool(cmd) and "claude" in cmd


def repo_action(exists: bool, contains_sha: bool, clean: bool, can_ff: bool) -> str:
    """What `go` may do with one repo on dayshift.

    Only a fast-forward is done unasked; missing or diverged repos stop the
    handover (no stash, reset or clone on anyone's behalf). Uncommitted edits
    there do not block it: `git merge --ff-only` itself refuses when an
    incoming commit touches a locally changed file and never overwrites one,
    and that refusal is reported as dirty. Observed 2026-10-28: dayshift's
    root repo carried a stray package-lock.json edit that a clean-tree rule
    would have let block every handover.
    """
    if not exists:
        return "stop:missing"
    if contains_sha:
        return "ok"
    if not can_ff:
        return "stop:diverged"
    return "fast-forward"


def tmux_name(topic: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:30].strip("-")
    return name or "handover"


def blocking_issues(report: dict) -> list[str]:
    """What in a wrap_up_mechanics finalize report would be missing on dayshift."""
    issues: list[str] = []
    if report.get("error"):
        issues.append(f"finalize: {report['error']}")
    for p in report.get("pushes", []):
        if p.get("ok") is False:
            issues.append(f"{p.get('repo')}: {p.get('note') or 'not pushed'}")
    for f in report.get("skipped_project_repos") or []:
        issues.append(f"{f}: in a code repo, which finalize never commits — commit and push it, then rerun")
    return issues


def write_checkpoint(session_id: str, claude_pid: int, mode: str | None, topic: str,
                     inflight: list[dict], repos: list[dict], state_dir: Path,
                     argv: list[str] | None = None) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"{session_id}.json"
    path.write_text(json.dumps({
        "session_id": session_id,
        "claude_pid": claude_pid,
        "argv": argv or ["claude"],
        "mode": mode,
        "topic": topic,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "inflight": inflight,
        "repos": repos,
    }, indent=2) + "\n")
    return path


# --- side effects -----------------------------------------------------------

def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _cmdline(pid: int) -> str | None:
    r = _run(["ps", "-o", "command=", "-p", str(pid)])
    return r.stdout.strip() or None


def _remote(script: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return _run(SSH + [script], timeout=timeout)


def _die(lines: list[str]) -> None:
    print("handover stopped:", file=sys.stderr)
    for line in lines:
        print(f"  - {line}", file=sys.stderr)
    sys.exit(1)


def cmd_checkpoint(a: argparse.Namespace) -> None:
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    pid = os.environ.get("CLAUDE_PID")
    if not sid or not pid:
        _die(["run this from inside the Claude Code session (/handover): no CLAUDE_CODE_SESSION_ID/CLAUDE_PID"])
    argv = shlex.split(_cmdline(int(pid)) or "claude")
    inflight = json.loads(Path(a.inflight).read_text()) if a.inflight else []

    fin = _run([sys.executable, str(LIB / "wrap_up_mechanics.py"), "finalize"], cwd=DATA, timeout=900)
    try:
        report = json.loads(fin.stdout)
    except ValueError:
        _die([f"finalize gave no report: {(fin.stderr or fin.stdout)[-400:]}"])
    issues = blocking_issues(report)
    if issues:
        _die(issues)

    repos = []
    for p in report.get("pushes", []):
        repo = DATA / p["repo"]
        branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).stdout.strip()
        upstream = _run(["git", "rev-parse", "@{u}"], cwd=repo)
        if upstream.returncode != 0:
            _die([f"{p['repo']}: branch '{branch}' has no upstream, so dayshift cannot get it"])
        repos.append({"path": p["repo"], "branch": branch, "sha": upstream.stdout.strip()})

    path = write_checkpoint(sid, int(pid), a.mode, a.topic, inflight, repos, STATE, argv)
    print(json.dumps({"checkpoint": str(path), "repos": repos,
                      "unversioned": report.get("unversioned") or []}, indent=2))
    print(f"\nExit Claude, then run:\n\n    handover {sid}\n")


def _sync_repo(r: dict) -> str | None:
    """Bring one dayshift repo to the pushed commit; return why not, or None."""
    q = shlex.quote
    probe = (f"cd ~/Data/{q(r['path'])} 2>/dev/null || {{ echo missing; exit 0; }}; "
             f"git fetch -q origin; "
             f"c=0; git merge-base --is-ancestor {q(r['sha'])} HEAD 2>/dev/null && c=1; "
             f"k=0; [ -z \"$(git status --porcelain --untracked-files=no)\" ] && k=1; "
             f"f=0; git merge-base --is-ancestor HEAD {q(r['sha'])} 2>/dev/null && f=1; "
             f"echo $c $k $f")
    out = _remote(probe).stdout.strip()
    if out == "missing":
        action = repo_action(False, False, False, False)
    else:
        try:
            c, k, f = (x == "1" for x in out.split())
        except ValueError:
            return f"{r['path']}: could not probe dayshift ({out!r})"
        action = repo_action(True, c, k, f)
    if action == "ok":
        return None
    if action == "fast-forward":
        ff = _remote(f"cd ~/Data/{q(r['path'])} && git merge -q --ff-only {q(r['sha'])}")
        if ff.returncode == 0:
            return None
        why = "dirty (local edits in the way)" if "overwritten" in ff.stderr else "fast-forward failed"
        return f"{r['path']} on dayshift: {why}: {ff.stderr.strip()[-200:]}"
    return f"{r['path']} on dayshift: {action.split(':')[1]} — fix it there, then rerun"


def cmd_go(a: argparse.Namespace) -> None:
    cp_path = STATE / f"{a.session_id}.json"
    if not cp_path.exists():
        _die([f"no checkpoint at {cp_path} — run /handover in the session first"])
    cp = json.loads(cp_path.read_text())
    if session_is_live(cp["claude_pid"], _cmdline):
        _die([f"the Mac session is still running (pid {cp['claude_pid']}) — exit Claude first"])

    problems = [p for p in (_sync_repo(r) for r in cp["repos"]) if p]
    if problems:
        _die(problems)

    project = Path.home() / ".claude" / "projects" / str(DATA).replace("/", "-")
    transcript = project / f"{a.session_id}.jsonl"
    if not transcript.exists():
        _die([f"no transcript at {transcript}"])
    home = _remote('printf %s "$HOME"').stdout.strip()
    remote_project = f"{home}/.claude/projects/" + f"{home}/Data".replace("/", "-")
    _remote(f"mkdir -p {shlex.quote(remote_project)} ~/.datacore/state/handover")
    copies = [["scp", "-q", str(transcript), f"{HOST}:{remote_project}/"],
              ["scp", "-q", str(cp_path), f"{HOST}:.datacore/state/handover/"]]
    if (project / a.session_id).is_dir():                    # subagent transcripts
        copies.append(["scp", "-qr", str(project / a.session_id), f"{HOST}:{remote_project}/"])
    for c in copies:
        if _run(c, timeout=600).returncode != 0:
            _die([f"copy failed: {' '.join(c)}"])

    keep, dropped = permission_args(cp["argv"], cp.get("mode"))
    name = tmux_name(cp["topic"])
    if _remote(f"tmux has-session -t ={shlex.quote(name)}").returncode == 0:
        name = f"{name}-{time.strftime('%H%M')}"
    note = f" Not available here: {', '.join(dropped)}." if dropped else ""
    message = (f"You were moved from the Mac to dayshift by /handover. Read "
               f"~/.datacore/state/handover/{a.session_id}.json, relaunch every in-flight item it "
               f"lists, then continue from where you stopped.{note}")
    claude = " ".join([REMOTE_CLAUDE, "--resume", shlex.quote(a.session_id)]
                      + [shlex.quote(k) for k in keep] + [shlex.quote(message)])
    start = _remote(f"tmux new -d -s {shlex.quote(name)} -c ~/Data {shlex.quote(claude)}")
    if start.returncode != 0:
        _die([f"tmux start failed: {start.stderr[-300:]}"])
    print(f"resumed on {HOST} as tmux session '{name}'"
          + (f" (dropped: {', '.join(dropped)})" if dropped else ""))
    ds = LIB.parent / "bin" / "ds"
    if ds.exists():
        os.execv(str(ds), [str(ds), name])
    os.execvp("ssh", ["ssh", "-t", HOST, f"tmux attach -t ={shlex.quote(name)}"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("checkpoint", help="in the session: commit, push, write the checkpoint")
    c.add_argument("--topic", required=True)
    c.add_argument("--mode", help="the session's current permission mode, e.g. auto")
    c.add_argument("--inflight", help="JSON file: [{what, progress, relaunch}]")
    g = sub.add_parser("go", help="owner, after exiting Claude: move the session to dayshift")
    g.add_argument("session_id")
    a = ap.parse_args()
    {"checkpoint": cmd_checkpoint, "go": cmd_go}[a.cmd](a)


if __name__ == "__main__":
    main()
