#!/usr/bin/env python3
"""Reclaim a retired publication worktree, on proof that its content is published.

`allocate_publication_workspace` says in as many words that "allocation does not
authorize automatic reclamation of earlier workspaces", and `retire_worktree`
keeps every byte deliberately: there is no recursive delete anywhere in that
module, because a half-published run's checkout is the recovery copy.

Nothing ever reclaimed them. Measured 2026-09-17 on nightshift: 5-plur held 19
publication workspaces of ~737MB each -- 13GB inside .git, on a host that had
fallen to 2.9GB free, where a full disk stops every job on the machine. Retention
without a reclamation route is a slow outage, the same shape as a correct refusal
nobody can clear.

THE PROOF, per worktree: its HEAD is an ancestor of the repository's published
default branch (origin/<default>). Then every file in it is reproducible from the
object store, and the checkout is a copy, not the only copy. A worktree whose HEAD
is NOT published is exactly what retention exists for: it is left alone and named.

Only registered worktrees are removed, through `git worktree remove`. Workspace
directories, `publication-intent.json` and anything else a human might read are
never touched.

  publication_workspace_gc.py [--root ~/Data]      # report, change nothing
  publication_workspace_gc.py --apply              # reclaim what is proven
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

WORKSPACES = "datacore-publication-workspaces"


def git(repo: Path, *args: str) -> tuple[int, str]:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout.strip()


def default_branch(repo: Path) -> str:
    rc, out = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    return out.split("/", 1)[1] if rc == 0 and "/" in out else "main"


def workspace_worktrees(repo: Path) -> list[Path]:
    rc, out = git(repo, "worktree", "list", "--porcelain")
    if rc != 0:
        return []
    return [Path(line.split(" ", 1)[1]) for line in out.splitlines()
            if line.startswith("worktree ") and WORKSPACES in line]


def published(repo: Path, worktree: Path, branch: str) -> bool:
    rc, head = git(worktree, "rev-parse", "HEAD")
    if rc != 0 or not head:
        return False
    return git(repo, "merge-base", "--is-ancestor", head, f"refs/remotes/origin/{branch}")[0] == 0


def reclaim(repo: Path, apply: bool) -> tuple[int, int, int]:
    """(reclaimed, retained, bytes reclaimed) for one repository."""
    worktrees = workspace_worktrees(repo)
    if not worktrees:
        return 0, 0, 0
    branch = default_branch(repo)
    git(repo, "fetch", "-q", "origin", branch)
    reclaimed = retained = freed = 0
    for worktree in worktrees:
        size = sum(f.stat().st_size for f in worktree.rglob("*") if f.is_file()) if worktree.is_dir() else 0
        if not published(repo, worktree, branch):
            retained += 1
            print(f"  RETAINED {worktree.name}: HEAD is not on origin/{branch} — this is the copy "
                  f"retention exists for")
            continue
        reclaimed += 1
        freed += size
        if not apply:
            continue
        rc, out = git(repo, "worktree", "remove", "--force", str(worktree))
        if rc != 0:
            reclaimed -= 1
            freed -= size
            retained += 1
            print(f"  REFUSED  {worktree.name}: git would not remove it ({out[:120]})")
    if apply and reclaimed:
        git(repo, "worktree", "prune")
    print(f"{repo.name}: {reclaimed} worktree(s) {'reclaimed' if apply else 'reclaimable'} "
          f"({freed / 1024 ** 3:.2f} GiB), {retained} retained")
    return reclaimed, retained, freed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path.home() / "Data")
    ap.add_argument("--repo", type=Path, help="one repository instead of the whole root")
    ap.add_argument("--apply", action="store_true", help="reclaim (default: report only)")
    args = ap.parse_args(argv)
    repos = [args.repo] if args.repo else [p for p in [args.root, *sorted(args.root.glob("[0-9]-*"))]
                                           if (p / ".git").exists()]
    total = freed = 0
    for repo in repos:
        count, _, size = reclaim(repo.resolve(), args.apply)
        total += count
        freed += size
    print(f"\n{total} worktree(s) {'reclaimed' if args.apply else 'reclaimable'}, "
          f"{freed / 1024 ** 3:.2f} GiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
