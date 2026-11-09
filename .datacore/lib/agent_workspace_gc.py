#!/usr/bin/env python3
"""Remove finished agent task copies under ~/.datacore/worktrees.

agent_workspace.create() checks out one worktree per task and nothing ever
removed them: on 2026-10-18 the overnight server held 15GB of them, some idle
18 days, on the morning its disk filled.

A copy is removed only when all three hold:
  1. nothing unsaved: no modified, staged or untracked file (ignored build
     output such as node_modules is regenerable and goes with it);
  2. its HEAD commit is on a remote ref, so the work survives elsewhere;
  3. idle for --idle-days (its index and HEAD untouched), so a task still
     running tonight is never touched.
Removal is `git worktree remove` WITHOUT --force: git re-checks at removal time
and refuses rather than deletes. The branch is kept. Anything that fails a
condition is left and named.

  agent_workspace_gc.py                 # report, change nothing
  agent_workspace_gc.py --apply         # remove what qualifies
"""
from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

ROOT = Path.home() / ".datacore" / "worktrees"


def git(path: Path, *args: str) -> tuple[int, str]:
    r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout.strip() or r.stderr.strip()


def copies(root: Path) -> list[Path]:
    return sorted(p.parent for p in root.glob("*/*/.git") if p.is_file())


def why_kept(path: Path, idle_days: float) -> str:
    rc, gitdir = git(path, "rev-parse", "--absolute-git-dir")
    if rc:
        return "not a readable worktree"
    touched = max((Path(gitdir) / f).stat().st_mtime for f in ("index", "HEAD") if (Path(gitdir) / f).exists())
    if time.time() - touched < idle_days * 86400:
        return f"active within {idle_days:g} days"
    rc, status = git(path, "status", "--porcelain", "--untracked-files=all")
    if rc or status:
        return "unsaved work"
    rc, remotes = git(path, "for-each-ref", "--contains", "HEAD", "--count=1", "refs/remotes")
    if rc or not remotes:
        return "commit not on any remote"
    return ""


def sweep(root: Path = ROOT, idle_days: float = 3, apply: bool = False) -> tuple[int, int]:
    """(removed or removable, kept)."""
    done = kept = 0
    for path in copies(root):
        why = why_kept(path, idle_days)
        if not why and apply:
            rc, common = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
            rc = rc or subprocess.run(["git", "--git-dir", common, "worktree", "remove", str(path)],
                                      capture_output=True, timeout=300).returncode
            why = "git would not remove it" if rc else ""
        if why:
            kept += 1
            print(f"  KEPT    {path.parent.name}/{path.name}: {why}")
        else:
            done += 1
            print(f"  {'REMOVED' if apply else 'WOULD REMOVE'} {path.parent.name}/{path.name}")
    print(f"{done} {'removed' if apply else 'removable'}, {kept} kept")
    return done, kept


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--idle-days", type=float, default=3)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    sweep(a.root, a.idle_days, a.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
