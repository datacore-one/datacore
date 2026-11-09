"""A pending record that captured nothing can be cleared on proof.

2026-10-01 03:04:24 UTC on the nightshift host: a claim in 3-fds reserved its
publication (`datacore-publication-pending.json`) and the process was replaced
by the next run 22 seconds later, before it captured anything. The record had
`expected_tree: null` and no capture ref. Every later publication into 3-fds was
refused ("Source inventory is unavailable or unresolved"), and
`publication_reconcile.py` refused too: "record has no captured tree -- nothing
to compare". The claim log it guarded had already reached origin by 06:25.

A record that captured nothing holds no work of its own: it reserved paths
whose only copy is the working file. So the proof is about that file: when the
current version of EVERY reserved path is already on origin, nothing can be
lost by clearing it. One path that is not there refuses, and the record stays
for a person. A record that did capture keeps its existing proof.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

EVENTS = '.datacore/events/nightshift.jsonl'


def _git(repo, *args):
    return subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True,
                          check=True).stdout.strip()


def _space(tmp_path):
    origin = tmp_path / 'origin.git'
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(origin)], check=True)
    repo = tmp_path / '3-fds'
    subprocess.run(['git', 'clone', '-q', str(origin), str(repo)], check=True, capture_output=True)
    for key, value in (('user.email', 't@t'), ('user.name', 't')):
        _git(repo, 'config', key, value)
    (repo / '.datacore' / 'events').mkdir(parents=True)
    (repo / EVENTS).write_text('{"n": 1}\n')
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-q', '-m', 'base')
    _git(repo, 'branch', '-M', 'main')
    _git(repo, 'push', '-q', '-u', 'origin', 'main')
    return repo


def _uncaptured_record(repo):
    """The 03:04 shape: a claim candidate reserved, then the process is gone."""
    from publication_state import Reservation
    base = _git(repo, 'rev-parse', 'origin/main')
    branch = 'datacore/claim-candidates/nightshift/fabbddd44311445aa75ea87015ecae80'
    _git(repo, 'update-ref', f'refs/heads/{branch}', base)
    with (repo / EVENTS).open('a') as log:
        log.write('{"n": 2}\n')  # the claim the process was publishing
    reservation = Reservation(repo, branch, [EVENTS])
    reservation.create()          # ...and nothing after it
    record = json.loads(reservation.path.read_text())
    assert record['expected_tree'] is None
    assert not _git(repo, 'for-each-ref', 'refs/datacore/publication-captures/')
    return reservation.path


def test_an_uncaptured_record_clears_once_the_reserved_content_is_on_origin(tmp_path):
    import publication_reconcile as PR
    from publication_state import require_clear
    repo = _space(tmp_path)
    record = _uncaptured_record(repo)
    # The transport later published the same log (Miles, 06:25 on the host).
    _git(repo, 'commit', '-q', '-am', 'ledger: autosave before converge')
    _git(repo, 'push', '-q', 'origin', 'main')
    with pytest.raises(RuntimeError):
        require_clear(repo)
    assert PR.reconcile(repo, apply=True) == 0
    assert not record.exists()
    require_clear(repo)  # publication into the space works again
    kept = list((repo / '.git' / 'datacore-publication-workspaces').glob('*/publication-intent.json'))
    assert kept and json.loads(kept[0].read_text())['paths'] == [EVENTS]


def test_an_uncaptured_record_stays_while_the_reserved_content_is_only_local(tmp_path, capsys):
    import publication_reconcile as PR
    repo = _space(tmp_path)
    record = _uncaptured_record(repo)
    assert PR.reconcile(repo, apply=True) == 1
    assert record.exists(), 'unpublished work must be left for a person'
    assert f'NOT PROVEN {EVENTS}' in capsys.readouterr().out


def test_an_uncaptured_record_stays_when_a_reserved_file_is_gone(tmp_path):
    import publication_reconcile as PR
    repo = _space(tmp_path)
    record = _uncaptured_record(repo)
    (repo / EVENTS).unlink()
    assert PR.reconcile(repo, apply=True) == 1
    assert record.exists()


def test_a_dry_run_never_clears(tmp_path):
    import publication_reconcile as PR
    repo = _space(tmp_path)
    record = _uncaptured_record(repo)
    _git(repo, 'commit', '-q', '-am', 'published')
    _git(repo, 'push', '-q', 'origin', 'main')
    assert PR.reconcile(repo, apply=False) == 0
    assert record.exists()
