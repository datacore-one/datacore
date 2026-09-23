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

THE PROOF, per worktree, all four of:

  1. it was RETIRED (`retire_worktree` moved it to retired-worktree-*/): its
     writer declared itself finished. A live candidate or reservation checkout
     mid-publication is not reclaimable, however clean it looks;
  2. its HEAD is an ancestor of the published default branch (origin/<default>);
  3. its files are exactly its index: no unstaged change, no untracked file and
     no IGNORED file (`git worktree remove` deletes ignored files silently);
  4. its index is the tree of a commit on origin/<default>.

Then every byte in it is reproducible from the published history, and the
checkout is a copy, not the only copy. HEAD alone proved nothing about the files:
a publication commit refused by a hook leaves HEAD at the published base with
the captured content staged, and `worktree remove --force` destroyed it (Lean:
DatacoreSpec/Publication.lean, `old_reclaim_loses_work`). (4) and not "index ==
HEAD" because knowledge_commit advances a checked-out target branch by update-ref,
so a successful reservation checkout reads as dirty in reverse while holding
exactly the published base tree.

Removal is `git worktree remove` WITHOUT --force, so git re-checks for modified
and untracked files at removal time and refuses rather than deletes. A status
check is still a snapshot (worktree_lifecycle's docstring): (1) is what makes the
writer's quiescence a declared fact rather than an inference. A worktree that
fails any condition is left alone and named. Workspace directories,
`publication-intent.json` and anything else a human might read are never touched.

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


def published(repo: Path, worktree: Path, branch: str) -> str:
    """HEAD when it is an ancestor of origin/<branch>, else ''."""
    rc, head = git(worktree, "rev-parse", "HEAD")
    if rc != 0 or not head:
        return ""
    ok = git(repo, "merge-base", "--is-ancestor", head, f"refs/remotes/origin/{branch}")[0] == 0
    return head if ok else ""


def retired(worktree: Path) -> bool:
    return worktree.parent.name.startswith("retired-worktree-")


def files_are_index(worktree: Path) -> bool:
    """No unstaged change, no untracked and no ignored file. Fails closed."""
    unstaged = git(worktree, "diff", "--quiet", "--no-ext-diff")[0]
    rc, others = git(worktree, "ls-files", "--others", "--directory", "--no-empty-directory")
    return unstaged == 0 and rc == 0 and not others


def index_tree(worktree: Path) -> str:
    """The index as a tree; '' when it cannot be one (e.g. unmerged entries)."""
    rc, tree = git(worktree, "write-tree")
    return tree if rc == 0 else ""


def published_trees(repo: Path, branch: str) -> dict[str, str]:
    """tree -> a commit on origin/<branch> that has it."""
    rc, out = git(repo, "log", "--format=%T %H", f"refs/remotes/origin/{branch}")
    if rc != 0:
        return {}
    return dict(line.split() for line in reversed(out.splitlines()) if len(line.split()) == 2)


def reclaimable(repo: Path, worktree: Path, branch: str, trees) -> tuple[str, str, str]:
    """(why retained, HEAD, published commit whose tree is the index); why == ''
    when every byte of the checkout is published."""
    if not retired(worktree):
        return "not retired -- its writer has not declared itself finished", "", ""
    head = published(repo, worktree, branch)
    if not head:
        return f"HEAD is not on origin/{branch} -- this is the copy retention exists for", "", ""
    if not files_are_index(worktree):
        return "unstaged, untracked or ignored files -- work that exists only here", "", ""
    commit = trees().get(index_tree(worktree), "")
    if not commit:
        return f"staged content is not a tree on origin/{branch} -- work that exists only here", "", ""
    return "", head, commit


def reclaim(repo: Path, apply: bool) -> tuple[int, int, int]:
    """(reclaimed, retained, bytes reclaimed) for one repository."""
    worktrees = workspace_worktrees(repo)
    if not worktrees:
        return 0, 0, 0
    branch = default_branch(repo)
    git(repo, "fetch", "-q", "origin", branch)
    reclaimed = retained = freed = 0
    cache: list[dict[str, str]] = []

    def trees() -> dict[str, str]:
        if not cache:
            cache.append(published_trees(repo, branch))
        return cache[0]

    for worktree in worktrees:
        size = sum(f.stat().st_size for f in worktree.rglob("*") if f.is_file()) if worktree.is_dir() else 0
        why, head, commit = reclaimable(repo, worktree, branch, trees)
        if why:
            retained += 1
            print(f"  RETAINED {worktree.name}: {why}")
            continue
        reclaimed += 1
        freed += size
        if not apply:
            continue
        # Point HEAD at the published commit that IS the index, by compare-and-
        # swap (a moved HEAD refuses), so the checkout reads clean; then remove
        # without --force, so git re-checks modified/untracked files and refuses
        # rather than deletes. HEAD was published, so moving it anchors nothing new
        # and loses nothing.
        rc = 0
        if commit != head:
            rc, out = git(worktree, "update-ref", "--no-deref", "HEAD", commit, head)
        if rc == 0:
            rc, out = git(repo, "worktree", "remove", str(worktree))
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
