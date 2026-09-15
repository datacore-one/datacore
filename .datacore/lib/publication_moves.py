"""Explicit preserved moves for Git publication; absence never requests deletion.

Receipts are issued by trusted filesystem writers while their source is still
present. They bind the prior Git version and the exact preserved content. They
are cooperative publication preconditions, not an authorization credential.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re


@dataclass(frozen=True)
class MoveSource:
    source: str
    source_blob: str | None
    content_hash: str

    def to(self, destination: str) -> 'MoveReceipt':
        return MoveReceipt(self.source, destination, self.source_blob, self.content_hash)


@dataclass(frozen=True)
class MoveReceipt:
    source: str
    destination: str
    source_blob: str | None
    content_hash: str


def _relative(value):
    if not isinstance(value, str) or not value:
        raise ValueError('move path must be a repository-relative file')
    path = Path(value)
    if path.is_absolute() or '..' in path.parts or '.git' in path.parts or path.as_posix() != value:
        raise ValueError('move path must be canonical and repository-relative')
    return path


def blob_at(repo, base, relative):
    from knowledge_commit import _git, _read_at
    # The regular-file check must precede accepting an object identity.
    if _read_at(repo, base, relative) is None:
        return None
    return _git(repo, 'rev-parse', '--verify', f'{base}:{relative}')


def capture_source(repo, source: str) -> MoveSource:
    from knowledge_commit import _capture, _git
    _relative(source)
    repo = Path(repo).resolve(strict=True)
    base = _git(repo, 'rev-parse', '--verify', 'HEAD^{commit}')
    content, _ = _capture(repo, source)
    blob = blob_at(repo, base, source)
    if _git(repo, 'rev-parse', '--verify', 'HEAD^{commit}') != base:
        raise ValueError('source commit changed during move capture')
    return MoveSource(source, blob, hashlib.sha256(content).hexdigest())


def checked_moves(repo, moves):
    from knowledge_commit import _parent_fd
    import os
    if not isinstance(moves, (list, tuple)):
        raise ValueError('publication moves require explicit receipts')
    seen = set()
    for move in moves:
        if not isinstance(move, MoveReceipt):
            raise ValueError('publication move is not a writer receipt')
        for raw in (move.source, move.destination):
            _relative(raw)
            if raw in seen:
                raise ValueError('publication moves overlap')
            seen.add(raw)
        if ((move.source_blob is not None and
             (not isinstance(move.source_blob, str)
              or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', move.source_blob)))
                or not isinstance(move.content_hash, str)
                or not re.fullmatch(r'[0-9a-f]{64}', move.content_hash)):
            raise ValueError('publication move has invalid content identities')
        # Missing source is expected, but links and reappearing files are not.
        parent = _parent_fd(repo, move.source)
        try:
            try:
                os.stat(Path(move.source).name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError('moved source reappeared; retain both versions')
        finally:
            os.close(parent)
    return tuple(moves)


def validate_captures(repo, base, moves, captured):
    from knowledge_commit import _read_at
    checked_moves(repo, moves)
    for move in moves:
        content = captured[move.destination][0]
        if hashlib.sha256(content).hexdigest() != move.content_hash:
            raise ValueError('archive changed after the move; retain every version')
        source = blob_at(repo, base, move.source)
        if source is not None and source != move.source_blob:
            raise ValueError('publication source has a newer version; deletion refused')
        destination = _read_at(repo, base, move.destination)
        if destination is not None and destination != content:
            raise ValueError('publication would replace an independent archive')


def verify_archive_blob(move, content):
    if hashlib.sha256(content).hexdigest() != move.content_hash:
        raise ValueError('Git filters changed preserved archive bytes; publication refused')
