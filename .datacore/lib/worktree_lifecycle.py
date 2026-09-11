"""Preserve Git hooks and retire workspaces without deleting late writes.

A clean status is a snapshot, not evidence that all writers have stopped.
Git worktree removal also ignores ignored files. Automatic retirement therefore
moves the directory and retains it with a detached HEAD anchoring its commits.
Reclamation is a separate operation requiring writer quiescence and an explicit
decision about retained files. This is cooperative lifecycle safety, not an OS
or credential boundary.
"""
from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


def git_environment() -> dict[str, str]:
    env = dict(os.environ)
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR',
                'GIT_NAMESPACE', 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES'):
        env.pop(key, None)
    env.update(GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0',
               GIT_NO_REPLACE_OBJECTS='1', GIT_MERGE_AUTOEDIT='no')
    env.setdefault('GIT_SSH_COMMAND', 'ssh -o ConnectTimeout=5 -o BatchMode=yes')
    return env


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                              text=True, errors='replace', timeout=120, env=git_environment())
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError('Git workspace operation failed or timed out; existing work retained') from None


def _checked(repo: Path, *args: str) -> str:
    result = _git(repo, *args)
    if result.returncode:
        raise RuntimeError(f'Git workspace {args[0]} failed; existing work retained')
    return result.stdout.strip()


def publication_hooks(repo: Path) -> Path:
    """Resolve relative operator hook paths before leaving the shared tree."""
    result = _git(repo, 'config', '--path', '--get', 'core.hooksPath')
    if result.returncode == 1:
        return Path(_checked(repo, 'rev-parse', '--path-format=absolute', '--git-path', 'hooks'))
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError('Cannot resolve configured Git hooks')
    path = Path(result.stdout.strip())
    return (repo / path).resolve() if not path.is_absolute() else path


def allocate_publication_workspace(repo: Path) -> Path:
    """Retain recovery data with its repository, outside OS temporary cleanup.

    The shared Git directory also gives linked worktrees one recovery location.
    This is deliberately outside tracked source paths and private to its owner.
    Allocation does not authorize automatic reclamation of earlier workspaces.
    """
    common = Path(_checked(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
    root = common / 'datacore-publication-workspaces'
    try:
        root.mkdir(mode=0o700, exist_ok=True)
        metadata = root.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or root.is_symlink()
                or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077):
            raise RuntimeError('Publication recovery directory requires private owner permissions')
        return Path(tempfile.mkdtemp(prefix='publication-', dir=root)).resolve()
    except OSError:
        raise RuntimeError('Cannot allocate persistent publication recovery workspace') from None


@dataclass(frozen=True)
class RetiredWorktree:
    path: Path
    head: str


def retire_worktree(source: Path, worktree: Path) -> RetiredWorktree:
    """Move a registered checkout aside; keep bytes and anchor its history.

    The destination is on the same filesystem and inaccessible to other OS
    users. A failed move/detach preserves both locations for inspection; there
    is no recursive-delete or force-remove fallback. Open file descriptors and
    a process's existing working directory continue to refer to retained data.
    """
    source, worktree = Path(source).absolute(), Path(worktree).absolute()
    if source != source.resolve() or worktree != worktree.resolve():
        raise RuntimeError('Git workspace retirement requires canonical paths')
    if Path(_checked(worktree, 'rev-parse', '--show-toplevel')) != worktree:
        raise RuntimeError('Git workspace retirement requires a repository root')
    head = _checked(worktree, 'rev-parse', '--verify', 'HEAD^{commit}')
    try:
        destination = Path(tempfile.mkdtemp(prefix='retired-worktree-', dir=worktree.parent)) / 'worktree'
    except OSError:
        raise RuntimeError('Cannot allocate retirement directory; original workspace retained') from None
    try:
        _checked(source, 'worktree', 'move', '--', str(worktree), str(destination))
        # Updating HEAD directly detaches it without checking out files or
        # changing the index. A concurrently advanced HEAD refuses the CAS.
        _checked(destination, 'update-ref', '--no-deref', 'HEAD', head, head)
    except RuntimeError as exc:
        raise RuntimeError(f'{exc}; inspect {worktree} and {destination}') from None
    return RetiredWorktree(destination, head)
