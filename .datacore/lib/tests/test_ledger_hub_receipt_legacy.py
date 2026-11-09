"""A receipt in a space activated over existing history (activation, 2026-10-24).

Every real space has unsigned history from before the cutover; the owner's
activation record lists it as legacy (the cutover manifest), and admission
admits it. The receipt check judged the whole hub log and the writer's whole log
as if every line had to be signed, so the first live receipt in a real space
read "failed" over history the record says is valid. Inside the legacy prefix an
unsigned event is valid history: it is still compared byte for byte, never
signature-checked.
"""
from __future__ import annotations

from ledger import hub as hubmod
from ledger.log import EventLog

from tests.test_ledger_hub import GRANTOR, HUB_ID, OWNER, TEST_POLICY, env  # noqa: F401  (fixture)


def test_a_receipt_over_legacy_unsigned_history_is_confirmed(env):
    space = env.tmp / "legacy-space"
    (space / ".datacore" / "events").mkdir(parents=True)
    for actor in ("own", GRANTOR):                       # history from before the cutover, unsigned
        EventLog(space, actor, sign=False).append("item.create", {"id": f"{actor}-old", "title": "before"})
    for actor in ("own", GRANTOR):
        hubmod.declare_ready(space, actor, keys_dir=env.keys_dir, registry_path=env.registry)
    hubmod.enable(space, hub_id=HUB_ID, grantor=GRANTOR, owner=OWNER, keys_dir=env.keys_dir,
                  registry_path=env.registry, policy=TEST_POLICY)
    hub = env.hub(space=space)
    writer = hubmod.HubWriter(space, "own", hubmod.LocalTransport(hub), keys_dir=env.keys_dir,
                              registry_path=env.registry, state_dir=env.tmp / "w-own", clock=env.clock,
                              policy=TEST_POLICY)
    ev, _ = writer.append("item.create", {"id": "new", "title": "after the cutover"})
    hub.seal()
    report = writer.receipt_check(space, hub_actor=GRANTOR, required=[ev.hash])
    assert report["status"] == "confirmed", report


def test_an_edited_legacy_event_still_fails_the_receipt(env):
    space = env.tmp / "legacy-edit"
    (space / ".datacore" / "events").mkdir(parents=True)
    EventLog(space, "own", sign=False).append("item.create", {"id": "own-old", "title": "before"})
    hubmod.declare_ready(space, "own", keys_dir=env.keys_dir, registry_path=env.registry)
    hubmod.enable(space, hub_id=HUB_ID, grantor=GRANTOR, owner=OWNER, keys_dir=env.keys_dir,
                  registry_path=env.registry, policy=TEST_POLICY)
    hub = env.hub(space=space)
    writer = hubmod.HubWriter(space, "own", hubmod.LocalTransport(hub), keys_dir=env.keys_dir,
                              registry_path=env.registry, state_dir=env.tmp / "w-own2", clock=env.clock,
                              policy=TEST_POLICY)
    writer.append("item.create", {"id": "new", "title": "after"})
    hub.seal()
    sealed = env.tmp / "sealed-copy"
    import shutil
    shutil.copytree(space, sealed)
    log = sealed / ".datacore" / "events" / "own.jsonl"
    lines = log.read_text().splitlines()
    lines[0] = lines[0].replace("before", "BEFORE")
    log.write_text("\n".join(lines) + "\n")
    assert writer.receipt_check(sealed, hub_actor=GRANTOR)["status"] == "failed"
