"""LED-12: If a hub space has lost its hub settings, Datacore refuses to read
or write it rather than reading it without the hub's checks; a space that
never moved to the hub is never blocked.

Kind: deterministic (temporary spaces; never the live installation).

Seeded failure: the real round-6 final and round-7 audit findings. On
7393dc67 a hub space copied without its config was read as a plain space, so
an unsigned claim by a writer the task was never given to took the task
(final audit F2). On the round-7 audited head (0758f855) the fix still read a
missing key registry as "no hub" and plain-folded the same claim (R3), and a
writer kept writing unsigned events (R5). On e5566139 an ordinary task titled
"hub.enable" blocked writes in a plain space (re-check N5).

"Never moved to the hub" means the space's logs hold no activation record and
no hub-stamped event. By the owner's decision of 2026-10-23 (round 8, item 2)
a planted activation record does block a space until the owner removes it;
that is the accepted cost of the strict rule and is not tested as a defect.

Promise, as evals: a hub space copied with its logs but without its config
-- also with the key registry missing, with the activation record
JSON-escaped, split by a carriage return, or behind a damaged line -- is
refused by every reader ("unavailable"), unsigned writes to it are refused,
and the generic correction reader excuses nothing in it. A plain space with
ordinary content that merely mentions hubs reads and writes normally.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import _replica, env  # noqa: F401
from ledger import hub as H
from ledger.events import to_line
from ledger.log import EventLog, read_events

VARIANTS = ["config-lost", "config-and-registry-lost", "escaped-record", "carriage-return", "damaged-line-first"]


def _hub_copy_without_config(env, variant):
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t", "assignee": "wa"})
    wb = env.writer("wb", None)
    fake = replace(T._signed_into(env, wb, "item.claim", {"id": "t"}), sig="")
    (wb.space_dir / ".datacore/events/wb.jsonl").write_text(to_line(fake) + "\n")
    T.git_pull(wb.space_dir, env.hub_dir)
    assert h._state()[1].items["t"].owner is None
    copy = _replica(env, env.hub_dir)
    (copy / H.CONFIG).unlink()
    owner_log = copy / ".datacore/events" / f"{T.OWNER}.jsonl"
    text = owner_log.read_text()
    if variant == "escaped-record":
        owner_log.write_text(text.replace('"hub.enable"', '"hub.\\u0065nable"'))
    elif variant == "carriage-return":
        owner_log.write_text(text.replace('"type":', '\r"type":', 1).replace('"type": ', '\r"type": ', 1))
    elif variant == "damaged-line-first":
        owner_log.write_text("{not json\n" + text)
    registry = env.tmp / "never-restored-registry.yaml" if variant == "config-and-registry-lost" else env.registry
    return copy, registry


@pytest.mark.parametrize("variant", VARIANTS)
def test_a_hub_space_without_its_settings_is_never_read_around_the_hub(env, variant):
    copy, registry = _hub_copy_without_config(env, variant)
    try:
        state = H.authoritative_state(copy, registry_path=registry, policy=T.TEST_POLICY)
    except H.HubUnavailable:
        state = None
    assert state is None, (f"[{variant}] expected the space to be refused; it was read without the hub's checks "
                           f"and the task's owner is {state.items['t'].owner!r}")
    with pytest.raises(H.HubUnavailable):
        H.strict_problems(copy, registry_path=registry)


@pytest.mark.parametrize("variant", VARIANTS)
def test_a_hub_space_without_its_settings_takes_no_unsigned_write(env, variant):
    copy, registry = _hub_copy_without_config(env, variant)
    before = sorted(e.hash for e in read_events(copy))
    try:
        e = EventLog(copy, "wb", sign=False, registry_path=registry).append("item.claim", {"id": "t"})
    except Exception as exc:                          # refused: the right answer
        assert "hub" in f"{type(exc).__name__} {exc}".lower(), f"[{variant}] refused for another reason: {exc!r}"
        return
    pytest.fail(f"[{variant}] an unsigned write was accepted into a hub space that lost its settings "
                f"(sig={e.sig!r}, hub={e.hub!r}); before: {len(before)} events")


def test_the_correction_reader_excuses_nothing_without_the_settings(env, monkeypatch):
    from ledger import voids
    copy, registry = _hub_copy_without_config(env, "config-lost")
    try:
        got = voids.for_events_dir(copy / ".datacore/events", registry_path=registry)
    except H.HubUnavailable:
        return
    assert not got.effective, f"the correction reader excused events in a hub space without settings: {got.effective}"


def test_a_space_that_never_moved_to_the_hub_is_never_blocked(env):
    plain = env.tmp / "plain-space"
    log = EventLog(plain, "wa", sign=False)
    log.append("item.create", {"id": "p1", "title": "hub.enable"})
    log.append("item.create", {"id": "p2", "title": "move the team \"hub\" notes \\ later", "hub_note": ""})
    log.append("item.update", {"id": "p1", "title": "about the hub: {\"hub\": \"x\"}"})
    state = H.authoritative_state(plain, registry_path=env.registry)
    assert set(state.items) >= {"p1", "p2"}, "a plain space that only mentions hubs could not be read"
    e = EventLog(plain, "wb", sign=False).append("item.claim", {"id": "p2"})
    assert e.hash, "a plain space that only mentions hubs refused an ordinary write"
    assert H.authoritative_state(plain, registry_path=env.registry).items["p2"].owner == "wb"
