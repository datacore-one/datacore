#!/usr/bin/env python3
"""Audit every git repo in a Datacore installation for stranded work.

Answers one question: is any work sitting on this machine that will never
reach anyone else?

Work gets stranded three ways, in descending order of danger:

  UNCOMMITTED  — exists only in this working tree. Not in git objects, not
                 on any remote. A disk failure loses it. No health check
                 anywhere notices it.
  UNPUSHED     — committed, so it survives a crash, but it lives on no
                 remote. Invisible to every other machine and person.
  STRAY BRANCH — HEAD is on a branch other than the repo default. Commits
                 land there and are pushed, so nothing *looks* broken —
                 but they never reach main, so no one reads them.

The stray-branch case is the quiet one. A healthy checkout on the wrong
branch is indistinguishable from a healthy checkout on main by every other
signal: no rebase in flight, HEAD not detached, upstream tracking intact.
That is how ~/Data/5-plur sat on ops/b17-sprint-claim from 2026-05 to
2026-07 and took 610 commits of journals, zettels and content calendars
with it, while the nightly git health check reported everything fine.

Read-only. Never writes, never fetches, never repairs.

Usage:
    python3 .datacore/lib/git_fleet_audit.py [data_dir] [--json]
"""

import json
import subprocess
import sys
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    """Run a git command, returning stripped stdout ('' on any failure)."""
    r = subprocess.run(
        ['git', *args], cwd=repo, capture_output=True, text=True
    )
    return (r.stdout or '').strip() if r.returncode == 0 else ''


def default_branch(repo: Path) -> str:
    """Resolve the repo's default branch from origin/HEAD, falling back to main."""
    ref = git(repo, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD')
    return ref.split('/', 1)[1] if ref.startswith('origin/') else 'main'


def audit_repo(repo: Path) -> dict:
    """Audit one repo. Compares against local remote-tracking refs only — no
    network — so a stale fetch understates 'behind' but never invents work."""
    branch = git(repo, 'branch', '--show-current')
    default = default_branch(repo)

    dirty = [l for l in git(repo, 'status', '--porcelain').splitlines() if l.strip()]

    # Commits on HEAD that no remote branch contains — the unpushed set.
    unpushed = git(repo, 'log', '--oneline', '--not', '--remotes')
    unpushed_commits = [l for l in unpushed.splitlines() if l.strip()]

    # Divergence from the default branch, if we have a remote ref for it.
    ahead = behind = 0
    if git(repo, 'rev-parse', '--verify', f'origin/{default}'):
        counts = git(repo, 'rev-list', '--left-right', '--count',
                     f'origin/{default}...HEAD')
        if counts:
            parts = counts.split()
            if len(parts) == 2:
                behind, ahead = int(parts[0]), int(parts[1])

    on_stray = bool(branch) and branch != default
    detached = not branch

    problems = []
    if detached:
        problems.append('DETACHED HEAD')
    if on_stray:
        problems.append(f"STRAY BRANCH ({branch}, not {default})")
    if unpushed_commits:
        problems.append(f'{len(unpushed_commits)} UNPUSHED commit(s)')
    if dirty:
        problems.append(f'{len(dirty)} UNCOMMITTED file(s)')

    return {
        'repo': str(repo),
        'name': repo.name,
        'branch': branch or '(detached)',
        'default': default,
        'stray': on_stray,
        'detached': detached,
        'dirty_files': len(dirty),
        'unpushed': len(unpushed_commits),
        'ahead_of_default': ahead,
        'behind_default': behind,
        'problems': problems,
        'stranded': bool(problems),
    }


def find_repos(root: Path) -> list[Path]:
    """Every git repo in the installation: the root, each space, and any
    module or project repo nested inside them."""
    repos = []
    if (root / '.git').exists():
        repos.append(root)

    for sub in sorted(root.iterdir()):
        if not sub.is_dir() or sub.name.startswith('.'):
            continue
        if (sub / '.git').exists():
            repos.append(sub)

    # Modules and projects are frequently their own repos.
    for pattern in ('.datacore/modules/*/.git', '*/2-projects/*/.git'):
        for gitdir in root.glob(pattern):
            repos.append(gitdir.parent)

    return sorted(set(repos))


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    as_json = '--json' in sys.argv
    root = Path(args[0]).expanduser() if args else Path.home() / 'Data'

    if not root.exists():
        print(f"ERROR: {root} does not exist", file=sys.stderr)
        return 2

    results = [audit_repo(r) for r in find_repos(root)]
    stranded = [r for r in results if r['stranded']]

    if as_json:
        print(json.dumps({'root': str(root), 'repos': results}, indent=2))
        return 1 if stranded else 0

    print(f"Git fleet audit: {root}")
    print(f"{len(results)} repos, {len(stranded)} with stranded work\n")

    if not stranded:
        print("  All clean — every repo on its default branch, nothing unpushed.")
        return 0

    # Stray branches first: they are the ones no existing health check catches.
    for r in sorted(stranded, key=lambda x: (not x['stray'], -x['unpushed'])):
        flag = '!!' if r['stray'] or r['detached'] else ' *'
        print(f"{flag} {r['name']}")
        print(f"     branch: {r['branch']}  (default: {r['default']})")
        for p in r['problems']:
            print(f"     - {p}")
        if r['ahead_of_default']:
            print(f"     - {r['ahead_of_default']} commit(s) ahead of "
                  f"origin/{r['default']}, {r['behind_default']} behind")
        print()

    print("!! = work is invisible to other machines (stray/detached HEAD)")
    print(" * = work exists only on this disk (unpushed/uncommitted)")
    return 1


if __name__ == '__main__':
    sys.exit(main())
