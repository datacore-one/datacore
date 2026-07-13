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

So knowledge writes must be BRANCH-INDEPENDENT. Git can do this natively:
hash-object / commit-tree / update-ref will land a commit on the default branch
while HEAD stays on the feature branch, touching neither the index nor the
working tree. No checkout. No worktree. Works across any number of repos, which
is what "the system should deal flexibly with several repos" actually requires.

Deliberately deterministic. A journal entry's destination is not a judgment call
and must not cost an LLM round-trip — there are hundreds of these. Memory is for
deciding when the solution is unclear; this is not one of those times.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

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
    pass


def _git(repo: Path, *args: str, env=None, check=True) -> str:
    r = subprocess.run(['git', *args], cwd=repo, capture_output=True,
                       text=True, env=env)
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {(r.stderr or '').strip()}")
    return (r.stdout or '').strip()


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


def _blob_mode(repo: Path, rel: str) -> str:
    return '100755' if os.access(repo / rel, os.X_OK) else '100644'


def commit_to_branch(repo: Path, branch: str, paths, message: str,
                     push: bool = True) -> str:
    """Commit `paths` onto `branch` WITHOUT checking it out.

    Builds the tree with plumbing against the branch tip, so HEAD, the index and
    the working tree are all left exactly as they were. This is what lets an
    agent mid-feature-branch still land its journal entry on main.

    Returns the new commit sha, or '' if there was nothing to do.
    """
    paths = [p for p in paths if (repo / p).is_file()]
    if not paths:
        return ''

    head = current_branch(repo)

    # If we are already standing on the target, there is nothing clever to do —
    # and plumbing would be actively WRONG here: moving the ref under a checked
    # out branch without touching index/worktree makes the tree read as dirty in
    # reverse. Use the ordinary path.
    if head == branch:
        for p in paths:
            _git(repo, 'add', '--', p)
        if not _git(repo, 'diff', '--cached', '--name-only'):
            return ''
        _git(repo, 'commit', '-m', message)
        sha = _git(repo, 'rev-parse', 'HEAD')
        if push:
            _git(repo, 'push', 'origin', branch)
        return sha

    # HEAD is elsewhere. Land on `branch` via plumbing.
    try:
        base = _git(repo, 'rev-parse', f'refs/heads/{branch}')
    except GitError:
        raise GitError(f"{repo.name}: no local branch '{branch}' to commit onto")

    tmp_index = tempfile.NamedTemporaryFile(delete=False, suffix='.idx')
    tmp_index.close()
    env = {**os.environ, 'GIT_INDEX_FILE': tmp_index.name}

    try:
        # Start from the target branch's tree, NOT from our own index — our index
        # reflects the feature branch and would drag its changes along.
        _git(repo, 'read-tree', base, env=env)

        for rel in paths:
            blob = _git(repo, 'hash-object', '-w', '--', str(repo / rel), env=env)
            _git(repo, 'update-index', '--add', '--cacheinfo',
                 f"{_blob_mode(repo, rel)},{blob},{rel}", env=env)

        tree = _git(repo, 'write-tree', env=env)

        # No-op guard: if the tree is identical to the branch tip's, committing
        # would create an empty commit on every wrap-up forever.
        if tree == _git(repo, 'rev-parse', f'{base}^{{tree}}'):
            return ''

        sha = _git(repo, 'commit-tree', tree, '-p', base, '-m', message, env=env)
        _git(repo, 'update-ref', f'refs/heads/{branch}', sha, base,
             env=env)  # old-value guard: refuses if branch moved under us
    finally:
        os.unlink(tmp_index.name)

    # The file is now on `branch`, but it is still sitting UNTRACKED in this
    # branch's working tree — and git will then refuse to switch branches:
    #
    #   error: The following untracked working tree files would be overwritten
    #          by checkout: journal/2026-07-13.md
    #
    # That would deadlock the agent onto the feature branch, and in particular it
    # would break check_and_repair_git()'s stray-branch recovery, which works by
    # checking out the default branch. A fix that jams the other fix.
    #
    # The content is committed and safe on `branch`, so the working-tree copy is
    # litter. Drop it — but only if it is untracked HERE (a tracked file is a real
    # modification on this branch and is not ours to throw away), and only after
    # confirming the blob actually landed.
    _drop_landed_untracked(repo, branch, paths)

    if push:
        _git(repo, 'push', 'origin', branch)

    return sha


def _drop_landed_untracked(repo: Path, branch: str, paths) -> None:
    """Remove working-tree copies of files we just committed onto another branch.

    Only touches paths that are untracked on the CURRENT branch and verifiably
    present in `branch`. Anything else is left exactly as we found it.
    """
    for rel in paths:
        tracked = _git(repo, 'ls-files', '--', rel, check=False)
        if tracked:
            continue  # tracked here — a real change on this branch, not litter

        landed = _git(repo, 'cat-file', '-e', f'{branch}:{rel}', check=False)
        # cat-file -e prints nothing and exits 0 on success; use rev-parse to be sure.
        try:
            _git(repo, 'rev-parse', f'{branch}:{rel}')
        except GitError:
            continue  # did not actually land — keep the file, do not lose data

        (repo / rel).unlink(missing_ok=True)


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
