#!/usr/bin/env python3
"""Which branches still hold content the trunk does not have.

Written for the 2026-09-16 cleanup, where ~80 branches had accumulated and
"merged or not" was the wrong question: most of the audit fixes were landed by
re-application rather than by merge, so `git branch --merged` called them
unmerged and `git cherry` -- which compares patch-ids -- called an adapted
landing outstanding. Both answers would have kept dead branches alive, and the
cost of that is not clutter: it is that a branch which DOES hold unlanded work
sits in a list nobody trusts.

The question this asks instead is about content, per file:

    for every path the branch changed since it forked, does the branch's blob
    equal the trunk's blob?

All equal -> the trunk already has everything this branch says, whatever route
it took, and the branch is safe to delete. Any differing -> report it, with the
paths, and let a human look. The test is deliberately one-sided: a trunk that
edited a file FURTHER after landing the branch's change reads as differing, so
this over-reports rather than proposing a delete it cannot justify.

    git_branch_hygiene.py [--repo DIR] [--trunk main] [--remote] [--json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(['git', '-C', str(repo), *args],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f'git {" ".join(args)}: {r.stderr.strip()}')
    return r.stdout


def branches(repo: Path, trunk: str, remote: bool) -> list[str]:
    fmt = '%(refname:short)'
    args = ['for-each-ref', '--format', fmt,
            'refs/remotes/origin' if remote else 'refs/heads']
    names = [b for b in git(repo, *args).splitlines() if b.strip()]
    # `%(refname:short)` renders refs/remotes/origin/HEAD as bare "origin", not
    # "origin/HEAD" -- so the symbolic ref arrives looking like a branch called
    # after the remote itself, and anything keyed on its name creates a ref that
    # blocks every sibling. Skip both spellings.
    skip = {trunk, f'origin/{trunk}', 'origin/HEAD', 'origin'}
    return [b for b in names if b not in skip]


def blob(repo: Path, rev: str, path: str) -> str | None:
    r = subprocess.run(['git', '-C', str(repo), 'rev-parse', f'{rev}:{path}'],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def landed_earlier(repo: Path, branch: str, trunk: str, path: str) -> bool:
    """Did the trunk ever HOLD the branch's version of this file?

    The blob test alone is one-sided in a way that matters here: a change that
    landed and was then built on further leaves the trunk's blob different from
    the branch's, and reads as outstanding. It is not -- the branch's blob is
    in the trunk's history for that path. `projection-base-bootstrap` was the
    case that forced this: its whole change is in main, and main then grew the
    header dedupe on top of the same file.
    """
    want = blob(repo, branch, path)
    if want is None:
        return False
    revs = git(repo, 'rev-list', trunk, '--', path).split()
    return any(blob(repo, rev, path) == want for rev in revs)


def classify(repo: Path, branch: str, trunk: str) -> dict:
    try:
        base = git(repo, 'merge-base', trunk, branch).strip()
    except RuntimeError:
        base = ''
    if not base:
        # No common ancestor at all: a branch pushed from an unrelated history,
        # which happens when a space is re-initialised or a project is grafted
        # in. There is no "does the trunk already have this" to answer, and it
        # is certainly not safe to delete, so say so rather than crash -- one
        # such branch used to abort the whole report for its repository.
        return {'branch': branch, 'verdict': 'unrelated', 'ahead': 0,
                'changed': 0, 'differing': []}
    tip = git(repo, 'rev-parse', branch).strip()
    if base == tip:
        return {'branch': branch, 'verdict': 'merged', 'differing': []}
    changed = [p for p in git(repo, 'diff', '--name-only', base, branch).splitlines() if p]
    differing = [p for p in changed if blob(repo, branch, p) != blob(repo, trunk, p)]
    ahead = len(git(repo, 'rev-list', f'{trunk}..{branch}').splitlines())
    verdict = 'superseded'
    if differing:
        stale = [p for p in differing if not landed_earlier(repo, branch, trunk, p)]
        verdict = 'built-on' if not stale else 'outstanding'
        differing = stale or differing
    return {
        'branch': branch,
        'verdict': verdict,
        'ahead': ahead,
        'changed': len(changed),
        'differing': differing,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--repo', default='.', type=Path)
    ap.add_argument('--trunk', default='main')
    ap.add_argument('--remote', action='store_true',
                    help='classify refs/remotes/origin/* instead of local heads')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)

    repo = a.repo.resolve()
    rows = [classify(repo, b, a.trunk) for b in branches(repo, a.trunk, a.remote)]
    if a.json:
        print(json.dumps(rows, indent=2))
        return 0
    for verdict in ('merged', 'superseded', 'built-on', 'outstanding', 'unrelated'):
        group = [r for r in rows if r['verdict'] == verdict]
        if not group:
            continue
        print(f'== {verdict} ({len(group)})')
        for r in group:
            extra = ''
            if verdict in ('outstanding', 'unrelated'):
                shown = ', '.join(r['differing'][:3])
                more = f' +{len(r["differing"]) - 3}' if len(r['differing']) > 3 else ''
                extra = f'  [{r["ahead"]} ahead] {shown}{more}'
            print(f'   {r["branch"]}{extra}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
