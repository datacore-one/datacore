"""The owner's follow-up decisions for the sync and guard areas (2026-09-23), pinned.

specs/datacore-lean/FOLLOWUPS.md section B, decided on a second board as:

* Q2  tool_policy resolves this host's actor strictly
      (actor_identity.this_actor(strict=True)): an undeclared host is denied,
      never guessed from its hostname.
* Q4  call_text leaves the Bash `description` field (prose) out of the JSON it
      matches; every other field stays (Guards.lean `call_text_covers_every_field`,
      `call_text_bash_description_unmatched`).
* Q6  git_fleet_sync runs its pull (fetch + merge) under the same
      ledger_transport._repo_lock as stage/commit/push, one critical section per
      repository (Publication.lean §5 `lock_mutual_exclusion`, `holder_stable`).
* Q7  a deletion refusal still fails the run (exit 1).
* Q8  the deletion check fetches origin/<default> first, even without --pull;
      a failed fetch refuses the push and says so (GitFleet.lean `q8_pass_sound`).
* Q9  an issue closed as a duplicate names its canonical issue, looked up from
      the MarkedAsDuplicate timeline event; a failed lookup names the duplicate
      (Reconcile.lean `q9_names_canonical`, `q9_fallback_names_duplicate`).

Throwaway repositories under tmp_path; "origin" is a local bare repo; the
GitHub CLI and the actor registry are stubbed. No network, no real data.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import actor_identity  # noqa: E402
import gh_reconcile  # noqa: E402
import git_fleet_sync as fs  # noqa: E402
import tool_policy as tp  # noqa: E402


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
    for name in ('keep.md', 'upstream.md'):
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


def _push_from_other(repo, name='other.md', rm=None):
    other = _clone(repo['origin'], repo['tmp'] / f'other-{name}')
    if rm:
        git(other, 'rm', '-q', rm)
    else:
        (other / name).write_text('from another machine\n')
        git(other, 'add', name)
    git(other, 'commit', '-qm', 'another machine')
    git(other, 'push', '-q', 'origin', 'main')
    return git(other, 'rev-parse', 'HEAD')


# ── Q2: tool_policy never guesses the actor from the hostname ───────────────

@pytest.fixture
def undeclared(monkeypatch):
    monkeypatch.delenv('DATACORE_ACTOR', raising=False)
    monkeypatch.setattr(actor_identity, 'resolve', lambda *a, **k: (None, 'none'))
    monkeypatch.setattr(actor_identity, 'short_hostname', lambda: 'stray-host')


def test_q2_principal_for_refuses_an_undeclared_host(undeclared):
    with pytest.raises(actor_identity.UndeclaredActor):
        tp.principal_for()


def test_q2_the_hook_denies_on_an_undeclared_host(undeclared):
    out = tp.evaluate_hook({'tool_name': 'Bash', 'tool_input': {'command': 'ls'}},
                           env={}, record=False, effects={})
    assert out is not None
    assert out['hookSpecificOutput']['permissionDecision'] == 'deny'


def test_q2_a_refusal_is_not_recorded_under_the_hostname(undeclared, tmp_path, monkeypatch):
    space = tmp_path / 'space'
    (space / '.datacore' / 'events').mkdir(parents=True)
    made = []
    import ledger.log as ledger_log
    monkeypatch.setattr(ledger_log, 'EventLog', lambda s, actor: made.append(actor))
    d = tp.Decision(False, {'payment'}, 'no', 'never')
    assert tp.record_refusal(d, principal='miles', tool_name='Bash', space_dir=space) is False
    assert made == [], 'a log was opened under a guessed actor'


# ── Q4: the Bash description is prose, not a call ──────────────────────────

PAY = {'payment': {'tools': ['Bash', 'mcp__*'], 'tool_patterns': [],
                   'patterns': [re.compile(r'api\.stripe\.com/v1/charges', re.I)]}}


def test_q4_a_bash_description_quoting_an_effect_does_not_classify():
    inp = {'command': 'git status', 'description': 'check before api.stripe.com/v1/charges work'}
    assert tp.classify('Bash', inp, PAY) == set()
    text = tp.call_text(inp, 'Bash')
    assert 'stripe' not in text
    first, _, rest = text.partition('\n')
    assert first == 'git status'
    assert json.loads(rest) == {'command': 'git status'}


def test_q4_every_other_bash_field_is_still_matched():
    inp = {'command': 'ls', 'description': 'list', 'extra': 'api.stripe.com/v1/charges'}
    assert tp.classify('Bash', inp, PAY) == {'payment'}
    inp = {'command': 'curl https://api.stripe.com/v1/charges', 'description': 'harmless'}
    assert tp.classify('Bash', inp, PAY) == {'payment'}


def test_q4_a_description_outside_bash_is_still_matched():
    inp = {'title': 't', 'description': 'POST api.stripe.com/v1/charges'}
    assert tp.classify('mcp__http__request', inp, PAY) == {'payment'}
    # A caller that does not say which tool keeps the whole JSON (S1).
    assert 'stripe' in tp.call_text(inp)


def test_q4_the_hook_allows_the_innocent_command(policy_file):
    payload = {'tool_name': 'Bash',
               'tool_input': {'command': 'ls', 'description': 'api.stripe.com/v1/charges'}}
    out = tp.evaluate_hook(payload, env={'DATACORE_POLICY_PRINCIPAL': 'miles'}, record=False,
                           effects=PAY, policy_path=policy_file)
    assert out is None


@pytest.fixture
def policy_file(tmp_path):
    import yaml
    p = tmp_path / 'approvals_policy.yaml'
    p.write_text(yaml.safe_dump({
        'version': 1, 'approver': 'human',
        'cosign_effects': ['payment'], 'known_effects': ['payment'],
        'principals': {'miles': {'never_effects': ['payment']}},
    }))
    return p


# ── Q6: the pull runs under the repository lock ─────────────────────────────

def _hold_lock(repo: Path, release: Path) -> subprocess.Popen:
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


def test_q6_the_pull_waits_for_the_repo_lock(repo, monkeypatch):
    import ledger_transport
    monkeypatch.setattr(ledger_transport, '_REPO_LOCK_TIMEOUT', 0.5)
    local = repo['local']
    _push_from_other(repo)
    head = git(local, 'rev-parse', 'HEAD')
    tracking = git(local, 'rev-parse', 'origin/main')
    release = repo['tmp'] / 'release'
    holder = _hold_lock(local, release)
    try:
        res = fs.sync_repo(local, execute=True, pull=True)
    finally:
        release.write_text('go')
        holder.wait(timeout=30)

    assert res['status'].startswith('BUSY'), res
    assert res['pull'] == '', 'pulled while another writer held the lock'
    assert git(local, 'rev-parse', 'HEAD') == head, 'merged under another writer'
    assert git(local, 'rev-parse', 'origin/main') == tracking, 'fetched under another writer'

    res = fs.sync_repo(local, execute=True, pull=True)
    assert res['pull'] == 'pulled', res
    assert (local / 'other.md').exists()


def test_q6_pull_and_push_share_one_critical_section(repo, monkeypatch):
    """The lock is entered once per repository, and the fetch, the merge and
    the push all run inside it."""
    import ledger_transport
    real_lock = ledger_transport._repo_lock
    state = {'inside': False, 'entries': 0, 'calls': []}

    @contextmanager
    def counting(path):
        with real_lock(path):
            state['entries'] += 1
            state['inside'] = True
            try:
                yield
            finally:
                state['inside'] = False

    real_run = subprocess.run

    def spy(cmd, *a, **k):
        if isinstance(cmd, list) and cmd[:1] == ['git'] and any(
                c in cmd for c in ('fetch', 'pull', 'push')):
            verb = next(c for c in ('fetch', 'pull', 'push') if c in cmd)
            state['calls'].append((verb, state['inside']))
        return real_run(cmd, *a, **k)

    local = repo['local']
    _push_from_other(repo)
    (local / 'agent.md').write_text('agent work\n')
    monkeypatch.setattr(ledger_transport, '_repo_lock', counting)
    monkeypatch.setattr(fs.subprocess, 'run', spy)

    res = fs.sync_repo(local, execute=True, pull=True)

    assert res['pull'] == 'pulled' and res['status'].startswith('PUSHED'), res
    assert state['entries'] == 1
    verbs = [v for v, _ in state['calls']]
    assert 'pull' in verbs and 'push' in verbs and 'fetch' in verbs
    assert all(inside for _, inside in state['calls']), state['calls']


def test_q6_the_merge_hook_sees_the_lock_held(repo):
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
    hook = local / '.git' / 'hooks' / 'post-merge'
    hook.write_text(probe)
    hook.chmod(0o755)
    _push_from_other(repo)

    res = fs.sync_repo(local, execute=True, pull=True)

    assert res['pull'] == 'pulled', res
    assert record.read_text().splitlines() == ['post-merge HELD']


def test_q6_the_dry_run_neither_pulls_nor_locks(repo, monkeypatch):
    import ledger_transport

    def boom(_):
        raise AssertionError('the dry run took the lock')
    monkeypatch.setattr(ledger_transport, '_repo_lock', boom)
    local = repo['local']
    _push_from_other(repo)
    head = git(local, 'rev-parse', 'HEAD')
    res = fs.sync_repo(local, execute=False, pull=True)
    assert res['pull'] == ''
    assert git(local, 'rev-parse', 'HEAD') == head


# ── Q8: fetch before the deletion check, even without --pull ────────────────

def test_q8_a_stale_tracking_ref_does_not_refuse_origins_own_deletion(repo):
    """origin deleted upstream.md; this checkout already has that commit (it
    was pulled by URL, so origin/main was not moved). Judged against the stale
    ref, origin's own deletion looked like one this push would publish."""
    local = repo['local']
    _push_from_other(repo, rm='upstream.md')
    git(local, 'pull', '-q', '--no-rebase', str(repo['origin']), 'main')
    assert git(local, 'rev-parse', 'origin/main') != git(local, 'rev-parse', 'HEAD')
    (local / 'agent.md').write_text('agent work\n')

    res = fs.sync_repo(local, execute=True)

    assert res['status'].startswith('PUSHED'), res
    assert 'agent.md' in _origin_files(repo['origin'])


def test_q8_a_fresh_ref_still_refuses_a_local_deletion(repo):
    local = repo['local']
    _push_from_other(repo)
    git(local, 'rm', '-q', 'keep.md')
    git(local, 'commit', '-qm', 'an agent removed a file')
    (local / 'agent.md').write_text('agent work\n')

    res = fs.sync_repo(local, execute=True)

    assert 'PUSH REFUSED' in res['status'], res
    assert [p for _, p in res['deletions']] == [['keep.md']]
    assert 'keep.md' in _origin_files(repo['origin'])


def test_q8_a_failed_fetch_refuses_and_names_it(repo, capsys, monkeypatch):
    local = repo['local']
    git(local, 'remote', 'set-url', 'origin', str(repo['tmp'] / 'nowhere.git'))
    git(local, 'remote', 'set-url', '--push', 'origin', str(repo['origin']))
    (local / 'agent.md').write_text('agent work\n')

    res = fs.sync_repo(local, execute=True)

    assert 'PUSH REFUSED' in res['status'] and 'fetch' in res['status'], res
    assert 'agent.md' not in _origin_files(repo['origin']), 'pushed without a fresh check'
    assert git(local, 'log', '-1', '--format=%s').startswith('sync:')

    # Q7: the refusal fails the run.
    (local / 'agent2.md').write_text('more\n')
    monkeypatch.setattr(sys, 'argv', ['git_fleet_sync.py', str(local.parent), '--execute'])
    assert fs.main() == 1
    out = capsys.readouterr().out
    assert 'fetch' in out and 'origin/main' in out


# ── Q9: a duplicate names its canonical issue ──────────────────────────────

@pytest.fixture
def gh(monkeypatch, tmp_path):
    monkeypatch.setenv('DATACORE_ROOT', str(tmp_path / 'data'))
    table = {}
    canon = {}
    calls = []

    def fake_run(cmd, **kw):
        assert cmd[:2] == ['gh', 'api'], cmd
        calls.append(cmd)
        if cmd[2] == 'graphql':
            args = dict(a.split('=', 1) for a in cmd if '=' in a and not a.startswith('query='))
            key = f"{args['owner']}/{args['repo']}/issues/{args['num']}"
            if key not in canon or canon[key] == 'FAIL':
                return types.SimpleNamespace(returncode=1, stdout='', stderr='HTTP 502')
            return types.SimpleNamespace(returncode=0, stdout=json.dumps(canon[key]) + '\n',
                                         stderr='')
        m = re.match(r'repos/(.+?)/(.+?)/(pulls|issues)/(\d+)', cmd[2])
        obj = table.get(f'{m.group(1)}/{m.group(2)}/{m.group(3)}/{m.group(4)}')
        if obj is None:
            return types.SimpleNamespace(returncode=1, stdout='', stderr='HTTP 502')
        keys = re.findall(r'(\w+): \.(\w+)', cmd[cmd.index('--jq') + 1])
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({k: obj.get(v) for k, v in keys}),
                                     stderr='')

    monkeypatch.setattr(gh_reconcile.subprocess, 'run', fake_run)
    gh_reconcile._api_cache.clear()
    gh_reconcile._duplicate_cache.clear()
    yield {'table': table, 'canon': canon, 'calls': calls}
    gh_reconcile._api_cache.clear()
    gh_reconcile._duplicate_cache.clear()


def _dup():
    return {'state': 'closed', 'closed_at': '2026-09-01T00:00:00Z',
            'state_reason': 'duplicate', 'pull_request': None}


def _reconcile(tmp_path, text):
    f = tmp_path / 'next_actions.org'
    f.write_text(text, encoding='utf-8')
    n = gh_reconcile.reconcile_file(f, 'o', 'r', False, data_dir=tmp_path, output_index={})
    return n, f.read_text(encoding='utf-8')


def test_q9_the_reason_names_the_canonical_issue(gh, tmp_path):
    gh['table']['o/r/issues/4'] = _dup()
    gh['canon']['o/r/issues/4'] = {'repo': 'o/other', 'number': 9}
    n, text = _reconcile(tmp_path, '* TODO Do it\n  https://github.com/o/r/issues/4\n')
    assert n == 1 and text.startswith('* CANCELLED Do it')
    reason = re.search(r':CANCEL_REASON: (.*)', text).group(1)
    assert 'o/r#4' in reason and 'duplicate of o/other#9' in reason


def test_q9_a_failed_lookup_names_the_duplicate(gh, tmp_path):
    gh['table']['o/r/issues/4'] = _dup()
    gh['canon']['o/r/issues/4'] = 'FAIL'
    n, text = _reconcile(tmp_path, '* TODO Do it\n  https://github.com/o/r/issues/4\n')
    assert n == 1 and text.startswith('* CANCELLED Do it')
    reason = re.search(r':CANCEL_REASON: (.*)', text).group(1)
    assert reason == 'Issue o/r#4 closed as a duplicate'


def test_q9_no_event_names_the_duplicate(gh, tmp_path):
    gh['table']['o/r/issues/4'] = _dup()
    gh['canon']['o/r/issues/4'] = None     # no MarkedAsDuplicate event (or unmarked since)
    n, text = _reconcile(tmp_path, '* TODO Do it\n  https://github.com/o/r/issues/4\n')
    reason = re.search(r':CANCEL_REASON: (.*)', text).group(1)
    assert reason == 'Issue o/r#4 closed as a duplicate'


def test_q9_one_lookup_per_duplicate_and_cached(gh):
    gh['canon']['o/r/issues/4'] = {'repo': 'o/r', 'number': 2}
    ref = gh_reconcile.GithubRef('o', 'r', 4, 'issue')
    assert gh_reconcile.canonical_of_duplicate(ref) == 'o/r#2'
    assert gh_reconcile.canonical_of_duplicate(ref) == 'o/r#2'
    graphql = [c for c in gh['calls'] if c[2] == 'graphql']
    assert len(graphql) == 1
    q = next(a for a in graphql[0] if a.startswith('query='))
    assert 'MarkedAsDuplicateEvent' in q and 'canonical' in q


def test_q9_no_lookup_for_other_close_reasons(gh, tmp_path):
    gh['table']['o/r/issues/4'] = {**_dup(), 'state_reason': 'not_planned'}
    _reconcile(tmp_path, '* TODO Do it\n  https://github.com/o/r/issues/4\n')
    assert not [c for c in gh['calls'] if c[2] == 'graphql']
