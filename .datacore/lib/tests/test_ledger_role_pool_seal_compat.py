"""Round 5, amendment 9 (older-seal compatibility, Codex round 4): the pool
branch's `pool_work` field must not change the state root of anything an older
fold could have produced. Before pools, a role-only task could be created but
never leased, so an item without a lease hashes exactly as it did before the
field existed -- whether or not it was created with a role, and in a space
with pools off or on."""
from __future__ import annotations

from ledger.events import Event, body_dict, compute_hash
from ledger.fold import fold


def _ev(seq, ms, actor, type, payload, prev="GENESIS"):
    body = body_dict(seq, f"{ms:013d}.0000.{actor}", actor, type, payload, prev)
    return Event(**body, hash=compute_hash(body), sig="")


def _root_without_pool_work(state):
    for it in state.items.values():
        it.pool_work = False          # what a fold from before pools carried: no such field
    return state.state_root()


def test_r5_a_role_only_creation_hashes_as_an_older_seal_did():
    a = _ev(0, 1_700_000_000_000, "own", "item.create", {"id": "p1", "title": "pool task", "role": "writer"})
    b = _ev(1, 1_700_000_000_001, "own", "item.create", {"id": "t1", "title": "addressed", "assignee": "wa"}, a.hash)
    state = fold([a, b])
    assert state.state_root() == _root_without_pool_work(fold([a, b]))


def test_r5_an_unleased_role_task_edited_hashes_as_an_older_seal_did():
    a = _ev(0, 1_700_000_000_000, "own", "item.create", {"id": "p1", "title": "pool task", "role": "writer"})
    b = _ev(1, 1_700_000_000_001, "own", "item.update", {"id": "p1", "title": "renamed"}, a.hash)
    assert fold([a, b]).state_root() == _root_without_pool_work(fold([a, b]))
