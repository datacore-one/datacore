"""Replays of the GitFleet formal findings (specs/datacore-lean/DatacoreSpec/GitFleet.lean).

Every test builds throwaway git repos and a bare "origin" under tmp_path. No
real remote, no ssh: the relay's host is a second clone, reached by rewriting
the `host:path` URL the relay would hand to ssh into a plain filesystem path.

The system property under test is "NEVER PUSH A FORK": whatever path publishes
a commit, every per-writer ledger log that origin already holds must be an
event-prefix of the log in the published commit.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import git_fleet_sync as fs  # noqa: E402
import git_relay  # noqa: E402
from ledger import fork  # noqa: E402
from ledger.log import EventLog  # noqa: E402

HOST = 'HOSTX'


def git(cwd: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(f'git {args}: {r.stderr}')
    return r.stdout.strip()


def _configure(repo: Path) -> None:
    git(repo, 'config', 'user.email', 'test@example.com')
    git(repo, 'config', 'user.name', 'Test')
    git(repo, 'config', 'commit.gpgsign', 'false')


def _clone(origin: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    git(dest.parent, 'clone', '-q', str(origin), dest.name)
    _configure(dest)
    return dest


def _log_lines(repo: Path, actor: str = 'w') -> list[str]:
    return (repo / '.datacore' / 'events' / f'{actor}.jsonl').read_text().splitlines()


def _write_log(repo: Path, lines: list[str], actor: str = 'w') -> None:
    (repo / '.datacore' / 'events' / f'{actor}.jsonl').write_text('\n'.join(lines) + '\n')


def _alt_chain(tmp: Path, keep: list[str], n_new: int, tag: str) -> list[str]:
    """A VALID chain that shares `keep` and then diverges: a genuine fork."""
    alt = tmp / f'alt-{tag}'
    (alt / '.datacore' / 'events').mkdir(parents=True)
    _write_log(alt, keep)
    log = EventLog(alt, 'w')
    for i in range(n_new):
        log.append('item.create', {'id': f'{tag}{i}', 'title': tag})
    return _log_lines(alt)


@pytest.fixture
def fleet(tmp_path):
    origin = tmp_path / 'origin.git'
    git(tmp_path, 'init', '-q', '--bare', '--initial-branch=main', str(origin))
    seed = tmp_path / 'seed'
    seed.mkdir()
    git(seed, 'init', '-q', '--initial-branch=main')
    _configure(seed)
    git(seed, 'remote', 'add', 'origin', str(origin))
    (seed / '.gitignore').write_text('.datacore/state/\n')
    log = EventLog(seed, 'w')
    for i in range(4):
        log.append('item.create', {'id': f'e{i}', 'title': 'base'})
    git(seed, 'add', '-A')
    git(seed, 'commit', '-q', '-m', 'base ledger')
    git(seed, 'push', '-q', 'origin', 'main')
    git(origin, 'symbolic-ref', 'HEAD', 'refs/heads/main')
    local = _clone(origin, tmp_path / 'data' / '1-space')
    git(local, 'remote', 'set-head', 'origin', 'main')
    host = _clone(origin, tmp_path / 'host' / '1-space')
    return {'tmp': tmp_path, 'origin': origin, 'local': local, 'host': host,
            'data': tmp_path / 'data', 'host_root': tmp_path / 'host'}


def _origin_log(fleet) -> str:
    return git(fleet['origin'], 'show', 'main:.datacore/events/w.jsonl')


def _no_fork_on_origin(fleet, before: str) -> bool:
    """Origin's log after = an event-prefix extension of origin's log before."""
    old, new = fork._index(before), fork._index(_origin_log(fleet))
    return all(new.get(k) == h for k, h in old.items())


def _relay(fleet, monkeypatch):
    real_run = git_relay._run

    def run(cmd, cwd=None, timeout=180):
        prefix = f'{HOST}:'
        cmd = [c[len(prefix):] if isinstance(c, str) and c.startswith(prefix) else c
               for c in cmd]
        return real_run(cmd, cwd=cwd, timeout=timeout)

    def ssh(host, script, timeout=120):
        return subprocess.CompletedProcess([], 0, stdout=str(fleet['origin']) + '\n', stderr='')

    monkeypatch.setattr(git_relay, '_run', run)
    monkeypatch.setattr(git_relay, '_ssh', ssh)
    return git_relay.relay(HOST, str(fleet['host_root']), '1-space', fleet['data'])


# ── (1) NEVER PUSH A FORK ────────────────────────────────────────────────────

def test_relay_does_not_publish_a_host_rewrite(fleet, monkeypatch):
    """GitFleet.lean `merge_rewrite_is_not_union`: the host rewound its log
    and re-appended seq 2,3 differently. Local never touched the file, so a
    three-way merge takes the host's copy WHOLE -- a valid chain, so the
    chain check passes -- and origin's (w,2),(w,3) would be replaced."""
    host = fleet['host']
    lines = _log_lines(host)
    _write_log(host, _alt_chain(fleet['tmp'], lines[:2], 2, 'x'))
    git(host, 'commit', '-qam', 'host rewrote its log')
    before = _origin_log(fleet)
    head_before = git(fleet['local'], 'rev-parse', 'HEAD')

    out = _relay(fleet, monkeypatch)

    assert _no_fork_on_origin(fleet, before), f'relay published a fork: {out}'
    assert 'REFUSED' in out
    assert git(fleet['local'], 'rev-parse', 'HEAD') == head_before, (
        'the refused merge must not stay where the next push publishes it')


def test_relay_refusal_does_not_leave_the_merge_for_the_next_push(fleet, monkeypatch):
    """The survey's lead: relay detects the bad merge and returns, but the
    merge commit stays on the branch; git_fleet_sync then pushes HEAD."""
    host, local = fleet['host'], fleet['local']
    lines = _log_lines(host)
    tampered = json.loads(lines[1])
    tampered['payload']['title'] = 'edited in place'
    lines[1] = json.dumps(tampered, sort_keys=True, separators=(',', ':'))
    _write_log(host, lines)
    git(host, 'commit', '-qam', 'host edited history')
    log = EventLog(local, 'w')                        # local appends seq 4
    log.append('item.create', {'id': 'e4', 'title': 'local'})
    git(local, 'commit', '-qam', 'local append')
    git(local, 'push', '-q', 'origin', 'main')
    before = _origin_log(fleet)
    head_before = git(local, 'rev-parse', 'HEAD')

    out = _relay(fleet, monkeypatch)
    assert 'REFUSED' in out
    assert git(local, 'rev-parse', 'HEAD') == head_before
    parked = git(local, 'for-each-ref', '--format=%(refname)', 'refs/relay-refused/')
    assert parked, 'the refused merge is kept as evidence, off the branch'

    (local / 'note.md').write_text('agent work\n')    # next sweep has work
    res = fs.sync_repo(local, execute=True)
    assert res['status'].startswith('PUSHED'), res
    assert _no_fork_on_origin(fleet, before)
    assert fork._index(_origin_log(fleet)) == fork._index(before)


def test_fleet_sync_refuses_to_publish_a_rewritten_log(fleet):
    """git_fleet_sync commits working-tree ledger logs and pushes HEAD with no
    fork check: a host whose log was rewound and re-appended publishes it."""
    local = fleet['local']
    before = _origin_log(fleet)
    _write_log(local, _alt_chain(fleet['tmp'], _log_lines(local)[:2], 3, 'y'))

    res = fs.sync_repo(local, execute=True)

    assert _no_fork_on_origin(fleet, before), f'fleet sync published a fork: {res}'
    assert 'REFUSED' in res['status']
    assert res.get('ledger_fork')
    assert git(local, 'log', '-1', '--format=%s').startswith('sync:'), (
        'the work is still committed locally -- refusing to push is not discarding')


def test_fleet_sync_still_publishes_an_honest_append(fleet):
    local = fleet['local']
    EventLog(local, 'w').append('item.create', {'id': 'e4', 'title': 'more'})
    res = fs.sync_repo(local, execute=True)
    assert res['status'].startswith('PUSHED'), res
    assert len(fork._index(_origin_log(fleet))) == 5


def test_publication_check_names_a_dropped_tail_as_a_rewind(fleet):
    local = fleet['local']
    _write_log(local, _log_lines(local)[:2])
    git(local, 'commit', '-qam', 'truncate')
    issues = git_relay.publication_forks(local, 'HEAD', 'origin/main')
    assert any('rewind' in i for i in issues), issues


# ── (2) git_fleet_sync guards ────────────────────────────────────────────────

def test_merge_in_progress_in_a_linked_worktree_is_not_aborted(tmp_path):
    """`.git` is a FILE in a linked worktree (and a submodule), so
    `repo/.git/MERGE_HEAD` never exists there. The guard missed the merge, the
    pull failed with "You have not concluded your merge", and the failure path
    ran `git merge --abort` -- discarding a human's hand resolution."""
    origin = tmp_path / 'origin.git'
    git(tmp_path, 'init', '-q', '--bare', '--initial-branch=main', str(origin))
    main = _clone(origin, tmp_path / 'main')
    (main / 'f.txt').write_text('base\n')
    git(main, 'add', 'f.txt')
    git(main, 'commit', '-qm', 'base')
    git(main, 'push', '-q', 'origin', 'main')
    git(main, 'remote', 'set-head', 'origin', 'main')
    git(main, 'checkout', '-qb', 'side')
    (main / 'f.txt').write_text('side\n')
    git(main, 'commit', '-qam', 'side')
    wt = tmp_path / 'wt'
    git(main, 'worktree', 'add', '-q', str(wt), 'main')
    (wt / 'f.txt').write_text('mainline\n')
    git(wt, 'commit', '-qam', 'mainline')
    git(wt, 'merge', 'side', check=False)            # conflicts
    (wt / 'f.txt').write_text('hand resolved\n')      # a human mid-resolution
    git(wt, 'add', 'f.txt')

    res = fs.sync_repo(wt, execute=True, pull=True)

    assert 'in progress' in res['status'], res
    assert (wt / 'f.txt').read_text() == 'hand resolved\n'
    assert git(wt, 'rev-parse', '-q', '--verify', 'MERGE_HEAD', check=False)


def test_in_progress_detects_every_layout(tmp_path):
    repo = tmp_path / 'r'
    repo.mkdir()
    git(repo, 'init', '-q', '--initial-branch=main')
    assert fs.in_progress(repo) == ''
    (repo / '.git' / 'MERGE_HEAD').write_text('0' * 40 + '\n')
    assert fs.in_progress(repo) == 'merge'


# ── (3) state_loop_rollout writes under the declared actor ──────────────────

def _legacy_space(root: Path) -> Path:
    space = root / '0-space'
    (space / 'org').mkdir(parents=True)
    EventLog(space, 'seed').append('item.create', {'id': 'a', 'title': 't', 'state': 'WORKING'})
    return space


def test_rollout_appends_as_this_actor_not_the_hostname(tmp_path, monkeypatch):
    """Two hosts sharing a short hostname (two cloud VMs both called `ubuntu`)
    but declaring different actors. Filed under the hostname, both append
    `ubuntu.jsonl` seq 0 with different hashes: a fork the moment they merge."""
    import socket
    import state_loop_rollout as roll
    from ledger.log import read_events
    monkeypatch.setattr(socket, 'gethostname', lambda: 'ubuntu.cloud.internal')
    written = {}
    for actor in ('alpha', 'beta'):
        monkeypatch.setenv('DATACORE_ACTOR', actor)
        space = _legacy_space(tmp_path / actor)
        roll.migrate_ledger(space, execute=True)
        written[actor] = {(e.actor, e.seq): e.hash for e in read_events(space)
                          if e.actor != 'seed'}
    assert set(written['alpha']) == {('alpha', 0)}, written
    assert set(written['beta']) == {('beta', 0)}, written
    shared = written['alpha'].keys() & written['beta'].keys()
    assert not shared, 'two hosts must never write the same (actor, seq)'


def test_rollout_commit_refuses_to_push_a_ledger_fork(fleet, monkeypatch, capsys):
    """--commit pushes HEAD after `pull`; it now applies the same publication
    check as git_relay and git_fleet_sync."""
    import state_loop_rollout as roll
    local = fleet['local']
    (local / 'org').mkdir()
    (local / 'org' / 'next.org').write_text('* WORKING thing\n')
    _write_log(local, _alt_chain(fleet['tmp'], _log_lines(local)[:2], 3, 'z'))
    git(local, 'add', '-A')
    git(local, 'commit', '-qm', 'local rewrite + legacy org file')
    before = _origin_log(fleet)
    (local / 'org' / 'next.org').write_text('* WORKING thing again\n')
    monkeypatch.setattr(sys, 'argv', ['state_loop_rollout.py', '--data-dir',
                                      str(fleet['data']), '--commit'])
    roll.main()
    out = capsys.readouterr().out
    assert _no_fork_on_origin(fleet, before), out
    assert 'REFUSED' in out


# ── (4) cron_install reconcile ──────────────────────────────────────────────

def test_cron_reconcile_splits_lines_only_at_newline():
    """str.splitlines treats \\x0c, \\x85, \\u2028 ... as line ends; cron does
    not. A managed-looking fragment inside one unmanaged cron line was cut out
    of it, rewriting a line the installer promises to preserve verbatim."""
    import cron_install as C
    unmanaged = '0 1 * * * echo keep\x0c0 2 * * * /opt/x/.datacore/lib/foo.py # one cron line\n'
    entries = {'foo': '5 * * * * /opt/x/.datacore/lib/foo.py'}
    out = C.reconcile(unmanaged, entries)
    assert out.startswith(unmanaged), repr(out)
    assert C.reconcile(out, entries) == out


# ── (5) git_branch_hygiene: a revert is not "built-on" ──────────────────────

def test_branch_reverting_to_old_content_is_outstanding(tmp_path):
    """landed_earlier searched the WHOLE trunk history for the branch's blob.
    A branch that reverts a file to content the trunk held BEFORE the branch
    forked finds that old blob and is called `built-on` (safe to delete) --
    though the trunk never took the revert."""
    import git_branch_hygiene as H
    repo = tmp_path / 'r'
    repo.mkdir()
    git(repo, 'init', '-q', '--initial-branch=main')
    _configure(repo)
    f = repo / 'policy.txt'
    f.write_text('v1 lenient\n'); git(repo, 'add', 'policy.txt'); git(repo, 'commit', '-qm', 'v1')
    f.write_text('v2 strict\n'); git(repo, 'commit', '-qam', 'v2')
    git(repo, 'checkout', '-qb', 'revert-policy')
    f.write_text('v1 lenient\n'); git(repo, 'commit', '-qam', 'revert to v1')
    git(repo, 'checkout', '-q', 'main')
    f.write_text('v3 stricter\n'); git(repo, 'commit', '-qam', 'v3')

    row = H.classify(repo, 'revert-policy', 'main')
    assert row['verdict'] == 'outstanding', row


def test_branch_landed_then_built_on_is_still_built_on(tmp_path):
    import git_branch_hygiene as H
    repo = tmp_path / 'r'
    repo.mkdir()
    git(repo, 'init', '-q', '--initial-branch=main')
    _configure(repo)
    f = repo / 'a.txt'
    f.write_text('base\n'); git(repo, 'add', 'a.txt'); git(repo, 'commit', '-qm', 'base')
    git(repo, 'checkout', '-qb', 'feature')
    f.write_text('feature\n'); git(repo, 'commit', '-qam', 'feature')
    git(repo, 'checkout', '-q', 'main')
    f.write_text('feature\n'); git(repo, 'commit', '-qam', 're-applied')
    f.write_text('feature + more\n'); git(repo, 'commit', '-qam', 'built on')
    assert H.classify(repo, 'feature', 'main')['verdict'] == 'built-on'


# ── (6) visitor_join: one join at a time ────────────────────────────────────

def test_a_tick_during_a_running_join_does_not_start_a_second(tmp_path, monkeypatch):
    """The attempt marker is written AFTER converge (up to 1500 s) and the
    duties (up to 3900 s each), so a tick that fires mid-join sees the
    previous attempt -- or none -- and is due: two joins, two phase-1 cycles,
    two runs of every duty. MIN_SPACING_S only spaces joins that have ended."""
    import visitor_join as vj
    from jobs import awake
    monkeypatch.setattr(vj, 'RECORD', tmp_path / 'join.json')
    monkeypatch.setattr(vj, 'ATTEMPT', tmp_path / 'join-attempt.json')
    monkeypatch.setattr(vj, 'LOG', tmp_path / 'join.log')
    monkeypatch.setattr(awake, 'in_dark_wake', lambda **kw: False)
    clean = {'ahead': 0, 'behind': 0, 'gap': 0, 'blocked': [], 'errors': []}
    monkeypatch.setattr(vj, 'measure', lambda **kw: clean)
    monkeypatch.setattr(vj, 'run_duties', lambda *, arrival: {})
    seen = {}

    def converge():
        seen['due'] = vj.due(now=1_800_000_700.0, log='')
        seen['nested'] = vj.join(now=1_800_000_700.0, log='')
        return 0, 'stub'

    monkeypatch.setattr(vj, 'converge', converge)
    outer = vj.join(now=1_800_000_000.0, log='')
    assert outer['converged'] is True
    assert seen['due'][0] is False, seen['due']
    assert seen['nested'].get('busy') is True, seen['nested']
