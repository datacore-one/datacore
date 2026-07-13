#!/usr/bin/env python3
"""Tests for knowledge_commit — the destination resolver.

The scenario that cost two months: an agent is on a feature branch doing code
work, and writes a journal entry / zettel while it's there. The knowledge must
reach main. The code must NOT.

Run: python3 .datacore/tests/test_knowledge_commit.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from knowledge_commit import (  # noqa: E402
    classify, commit_knowledge, commit_to_branch, current_branch, is_knowledge,
)

PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def git(repo, *a):
    r = subprocess.run(['git', *a], cwd=repo, capture_output=True, text=True)
    return r.stdout.strip()


def make_repo(tmp: Path) -> Path:
    """A repo shaped like a space: knowledge dirs + a code dir, main + feature."""
    origin = tmp / 'origin.git'
    subprocess.run(['git', 'init', '-q', '--bare', str(origin)], check=True)

    repo = tmp / 'space'
    subprocess.run(['git', 'clone', '-q', str(origin), str(repo)], check=True)
    git(repo, 'config', 'user.email', 't@t.t')
    git(repo, 'config', 'user.name', 'Test')

    (repo / 'journal').mkdir()
    (repo / '2-projects').mkdir()
    (repo / 'journal' / 'seed.md').write_text('seed\n')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'init')
    git(repo, 'branch', '-M', 'main')
    git(repo, 'push', '-q', '-u', 'origin', 'main')
    git(repo, 'remote', 'set-head', 'origin', 'main')
    return repo


print("=== classification (deterministic, no LLM) ===")
check("journal is knowledge",        is_knowledge('journal/2026-07-13.md'), True)
check("zettel is knowledge",         is_knowledge('3-knowledge/zettel/x.md'), True)
check("content calendar is knowledge", is_knowledge('1-tracks/comms/cal.md'), True)
check("org task is knowledge",       is_knowledge('org/next_actions.org'), True)
check("project source is NOT",       is_knowledge('2-projects/plur/src/a.ts'), False)
check("module source is NOT",        is_knowledge('.datacore/modules/mail/lib/x.py'), False)

split = classify(['journal/a.md', '2-projects/p/src/b.ts', '3-knowledge/z.md'])
check("split routes correctly",
      (sorted(split['knowledge']), split['code']),
      (['3-knowledge/z.md', 'journal/a.md'], ['2-projects/p/src/b.ts']))

with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    repo = make_repo(tmp)

    print("\n=== THE REGRESSION: agent on a feature branch writes a journal entry ===")

    # Agent checks out a feature branch to do code work — entirely legitimate.
    git(repo, 'checkout', '-qb', 'ops/b17-sprint-claim')
    (repo / '2-projects').mkdir(exist_ok=True)
    (repo / '2-projects' / 'feature.ts').write_text('export const x = 1\n')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'feat: code work on the sprint branch')

    # While there, it produces knowledge — a journal entry and a zettel.
    (repo / 'journal' / '2026-07-13.md').write_text('what I did today\n')
    (repo / '3-knowledge').mkdir(exist_ok=True)
    (repo / '3-knowledge' / 'zettel.md').write_text('a lesson\n')

    check("agent is on the feature branch", current_branch(repo), 'ops/b17-sprint-claim')

    r = commit_knowledge(
        repo,
        ['journal/2026-07-13.md', '3-knowledge/zettel.md', '2-projects/feature.ts'],
        'knowledge from a session on a feature branch',
        push=False,
    )

    # The whole point: knowledge reached main, from a feature branch, untouched.
    main_files = git(repo, 'ls-tree', '-r', '--name-only', 'main').splitlines()
    check("journal landed on main",  'journal/2026-07-13.md' in main_files, True)
    check("zettel landed on main",   '3-knowledge/zettel.md' in main_files, True)
    check("code did NOT land on main", '2-projects/feature.ts' in main_files, False)
    check("code was left alone",     r['code_left_alone'], ['2-projects/feature.ts'])

    # ...and it did so without disturbing the agent's work in progress.
    check("HEAD never moved",        current_branch(repo), 'ops/b17-sprint-claim')
    check("feature commit intact",
          'feat: code work on the sprint branch' in git(repo, 'log', '--oneline', 'HEAD'),
          True)
    # The journal is gone from the FEATURE branch's working tree, on purpose: it
    # belongs to main, it is committed there, and leaving it untracked here is
    # what blocks `git checkout main` (see the deadlock section below).
    check("journal removed from the feature working tree",
          (repo / 'journal' / '2026-07-13.md').exists(), False)
    check("code file untouched in the working tree",
          (repo / '2-projects' / 'feature.ts').exists(), True)

    print("\n=== idempotence: a second wrap-up must not spam empty commits ===")
    # Recreate it with identical content — otherwise the file simply does not
    # exist and we would be asserting a no-op for the wrong reason.
    (repo / 'journal' / '2026-07-13.md').write_text('what I did today\n')
    before = git(repo, 'rev-parse', 'main')
    r2 = commit_knowledge(repo, ['journal/2026-07-13.md'], 'same content again',
                          push=False)
    check("no-op when content unchanged", r2['commit'], '')
    check("main did not move",            git(repo, 'rev-parse', 'main'), before)

    # A no-op must not delete the file either — nothing landed, so nothing is safe
    # to throw away. Losing an uncommitted journal entry here would be the exact
    # data loss this whole exercise exists to prevent.
    check("no-op leaves the file on disk",
          (repo / 'journal' / '2026-07-13.md').exists(), True)
    (repo / 'journal' / '2026-07-13.md').unlink()  # tidy for the deadlock check

    print("\n=== the deadlock: agent must still be able to return to main ===")
    # After a plumbing commit the file is on main but UNTRACKED here, and git
    # then refuses to switch branches ("untracked working tree files would be
    # overwritten by checkout"). That would strand the agent on the feature
    # branch AND break check_and_repair_git()'s stray-branch recovery, which
    # recovers by checking out main. A fix that jams the other fix.
    check("no untracked litter left behind",
          [l for l in git(repo, 'status', '--porcelain').splitlines()
           if l.startswith('??')],
          [])

    co = subprocess.run(['git', 'checkout', 'main'], cwd=repo,
                        capture_output=True, text=True)
    check("agent CAN check out main afterwards", co.returncode, 0)
    check("actually on main now", current_branch(repo), 'main')
    check("and the journal is there, tracked",
          (repo / 'journal' / '2026-07-13.md').read_text(), 'what I did today\n')

    print("\n=== already on the default branch: use the ordinary path ===")
    (repo / 'journal' / 'onmain.md').write_text('written while on main\n')
    sha = commit_to_branch(repo, 'main', ['journal/onmain.md'], 'on main', push=False)
    check("committed normally when HEAD == target", bool(sha), True)
    check("working tree is clean afterwards", git(repo, 'status', '--porcelain'), '')
    check("HEAD advanced on main", git(repo, 'log', '-1', '--format=%s'), 'on main')

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
