#!/usr/bin/env python3
"""One task, one checkout (DIP-0046 E4).

Three systemd units run as the same user with the same WorkingDirectory on the
nightshift box — `nightshift-overnight`, `ledger-claim` and
`datacore-telegram` — so a Telegram session can start while the overnight batch
is mid-task, in that one tree, editing the same files. The batch is sequential
internally; that says nothing about what else is running beside it.

E3 already stops a run COMMITTING work it did not produce. It cannot stop a run
READING a file another writer is halfway through changing, and that is what a
separate checkout fixes: `git worktree add` gives each task its own working
directory sharing one object store, so two runs cannot see each other's
half-written files at all.

Two rules make this safe rather than merely present:

  A COLLISION FAILS LOUDLY. If `agent/<task-id>` already exists, this raises.
  It does not add a suffix and continue: a task id that appears twice means
  either the same task is running twice, or an earlier run died without
  cleaning up, and both are things the operator needs to know rather than have
  quietly worked around.

  IT NEVER FALLS BACK TO THE SOURCE CHECKOUT. A failure to isolate returns an
  error, never the shared tree. Silent degradation to the shared tree is
  indistinguishable from success right up until two runs corrupt each other,
  and it is the specific failure this module exists to make impossible.

Cleanup retires the directory beside its old location and KEEPS the branch
when it holds commits. Retired directories retain late/ignored writes and a
detached commit anchor; they require separate reclamation after writers have
stopped and their contents have been reviewed. Task IDs with no new commits
can be reused without erasing the retired generation.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from worktree_lifecycle import git_environment, retire_worktree


class IsolationError(RuntimeError):
    """Isolation could not be established. NEVER caught to fall back."""


@dataclass
class Workspace:
    path: Path
    branch: str
    source: Path


def _git(repo: Path, *args: str, timeout: int = 120) -> tuple[int, str, str]:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, errors='replace', timeout=timeout,
                       env=git_environment())
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def branch_exists(repo: Path, branch: str) -> bool:
    rc, _, _ = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    return rc == 0


def create(source: Path, task_id: str, *, root: Path | None = None) -> Workspace:
    """A private checkout on `agent/<task-id>`. Raises rather than degrading."""
    if not task_id or "/" in task_id or task_id.strip() != task_id:
        raise IsolationError(f"unusable task id for a branch name: {task_id!r}")
    # Subsequent executor Git commands run outside this helper. A per-command
    # override while creating the worktree cannot preserve their hooks. Git's
    # default shared hooks or an absolute operator-configured path work in all
    # checkouts; a relative path can disappear or load branch-controlled hooks.
    rc, hooks, _ = _git(source, 'config', '--path', '--get', 'core.hooksPath')
    if rc not in (0, 1):
        raise IsolationError('cannot inspect configured Git hooks')
    if rc == 0 and (not hooks.strip() or not Path(hooks.strip()).is_absolute()):
        raise IsolationError('agent workspaces require an absolute hooksPath or Git default hooks')
    branch = f"agent/{task_id}"
    base = root or (Path.home() / ".datacore" / "worktrees")
    path = base / task_id

    if branch_exists(source, branch):
        raise IsolationError(
            f"branch {branch} already exists — the same task is running twice, "
            "or an earlier run did not clean up. Refusing to guess which.")
    if path.exists():
        raise IsolationError(f"worktree path {path} already exists — refusing to reuse")

    base.mkdir(parents=True, exist_ok=True)
    rc, _, err = _git(source, "worktree", "add", "-b", branch, str(path))
    if rc != 0:
        # No fallback. An un-isolated run is the thing being prevented.
        raise IsolationError(f"could not create worktree for {task_id}: {err.strip()[:200]}")
    return Workspace(path=path, branch=branch, source=Path(source))


def cleanup(ws: Workspace, *, keep_branch_if_commits: bool = True) -> str:
    """Retire a clean worker's checkout without deleting possible late writes."""
    rc, status, _ = _git(ws.path, 'status', '--porcelain', '--untracked-files=all', '--ignored=matching')
    if rc or status:
        raise IsolationError('worktree has uncommitted/ignored files or cannot be inspected; preserved for review')
    rc, out, _ = _git(ws.source, "rev-list", "--count", f"{_base(ws)}..{ws.branch}")
    if rc or not out.strip().isdecimal():
        raise IsolationError('cannot establish branch history; worktree and branch preserved')
    commits = int(out.strip())
    if commits and not keep_branch_if_commits:
        raise IsolationError('cleanup cannot discard unmerged commits; preserve or merge the branch first')

    try:
        retired = retire_worktree(ws.source, ws.path)
    except RuntimeError as exc:
        raise IsolationError(str(exc)) from None

    if commits and keep_branch_if_commits:
        # Deleting a branch that holds commits to tidy up is how 610 of them
        # were stranded. An orphan branch is findable; a deleted one is not.
        return f"kept {ws.branch} ({commits} commit(s)); retained workspace at {retired.path}"
    rc, _, _ = _git(ws.source, 'update-ref', '-d', f'refs/heads/{ws.branch}', retired.head)
    if rc:
        return f'kept {ws.branch} (Git refused branch deletion)'
    return f"removed {ws.branch}; retained workspace at {retired.path}"


def _base(ws: Workspace) -> str:
    rc, out, _ = _git(ws.source, "symbolic-ref", "--short", "HEAD")
    return out.strip() if rc == 0 and out.strip() else "main"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="per-task worktree isolation")
    ap.add_argument("op", choices=["create", "list", "prune"])
    ap.add_argument("--source", type=Path, default=Path.cwd())
    ap.add_argument("--task-id")
    a = ap.parse_args()

    if a.op == "create":
        if not a.task_id:
            ap.error("--task-id required")
        ws = create(a.source, a.task_id)
        print(f"{ws.path}\t{ws.branch}")
    elif a.op == "list":
        print(_git(a.source, "worktree", "list")[1].rstrip())
    else:
        print(_git(a.source, "worktree", "prune", "-v")[1].rstrip() or "nothing to prune")
