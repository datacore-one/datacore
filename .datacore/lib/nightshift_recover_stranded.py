#!/usr/bin/env python3
"""Recover nightshift output stranded on un-mergeable run branches.

Between 2026-08-06 and 2026-08-11, nightshift's PR flow cut a
`nightshift/run-<ts>` branch in every space repo and then tried to land it with
`gh pr create`. Every space repo is Gitea-hosted, so `gh` refused ("none of the
git remotes configured for this repository point to a known GitHub host"), the
branch stayed, and the next run cut another one. 181 branches accumulated; 73
of them carry commits that never reached a default branch.

They are NOT fast-forwardable. Each run branched independently off the default,
so 18 sibling branches per repo each carry 2-8 commits. Merging all of them
would conflict repeatedly in the files every run touches (`org/next_actions.org`
above all, edited by 19 separate branches in 6-meridian).

So this recovers the part that is unambiguous: **files a branch ADDED that do
not exist on the default branch.** Those are the deliverables — reports, NAV
calculations, literature reviews, nightshift summaries — and landing them cannot
conflict, because nothing on the default branch claims that path.

Deliberately NOT recovered, and reported instead of silently dropped:
  - modifications to files that already exist (`org/next_actions.org`, heartbeat
    and cadence logs). The default branch has since moved on; a stranded task
    state from 2026-08-06 is stale, not authoritative, and replaying it would
    resurrect claims and TODO states that later runs already superseded.
  - deletions, always. See git_fleet_sync.py for why a sweep never propagates
    one.

Dry-run by default. Pass --execute to stage and commit.

Usage:
    python3 nightshift_recover_stranded.py [data_dir] [--execute]
"""

import subprocess
import sys
from collections import defaultdict
from pathlib import Path

BRANCH_GLOB = 'nightshift/run-*'


def git(repo: Path, *args: str):
    return subprocess.run(['git', *args], cwd=repo,
                          capture_output=True, text=True)


def out(repo: Path, *args: str) -> str:
    r = git(repo, *args)
    return (r.stdout or '').strip() if r.returncode == 0 else ''


def default_branch(repo: Path) -> str:
    ref = out(repo, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD')
    return ref.split('/', 1)[1] if ref.startswith('origin/') else ''


def find_repos(root: Path) -> list:
    repos = [root] if (root / '.git').exists() else []
    repos += [d for d in sorted(root.iterdir())
              if d.is_dir() and d.name[:1].isdigit() and (d / '.git').exists()]
    return repos


def recover_repo(repo: Path, execute: bool) -> dict:
    res = {'name': repo.name, 'db': '', 'branches': 0, 'added': {},
           'skipped': defaultdict(list), 'status': ''}

    db = default_branch(repo)
    if not db:
        res['status'] = 'SKIP — origin/HEAD unset; run `git remote set-head origin -a`'
        return res
    res['db'] = db

    branches = [b.strip() for b in
                out(repo, 'branch', '--list', BRANCH_GLOB).splitlines()
                if b.strip()]
    branches = [b.lstrip('* ').strip() for b in branches]
    if not branches:
        res['status'] = 'no run branches'
        return res
    res['branches'] = len(branches)

    on_default = set(out(repo, 'ls-tree', '-r', '--name-only',
                         f'origin/{db}').splitlines())

    # newest branch wins a path claimed by two branches — later runs supersede
    for branch in sorted(branches):
        r = git(repo, 'diff', '--name-status', f'origin/{db}...{branch}')
        if r.returncode != 0:
            res['skipped']['unreadable diff'].append(branch)
            continue
        for line in (r.stdout or '').splitlines():
            if not line.strip():
                continue
            parts = line.split('\t')
            if len(parts) < 2:
                continue
            status, path = parts[0].strip(), parts[-1].strip()
            if status.startswith('D'):
                res['skipped']['deletion'].append(f'{branch}:{path}')
            elif status.startswith('A') and path not in on_default:
                res['added'][path] = branch
            else:
                res['skipped']['modifies an existing file'].append(
                    f'{branch}:{path}')

    if not res['added']:
        res['status'] = f'{len(branches)} branches, nothing new to land'
        return res

    if not execute:
        res['status'] = f'WOULD land {len(res["added"])} new file(s)'
        return res

    cur = out(repo, 'branch', '--show-current')
    if cur != db:
        co = git(repo, 'checkout', db)
        if co.returncode != 0:
            res['status'] = (f'ABORT — cannot check out {db}: '
                             f'{(co.stderr or "").strip()[:120]}')
            return res

    for path, branch in sorted(res['added'].items()):
        git(repo, 'checkout', branch, '--', path)

    n_br = len({b for b in res['added'].values()})
    msg = (f'recover: land {len(res["added"])} nightshift file(s) stranded on '
           f'{n_br} run branch(es)\n\n'
           'The PR flow cut nightshift/run-* branches in Gitea-hosted repos and\n'
           'tried to land them with `gh pr create`, which only speaks to GitHub.\n'
           'The PRs were never created and the output never reached this branch.\n\n'
           'Only files ADDED by those branches and absent here are recovered.\n'
           'Modifications to existing files (org/next_actions.org, logs) are NOT\n'
           'replayed — this branch has since moved on and is authoritative.\n')
    c = git(repo, 'commit', '-m', msg)
    if c.returncode != 0:
        res['status'] = f'COMMIT FAILED: {(c.stderr or "").strip()[:120]}'
        return res

    # These repos are shared with other hosts and the mac, so the default
    # branch has almost always moved since the last fetch — pushing without
    # integrating first just returns "fetch first" and strands the recovery
    # commit locally, which is the same failure mode being cleaned up here.
    git(repo, 'fetch', '-q', 'origin')
    pull = git(repo, 'pull', '--rebase', '--autostash', 'origin', db)
    if pull.returncode != 0:
        git(repo, 'rebase', '--abort')
        res['status'] = (f'committed, NOT PUSHED — rebase onto origin/{db} '
                         f'conflicts, needs a human')
        return res

    p = git(repo, 'push', 'origin', db)
    res['status'] = (f'LANDED {len(res["added"])} file(s)'
                     if p.returncode == 0
                     else f'committed, PUSH FAILED: {(p.stderr or "").strip()[:160]}')
    return res


def prune_repo(repo: Path, execute: bool) -> dict:
    """Delete run branches carrying no commits the default branch lacks.

    Guards, because nightshift may be mid-run:
      - never the branch currently checked out here
      - never the newest run branch, which is very likely the live run's
      - never a branch whose commit count cannot be READ (see git_fleet_sync:
        an errored rev-list is not the number zero)
    """
    res = {'name': repo.name, 'deleted': [], 'kept': 0, 'status': ''}
    db = default_branch(repo)
    if not db:
        res['status'] = 'SKIP — origin/HEAD unset'
        return res

    branches = sorted(b.lstrip('* ').strip() for b in
                      out(repo, 'branch', '--list', BRANCH_GLOB).splitlines()
                      if b.strip())
    if not branches:
        return res

    current = out(repo, 'branch', '--show-current')
    protected = {current, branches[-1]}

    for branch in branches:
        if branch in protected:
            res['kept'] += 1
            continue
        r = git(repo, 'rev-list', '--count', f'origin/{db}..{branch}')
        n = (r.stdout or '').strip()
        if r.returncode != 0 or not n.isdigit() or n != '0':
            res['kept'] += 1
            continue
        if execute:
            d = git(repo, 'branch', '-D', branch)
            if d.returncode != 0:
                res['kept'] += 1
                continue
            git(repo, 'push', 'origin', '--delete', branch)
        res['deleted'].append(branch)

    res['status'] = (f"{'deleted' if execute else 'would delete'} "
                     f"{len(res['deleted'])}, kept {res['kept']}")
    return res


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    execute = '--execute' in sys.argv
    prune = '--prune-empty' in sys.argv
    root = Path(args[0]).expanduser() if args else Path.home() / 'Data'

    if prune:
        print(f"{'PRUNING' if execute else 'DRY RUN'} — empty nightshift run "
              f"branches under {root}\n")
        total = 0
        for repo in find_repos(root):
            r = prune_repo(repo, execute)
            if not r['deleted'] and not r['status']:
                continue
            print(f"  {r['name']:<24} {r['status']}")
            total += len(r['deleted'])
        print(f"\n{'Deleted' if execute else 'Would delete'} {total} branch(es).")
        return 0

    print(f"{'RECOVERING' if execute else 'DRY RUN'} — stranded nightshift "
          f"output under {root}\n")

    total_files = 0
    for repo in find_repos(root):
        r = recover_repo(repo, execute)
        if r['status'] in ('no run branches',):
            continue
        print(f"{r['name']}  [{r['db'] or '?'}]  {r['status']}")
        total_files += len(r['added'])
        for path, branch in sorted(r['added'].items())[:6]:
            print(f"    + {path}")
        if len(r['added']) > 6:
            print(f"    + … {len(r['added']) - 6} more")
        for reason, items in r['skipped'].items():
            print(f"    ~ {len(items)} skipped ({reason})")

    print(f"\n{'Landed' if execute else 'Would land'} {total_files} file(s).")
    if not execute:
        print("Re-run with --execute to commit and push.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
