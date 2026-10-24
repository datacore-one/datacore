"""In-flight writes at activation: the owner extends the cutover (2026-10-24).

Activation is distributed by git. Between the owner's `hub.enable` and a
writer's host pulling it, that writer keeps appending with no hub stamp. On the
first live activation (six spaces) Winston and Miles wrote real work in that
window -- item creates, claims, completions, a spend record -- signed by them
but unstamped. Admission refused the first such event (`wrong_hub`), and a
refused event ends the chain, so every later event of those writers in those
spaces was refused too.

Rule, written before the code:
  * the owner records `hub.cutover`: per log, the last in-flight event (seq and
    hash), extending the legacy prefix the manifest names;
  * `extend_cutover` covers only the run of UNSTAMPED events directly after the
    current boundary, each verifying as signed by its own writer; it stops at
    the first stamped, unsigned or wrongly signed event;
  * only the owner extends; a boundary never moves back;
  * after it, the in-flight events and the writer's later stamped events are
    admitted.
"""
from __future__ import annotations

import pytest

from ledger import hub as hubmod
from ledger.log import EventLog, read_events

from tests.test_ledger_hub import GRANTOR, HUB_ID, OWNER, TEST_POLICY, env  # noqa: F401  (fixture)


def _activated(env, name="inflight"):
    space = env.tmp / name
    (space / ".datacore" / "events").mkdir(parents=True)
    EventLog(space, "wa", sign=False).append("item.create", {"id": "old", "title": "before"})
    hubmod.declare_ready(space, "wa", keys_dir=env.keys_dir, registry_path=env.registry)
    hubmod.enable(space, hub_id=HUB_ID, grantor=GRANTOR, owner=OWNER, keys_dir=env.keys_dir,
                  registry_path=env.registry, policy=TEST_POLICY)
    return space


def _inflight(env, space, actor, n):
    """Signed appends from a host that has not pulled the activation yet: no hub stamp."""
    log = EventLog(space, actor, keys_dir=env.keys_dir, registry_path=env.registry, sign=True, hub="")
    return [log.append("item.create", {"id": f"{actor}-inflight-{i}", "title": "in flight"}) for i in range(n)]


def _append_unsigned(space, actor, prior, payload):
    """A line nobody signed, chained onto `prior` (what a forger could add to the file)."""
    import json
    from ledger.events import compute_hash, event_body, to_line
    d = json.loads(to_line(prior))
    d.update(seq=prior.seq + 1, prev=prior.hash, payload=payload, sig="")
    from ledger.events import from_line
    e = from_line(json.dumps(d))
    d["hash"] = compute_hash(event_body(e))
    with open(space / ".datacore" / "events" / f"{actor}.jsonl", "a") as f:
        f.write(json.dumps(d, sort_keys=True) + "\n")


def _stamped(env, space, actor, item):
    return EventLog(space, actor, keys_dir=env.keys_dir, registry_path=env.registry, sign=True).append(
        "item.create", {"id": item, "title": "after the pull"})


def _items(env, space):
    return hubmod.authoritative_state(space, registry_path=env.registry, policy=TEST_POLICY,
                                      now_ms=env.clock()).items


def test_in_flight_writes_and_everything_after_them_are_admitted_once_the_owner_extends(env):
    space = _activated(env)
    inflight = _inflight(env, space, "wa", 2)
    _stamped(env, space, "wa", "after")
    items = _items(env, space)
    assert "wa-inflight-0" not in items and "after" not in items          # the defect
    rec = hubmod.extend_cutover(space, by=OWNER, reason="writes in flight at activation",
                                keys_dir=env.keys_dir, registry_path=env.registry)
    assert rec.type == "hub.cutover"
    assert rec.payload["logs"] == {"wa": {"seq": inflight[-1].seq, "head": inflight[-1].hash}}
    items = _items(env, space)
    assert {"old", "wa-inflight-0", "wa-inflight-1", "after"} <= set(items)


def test_the_extension_stops_at_an_event_its_writer_did_not_sign(env):
    space = _activated(env, "forged")
    good = _inflight(env, space, "wa", 1)
    _append_unsigned(space, "wa", good[-1], {"id": "forged", "title": "unsigned"})
    rec = hubmod.extend_cutover(space, by=OWNER, reason="in flight", keys_dir=env.keys_dir,
                                registry_path=env.registry)
    assert rec.payload["logs"] == {"wa": {"seq": good[-1].seq, "head": good[-1].hash}}
    items = _items(env, space)
    assert "wa-inflight-0" in items and "forged" not in items


def test_only_the_owner_extends(env):
    space = _activated(env, "notowner")
    _inflight(env, space, "wa", 1)
    with pytest.raises(hubmod.HubRefused) as exc:
        hubmod.extend_cutover(space, by="wa", reason="mine", keys_dir=env.keys_dir, registry_path=env.registry)
    assert exc.value.code == "not_authorized"


def test_nothing_in_flight_writes_nothing(env):
    space = _activated(env, "quiet")
    _stamped(env, space, "wa", "after")
    before = len(read_events(space))
    assert hubmod.extend_cutover(space, by=OWNER, reason="check", keys_dir=env.keys_dir,
                                 registry_path=env.registry) is None
    assert len(read_events(space)) == before


def test_a_boundary_never_moves_back(env):
    from ledger.hub_admission import Authority
    space = _activated(env, "back")
    _inflight(env, space, "wa", 2)
    hubmod.extend_cutover(space, by=OWNER, reason="in flight", keys_dir=env.keys_dir, registry_path=env.registry)
    cfg = hubmod.load_config(space)
    auth = hubmod.authority(space, cfg, env.registry)
    assert isinstance(auth, Authority) and auth.legacy["wa"][0] == 2


def test_a_running_hub_seals_over_the_extension_without_a_restart(env):
    """Found live: the box daemon's hub kept sealing below the in-flight
    events until it was restarted. A running hub takes the owner's extension
    the moment it is in the ledger."""
    space = _activated(env, "running")
    hub = env.hub(space=space)
    inflight = _inflight(env, space, "wa", 2)
    after = _stamped(env, space, "wa", "after")
    hub.seal()
    hubmod.extend_cutover(space, by=OWNER, reason="in flight", keys_dir=env.keys_dir, registry_path=env.registry)
    hub.seal()
    seal = [e for e in read_events(space) if e.type == "ledger.seal"][-1]
    assert seal.payload["watermarks"].get("wa", -1) >= after.seq, (seal.payload["watermarks"], inflight[-1].seq)
