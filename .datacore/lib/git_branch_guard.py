#!/usr/bin/env python3
"""Assert every managed repo sits on its expected branch; repair when safe.

WHY THIS EXISTS. On 2026-08-27 23:26 an agent session on winston ran
`git checkout -b fix/cos-verification-drift-54-55`, committed a real fix
15 minutes later, and never went back. Winston then served as Chief of
Staff from that branch for four days, 29 commits behind main, and every
deploy to it silently stopped: `cos_sync.sh` fast-forwards the system repo
with `git merge --ff-only origin/main`, which cannot succeed from a feature
branch.

The condition WAS detected — 1,326 times, once per 15-minute cycle. It was
written to `~/.datacore/cos/sync.log` and nowhere else. That one failure
path is the only one in cos_sync.sh with no `cos_alert.sh` call: a push
failure alerts, a sync conflict alerts, a host quietly receiving no updates
at all does not. A check whose only output is a logfile nobody reads is not
a check.

WHAT IT DOES. For the system repo and every `[0-9]-*` space repo: compare
HEAD to the expected branch (origin's default, else `main`). Anything off
its branch is reported. A repo is REPAIRED automatically only on positive
evidence that nothing is lost — clean worktree, and no commit on the
current branch that the expected branch lacks. Otherwise it is STUCK: the
branch is carrying work, and moving it is a human's decision. That is the
same rule the rest of this tree already applies to unreachable repos and to
ledger dismissal — act on evidence of safety, never on absence of evidence
of harm.

Exit 1 if anything is stuck or still off-branch, so a caller can alert.

    python3 git_branch_guard.py             # report only
    python3 git_branch_guard.py --repair    # also return safe repos to main
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, errors="replace")


def out(repo: Path, *args: str) -> str:
    r = git(repo, *args)
    return r.stdout.strip() if r.returncode == 0 else ""


def expected_branch(repo: Path) -> str:
    """Origin's default branch, falling back to main.

    Read from the remote HEAD symref rather than assumed, because a repo
    whose default is `master` would otherwise be "repaired" onto a branch
    that does not exist.
    """
    ref = out(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if ref.startswith("origin/"):
        return ref.split("/", 1)[1]
    for cand in ("main", "master"):
        if git(repo, "rev-parse", "--verify", f"refs/heads/{cand}").returncode == 0:
            return cand
    return "main"


def inspect(repo: Path) -> dict:
    current = out(repo, "rev-parse", "--abbrev-ref", "HEAD")
    name = repo.name
    if not current:
        return {"repo": name, "state": "SKIP", "detail": "not a git repo"}
    if current == "HEAD":
        # Detached: never touched. cos_sync.sh leaves these for a human too,
        # and guessing which branch a detached HEAD "meant" is how work gets
        # orphaned.
        return {"repo": name, "state": "DETACHED", "detail": "detached HEAD"}

    want = expected_branch(repo)
    if current == want:
        return {"repo": name, "state": "OK", "branch": current}

    # TRACKED changes only. `--porcelain` alone counts untracked files, which
    # a branch switch does not touch at all — and every long-lived host has
    # some (winston carries .datacore/keys/, scp backups, host-local scripts).
    # Counting those made the guard report STUCK forever on exactly the
    # machines it exists to repair: the first drill refused to move a branch
    # whose only "dirt" was files git was never tracking.
    dirty = bool(out(repo, "status", "--porcelain", "--untracked-files=no"))
    # Commits on this branch that the expected branch does not have. Compared
    # against the REMOTE expected branch: comparing against a local `main`
    # that is itself stale would call work unique when origin already has it,
    # and refuse a repair that was perfectly safe.
    base = f"origin/{want}"
    if git(repo, "rev-parse", "--verify", base).returncode != 0:
        base = want
    unique = out(repo, "rev-list", "--count", f"{base}..HEAD") or "?"

    return {"repo": name, "state": "OFF_BRANCH", "branch": current,
            "want": want, "dirty": dirty, "unique": unique, "base": base}


def repair(repo: Path, info: dict) -> dict:
    """Return to the expected branch, but only when nothing can be lost."""
    if info["dirty"]:
        info["state"] = "STUCK"
        info["detail"] = "uncommitted changes — not moving them"
        return info
    if info["unique"] != "0":
        info["state"] = "STUCK"
        info["detail"] = (f"{info['unique']} commit(s) not on {info['base']} — "
                          f"merge or push them first")
        return info
    r = git(repo, "checkout", info["want"])
    if r.returncode != 0:
        info["state"] = "STUCK"
        info["detail"] = f"checkout failed: {r.stderr.strip()[:120]}"
        return info
    info["state"] = "REPAIRED"
    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("DATACORE_ROOT")
                    or str(Path.home() / "Data"))
    ap.add_argument("--repair", action="store_true",
                    help="return repos to the expected branch when provably safe")
    a = ap.parse_args()
    root = Path(a.root).expanduser()

    repos = [root] + sorted(p for p in root.glob("[0-9]-*")
                            if (p / ".git").exists())

    bad = 0
    for repo in repos:
        info = inspect(repo)
        if info["state"] == "OFF_BRANCH" and a.repair:
            info = repair(repo, info)
        if info["state"] in ("OK", "SKIP"):
            continue
        bad += 1
        if info["state"] == "REPAIRED":
            print(f"REPAIRED {info['repo']}: was on {info['branch']}, "
                  f"returned to {info['want']} (no unique commits, clean tree)")
        elif info["state"] == "DETACHED":
            print(f"DETACHED {info['repo']}: {info['detail']} — left for a human")
        elif info["state"] == "OFF_BRANCH":
            print(f"OFF-BRANCH {info['repo']}: on {info['branch']}, expected "
                  f"{info['want']} ({info['unique']} unique commit(s)"
                  f"{', dirty' if info['dirty'] else ''}) — deploys to this "
                  f"host are NOT arriving")
        else:
            print(f"STUCK {info['repo']}: on {info['branch']}, expected "
                  f"{info['want']} — {info.get('detail', '')}")

    if not bad:
        print(f"all {len(repos)} repo(s) on their expected branch")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
