"""Round 8 of the ledger hub: POLICY VERSIONS (owner decision 1, 2026-10-23;
closes the round-7 audit's R10).

  Every policy change is a ledger record signed by the owner; each event is
  judged by the policy in force when the hub admitted it. A later policy
  change never changes an earlier outcome.

How it is built: the owner's `hub.enable` records the policy in force at
activation; each later change is an owner-signed `policy.set` in the owner's
log, which takes effect only when the active grantor admits it -- its
`ledger.admit` records the frontier (each log's highest seq the hub held).
An event at or below the frontier was admitted under the earlier policy; one
above it, under the new one. Every reader takes the policy from these records
alone, never from the policy file, so every replica agrees.

The first test is the round-7 auditor's R10 probe with its assertions turned
around; it was seen failing on 8f98410f.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import _replica
from ledger import hub as H
from ledger.events import to_line
from ledger.log import EventLog, read_events

INITIAL = None
try:
    from ledger.hub_policy import PolicyInputs
    INITIAL = PolicyInputs(approver=T.APPROVER, cosign_effects=frozenset(), known_effects=frozenset({"publish"}))
    CHANGED = replace(INITIAL, cosign_effects=frozenset({"publish"}))
except ImportError:  # pragma: no cover
    pass


@pytest.fixture
def penv(tmp_path, monkeypatch):
    """A hub space activated under INITIAL (the owner's hub.enable records it)."""
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "runtime"))
    monkeypatch.setattr(T, "TEST_POLICY", INITIAL)
    e = T.Env(tmp_path, monkeypatch)
    yield e
    e.close()


def _reader(env, h, policy, name):
    return H.HubWriter(env.tmp / "own-space", "own", H.LocalTransport(h), keys_dir=env.keys_dir,
                       registry_path=env.registry, state_dir=env.tmp / name, clock=env.clock, policy=policy)


def _status(writer, env, e):
    return writer.receipt_check(env.hub_dir, required=[e.hash])["status"]


def _set_policy(env, policy, reason="eval"):
    return H.set_policy(env.hub_dir, policy, by=T.OWNER, reason=reason, keys_dir=env.keys_dir,
                        registry_path=env.registry)


# ── R10: a policy file edited after the fact changes nothing ────────────────

def test_r8_an_edited_policy_file_never_changes_a_confirmed_outcome(penv):
    env = penv
    h = env.hub(policy=INITIAL)
    own = env.writer("own", H.LocalTransport(h))
    event, _ = own.append("item.create", {"id": "t", "effects": ["publish"]})
    h.seal()
    assert _status(own, env, event) == "confirmed"
    h.close()
    # The file now says `publish` needs a co-sign; nobody recorded that change.
    h = env.hub(policy=CHANGED, record_policy=False)
    reader = _reader(env, h, CHANGED, "policy-reader")
    assert _status(reader, env, event) == "confirmed", "an unrecorded policy edit changed a confirmed creation"
    h.seal()
    assert _status(reader, env, event) == "confirmed"
    assert "t" in h._state()[1].items
    replica = _replica(env, env.hub_dir)
    assert "t" in H.authoritative_state(replica, registry_path=env.registry, policy=CHANGED).items


def test_r8_a_recorded_policy_change_applies_forward_only(penv):
    env = penv
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    before, _ = own.append("item.create", {"id": "t", "effects": ["publish"]})
    h.seal()
    assert _status(own, env, before) == "confirmed"
    _set_policy(env, CHANGED)
    h.seal()                                   # the grantor admits the change, then seals
    admits = [e for e in read_events(env.hub_dir) if e.type == "ledger.admit"
              and (e.payload or {}).get("kind") == "policy"]
    assert len(admits) == 1 and isinstance(admits[0].payload.get("frontier"), dict)
    # The earlier creation keeps its outcome, on the hub and on every replica.
    assert _status(own, env, before) == "confirmed"
    assert "t" in h._state()[1].items
    # A creation after the change is judged by the new policy: refused at the door.
    with pytest.raises(H.HubRefused) as exc:
        own.append("item.create", {"id": "t2", "effects": ["publish"]})
    assert exc.value.code == "not_authorized"
    h.seal()
    assert _status(own, env, before) == "confirmed"
    for policy in (INITIAL, CHANGED, T.TEST_POLICY, None):
        state = H.authoritative_state(_replica(env, env.hub_dir), registry_path=env.registry, policy=policy)
        assert "t" in state.items and "t2" not in state.items


def test_r8_a_git_arrival_after_the_change_is_judged_by_the_new_policy(penv):
    env = penv
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "seed"})
    # wb writes a creation the hub has never seen (its log is not in the hub yet).
    wb_space = env.tmp / "wb-space"
    (wb_space / ".datacore" / "events").mkdir(parents=True)
    T.git_pull(env.hub_dir, wb_space)
    late = T._signed_into(env, env.writer("wb", H.LocalTransport(h)), "item.create",
                          {"id": "late", "effects": ["publish"]})
    _set_policy(env, CHANGED)
    h.seal()
    T.git_pull(wb_space, env.hub_dir)          # arrives after the change: above the frontier
    h.seal()
    assert "late" not in h._state()[1].items, "a git arrival after the change was judged by the old policy"


def test_r8_a_policy_record_by_anyone_but_the_owner_has_no_effect(penv):
    env = penv
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    with pytest.raises(H.HubRefused):
        own.append("policy.set", {"space": h.config().space_id, "hub": T.HUB_ID, "policy": CHANGED.to_record(),
                                  "reason": "not mine to set"})
    # Written straight into a plain log, signed, it is still not the owner's record.
    EventLog(env.hub_dir, "wa", keys_dir=env.keys_dir, registry_path=env.registry, sign=True,
             hub=T.HUB_ID).append("policy.set", {"space": h.config().space_id, "hub": T.HUB_ID,
                                                 "policy": CHANGED.to_record(), "reason": "forged"})
    h.seal()
    assert own.append("item.create", {"id": "t", "effects": ["publish"]})[1]["status"] == "accepted"
    h.seal()
    assert "t" in h._state()[1].items


def test_r8_an_owner_record_the_grantor_never_admitted_has_no_effect(penv):
    env = penv
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t", "effects": ["publish"]})
    h.seal()
    h.close()
    replica = _replica(env, env.hub_dir)
    # The owner's record reaches a replica that the grantor never saw it on.
    _set_policy(env, CHANGED)
    owner_log = env.hub_dir / ".datacore" / "events" / f"{T.OWNER}.jsonl"
    (replica / ".datacore" / "events" / f"{T.OWNER}.jsonl").write_bytes(owner_log.read_bytes())
    assert "t" in H.authoritative_state(replica, registry_path=env.registry).items


def test_r8_enable_records_the_policy_and_a_record_without_one_is_refused(penv):
    env = penv
    enable = next(e for e in read_events(env.hub_dir) if e.type == "hub.enable")
    assert PolicyInputs.from_record(enable.payload["policy"]) == INITIAL
    # A hub.enable without a policy (an older format) is not read around the policy.
    space = env.tmp / "no-policy-space"
    (space / ".datacore" / "events").mkdir(parents=True)
    monkey_enable = H.enable(space, hub_id="hub-np", grantor=T.GRANTOR, owner=T.OWNER, keys_dir=env.keys_dir,
                             registry_path=env.registry, policy=INITIAL)
    assert monkey_enable.hub_id == "hub-np"
    owner_log = space / ".datacore" / "events" / f"{T.OWNER}.jsonl"
    rec = next(e for e in read_events(space) if e.type == "hub.enable")
    stripped = {k: v for k, v in rec.payload.items() if k != "policy"}
    from ledger.keys import sign
    from ledger.events import canonical_bytes, compute_hash, event_body
    new = replace(rec, payload=stripped, hash="", sig="")
    new = replace(new, hash=compute_hash(event_body(new)))
    new = replace(new, sig=sign(T.OWNER, canonical_bytes(event_body(new)), keys_dir=env.keys_dir))
    owner_log.write_text(to_line(new) + "\n")
    with pytest.raises(H.HubRefused) as exc:
        H.authoritative_state(space, registry_path=env.registry, policy=INITIAL)
    assert exc.value.code == "no_policy_record"


def test_r8_status_reports_a_policy_file_that_differs_from_the_ledger(penv):
    env = penv
    h = env.hub(policy=CHANGED, record_policy=False)
    st = h.status()
    assert st["policy"]["recorded_matches_configured"] is False
    h.close()
    h = env.hub(policy=INITIAL)
    assert h.status()["policy"]["recorded_matches_configured"] is True
