"""Lossless Git path inventory. Unavailable status is never a clean tree."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from worktree_lifecycle import git_environment


@dataclass(frozen=True)
class Change:
    status: str
    path: str
    source: str | None = None

    @property
    def unmerged(self) -> bool:
        return self.status in {'DD', 'AU', 'UD', 'UA', 'DU', 'AA', 'UU'}

    @property
    def changed_paths(self) -> tuple[str, ...]:
        # Copy detection names an unchanged source; renames also remove it.
        if self.source is not None and 'R' in self.status:
            return self.path, self.source
        return (self.path,)


def _output(repo: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ['git', '--literal-pathspecs', '-C', str(repo), *args],
            capture_output=True, timeout=60, env=git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError('Git inventory unavailable; publication must not assume a clean tree') from None
    if result.returncode:
        raise RuntimeError('Git inventory failed; publication must not assume a clean tree')
    return result.stdout


def _fields(raw: bytes) -> list[bytes]:
    if not raw:
        return []
    if not raw.endswith(b'\0'):
        raise RuntimeError('Git inventory is incomplete')
    fields = raw[:-1].split(b'\0')
    if any(not field for field in fields):
        raise RuntimeError('Git inventory contains an empty record')
    return fields


def parse_status(raw: bytes) -> list[Change]:
    """Parse porcelain v1 -z, including the second field of renames/copies."""
    fields = iter(_fields(raw))
    changes = []
    for field in fields:
        if (len(field) < 4 or field[2:3] != b' '
                or any(c not in b' MADRCUT?!' for c in field[:2])):
            raise RuntimeError('Git inventory contains an invalid status record')
        status, path = field[:2].decode('ascii'), os.fsdecode(field[3:])
        source = None
        if 'R' in status or 'C' in status:
            try:
                source = os.fsdecode(next(fields))
            except StopIteration:
                raise RuntimeError('Git inventory contains an incomplete rename/copy') from None
        changes.append(Change(status, path, source))
    return changes


def changes(repo: Path, *paths: str) -> list[Change]:
    return parse_status(_output(repo, 'status', '--porcelain=v1', '-z',
                                '--untracked-files=all', '--', *paths))


def dirty_paths(repo: Path) -> list[str]:
    inventory = require_resolved(repo)
    return list(dict.fromkeys(path for change in inventory for path in change.changed_paths))


def require_resolved(repo: Path) -> list[Change]:
    inventory = changes(repo)
    if any(change.unmerged for change in inventory):
        raise RuntimeError('Unresolved Git index requires explicit resolution before publication')
    return inventory


def tracked_paths(repo: Path) -> set[str]:
    return {os.fsdecode(path) for path in _fields(_output(repo, 'ls-files', '-z'))}
