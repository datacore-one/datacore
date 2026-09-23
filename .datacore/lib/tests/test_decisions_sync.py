"""The owner's decisions for the sync area (2026-09-23), pinned.

specs/datacore-lean/DECISIONS.md items 41-46, decided on the board as:

* P1  git_fleet_sync takes ledger_transport._repo_lock around its
      stage/commit/push (Publication.lean §5, `lock_mutual_exclusion`).
* P2  git_fleet_sync refuses to push when a commit in origin/<default>..HEAD
      deletes a tracked file; it names the commits and paths and keeps the
      work committed locally (GitFleet.lean `range_gate_keeps_origin_paths`).
* P5  an archived nightshift output closes a task only if none of its tracked
      refs is open or unknown (Reconcile.lean `new_done_sound_all`).
* P6  an issue closed as a duplicate cancels the task, with :CANCEL_REASON:
      naming the duplicate.
* P7  ConflictDetector / ConflictResolver are removed until a sync engine
      exists; the three-way rule stays as the spec (Reconcile.lean `resolve3`).

Throwaway repositories under tmp_path; the "origin" is a local bare repo; the
GitHub CLI is stubbed. No network, no real data.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import git_fleet_sync as fs  # noqa: E402
import gh_reconcile  # noqa: E402


def git(cwd: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(f'git {args}: {r.stderr}')
    return r.stdout.strip()


def _clone(origin: Path, dest: Path) -> Path:
    git(dest.parent, 'clone', '-q', str(origin), dest.name)
    for k, v in (('user.email', 't@example.com'), ('user.name', 'T'),
                 ('commit.gpgsign', 'false')):
        git(dest, 'config', k, v)
    return dest


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / 'origin.git'
    git(tmp_path, 'init', '-q', '--bare', '--initial-branch=main', str(origin))
    seed = tmp_path / 'seed'
    seed.mkdir()
    git(seed, 'init', '-q', '--initial-branch=main')
    for k, v in (('user.email', 't@example.com'), ('user.name', 'T')):
        git(seed, 'config', k, v)
    git(seed, 'remote', 'add', 'origin', str(origin))
    for name in ('keep.md', 'doomed.md', 'upstream.md'):
        (seed / name).write_text(f'{name}\n')
    git(seed, 'add', '-A')
    git(seed, 'commit', '-qm', 'base')
    git(seed, 'push', '-q', 'origin', 'main')
    git(origin, 'symbolic-ref', 'HEAD', 'refs/heads/main')
    (tmp_path / 'data').mkdir()
    local = _clone(origin, tmp_path / 'data' / '1-space')
    git(local, 'remote', 'set-head', 'origin', 'main')
    return {'tmp': tmp_path, 'origin': origin, 'local': local}


def _origin_files(origin: Path) -> set[str]:
    return set(git(origin, 'ls-tree', '-r', '--name-only', 'main').splitlines())


# ── P1: the sweep takes the transport's repository lock ─────────────────────

def _hold_lock(repo: Path, release: Path) -> subprocess.Popen:
    """Another process (the transport, a publication) holds _repo_lock."""
    probe = textwrap.dedent(f"""
        import sys, time; sys.path.insert(0, {str(LIB)!r})
        from pathlib import Path
        import ledger_transport
        with ledger_transport._repo_lock(Path({str(repo)!r})):
            print('HELD', flush=True)
            while not Path({str(release)!r}).exists():
                time.sleep(0.05)
    """)
    p = subprocess.Popen([sys.executable, '-c', probe], stdout=subprocess.PIPE, text=True,
                         env=os.environ.copy())
    assert p.stdout.readline().strip() == 'HELD'
    return p


def test_p1_sweep_waits_for_the_repo_lock_and_does_not_commit_under_it(repo, monkeypatch):
    import ledger_transport
    monkeypatch.setattr(ledger_transport, '_REPO_LOCK_TIMEOUT', 0.5)
    local = repo['local']
    (local / 'agent.md').write_text('agent work\n')
    head = git(local, 'rev-parse', 'HEAD')
    release = repo['tmp'] / 'release'
    holder = _hold_lock(local, release)
    try:
        res = fs.sync_repo(local, execute=True)
    finally:
        release.write_text('go')
        holder.wait(timeout=30)

    assert res['status'].startswith('BUSY'), res
    assert git(local, 'rev-parse', 'HEAD') == head, 'committed while another writer held the lock'
    assert git(local, 'status', '--porcelain') == '?? agent.md', 'the work stays where it was'

    res = fs.sync_repo(local, execute=True)
    assert res['status'].startswith('PUSHED'), res


def test_p1_commit_and_push_happen_while_the_lock_is_held(repo):
    """A hook in the sweep's own commit and push asks, from another process,
    whether the repository lock is free. It must not be."""
    local = repo['local']
    record = repo['tmp'] / 'probe.log'
    probe = textwrap.dedent(f"""\
        #!{sys.executable}
        import sys; sys.path.insert(0, {str(LIB)!r})
        from pathlib import Path
        import ledger_transport
        ledger_transport._REPO_LOCK_TIMEOUT = 0
        try:
            with ledger_transport._repo_lock(Path({str(local)!r})):
                state = 'FREE'
        except TimeoutError:
            state = 'HELD'
        with open({str(record)!r}, 'a') as f:
            f.write(Path(sys.argv[0]).name + ' ' + state + '\\n')
    """)
    hooks = local / '.git' / 'hooks'
    for name in ('pre-commit', 'pre-push'):
        (hooks / name).write_text(probe)
        (hooks / name).chmod(0o755)
    (local / 'agent.md').write_text('agent work\n')

    res = fs.sync_repo(local, execute=True)

    assert res['status'].startswith('PUSHED'), res
    assert record.read_text().split('\n')[:2] == ['pre-commit HELD', 'pre-push HELD']


# ── P2: refuse to push a range that deletes tracked files ──────────────────

def test_p2_an_earlier_local_deletion_is_not_pushed(repo, capsys, monkeypatch):
    local, origin = repo['local'], repo['origin']
    git(local, 'rm', '-q', 'doomed.md')
    git(local, 'commit', '-qm', 'an agent removed a file')
    deleting = git(local, 'rev-parse', 'HEAD')
    (local / 'agent.md').write_text('agent work\n')

    res = fs.sync_repo(local, execute=True)

    assert 'PUSH REFUSED' in res['status'], res
    assert res['deletions'] == [(deleting, ['doomed.md'])]
    assert 'doomed.md' in _origin_files(origin), 'the deletion reached origin'
    assert git(local, 'log', '-1', '--format=%s').startswith('sync:'), (
        'the work stays committed locally')

    (local / 'agent2.md').write_text('the next run has work too\n')
    monkeypatch.setattr(sys, 'argv', ['git_fleet_sync.py', str(local.parent), '--execute'])
    rc = fs.main()
    out = capsys.readouterr().out
    assert rc == 1
    assert deleting in out and 'doomed.md' in out


def test_p2_a_deletion_pulled_from_origin_does_not_block(repo):
    """Another machine deleted upstream.md on origin. The sweep's merge brings
    that in; it is origin's own change, not one this push would publish."""
    other = _clone(repo['origin'], repo['tmp'] / 'other')
    git(other, 'rm', '-q', 'upstream.md')
    git(other, 'commit', '-qm', 'deleted on another machine')
    git(other, 'push', '-q', 'origin', 'main')
    local = repo['local']
    (local / 'agent.md').write_text('agent work\n')
    git(local, 'add', 'agent.md')
    git(local, 'commit', '-qm', 'local work')
    (local / 'more.md').write_text('more\n')

    res = fs.sync_repo(local, execute=True, pull=True)

    assert res['pull'] == 'pulled', res
    assert res['status'].startswith('PUSHED'), res
    assert {'agent.md', 'more.md'} <= _origin_files(repo['origin'])


def test_p2_a_merge_that_itself_deletes_is_refused(repo):
    other = _clone(repo['origin'], repo['tmp'] / 'other')
    (other / 'o.md').write_text('o\n')
    git(other, 'add', 'o.md')
    git(other, 'commit', '-qm', 'other work')
    git(other, 'push', '-q', 'origin', 'main')
    local = repo['local']
    (local / 'l.md').write_text('l\n')
    git(local, 'add', 'l.md')
    git(local, 'commit', '-qm', 'local work')
    git(local, 'fetch', '-q', 'origin')
    git(local, 'merge', '-q', '--no-commit', 'origin/main', check=False)
    git(local, 'rm', '-q', 'keep.md')
    git(local, 'commit', '-qm', 'merge that also drops keep.md')
    (local / 'agent.md').write_text('agent\n')

    res = fs.sync_repo(local, execute=True)

    assert 'PUSH REFUSED' in res['status'], res
    assert [paths for _, paths in res['deletions']] == [['keep.md']]
    assert 'keep.md' in _origin_files(repo['origin'])


# ── P5 / P6: gh_reconcile ───────────────────────────────────────────────────

@pytest.fixture
def gh(monkeypatch, tmp_path):
    monkeypatch.setenv('DATACORE_ROOT', str(tmp_path / 'data'))
    table = {}

    def fake_run(cmd, **kw):
        assert cmd[:2] == ['gh', 'api'], cmd
        m = re.match(r'repos/(.+?)/(.+?)/(pulls|issues)/(\d+)', cmd[2])
        obj = table.get(f'{m.group(1)}/{m.group(2)}/{m.group(3)}/{m.group(4)}')
        if obj is None:
            return types.SimpleNamespace(returncode=1, stdout='', stderr='HTTP 502')
        keys = re.findall(r'(\w+): \.(\w+)', cmd[cmd.index('--jq') + 1])
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({k: obj.get(v) for k, v in keys}),
                                     stderr='')

    monkeypatch.setattr(gh_reconcile.subprocess, 'run', fake_run)
    gh_reconcile._api_cache.clear()
    yield table
    gh_reconcile._api_cache.clear()


def _reconcile(tmp_path, text, archived=True, monkeypatch=None):
    f = tmp_path / 'next_actions.org'
    f.write_text(text, encoding='utf-8')
    if monkeypatch is not None:
        monkeypatch.setattr(gh_reconcile, 'check_nightshift_output_archived',
                            lambda *a, **k: {'reason': 'output archived'} if archived else None)
    n = gh_reconcile.reconcile_file(f, 'o', 'r', False, data_dir=tmp_path, output_index={})
    return n, f.read_text(encoding='utf-8')


REVIEW = ('* TODO Review: result\n  :PROPERTIES:\n'
          '  :NIGHTSHIFT_OUTPUT: 0-inbox/nightshift-exec-x.md\n'
          '  :PR_URL: https://github.com/o/r/pull/3\n  :END:\n')


def test_p5_archived_output_does_not_close_over_an_open_pr(gh, tmp_path, monkeypatch):
    gh['o/r/pulls/3'] = {'state': 'open', 'merged_at': None, 'merged': False}
    n, text = _reconcile(tmp_path, REVIEW, monkeypatch=monkeypatch)
    assert n == 0
    assert text.startswith('* TODO Review')


def test_p5_archived_output_does_not_close_over_an_unknown_ref(gh, tmp_path, monkeypatch):
    n, text = _reconcile(tmp_path, REVIEW, monkeypatch=monkeypatch)  # lookup fails
    assert n == 0
    assert text.startswith('* TODO Review')


def test_p5_archived_output_still_closes_a_task_with_no_refs(gh, tmp_path, monkeypatch):
    text = REVIEW.replace('  :PR_URL: https://github.com/o/r/pull/3\n', '')
    n, text = _reconcile(tmp_path, text, monkeypatch=monkeypatch)
    assert n == 1
    assert text.startswith('* DONE Review')


def test_p5_merged_pr_with_archived_output_closes(gh, tmp_path, monkeypatch):
    gh['o/r/pulls/3'] = {'state': 'closed', 'merged_at': '2026-09-01T00:00:00Z', 'merged': True}
    n, text = _reconcile(tmp_path, REVIEW, monkeypatch=monkeypatch)
    assert n == 1
    assert text.startswith('* DONE Review')


def _issue(reason):
    return {'state': 'closed', 'closed_at': '2026-09-01T00:00:00Z',
            'state_reason': reason, 'pull_request': None}


def test_p6_an_issue_closed_as_duplicate_cancels_the_task(gh, tmp_path):
    gh['o/r/issues/4'] = _issue('duplicate')
    n, text = _reconcile(tmp_path, '* TODO Do it\n  https://github.com/o/r/issues/4\n')
    assert n == 1
    assert text.startswith('* CANCELLED Do it')
    reason = re.search(r':CANCEL_REASON: (.*)', text).group(1)
    assert 'o/r#4' in reason and 'duplicate' in reason


def test_p6_duplicate_and_merged_together_are_left_for_a_human(gh, tmp_path):
    gh['o/r/pulls/1'] = {'state': 'closed', 'merged_at': '2026-09-01T00:00:00Z', 'merged': True}
    gh['o/r/issues/4'] = _issue('duplicate')
    n, text = _reconcile(tmp_path, '* TODO Mixed\n  https://github.com/o/r/pull/1\n'
                                   '  https://github.com/o/r/issues/4\n')
    assert n == 0
    assert text.startswith('* TODO Mixed')


# ── P7: the two-way detector is gone until an engine exists ─────────────────

def test_p7_two_way_detector_and_resolver_are_removed():
    import sync
    import sync.conflict as conflict
    for name in ('ConflictDetector', 'ConflictResolver'):
        assert not hasattr(conflict, name), name
        assert name not in sync.__all__
    # What stays: the queue, the strategy vocabulary, the config loader.
    assert conflict.ConflictQueue and conflict.ConflictStrategy and conflict.load_conflict_config


def test_p7_engine_still_loads_and_reports(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    from sync.engine import SyncEngine
    engine = SyncEngine(str(tmp_path))
    engine.load_config()
    assert not hasattr(engine, 'detect_conflicts')
    assert engine.diagnostic()['conflicts']['enabled'] is True
