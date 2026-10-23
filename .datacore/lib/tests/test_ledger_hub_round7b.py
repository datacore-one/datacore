"""Round 7, second pass: the round-7 independent audit's first reproductions
(Codex, test_r7_probes.py) turned into regression tests, assertion turned
around, each seen failing on 0758f855 before the fix.

  * an unsealed event stamped AT the sealed history's last stamp is late too;
  * a successor's seal covers what EVERY earlier epoch's last seal covered,
    also across an epoch that never sealed;
  * no writer (EventLog, the CLI) writes a reversal of a control record it
    holds; a writer whose space records a hub but lost its config refuses to
    write unsigned;
  * fail-closed on a missing config is bound to THIS space (the activation
    record's manifest names logs this space holds), holds when the key
    registry is missing too, and is not dodged by a JSON escape;
  * a reversed release does not resolve an uncertain attempt.
"""
from __future__ import annotations

import time

import pytest

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import _confirmed, _replica, _setup, _void, env  # noqa: F401
from tests.test_ledger_hub_round7 import _logs_only_replica
from ledger import hub as H
from ledger.events import to_line
from ledger.log import EventLog, read_events


def _rejected(h):
    return {x for (_, _, x) in h._rejected()}


def _void_of(e, reason="the record was incorrect"):
    return {"log": getattr(e, "log", None) or e.actor, "seq": e.seq, "hash": e.hash, "reason": reason}


@pytest.mark.parametrize("arrival", ["append", "git"])
def test_r7b_an_equal_stamp_never_reorders_a_sealed_claim(env, arrival):
    h = env.hub()
    own, wa, wb = [env.writer(a, H.LocalTransport(h)) for a in ("own", "wa", "wb")]
    base = int(time.time() * 1000)
    h.append(own._submission(T._signed_into(env, own, "item.create", {"id": "t"}, hlc=f"{base:013d}.0000.own")))
    stamp = f"{base + 200:013d}.0000.wb"
    original = T._signed_into(env, wb, "item.claim", {"id": "t"}, hlc=stamp)
    h.append(wb._submission(original))
    h.seal()
    late = T._signed_into(env, wa, "item.claim", {"id": "t"}, hlc=stamp)
    if arrival == "append":
        try:
            r = h.append(wa._submission(late))
            assert r["status"] == "rejected", r
        except H.HubRefused as exc:
            assert exc.code == "sealed_late", exc
    else:
        T.git_pull(wa.space_dir, env.hub_dir)
    h.seal()
    assert wb.receipt_check(env.hub_dir, required=[original.hash])["status"] == "confirmed"
    assert h._state()[1].items["t"].owner == "wb"


def test_r7b_three_epochs_keep_the_oldest_sealed_coverage(env):
    h = env.hub()
    own, wa, wb = [env.writer(a, H.LocalTransport(h)) for a in ("own", "wa", "wb")]
    base = int(time.time() * 1000)
    h.append(own._submission(T._signed_into(env, own, "item.create", {"id": "t"}, hlc=f"{base:013d}.0000.own")))
    original = T._signed_into(env, wa, "item.claim", {"id": "t"}, hlc=f"{base + 200:013d}.0000.wa")
    h.append(wa._submission(original))
    h.seal()
    h.fence("first promotion")
    H.promote(env.hub_dir, "hubb", by=T.OWNER, reason="first", keys_dir=env.keys_dir,
              registry_path=env.registry, clock=env.clock)
    middle = env.hub("hubb")
    middle.fence("second promotion without a seal")
    H.promote(env.hub_dir, "hubc", by=T.OWNER, reason="second", keys_dir=env.keys_dir,
              registry_path=env.registry, clock=env.clock)
    replica = _replica(env, env.hub_dir)
    (replica / ".datacore/events/wa.jsonl").unlink()
    new = env.hub("hubc", space=replica, state="third")
    late = T._signed_into(env, wb, "item.claim", {"id": "t"}, hlc=f"{base + 100:013d}.0000.wb")
    try:
        new.append(wb._submission(late))
    except H.HubRefused:
        pass
    with pytest.raises(H.HubRefused) as exc:                 # it does not hold what epoch 0 sealed
        new.seal()
    assert exc.value.code == "behind_predecessor", exc.value
    T.git_pull(wa.space_dir, replica)
    new.seal()
    assert wa.receipt_check(replica, required=[original.hash])["status"] == "confirmed"
    assert new._state()[1].items["t"].owner == "wa"


def test_r7b_the_event_log_refuses_a_reversal_of_a_control_record(env, monkeypatch):
    from ledger.log import NotReversible
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    rotation, _ = wa.append("key.rotate", {"actor": "wa", "reason": "review"})
    T.git_pull(env.hub_dir, owner.space_dir)
    before = owner.log.path.read_text()
    with pytest.raises(NotReversible):
        owner.log.append("ledger.void", _void_of(rotation))
    assert owner.log.path.read_text() == before


def test_r7b_the_cli_refuses_a_reversal_of_a_control_record(env, monkeypatch):
    import argparse
    import ledger_cli
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    rotation, _ = wa.append("key.rotate", {"actor": "wa", "reason": "review"})
    T.git_pull(env.hub_dir, owner.space_dir)
    monkeypatch.setattr(ledger_cli, "EventLog", lambda space, actor: EventLog(
        space, actor, sign=True, keys_dir=env.keys_dir, registry_path=env.registry))
    with pytest.raises(SystemExit):
        ledger_cli.cmd_void(argparse.Namespace(space=str(owner.space_dir), actor=T.OWNER,
                                               log="wa", seq=rotation.seq, reason="bad rotation"))
    assert not any(e.type == "ledger.void" and e.payload.get("hash") == rotation.hash
                   for e in read_events(owner.space_dir))


def test_r7b_a_writer_whose_space_lost_its_config_refuses_unsigned_writes(env):
    from ledger.log import HubSigningRequired
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t", "assignee": "wa"})
    replica = _replica(env, env.hub_dir)
    (replica / H.CONFIG).unlink()
    with pytest.raises(HubSigningRequired):
        EventLog(replica, "wb", sign=False, registry_path=env.registry).append("item.claim", {"id": "t"})


def test_r7b_an_activation_record_from_another_space_does_not_block_a_plain_space(env):
    foreign = next(e for e in read_events(env.hub_dir) if e.type == "hub.enable")
    plain = env.tmp / "unrelated-plain-space"
    EventLog(plain, "wa", sign=False).append("item.create", {"id": "local"})
    (plain / ".datacore/events" / f"{foreign.actor}.jsonl").write_text(to_line(foreign) + "\n")
    assert H.authoritative_state(plain, registry_path=env.registry).items["local"].status == "created"


def test_r7b_a_missing_key_registry_does_not_reopen_the_plain_fold(env):
    replica = _logs_only_replica(env)
    missing = env.tmp / "unrestored-registry.yaml"
    with pytest.raises(H.HubUnavailable):
        H.authoritative_state(replica, registry_path=missing, policy=T.TEST_POLICY)
    with pytest.raises(H.HubUnavailable):
        H.strict_problems(replica, registry_path=missing)


def test_r7b_a_json_escape_does_not_hide_the_activation_record(env, monkeypatch):
    from ledger import voids
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    rotation, _ = wa.append("key.rotate", {"actor": "wa", "reason": "review"})
    T._signed_into(env, owner, "ledger.void", _void_of(rotation))
    T.git_pull(owner.space_dir, env.hub_dir)
    h.seal()
    replica = _replica(env, env.hub_dir)
    (replica / H.CONFIG).unlink()
    owner_path = replica / ".datacore/events" / f"{T.OWNER}.jsonl"
    owner_path.write_text(owner_path.read_text().replace('"hub.enable"', '"hub.\\u0065nable"'))
    assert not voids.for_events_dir(replica / ".datacore/events", registry_path=env.registry).applies("wa", rotation)


def test_r7b_a_reversed_release_does_not_resolve_an_uncertain_attempt(env):
    h, own, wa, grant = _setup(env)
    release, _ = wa.append("item.release", {"id": "t", "attempt": grant["attempt"], "task_hash": grant["task_hash"]})
    owner = env.writer(T.OWNER, H.LocalTransport(h))
    owner.append("ledger.void", _void(release))
    events, state = h._state()
    assert state.finality.voids.applies("wa", release)
    env.clock.advance(901)
    h.expire_due()
    events, state = h._state()
    assert not h._attempt_resolved(state.items["t"], events, "wa", state)


def test_r7b_an_answered_request_is_retried_as_is_after_the_freshness_window(env):
    """J8 end to end (round-7 audit, R9): a writer whose answer was lost
    retries the SAME request however late, and gets the hub's stored answer."""
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t"})
    wa = env.writer("wa", T._LosesFirstAnswer(h))
    with pytest.raises(H.HubUnavailable):
        wa.request_lease("t")
    env.clock.advance(H.REQUEST_WINDOW_MS / 1000 + 1)
    answer = wa.request_lease("t")
    assert answer["status"] == "granted", answer
    assert wa.receipts(), "the writer kept no receipt of the grant it was given"


def test_r7b_an_unanswered_stale_request_is_replaced_by_a_fresh_one(env):
    """A pending request the hub never answered and that has gone stale is
    refused by the hub; the writer then asks afresh, once."""
    import json
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t"})
    wa = env.writer("wa", H.LocalTransport(h))
    req = H.sign_request("wa", "lease.request", {"hub": wa.cfg.hub_id, "space": wa.cfg.space_id, "item": "t"},
                         keys_dir=env.keys_dir, clock=env.clock)
    wa._pending_path("lease.request", "t").write_text(json.dumps(req, sort_keys=True))
    env.clock.advance(H.REQUEST_WINDOW_MS / 1000 + 1)
    assert wa.request_lease("t")["status"] == "granted"
