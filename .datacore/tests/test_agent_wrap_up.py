#!/usr/bin/env python3
"""Tests for agent_wrap_up — the trigger.

The shape that matters: ONE session touches SEVERAL spaces (a venture space, a
personal space, maybe a code repo), and at least one of them is sitting on a
stray branch. Every space that saw work gets its own journal entry, and the
knowledge reaches main regardless of what HEAD is doing.

Run: python3 .datacore/tests/test_agent_wrap_up.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from agent_wrap_up import (  # noqa: E402
    changed_paths, discover_spaces, journal_path, wrap_up,
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
    return subprocess.run(['git', *a], cwd=repo, capture_output=True,
                          text=True).stdout.strip()


def make_space(root: Path, name: str, personal: bool = False) -> Path:
    origin = root / f'{name}.git'
    subprocess.run(['git', 'init', '-q', '--bare', str(origin)], check=True)
    repo = root / name
    subprocess.run(['git', 'clone', '-q', str(origin), str(repo)], check=True)
    git(repo, 'config', 'user.email', 't@t.t')
    git(repo, 'config', 'user.name', 'Test')
    (repo / ('notes/journals' if personal else 'journal')).mkdir(parents=True)
    (repo / '3-knowledge').mkdir(exist_ok=True)
    (repo / 'seed.md').write_text('seed\n')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'init')
    git(repo, 'branch', '-M', 'main')
    git(repo, 'push', '-q', '-u', 'origin', 'main')
    git(repo, 'remote', 'set-head', 'origin', 'main')
    return repo



def main() -> int:
    """Runs every check; returns the exit code the script always had."""
    with tempfile.TemporaryDirectory() as td:
        data = Path(td) / 'Data'
        data.mkdir()

        plur = make_space(data, '5-plur')
        personal = make_space(data, '0-personal', personal=True)
        make_space(data, '9-untouched')          # no work — must get no journal

        print("=== discovery ===")
        check("finds all space repos",
              sorted(s.name for s in discover_spaces(data)),
              ['0-personal', '5-plur', '9-untouched'])
        check("personal journal path uses notes/journals/",
              journal_path(personal, '2026-07-13').relative_to(personal).as_posix(),
              'notes/journals/2026-07-13.md')
        check("space journal path uses journal/",
              journal_path(plur, '2026-07-13').relative_to(plur).as_posix(),
              'journal/2026-07-13.md')

        print("\n=== one session, several spaces, one of them on a stray branch ===")
        # The venture space is wrongly on a sprint branch — the 5-plur situation.
        git(plur, 'checkout', '-qb', 'ops/b17-sprint-claim')
        (plur / '3-knowledge' / 'lesson.md').write_text('a zettel\n')

        # Personal space is fine, on main.
        (personal / '3-knowledge' / 'private-thought.md').write_text('a thought\n')

        r = wrap_up(
            data, agent='miles', tier='session',
            summary='fallback summary',
            entries={
                '5-plur': 'Professional: shipped the reranker flip. Next: benchmark it.',
                '0-personal': 'Verbose: went down a dead end on the tokenizer first.',
            },
        )

        touched = sorted(s['space'] for s in r['spaces'])
        check("only spaces with work were wrapped", touched, ['0-personal', '5-plur'])
        check("untouched space got no journal",
              (data / '9-untouched' / 'journal' / '2026-07-13.md').exists(), False)

        by = {s['space']: s for s in r['spaces']}

        # The whole point: the venture space was on a stray branch, and its knowledge
        # STILL reached main. Without this, wrap-up just documents the disaster.
        check("plur HEAD stayed on the stray branch",
              by['5-plur']['branch'], 'ops/b17-sprint-claim')
        plur_main = git(plur, 'ls-tree', '-r', '--name-only', 'main').splitlines()
        check("plur zettel reached main",   '3-knowledge/lesson.md' in plur_main, True)
        check("plur journal reached main",
              any(f.startswith('journal/') and f.endswith('.md') for f in plur_main), True)
        check("nothing landed on the stray branch",
              '3-knowledge/lesson.md' in
              git(plur, 'ls-tree', '-r', '--name-only', 'ops/b17-sprint-claim').splitlines(),
              False)

        # Each space got its OWN entry — not one shared blob.
        jmain = [f for f in plur_main if f.startswith('journal/')][0]
        plur_journal = git(plur, 'show', f'main:{jmain}')
        check("plur journal has the professional entry",
              'shipped the reranker flip' in plur_journal, True)
        check("plur journal does NOT have the personal entry",
              'dead end on the tokenizer' in plur_journal, False)

        pers_main = git(personal, 'ls-tree', '-r', '--name-only', 'main').splitlines()
        pj = [f for f in pers_main if f.startswith('notes/journals/')][0]
        personal_journal = git(personal, 'show', f'main:{pj}')
        check("personal journal has the verbose entry",
              'dead end on the tokenizer' in personal_journal, True)
        check("agent is attributed in the entry",
              '## Miles' in personal_journal, True)

        print("\n=== a second wrap-up appends, it does not overwrite ===")
        (plur / '3-knowledge' / 'lesson2.md').write_text('another zettel\n')
        wrap_up(data, agent='tris', tier='session', summary='',
                entries={'5-plur': 'Tris adds a second entry the same day.'})
        j2 = git(plur, 'show', f'main:{jmain}')
        check("first agent's entry survived",  'shipped the reranker flip' in j2, True)
        check("second agent's entry appended", 'Tris adds a second entry' in j2, True)
        check("both agents attributed",
              ('## Miles' in j2 and '## Tris' in j2), True)

        print("\n=== nothing to do ===")
        r3 = wrap_up(data, agent='miles', tier='session', summary='x', entries={})
        check("clean tree wraps up nothing", r3['spaces'], [])

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def test_script_style_checks_pass():
    # pytest entry: the file used to run at import and call sys.exit(0),
    # which aborted collection of the whole directory (2026-09-06).
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
