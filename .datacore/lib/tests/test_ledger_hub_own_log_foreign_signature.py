"""A foreign-signed event in the grantor's OWN log holds its place, with no effect.

On 2026-11-05 01:50 a test run on the box wrote one `item.create` into
2-datacore's winston.jsonl (seq 10346), chained and hashed correctly but signed
with a key the test had generated for itself. Admission ended Winston's log
there, so the 116 correctly signed events after it -- every delegation Winston
filed for a day and a half -- counted for nothing anywhere, and no sanctioned
repair existed (voids retired 2026-11-01; a grantor's own log is admitted before
any `ledger.reject` is read; the cutover extension stops at a forged event).

Owner decision 2026-11-06: in a grantor's own log, an event that holds its
place in the chain (next seq, previous hash, its own hash) but whose signature
is not its writer's is HELD: it stays in the chain, has no effect on state, and
is named in `Admission.held` and in `strict_problems`, so the alarm survives
without blocking the work. Unsigned known types still end the log
(test_ledger_hub_own_log_proposal), and so does a foreign signature in a
writer's log.
"""
from __future__ import annotations

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import env  # noqa: F401
from ledger import hub as H
from ledger.events import body_dict, canonical_bytes, compute_hash, from_line, to_line, Event
from ledger.hub_admission import signing_domain
from ledger.log import read_events


def _admission(space):
    events = list(read_events(space))
    auth = H.authority(space, H.require_config_or_no_hub(space, events, None), None)
    return H.admit(events, auth, now_ms=H._wall_ms())


def _foreign_signed_into(env, writer, type_, payload, hlc, tmp_path):
    """Chained, hashed and domain-bound, but signed with a key the writer never registered."""
    from ledger.keys import ensure_keypair, sign
    lines = writer.log.path.read_text().splitlines()
    tail = from_line(lines[-1])
    cfg = H.load_config(writer.space_dir)
    body = body_dict(tail.seq + 1, hlc, writer.actor, type_, payload, tail.hash, hub=cfg.hub_id,
                     domain=signing_domain(cfg.space_id, cfg.hub_id, writer.log.log_name))
    stray = tmp_path / "stray-keys"          # a test run's own freshly generated key
    ensure_keypair(writer.actor, keys_dir=stray, registry_path=tmp_path / "stray-principals.yaml")
    e = Event(**body, hash=compute_hash(body), sig=sign(writer.actor, canonical_bytes(body), keys_dir=stray))
    with open(writer.log.path, "a", encoding="utf-8") as f:
        f.write(to_line(e) + "\n")
    return e


def test_a_foreign_signed_event_in_the_grantors_own_log_is_held_not_the_end(env, monkeypatch, tmp_path):
    import ledger.keys as keys
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)
    h = env.hub(eligibility=None)
    env.writer("own", H.LocalTransport(h)).append("item.create", {"id": "before", "assignee": "own"})
    ms = T._real_ms()
    bad = _foreign_signed_into(env, h, "item.create", {"id": "forged", "assignee": "own"},
                               f"{ms:013d}.0000.{h.actor}", tmp_path)
    after = T._signed_into(env, h, "item.create", {"id": "after", "assignee": "own"},
                           hlc=f"{ms + 1:013d}.0000.{h.actor}")
    res = _admission(h.space_dir)
    assert h.actor not in res.refused, res.refused
    admitted = {e.hash for e in res.events}
    assert after.hash in admitted and bad.hash in res.held_hashes   # in the chain, marked held
    assert h.actor in res.held and "bad_signature" in res.held[h.actor][0], res.held
    state = H.authoritative_state(h.space_dir)
    assert "after" in state.items and "forged" not in state.items
    assert any("held" in p and "bad_signature" in p for p in H.strict_problems(h.space_dir)), \
        H.strict_problems(h.space_dir)


def test_a_foreign_signed_event_out_of_place_still_ends_the_grantors_log(env, monkeypatch, tmp_path):
    import ledger.keys as keys
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)
    h = env.hub(eligibility=None)
    env.writer("own", H.LocalTransport(h)).append("item.create", {"id": "before", "assignee": "own"})
    bad = _foreign_signed_into(env, h, "item.create", {"id": "forged"}, f"{T._real_ms():013d}.0000.{h.actor}",
                               tmp_path)
    lines = h.log.path.read_text().splitlines()           # break its link to the previous event
    lines[-1] = lines[-1].replace(bad.prev, "0" * len(bad.prev))
    h.log.path.write_text("\n".join(lines) + "\n")
    res = _admission(h.space_dir)
    assert h.actor in res.refused, res.refused


def test_a_foreign_signature_in_a_writers_log_still_ends_it(env, monkeypatch, tmp_path):
    import ledger.keys as keys
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)
    h = env.hub(eligibility=None)
    wa = env.writer("wa", H.LocalTransport(h))
    wa.append("item.create", {"id": "t", "assignee": "wa"})
    _foreign_signed_into(env, wa, "item.create", {"id": "forged"}, f"{T._real_ms():013d}.0000.wa", tmp_path)
    res = _admission(wa.space_dir)
    assert "wa" in res.refused and "bad_signature" in res.refused["wa"], res.refused


def test_admitting_the_admitted_events_again_keeps_the_held_event_harmless(env, monkeypatch, tmp_path):
    """The claim job admits, then folds what it admitted (which admits again).
    The second pass must still see the chain whole, and still give the held
    event no effect."""
    import ledger.keys as keys
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)
    h = env.hub(eligibility=None)
    env.writer("own", H.LocalTransport(h)).append("item.create", {"id": "before", "assignee": "own"})
    ms = T._real_ms()
    _foreign_signed_into(env, h, "item.create", {"id": "forged", "assignee": "own"},
                         f"{ms:013d}.0000.{h.actor}", tmp_path)
    T._signed_into(env, h, "item.create", {"id": "after", "assignee": "own"}, hlc=f"{ms + 1:013d}.0000.{h.actor}")
    events, kw = H.authoritative_events(h.space_dir)
    state = H.authoritative_state(h.space_dir, events)
    assert "after" in state.items and "forged" not in state.items
    eff, _ = H.effective_authoritative_events(h.space_dir)
    assert not any((e.payload or {}).get("id") == "forged" for e in eff)
