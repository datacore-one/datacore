"""Activating a space on the hub never changes what its reversals already did
(round 6; owner, 2026-10-23: every space moves to the hub at once, so the old
cleanup reversals are carried by the activation record, not by an exemption).

The owner's `hub.enable` freezes the reversals in effect by the old rule at
activation; each keeps its effect for good, decided from the ledger alone.
Every reversal after activation follows the correction window and the owner
records. `ledger.activation` is the read-only dry run.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import env  # noqa: F401
from ledger import hub as H
from ledger.events import body_dict, compute_hash
from ledger.fold import fold
from ledger.log import read_events


def _chain(actor, rows, start_ms):
    prev, out = "GENESIS", []
    for seq, (type_, payload) in enumerate(rows):
        e = T._raw_event(seq, f"{start_ms + seq:013d}.0000.{actor}", actor, type_, payload, prev)
        out.append(e)
        prev = e.hash
    return out


def _cleaned_space(env, monkeypatch, name="cleaned"):
    """A space before the hub: wa completed t1, and a human principal (`own`,
    not the space owner) reversed that completion; wa also wrote a bad event
    (its stored hash never matched its body) that `own` cancelled with a
    body-pinned reversal -- the old cleanup of bad events."""
    T._with_principals(env, monkeypatch)
    space = env.tmp / name
    (space / ".datacore" / "events").mkdir(parents=True)
    ms = T._real_ms() - 600_000
    own0 = _chain("own", [("item.create", {"id": "t1", "title": "one", "assignee": "wa"}),
                          ("item.create", {"id": "t2", "title": "two", "assignee": "wa"})], ms)
    wa = _chain("wa", [("item.claim", {"id": "t1"}), ("item.complete", {"id": "t1"})], ms + 10)
    # a bad event: the body was edited after hashing, and the chain went on from its stored hash
    good = T._raw_event(2, f"{ms + 20:013d}.0000.wa", "wa", "item.claim", {"id": "t2"}, wa[-1].hash)
    bad = replace(good, payload={"id": "t2", "edited": True})
    nxt = T._raw_event(3, f"{ms + 21:013d}.0000.wa", "wa", "item.release", {"id": "t2"}, bad.hash)
    body_of_bad = compute_hash(body_dict(bad.seq, bad.hlc, bad.actor, bad.type, bad.payload, bad.prev))
    v1 = T._raw_event(2, f"{ms + 30:013d}.0000.own", "own", "ledger.void",
                      {"log": "wa", "seq": wa[1].seq, "hash": wa[1].hash, "reason": "completion withdrawn"},
                      own0[-1].hash)
    v2 = T._raw_event(3, f"{ms + 31:013d}.0000.own", "own", "ledger.void",
                      {"log": "wa", "seq": 2, "hash": bad.hash, "reason": "bad event", "body_sha256": body_of_bad},
                      v1.hash)
    T._write_raw(space, "own", own0 + [v1, v2])
    T._write_raw(space, "wa", wa + [bad, nxt])
    return space, {"completion": wa[1], "bad": bad, "v1": v1, "v2": v2}


def _activate(env, space):
    for w in ("own", "wa"):
        H.declare_ready(space, w, keys_dir=env.keys_dir, registry_path=env.registry)
    H.enable(space, hub_id=T.HUB_ID, grantor=T.GRANTOR, lease_ttl_s=900, owner=T.OWNER,
             keys_dir=env.keys_dir, registry_path=env.registry, policy=T.TEST_POLICY)


def _view(state):
    return {i: (it.status, it.owner) for i, it in sorted(state.items.items())}


def test_activation_keeps_every_pre_activation_reversal_effect(env, monkeypatch):
    import actor_identity
    space, ev = _cleaned_space(env, monkeypatch)
    before = _view(fold(read_events(space)))                        # the old rule, today
    assert before["t1"][0] == "claimed"                             # the completion is reversed
    _activate(env, space)
    rec = next(e for e in read_events(space) if e.type == "hub.enable")
    assert {(r["void"]["hash"]) for r in rec.payload["legacy_reversals"]} == {ev["v1"].hash, ev["v2"].hash}
    assert H.strict_problems(space, registry_path=env.registry) == []      # the bad event stays excused
    after = H.authoritative_state(space, registry_path=env.registry, policy=T.TEST_POLICY)
    assert _view(after) == before
    # decided from the ledger alone: a registry change, even an empty one, changes nothing
    Path(actor_identity.PRINCIPALS).write_text("principals: {}\n")
    assert _view(H.authoritative_state(space, registry_path=env.registry, policy=T.TEST_POLICY)) == before
    # and a seal over it verifies, with the same state
    h = env.hub(space=space, state="activated")
    h.seal()
    assert H.seal_status(space, registry_path=env.registry, policy=T.TEST_POLICY)[0] is True
    settled = H.authoritative_state(space, registry_path=env.registry, policy=T.TEST_POLICY, settled=True)
    assert _view(settled) == before


def test_after_activation_reversals_follow_the_window_and_the_owner_records(env, monkeypatch):
    space, ev = _cleaned_space(env, monkeypatch, "cleaned-after")
    _activate(env, space)
    h = env.hub(space=space, state="after")
    own = T._writer_in(env, space, "own", H.LocalTransport(h))
    v = T._signed_into(env, own, "ledger.void",
                       {"log": "own", "seq": 0, "hash": read_events(space)[0].hash, "reason": "x"})
    with pytest.raises(H.HubRefused) as exc:
        h.append(own._submission(v))
    assert exc.value.code in ("not_authorized", "reversal_closed"), exc.value


def test_the_activation_dry_run_reports_no_change_for_a_cleaned_space(env, monkeypatch):
    from ledger import activation
    space, _ = _cleaned_space(env, monkeypatch, "dry")
    files = {p.name: p.read_bytes() for p in (space / ".datacore/events").glob("*.jsonl")}
    report = activation.check(space, policy=T.TEST_POLICY)
    assert report["changes"] is False, report
    assert report["reversals_kept"] == 2
    assert files == {p.name: p.read_bytes() for p in (space / ".datacore/events").glob("*.jsonl")}
    assert not (space / H.CONFIG).exists()                          # read-only: nothing was written


def test_the_activation_dry_run_reports_what_would_change(env, monkeypatch):
    """A claim of work addressed to someone else folds today but is not an
    authorized transition under the hub's policy: the dry run must say so."""
    from ledger import activation
    T._with_principals(env, monkeypatch)
    space = env.tmp / "would-change"
    (space / ".datacore" / "events").mkdir(parents=True)
    ms = T._real_ms() - 600_000
    T._write_raw(space, "own", _chain("own", [("item.create", {"id": "t", "title": "t", "assignee": "wa"})], ms))
    T._write_raw(space, "wb", _chain("wb", [("item.claim", {"id": "t"})], ms + 10))
    report = activation.check(space, policy=T.TEST_POLICY)
    assert report["changes"] is True and "t" in report["items"], report
