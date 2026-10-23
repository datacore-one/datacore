"""LED-17: Moving a space onto the hub changes no task's state, except changes
the owner has explicitly accepted.

Kind: deterministic (a fixture space in a temporary folder; never the live
installation; the real spaces are checked by the read-only dry run
`python3 -m ledger.activation`).

Seeded failure: the round-6 activation finding, on 4b5c81ec (the round-6
head before activation froze the old reversals): moving a space onto the hub
re-judged its old cleanup reversals by the hub's rule (owner only, inside the
window), so every reversal a human principal had made under the old rule lost
its effect and the reversed completions came back. The real dry run then
showed 5-plur's and 6-meridian's old reversals (two of them cancelling job
attestations) would have been undone.

The one accepted change (owner, 2026-10-23, round 6): items whose dismissal
by a lower-ranked agent the hub's arbitration refuses reopen. It is listed
below in ACCEPTED, beside the eval; nothing else may change.

Promise, as evals: a space with pre-activation history -- an old-rule
reversal of a completion, an old-rule reversal of a job attestation, a bad
event excused by a body-pinned reversal, unsigned legacy events, and a
dismissal of another agent's task by a lower-ranked agent -- is checked by
the dry run (which writes nothing and reports only the accepted change) and
is then activated on a copy: every task reads as before except the accepted
one, and the frozen reversals keep their effect.
"""
from __future__ import annotations

import shutil
from dataclasses import replace

import pytest

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import env  # noqa: F401
from ledger import hub as H
from ledger.events import body_dict, compute_hash
from ledger.fold import fold
from ledger.log import read_events

#: The changes the owner accepted on activation (round 6): items reopened
#: because the hub's arbitration refuses a lower-ranked agent's dismissal.
ACCEPTED = {"t-dismissed-by-lower-rank"}


def _policy():
    from ledger.hub_policy import PolicyInputs
    return PolicyInputs(approver=T.APPROVER, cosign_effects=T.TEST_POLICY.cosign_effects,
                        arbitration=("owner", "wb", "wa"))


def _chain(actor, rows, start_ms, prev="GENESIS", seq0=0):
    out = []
    for i, (type_, payload) in enumerate(rows):
        e = T._raw_event(seq0 + i, f"{start_ms + i:013d}.0000.{actor}", actor, type_, payload, prev)
        out.append(e)
        prev = e.hash
    return out


def _fixture(env, monkeypatch):
    T._with_principals(env, monkeypatch)
    space = env.tmp / "before-the-hub"
    (space / ".datacore" / "events").mkdir(parents=True)
    ms = T._real_ms() - 600_000
    own = _chain("own", [("item.create", {"id": "t-done", "title": "one", "assignee": "wa"}),
                         ("item.create", {"id": "t-bad", "title": "two", "assignee": "wa"}),
                         ("item.create", {"id": "t-dismissed-by-lower-rank", "title": "three", "assignee": "wb"}),
                         ("item.create", {"id": "t-plain", "title": "four"})], ms)
    wa = _chain("wa", [("item.claim", {"id": "t-done"}), ("item.complete", {"id": "t-done"}),
                       ("metric.attest", {"metric": "job.verify", "job": "fixture-job", "ok": False,
                                          "failures": ["wrong"]})], ms + 10)
    good = T._raw_event(3, f"{ms + 20:013d}.0000.wa", "wa", "item.claim", {"id": "t-bad"}, wa[-1].hash)
    bad = replace(good, payload={"id": "t-bad", "edited": True})        # body edited after hashing
    nxt = T._raw_event(4, f"{ms + 21:013d}.0000.wa", "wa", "item.release", {"id": "t-bad"}, bad.hash)
    wb = _chain("wb", [("item.claim", {"id": "t-dismissed-by-lower-rank"})], ms + 25)
    wa2 = _chain("wa", [("item.dismiss", {"id": "t-dismissed-by-lower-rank"})], ms + 26, prev=nxt.hash, seq0=5)
    body_of_bad = compute_hash(body_dict(bad.seq, bad.hlc, bad.actor, bad.type, bad.payload, bad.prev))
    voids = _chain("own", [
        ("ledger.void", {"log": "wa", "seq": wa[1].seq, "hash": wa[1].hash, "reason": "completion withdrawn"}),
        ("ledger.void", {"log": "wa", "seq": wa[2].seq, "hash": wa[2].hash, "reason": "attestation was wrong"}),
        ("ledger.void", {"log": "wa", "seq": 3, "hash": bad.hash, "reason": "bad event", "body_sha256": body_of_bad}),
    ], ms + 30, prev=own[-1].hash, seq0=len(own))
    T._write_raw(space, "own", own + voids)
    T._write_raw(space, "wa", wa + [bad, nxt] + wa2)
    T._write_raw(space, "wb", wb)
    return space, {"completion": wa[1], "attest": wa[2], "bad": bad}


def _view(state):
    return {i: (it.status, it.owner) for i, it in sorted(state.items.items())}


def _files(space):
    return {p.name: p.read_bytes() for p in sorted((space / ".datacore").rglob("*")) if p.is_file()}


def test_the_dry_run_writes_nothing_and_reports_only_accepted_changes(env, monkeypatch):
    from ledger import activation
    space, _ = _fixture(env, monkeypatch)
    before = _files(space)
    report = activation.check(space, policy=_policy())
    assert _files(space) == before, "the dry run wrote into the space"
    unexpected = sorted(set(report["items"]) - ACCEPTED)
    assert not unexpected, f"activation would change tasks nobody accepted: {unexpected}: {report['items']}"
    assert not report["refused_logs"], f"activation would stop admitting logs: {report['refused_logs']}"
    assert report["reversals_kept"] == 3, f"expected the 3 old reversals kept, got {report['reversals_kept']}"


def test_activating_a_copy_changes_only_accepted_tasks(env, monkeypatch):
    space, ev = _fixture(env, monkeypatch)
    today = _view(fold(read_events(space)))                       # the old rule, before the hub
    assert today["t-done"][0] == "claimed", "fixture: the old-rule reversal of the completion is in effect"
    copy = env.tmp / "activated-copy"
    shutil.copytree(space, copy)
    for w in ("own", "wa", "wb"):
        H.declare_ready(copy, w, keys_dir=env.keys_dir, registry_path=env.registry)
    import inspect
    # The activation records the policy in force (round 8); older code had no such argument.
    extra = {"policy": _policy()} if "policy" in inspect.signature(H.enable).parameters else {}
    H.enable(copy, hub_id=T.HUB_ID, grantor=T.GRANTOR, lease_ttl_s=900, owner=T.OWNER,
             keys_dir=env.keys_dir, registry_path=env.registry, **extra)
    after = _view(H.authoritative_state(copy, registry_path=env.registry, policy=_policy()))
    changed = sorted(i for i in set(today) | set(after) if today.get(i) != after.get(i))
    unexpected = [f"{i}: {today.get(i)} -> {after.get(i)}" for i in changed if i not in ACCEPTED]
    assert not unexpected, f"activation changed tasks nobody accepted: {unexpected}"
    events, kw = H.authoritative_events(copy, registry_path=env.registry, policy=_policy())
    fin = fold(events, **kw).finality
    for name in ("completion", "attest", "bad"):
        e = ev[name]
        assert fin.voids.applies("wa", e), f"the old reversal of the {name} lost its effect on activation"
