#!/usr/bin/env python3
"""Route a commit to the branch it BELONGS on, instead of the branch you happen
to be standing on.

The problem this exists to kill
------------------------------
Nightshift's claim.py ran `git add -A` -> `git commit` -> `git push` with no
branch argument. It worked perfectly: 610 commits, on schedule, for two months —
all onto ops/b17-sprint-claim in 5-plur, because that is where HEAD happened to
be. 52 zettels, 19 literature notes, every weekly content calendar since
mid-June, 15 journal entries. None on main. None visible to anyone.

The defect was never the frequency of commits. It was the DESTINATION. So
"agents should commit their work" is necessary and not sufficient — wire that up
without this and you get beautifully authored journal entries pushed onto a
stray branch. The same disaster with better prose.

The insight
-----------
A repo like 5-plur holds two kinds of content with OPPOSITE branching semantics
in one working tree:

  CODE       belongs on a feature branch, merges via PR. Branching is correct.
  KNOWLEDGE  journals, zettels, cadence logs, content calendars, org tasks.
             No review step. Append-only. WORTHLESS until it is on the default
             branch, because that is what every other agent and machine reads.

When an agent checks out a feature branch to do code work, its knowledge writes
follow it there — same working tree, same HEAD. That is the whole bug.

Knowledge writes must be branch-independent. Cross-branch publication captures
source bytes once, reserves the target branch with Git worktree ownership, and
checks a detached commit with the configured hooks. Only that verified tree and
parent may advance the reserved branch. The shared checkout and index stay
untouched. Recovery workspaces are retained; their reclamation is separate.

Deliberately deterministic. A journal entry's destination is not a judgment call
and must not cost an LLM round-trip — there are hundreds of these. Memory is for
deciding when the solution is unclear; this is not one of those times.
"""

import os
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from git_inventory import require_resolved

from worktree_lifecycle import (
    allocate_publication_workspace, git_environment, publication_hooks, retire_worktree,
)

# Content that is worthless until it reaches the default branch. Everything here
# is append-only and unreviewed by design — there is no PR for a journal entry.
KNOWLEDGE_PREFIXES = (
    'journal/',
    'notes/',
    'org/',
    '0-inbox/',
    '1-tracks/',
    '3-knowledge/',
    '4-outbox/',
    '.datacore/state/',
    '.datacore/learning/',
)

# Content that legitimately lives on a feature branch and merges via PR.
# Listed for intent; anything not KNOWLEDGE is treated this way.
CODE_PREFIXES = (
    '2-projects/',
    '.datacore/modules/',
    'src/',
    'packages/',
)


class GitError(RuntimeError):
    non_fast_forward = False


def _push_commit(repo: Path, branch: str, sha: str) -> None:
    from git_publication import push_arguments
    try:
        args = push_arguments(sha, f'refs/heads/{branch}')
    except ValueError:
        raise GitError('Publication commit/ref is invalid; no push attempted') from None
    _git(repo, *args)


def _run_git(repo: Path, *args: str, env=None, input_bytes=None):
    from git_publication import durable_git_arguments
    environment = git_environment()
    if env is not None:
        environment.update(env)
    try:
        return subprocess.run(durable_git_arguments(['git', '--literal-pathspecs', *args]), cwd=repo, capture_output=True,
                              input=input_bytes, env=environment, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        raise GitError('Git publication failed or timed out; local work retained') from None


def _git(repo: Path, *args: str, env=None, check=True, input_bytes=None) -> str:
    r = _run_git(repo, *args, env=env, input_bytes=input_bytes)
    if check and r.returncode != 0:
        # Hook/transport output can include private file contents or URL credentials.
        error = GitError('Git publication command failed; inspect local Git configuration and hooks')
        error.non_fast_forward = 'push' in args and any(
            marker in (r.stderr or b'') for marker in (b'non-fast-forward', b'fetch first', b'behind'))
        raise error
    return (r.stdout or b'').decode('utf-8', errors='replace').strip()


def default_branch(repo: Path) -> str:
    """The repo's default branch, from origin/HEAD. Falls back to main."""
    try:
        ref = _git(repo, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD')
    except GitError:
        return 'main'
    return ref.split('/', 1)[1] if ref.startswith('origin/') else 'main'


def current_branch(repo: Path) -> str:
    return _git(repo, 'branch', '--show-current', check=False)


def is_knowledge(path: str) -> bool:
    """Does this path hold content that is useless anywhere but the default branch?"""
    p = path.lstrip('./')
    return p.startswith(KNOWLEDGE_PREFIXES)


def classify(paths) -> dict:
    """Split paths by where they belong. This is the whole routing decision."""
    knowledge, code = [], []
    for p in paths:
        (knowledge if is_knowledge(p) else code).append(p)
    return {'knowledge': knowledge, 'code': code}


def _parent_fd(repo: Path, rel: str, *, create=False) -> int:
    """Walk relative directories without following links, anchored to this root."""
    fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in Path(rel).parts[:-1]:
            if create:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _capture(repo: Path, rel: str) -> tuple[bytes, str]:
    """Read one regular file once; both validation and the commit use these bytes."""
    parent = _parent_fd(repo, rel)
    try:
        fd = os.open(Path(rel).name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(fd, 'rb') as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise GitError('publication source must be a regular file')
            content = source.read()
            after = os.fstat(source.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise GitError('publication source changed during capture; retry from current data')
            return content, '100755' if before.st_mode & 0o111 else '100644'
    finally:
        os.close(parent)


def _write_capture(worktree: Path, rel: str, content: bytes, mode: str) -> None:
    parent = _parent_fd(worktree, rel, create=True)
    name = '.publication-capture-' + uuid.uuid4().hex
    try:
        # Do not truncate an inode created by a checkout hook: it may be a
        # hard link. Replace from an exclusively created private file instead.
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        with os.fdopen(fd, 'wb') as target:
            target.write(content)
            os.fchmod(target.fileno(), 0o755 if mode == '100755' else 0o644)
        os.replace(name, Path(rel).name, src_dir_fd=parent, dst_dir_fd=parent)
    finally:
        os.close(parent)


def _read_at(repo: Path, ref: str, rel: str) -> bytes | None:
    mode = _git(repo, 'ls-tree', ref, '--', rel)
    if not mode:
        return None
    if not mode.startswith(('100644 ', '100755 ')):
        raise GitError('publication target is not a regular file')
    result = _run_git(repo, 'show', f'{ref}:{rel}')
    if result.returncode:
        raise GitError('cannot read publication base')
    return result.stdout


def _capture_outputs(repo, base, paths, moves):
    from publication_moves import validate_captures
    removed = {move.source for move in moves}
    captured = {rel: _capture(repo, rel) for rel in paths if rel not in removed}
    validate_captures(repo, base, moves, captured)
    return captured


def _expected_tree(repo: Path, base: str, paths: list[str], moves=()) -> str:
    """Capture the authorized tree without borrowing or changing the index.

    Normal Git commit hooks still run. This independent tree is the value their
    resulting commit must match before it can be acknowledged or published.
    Git retains the captured blobs even if a later writer changes the source.
    """
    from publication_moves import verify_archive_blob
    captured = _capture_outputs(repo, base, paths, moves)
    archives = {move.destination: move for move in moves}
    with tempfile.TemporaryDirectory(prefix='datacore-publication-index-') as directory:
        environment = {'GIT_INDEX_FILE': str(Path(directory) / 'index')}
        _git(repo, 'read-tree', base, env=environment)
        for rel, (content, mode) in captured.items():
            mode = _publication_mode(repo, base, rel, mode)
            blob = _git(repo, 'hash-object', '-w', f'--path={rel}', '--stdin', input_bytes=content)
            if rel in archives:
                verify_archive_blob(archives[rel], _run_git(repo, 'cat-file', 'blob', blob).stdout)
            _git(repo, 'update-index', '--add', '--cacheinfo', f'{mode},{blob},{rel}', env=environment)
        for move in moves:
            _git(repo, 'update-index', '--force-remove', '--', move.source, env=environment)
        return _git(repo, 'write-tree', env=environment)


def _publication_mode(repo: Path, base: str, rel: str, mode: str) -> str:
    if _git(repo, 'config', '--bool', '--get', 'core.filemode', check=False) == 'false':
        # Match git add on filesystems where executable bits are unreliable:
        # keep a tracked mode, and introduce ordinary files without +x.
        existing = _git(repo, 'ls-tree', base, '--', rel).split(' ', 1)[0]
        return existing if existing in ('100644', '100755') else '100644'
    return mode


def _commit_off_branch(repo: Path, branch: str, paths: list[str], message: str, push: bool,
                       *, append_only: bool = False, publication=None, moves=()) -> str:
    """Reserve the target checkout; validate a detached commit before advancing it."""
    base = (publication.data['target_head'] if publication is not None else
            _git(repo, 'rev-parse', '--verify', f'refs/heads/{branch}^{{commit}}'))
    source = (publication.data['source_head'] if publication is not None else
              _git(repo, 'rev-parse', '--verify', 'HEAD^{commit}'))
    from publication_moves import checked_moves, verify_archive_blob
    captured = _capture_outputs(repo, base, paths, moves)
    archives = {move.destination: move for move in moves}
    hooks = publication_hooks(repo)
    parent = allocate_publication_workspace(repo)
    reservation, candidate = parent / 'target', parent / 'candidate'
    created = []
    sha = ''
    try:
        # Git's own worktree ownership prevents a normal concurrent checkout of
        # this branch. If another writer already has it, no ref is changed.
        for path, target, detached in ((reservation, branch, False), (candidate, base, True)):
            try:
                _git(repo, '-c', f'core.hooksPath={hooks}', 'worktree', 'add',
                     *(['--detach'] if detached else []), '--', str(path), target)
            finally:
                # A rejecting post-checkout hook can return failure after Git
                # has registered the worktree. Keep and retire that data too.
                if (path / '.git').exists():
                    created.append(path)
        if _git(reservation, 'rev-parse', 'HEAD') != base:
            raise GitError('publication destination advanced during reservation')
        if _git(candidate, 'status', '--porcelain', '--untracked-files=all'):
            raise GitError('checkout hook changed publication workspace; all output retained')
        for rel, (content, mode) in captured.items():
            mode = _publication_mode(candidate, base, rel, mode)
            # --path preserves the target tree's clean filters and encoding/EOL
            # rules while still reading only the previously captured bytes.
            blob = _git(candidate, 'hash-object', '-w', f'--path={rel}', '--stdin', input_bytes=content)
            normalized = _run_git(candidate, 'cat-file', 'blob', blob)
            if normalized.returncode:
                raise GitError('cannot verify captured publication blob')
            if rel in archives:
                verify_archive_blob(archives[rel], normalized.stdout)
            original, destination = _read_at(repo, source, rel), _read_at(repo, base, rel)
            if (append_only and destination is not None and normalized.stdout != destination
                    and (not normalized.stdout.startswith(destination)
                         or (destination and not destination.endswith(b'\n')))):
                raise GitError('append-only publication would replace existing bytes; both versions retained')
            preserving_append = (destination is not None and (append_only or Path(rel).suffix in {'.md', '.org'})
                                 and destination.endswith(b'\n') and normalized.stdout.startswith(destination))
            if original != destination and normalized.stdout != destination and not preserving_append:
                raise GitError(f'{rel}: destination has independent changes; reconcile both versions before publication')
            _write_capture(candidate, rel, content, mode)
            _git(candidate, 'update-index', '--add', '--cacheinfo', f'{mode},{blob},{rel}')
        for move in moves:
            if _read_at(candidate, base, move.source) is not None:
                _git(candidate, 'rm', '--', move.source)
        tree = _git(candidate, 'write-tree')
        if publication is not None:
            publication.expected(tree)
        if tree != _git(repo, 'rev-parse', f'{base}^{{tree}}'):
            _git(candidate, '-c', f'core.hooksPath={hooks}', 'commit', '-m', message)
            sha = _git(candidate, 'rev-parse', 'HEAD')
            if (_git(candidate, 'rev-parse', 'HEAD^{tree}') != tree
                    or _git(candidate, 'show', '-s', '--format=%P', 'HEAD') != base
                    or _git(candidate, 'diff', '--name-only')
                    or _git(candidate, 'diff', '--cached', '--name-only')):
                raise GitError('commit hook changed publication content or parents; candidate retained')
            checked_moves(repo, moves)
            _git(repo, 'update-ref', f'refs/heads/{branch}', sha, base)
        if publication is not None:
            publication.verified = True
    finally:
        errors = []
        for path in reversed(created):
            try:
                retire_worktree(repo, path)
            except RuntimeError as exc:
                errors.append(str(exc))
        if errors:
            raise GitError('publication workspace retirement failed; retained work requires inspection')
    if push:
        # A later local branch advance is not part of this publication.
        _push_commit(repo, branch, sha or base)
    return sha


def _push_converging(repo: Path, branch: str, sha: str) -> None:
    """Push, and on a non-fast-forward rejection converge and retry once.

    MERGE, NEVER REBASE (DIP-0046). 6-meridian sat ahead 10 / behind 8 on
    2026-08-28 because another writer had pushed first and this code gave up
    on the first rejection — with an alert claiming the work was
    'uncommitted' when it was committed and merely unpushed.
    """
    try:
        _push_commit(repo, branch, sha)
        return
    except GitError as e:
        msg = str(e)
        if not e.non_fast_forward:
            raise GitError(
                f"{repo.name}: committed locally on {branch} ({sha[:10]}) but "
                f"push failed — {msg}")
    if current_branch(repo) != branch or _git(repo, 'rev-parse', 'HEAD') != sha:
        raise GitError('Publication source advanced; captured work retained for separate reconciliation')
    try:
        from git_integration import integrate
        integrate(repo, sha, f'refs/heads/{branch}')
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise GitError(
            f"{repo.name}: committed locally on {branch} ({sha[:10]}); remote "
            'integration was not acknowledged; source and private recovery state retained') from None


def commit_to_branch(repo: Path, branch: str, paths, message: str,
                     push: bool = True, *, append_only: bool = False,
                     expected_head: str | None = None, moves=()) -> str:
    """Commit `paths` onto an explicit branch.

    When HEAD is elsewhere, only private worktrees are checked out and the
    shared HEAD/index/files stay unchanged. When HEAD is on the target, the
    pathspec commit must match an independently captured tree and parent before
    acknowledgment or publication. This helper does not establish
    process isolation or grant execution ownership.

    append_only requires an isolated destination and preserves its complete
    newline-terminated byte prefix, including when source HEAD already contains
    that version. It refuses truncation or replacement of published log entries.

    expected_head binds a caller's prior target snapshot, including no-change
    results. A concurrent commit cannot substitute a different publication base.

    moves pairs an explicitly captured source version with its preserved archive.
    Only those missing source paths may be deleted; newer source or archive
    versions, reappearing files and lossy Git filters refuse publication.

    Returns the new commit sha, or '' if there was nothing to do.
    """
    repo = Path(repo).resolve()
    try:
        require_resolved(repo)
    except RuntimeError:
        raise GitError('Source inventory is unavailable or unresolved; no publication attempted') from None
    _git(repo, 'check-ref-format', f'refs/heads/{branch}')
    if expected_head is not None:
        from git_publication import _oid
        try:
            _oid(expected_head)
        except ValueError:
            raise GitError('expected publication head must be an immutable object identity') from None
    from publication_moves import checked_moves
    try:
        moves = checked_moves(repo, moves)
        if append_only and moves:
            raise ValueError('append-only publication cannot move files')
    except (OSError, TypeError, ValueError) as exc:
        raise GitError('invalid preserved move; no publication attempted') from exc
    removed = {move.source for move in moves}
    checked = []
    for value in paths:
        path = Path(value)
        target = repo / path
        if (path.is_absolute() or '..' in path.parts or '.git' in path.parts
                or target.is_symlink() or not target.resolve().is_relative_to(repo)):
            raise GitError('publication path escapes its repository')
        if not target.is_file():
            raise GitError('publication source is missing or not a regular file; no deletion was requested')
        checked.append(path.as_posix())
    paths = checked
    if set(paths).intersection(removed):
        raise GitError('a move source cannot also be an output file')
    for move in moves:
        paths.extend([move.source, move.destination])
    paths = list(dict.fromkeys(paths))
    if not paths:
        return ''

    from publication_state import reserve
    try:
        with reserve(repo, branch, paths) as reservation:
            if expected_head is not None and reservation.data['target_head'] != expected_head:
                raise GitError('publication target advanced since caller capture; work retained')
            return _commit_selected(repo, branch, paths, message, push, append_only, reservation, moves)
    except (OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        if isinstance(exc, GitError):
            raise
        raise GitError('publication failed; retained state and local work require inspection') from None


def _commit_selected(repo, branch, paths, message, push, append_only, reservation, moves=()):

    head = current_branch(repo)
    source_branch = f'refs/heads/{head}' if head else ''
    if (source_branch != reservation.data['source_branch']
            or _git(repo, 'rev-parse', '--verify', 'HEAD^{commit}') != reservation.data['source_head']):
        raise GitError('publication source advanced since reservation; work retained')
    if append_only and head == branch:
        raise GitError('append-only publication requires an isolated destination branch')

    # If we are already standing on the target, there is nothing clever to do —
    # and plumbing would be actively WRONG here: moving the ref under a checked
    # out branch without touching index/worktree makes the tree read as dirty in
    # reverse. Use the ordinary path.
    if head == branch:
        base = reservation.data['source_head']
        tree = _expected_tree(repo, base, paths, moves)
        reservation.expected(tree)
        if current_branch(repo) != branch or _git(repo, 'rev-parse', 'HEAD') != base:
            raise GitError('publication source changed during capture; retained work requires reconciliation')
        if tree == _git(repo, 'rev-parse', f'{base}^{{tree}}'):
            reservation.verified = True
            if push:
                _push_converging(repo, branch, base)
            return ''
        # An untracked source or an already-published deletion has no index
        # entry to stage. Its paired destination is still explicitly verified.
        selected = [p for p in paths if (repo / p).is_file() or _read_at(repo, base, p) is not None]
        for p in selected:
            _git(repo, 'add', '--', p)
        # Commit with an explicit pathspec, not the whole index. A pathspec
        # commit runs against a temporary index holding only these paths, so
        # leftover staged files from an earlier failed run can neither ride
        # along nor make the pre-commit hook reject OUR files for THEIR
        # violations. 1-datafund carried two stray staged reports/ files from
        # 2026-08-24 that blocked every batch-end commit for four days —
        # journal, org and inbox updates all bounced off a hook complaint
        # about files this code never touched.
        # Hook output is not a machine-readable success signal. A failing hook
        # saying "nothing to commit" must still refuse publication.
        _git(repo, 'commit', '-m', message, '--', *selected)
        sha = _git(repo, 'rev-parse', '--verify', 'HEAD^{commit}')
        if (current_branch(repo) != branch
                or _git(repo, 'rev-parse', f'{sha}^{{tree}}') != tree
                or _git(repo, 'show', '-s', '--format=%P', sha) != base):
            raise GitError('commit content, parent or branch changed; local commits retained, publication refused')
        reservation.verified = True
        if push:
            _push_converging(repo, branch, sha)
        return sha

    try:
        return _commit_off_branch(repo, branch, paths, message, push, append_only=append_only,
                                  publication=reservation, moves=moves)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        if isinstance(exc, GitError):
            raise
        raise GitError('publication failed; source data and recovery workspaces retained') from None


def commit_knowledge(repo: Path, paths, message: str, push: bool = True) -> dict:
    """Route each path to where it belongs, and land the knowledge half.

    Knowledge always goes to the default branch — no matter what HEAD is doing.
    Code is left alone: it belongs on whatever branch the agent is working on,
    and merges via PR like it should.
    """
    repo = Path(repo)
    split = classify(paths)
    dflt = default_branch(repo)

    sha = ''
    if split['knowledge']:
        sha = commit_to_branch(repo, dflt, split['knowledge'], message, push=push)

    return {
        'repo': repo.name,
        'head': current_branch(repo),
        'default_branch': dflt,
        'knowledge': split['knowledge'],
        'code_left_alone': split['code'],
        'commit': sha,
    }


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith('--')]
    if len(argv) < 3:
        print(__doc__)
        print("Usage: knowledge_commit.py <repo> <message> <path> [path...] [--no-push]")
        return 2

    repo, message, paths = Path(argv[0]), argv[1], argv[2:]
    r = commit_knowledge(repo, paths, message, push='--no-push' not in sys.argv)

    print(f"{r['repo']}: HEAD={r['head']} -> knowledge landed on {r['default_branch']}")
    for p in r['knowledge']:
        print(f"  + {p}")
    for p in r['code_left_alone']:
        print(f"  . {p}  (code — left on {r['head']})")
    print(f"  commit: {r['commit'] or '(nothing to do)'}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
