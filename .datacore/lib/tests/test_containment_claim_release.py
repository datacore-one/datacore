"""Only the claimant releases a claim (ledger upgrade Phase 5A, break 4).

The rogue-agent simulation (2026-10-03, F33) read off the code that nothing
compared a releasing writer with the claim's holder at write time: any agent
could run `ledger_cli append --type item.release` (that is `guarded_append`),
and an agent principal listed in the arbitration order passed `check_override`
for another agent's claim. The fold already ignores a release by anyone but
the holder; the write now refuses it too, so the record and the state agree.
The owner frees a stuck claim through the reconcile tool, never by releasing
someone else's claim.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from ledger.fold import fold  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ledger.policy import Policy, PolicyError, guarded_append  # noqa: E402


@pytest.fixture
def space(tmp_path, monkeypatch):
    reg = tmp_path / 'principals.yaml'
    reg.write_text('principals:\n  gregor: {kind: human, writes_as: [mac]}\n'
                   '  winston: {kind: agent, writes_as: [winston]}\n'
                   '  miles: {kind: agent, writes_as: [miles, nightshift]}\n'
                   '  data: {kind: agent, writes_as: [data]}\n')
    import actor_identity
    monkeypatch.setattr(actor_identity, 'PRINCIPALS', reg)
    sd = tmp_path / '5-plur'
    (sd / '.datacore' / 'events').mkdir(parents=True)
    log = EventLog(sd, 'nightshift')
    log.append('item.create', {'id': 't1', 'title': 'Fix forget(scope:)'})
    log.append('item.claim', {'id': 't1', 'executor': 'server:nightshift'})
    pol = Policy(approver='mac', cosign_effects=frozenset(), principals={},
                 arbitration=('gregor', 'winston'))
    return sd, pol


@pytest.mark.parametrize('thief', ['winston', 'data', 'miles'])
def test_a_release_of_another_writers_claim_is_refused_at_write(space, thief):
    sd, pol = space
    with pytest.raises(PolicyError, match='only the claimant'):
        guarded_append(EventLog(sd, thief), 'item.release', {'id': 't1', 'reason': 'mine now'},
                       policy=pol, space_dir=sd)
    assert fold(read_events(sd)).items['t1'].owner == 'nightshift'


def test_the_fold_ignores_a_release_that_got_past_the_write(space):
    sd, _ = space
    EventLog(sd, 'winston').append('item.release', {'id': 't1', 'reason': 'forged'})
    item = fold(read_events(sd)).items['t1']
    assert item.status == 'claimed' and item.owner == 'nightshift'


def test_the_claimant_still_releases_its_own_claim(space):
    sd, pol = space
    guarded_append(EventLog(sd, 'nightshift'), 'item.release', {'id': 't1', 'reason': 'failed'},
                   policy=pol, space_dir=sd)
    item = fold(read_events(sd)).items['t1']
    assert item.status == 'created' and item.owner is None


def test_arbitration_still_lets_an_arbiter_dismiss(space):
    """Closing is arbitration and stays as it was; only release is the claimant's."""
    sd, pol = space
    guarded_append(EventLog(sd, 'winston'), 'item.dismiss', {'id': 't1', 'kind': 'dropped', 'reason': 'x'},
                   policy=pol, space_dir=sd)
