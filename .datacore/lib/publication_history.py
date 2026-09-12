"""Keep verified local commit provenance separate from file selection.

A selected output does not authorize its ancestors. Before automatic publication,
every commit absent from the current destination must have a durable verification
receipt for that destination. Receipts survive failed pushes and process restarts;
no-op commits never create receipts for existing, unverified history.

These Git refs are a cooperative boundary, not protection from another process
with permission to modify the repository's Git storage.
"""
import hashlib
from pathlib import Path
import uuid


def origin_url(repo, *, optional=False):
    from knowledge_commit import _git, GitError
    if optional and 'origin' not in _git(repo, 'remote').splitlines():
        return None
    fetched = _git(repo, 'remote', 'get-url', '--all', 'origin').splitlines()
    pushed = _git(repo, 'remote', 'get-url', '--push', '--all', 'origin').splitlines()
    if len(fetched) != 1 or fetched != pushed:
        raise GitError('Publication requires one identical fetch/push origin')
    url = fetched[0]
    # Bind relative filesystem remotes to this repository, including when a
    # receipt is subsequently read from a linked worktree elsewhere.
    if ':' not in url:
        url = str((Path(repo) / url).resolve())
    return url


def scope_for(repo, branch, *, origin=None):
    from knowledge_commit import _git
    _git(repo, 'check-ref-format', f'refs/heads/{branch}')
    url = origin if origin is not None else origin_url(repo, optional=True)
    if url is None:
        return None  # Local commits are allowed, but cannot approve a later new remote.
    return hashlib.sha256((url + '\0refs/heads/' + branch).encode()).hexdigest()


def receipt_ref(scope, commit):
    return f'refs/datacore/publication-verified/{scope}/{commit}'


def record(repo, scope, commit, parent, tree):
    from knowledge_commit import _git, GitError
    from git_publication import _oid
    for value in (commit, parent, tree):
        _oid(value)
    if (_git(repo, 'show', '-s', '--format=%P', commit) != parent
            or _git(repo, 'rev-parse', commit + '^{tree}') != tree):
        raise GitError('Publication provenance does not match verified content and parent')
    if scope is not None:
        ref = receipt_ref(scope, commit)
        previous = _git(repo, 'rev-parse', '--verify', ref, check=False)
        if previous != commit:
            _git(repo, 'update-ref', ref, commit, '0' * len(commit))


def require_verified(repo, branch, source, base, *, origin=None):
    from knowledge_commit import _git, GitError
    from git_publication import _oid
    _oid(source)
    _oid(base)
    if _git(repo, 'rev-parse', '--is-shallow-repository') != 'false':
        raise GitError('Publication requires complete history; shallow repository retained')
    grafts = Path(_git(repo, 'rev-parse', '--path-format=absolute', '--git-path', 'info/grafts'))
    if grafts.exists():
        raise GitError('Publication cannot authorize rewritten graft history')
    scope = scope_for(repo, branch, origin=origin)
    outgoing = _git(repo, 'rev-list', '--max-count=10001', source, '--not', base).splitlines()
    if len(outgoing) > 10000:
        raise GitError('Publication history exceeds automatic verification limit; reconcile explicitly')
    for commit in outgoing:
        _oid(commit)
        if _git(repo, 'rev-parse', '--verify', receipt_ref(scope, commit), check=False) != commit:
            raise GitError('Publication includes unverified local history; all local work retained')


def checked_destination(repo, branch, source):
    """Fetch an exact destination and verify every outgoing commit against it.

    Failed attempts retain the fetched reference for recovery. The caller may
    remove it after acknowledgement. A missing remote branch needs explicit
    initialization; selecting one file cannot authorize an entire root history.
    """
    from knowledge_commit import _git
    origin = origin_url(repo)
    ref = 'refs/datacore/publication-bases/' + uuid.uuid4().hex
    _git(repo, 'fetch', '--no-tags', '--no-write-fetch-head', origin,
         f'refs/heads/{branch}:{ref}')
    base = _git(repo, 'rev-parse', '--verify', ref + '^{commit}')
    require_verified(repo, branch, source, base, origin=origin)
    return origin, ref, base
