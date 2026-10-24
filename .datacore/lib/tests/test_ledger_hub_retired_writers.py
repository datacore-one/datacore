"""Retired writers at activation (owner, 2026-10-24: activate every space).

The readiness gate (audit finding 17) asks every writer whose log is in the
cutover manifest to declare, from its own host, that its code speaks the hub
protocol. A writer that stopped writing for good -- a one-shot import, a log
name from before the identity scheme, a role log nobody writes -- has no host
to declare from, so the gate could never pass in the real installation.

Rule, written before the code:
  * the owner names retired writers; `enable` records them in the owner-signed
    `hub.enable` (`retired_writers`), so admission takes them from the ledger,
    never from a file;
  * a retired writer is not asked for readiness;
  * its history in the manifest stays valid; any event it writes after the
    cutover is refused (`retired_writer`), on the network path and when it
    arrives by git;
  * the owner and the grantor are never retired.
"""
from __future__ import annotations

import pytest

from ledger import hub as hubmod
from ledger.log import EventLog, read_events

from tests.test_ledger_hub import GRANTOR, HUB_ID, OWNER, TEST_POLICY, env  # noqa: F401  (fixture)


def _space(env, name="retired-space"):
    space = env.tmp / name
    (space / ".datacore" / "events").mkdir(parents=True)
    for actor in ("own", "wa"):
        EventLog(space, actor, sign=False).append("item.create", {"id": f"{actor}-old", "title": "before"})
    return space


def _enable(env, space, **kw):
    return hubmod.enable(space, hub_id=HUB_ID, grantor=GRANTOR, owner=OWNER, keys_dir=env.keys_dir,
                         registry_path=env.registry, policy=TEST_POLICY, **kw)


def test_a_retired_writer_is_not_asked_for_readiness_and_the_record_names_it(env):
    space = _space(env)
    hubmod.declare_ready(space, "own", keys_dir=env.keys_dir, registry_path=env.registry)
    with pytest.raises(hubmod.HubRefused) as exc:
        _enable(env, space, retired_writers={})
    assert exc.value.code == "not_ready" and "wa" in exc.value.message
    cfg = _enable(env, space, retired_writers={"wa": "dormant since the import"})
    rec = hubmod.owner_records(space, cfg, env.registry)[0]
    assert rec.type == "hub.enable" and rec.payload["retired_writers"] == ["wa"]
    assert hubmod.authority(space, cfg, env.registry).retired == frozenset({"wa"})


def test_a_retired_writer_keeps_its_history_and_cannot_write_after_the_cutover(env):
    space = _space(env)
    hubmod.declare_ready(space, "own", keys_dir=env.keys_dir, registry_path=env.registry)
    _enable(env, space, retired_writers={"wa": "dormant"})
    # A signed post-cutover event in the retired writer's own log, arriving by git.
    EventLog(space, "wa", keys_dir=env.keys_dir, registry_path=env.registry, sign=True).append(
        "item.create", {"id": "wa-new", "title": "after the cutover"})
    state = hubmod.authoritative_state(space, registry_path=env.registry, policy=TEST_POLICY,
                                       now_ms=env.clock())
    assert "wa-old" in state.items and "own-old" in state.items      # history stays valid
    assert "wa-new" not in state.items                                # never admitted


def test_the_network_path_refuses_a_retired_writers_append(env):
    from ledger.hub_admission import Refusal, check_event
    space = _space(env)
    hubmod.declare_ready(space, "own", keys_dir=env.keys_dir, registry_path=env.registry)
    cfg = _enable(env, space, retired_writers={"wa": "dormant"})
    auth = hubmod.authority(space, cfg, env.registry)
    ev = EventLog(space, "wa", keys_dir=env.keys_dir, registry_path=env.registry, sign=True).append(
        "item.create", {"id": "wa-new", "title": "after"})
    prior = [e for e in read_events(space) if e.actor == "wa" and e.seq == ev.seq - 1][0]
    with pytest.raises(Refusal) as exc:
        check_event(ev, "wa", prior, auth, now_ms=env.clock())
    assert exc.value.code == "retired_writer"


@pytest.mark.parametrize("who", [OWNER, GRANTOR])
def test_the_owner_and_the_grantor_are_never_retired(env, who):
    space = _space(env, f"retired-{who}")
    with pytest.raises(ValueError):
        _enable(env, space, retired_writers={who: "no"})
