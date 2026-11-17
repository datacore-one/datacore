"""One refused event never hides the events after it (report, never halt).

Owner decision 2026-11-17 (simplification spec, migration step 1; reliability
rules 1 and 2): an integrity finding is reported and never stops a read or a
write, and one bad record never hides others.

Until now admission ended a log at its first refused event: every event the
writer wrote after it counted for nothing, on every machine, and the hub
accepted nothing more onto that log ("chain_refused"). Two outages had that
shape (2026-10-28, 2026-11-05) and each got its own exception for the
grantor's log only.

The rule now, for every log: each event is judged on its own. A refused event
(unsigned, wrong signature, wrong hash, not the writer's to write) has no
effect and is named; the events after it are judged as usual. A break in the
chain (a missing or re-linked line) is named and refuses nothing by itself.
"""
from __future__ import annotations

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import env  # noqa: F401
from tests.test_ledger_hub_own_log_foreign_signature import _admission, _foreign_signed_into
from ledger import hub as H


def _setup(env, monkeypatch):
    import ledger.keys as keys
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)
    h = env.hub(eligibility=None)
    wa = env.writer("wa", H.LocalTransport(h))
    wa.append("item.create", {"id": "before", "assignee": "wa"})
    return h, wa


def test_events_after_a_refused_event_in_a_writers_log_still_count(env, monkeypatch, tmp_path):
    h, wa = _setup(env, monkeypatch)
    ms = T._real_ms()
    bad = _foreign_signed_into(env, wa, "item.create", {"id": "forged"}, f"{ms:013d}.0000.wa", tmp_path)
    after = T._signed_into(env, wa, "item.create", {"id": "after", "assignee": "wa"},
                           hlc=f"{ms + 1:013d}.0000.wa")
    res = _admission(wa.space_dir)
    admitted = {e.hash for e in res.events}
    assert after.hash in admitted, f"the event after the refused one is hidden: {res.refused}"
    assert bad.hash not in admitted, "the refused event itself must have no effect"
    assert "wa" in res.refused and "bad_signature" in res.refused["wa"], res.refused   # still named
    state = H.authoritative_state(wa.space_dir)
    assert {"before", "after"} <= set(state.items) and "forged" not in state.items
    assert any("bad_signature" in p for p in H.strict_problems(wa.space_dir))


def test_admitting_the_admitted_events_again_gives_the_same_state(env, monkeypatch, tmp_path):
    """The claim job admits, then folds what it admitted (which admits again):
    the refused event is gone from that list, and the event after it must not
    be lost for following a gap."""
    h, wa = _setup(env, monkeypatch)
    ms = T._real_ms()
    _foreign_signed_into(env, wa, "item.create", {"id": "forged"}, f"{ms:013d}.0000.wa", tmp_path)
    T._signed_into(env, wa, "item.create", {"id": "after", "assignee": "wa"}, hlc=f"{ms + 1:013d}.0000.wa")
    events, _kw = H.authoritative_events(wa.space_dir)
    state = H.authoritative_state(wa.space_dir, events)
    assert "after" in state.items and "forged" not in state.items


def test_every_refused_event_is_counted_not_only_the_first(env, monkeypatch, tmp_path):
    h, wa = _setup(env, monkeypatch)
    ms = T._real_ms()
    _foreign_signed_into(env, wa, "item.create", {"id": "f1"}, f"{ms:013d}.0000.wa", tmp_path)
    T._signed_into(env, wa, "item.create", {"id": "mid", "assignee": "wa"}, hlc=f"{ms + 1:013d}.0000.wa")
    _foreign_signed_into(env, wa, "item.create", {"id": "f2"}, f"{ms + 2:013d}.0000.wa", tmp_path)
    T._signed_into(env, wa, "item.create", {"id": "last", "assignee": "wa"}, hlc=f"{ms + 3:013d}.0000.wa")
    res = _admission(wa.space_dir)
    assert "2 event(s)" in res.refused["wa"], res.refused
    state = H.authoritative_state(wa.space_dir)
    assert {"before", "mid", "last"} <= set(state.items) and not ({"f1", "f2"} & set(state.items))


def test_the_hub_still_accepts_a_writers_events_after_a_refused_one(env, monkeypatch, tmp_path):
    """A write is never stopped by an older finding in the same log: the hub's
    copy of the log holds a refused event (it arrived by git), and the writer's
    next event is still accepted onto it."""
    import shutil
    h, wa = _setup(env, monkeypatch)
    _foreign_signed_into(env, wa, "item.create", {"id": "forged"}, f"{T._real_ms():013d}.0000.wa", tmp_path)
    shutil.copy(wa.log.path, h.log.path.parent / wa.log.path.name)      # as a git import leaves it
    wa.append("item.create", {"id": "later", "assignee": "wa"})        # raised chain_refused before
    state = H.authoritative_state(h.space_dir)
    assert "later" in state.items and "forged" not in state.items


def test_a_break_in_the_chain_is_named_and_hides_nothing(env, monkeypatch, tmp_path):
    """A line missing from the middle of a log: the events on both sides count."""
    h, wa = _setup(env, monkeypatch)
    wa.append("item.create", {"id": "gone", "assignee": "wa"})
    wa.append("item.create", {"id": "kept", "assignee": "wa"})
    lines = wa.log.path.read_text().splitlines()
    wa.log.path.write_text("\n".join(lines[:-2] + lines[-1:]) + "\n")   # drop the middle line
    res = _admission(wa.space_dir)
    assert "wa" in res.refused and "bad_chain" in res.refused["wa"], res.refused        # named
    state = H.authoritative_state(wa.space_dir)
    assert {"before", "kept"} <= set(state.items), "the event after the gap is hidden"


def test_the_grantors_log_survives_a_refused_event_out_of_place(env, monkeypatch, tmp_path):
    import ledger.keys as keys
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)
    h = env.hub(eligibility=None)
    env.writer("own", H.LocalTransport(h)).append("item.create", {"id": "before", "assignee": "own"})
    ms = T._real_ms()
    bad = _foreign_signed_into(env, h, "item.create", {"id": "forged"}, f"{ms:013d}.0000.{h.actor}", tmp_path)
    lines = h.log.path.read_text().splitlines()           # break its link to the previous event
    lines[-1] = lines[-1].replace(bad.prev, "0" * len(bad.prev))
    h.log.path.write_text("\n".join(lines) + "\n")
    T._signed_into(env, h, "item.create", {"id": "after", "assignee": "own"}, hlc=f"{ms + 1:013d}.0000.{h.actor}")
    state = H.authoritative_state(h.space_dir)
    assert "after" in state.items and "forged" not in state.items
    assert h.actor in _admission(h.space_dir).refused


def test_a_space_with_a_refused_event_still_seals_and_the_seal_verifies(env, monkeypatch, tmp_path):
    """A seal covers each log up to its first break, so sealing never stops and
    a hole filled later changes nothing under a watermark."""
    import shutil
    from ledger.log import read_events
    h, wa = _setup(env, monkeypatch)
    ms = T._real_ms()
    bad = _foreign_signed_into(env, wa, "item.create", {"id": "forged"}, f"{ms:013d}.0000.wa", tmp_path)
    after = T._signed_into(env, wa, "item.create", {"id": "after", "assignee": "wa"},
                           hlc=f"{ms + 1:013d}.0000.wa")
    shutil.copy(wa.log.path, h.log.path.parent / wa.log.path.name)
    h.seal()                                                           # raised unsealable before
    seal = [e for e in read_events(h.space_dir) if e.type == "ledger.seal"][-1]
    assert seal.payload["watermarks"].get("wa", -1) == bad.seq - 1, seal.payload["watermarks"]
    ok, detail = H.seal_status(h.space_dir)
    assert ok is True, detail
    assert "after" in H.authoritative_state(h.space_dir).items
    assert after.seq > seal.payload["watermarks"]["wa"]


def test_of_two_different_events_at_one_seq_the_first_stands(env, monkeypatch, tmp_path):
    from ledger.events import from_line
    h, wa = _setup(env, monkeypatch)
    lines = wa.log.path.read_text().splitlines()
    first = T._signed_into(env, wa, "item.create", {"id": "first", "assignee": "wa"})
    wa.log.path.write_text("\n".join(lines) + "\n")                    # rewind, then write a rival at the same seq
    rival = T._signed_into(env, wa, "item.create", {"id": "rival", "assignee": "wa"},
                           hlc=f"{T._real_ms() + 5:013d}.0000.wa")
    assert rival.seq == first.seq and rival.hash != first.hash
    from ledger.events import to_line
    wa.log.path.write_text("\n".join(lines + [to_line(first), to_line(rival)]) + "\n")
    res = _admission(wa.space_dir)
    assert "fork" in res.refused["wa"], res.refused
    state = H.authoritative_state(wa.space_dir)
    assert "first" in state.items and "rival" not in state.items
