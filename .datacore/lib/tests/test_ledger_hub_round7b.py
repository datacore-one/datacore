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


def _plain_space_with_older_history(env, name="unrelated-plain-space"):
    """A plain space whose history predates the hub fixture's activation."""
    from ledger.events import Event, body_dict, compute_hash
    plain = env.tmp / name
    (plain / ".datacore" / "events").mkdir(parents=True)
    body = body_dict(0, "1600000000000.0000.wa", "wa", "item.create", {"id": "local", "title": "local"}, "GENESIS")
    (plain / ".datacore/events/wa.jsonl").write_text(to_line(Event(**body, hash=compute_hash(body), sig="")) + "\n")
    return plain


def test_r7b_an_activation_record_copied_into_a_plain_space_is_read_fail_closed(env):
    """The availability cost of the coarse rule, stated as a test: a record
    that does not belong refuses reads (with the reason) until removed."""
    foreign = next(e for e in read_events(env.hub_dir) if e.type == "hub.enable")
    plain = _plain_space_with_older_history(env)
    assert H.authoritative_state(plain, registry_path=env.registry).items["local"].status == "created"
    (plain / ".datacore/events" / f"{foreign.actor}.jsonl").write_text(to_line(foreign) + "\n")
    with pytest.raises(H.HubUnavailable):
        H.authoritative_state(plain, registry_path=env.registry)
    (plain / ".datacore/events" / f"{foreign.actor}.jsonl").unlink()
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



# ── the round-7 re-check (Codex, targeted): N1-N5 ───────────────────────────

def _put(space, event):
    path = space / ".datacore/events" / f"{getattr(event, 'log', None) or event.actor}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(to_line(event) + "\n")


def _altered(event, **changes):
    from dataclasses import replace
    from ledger.events import compute_hash, event_body
    event = replace(event, **changes)
    return replace(event, hash=compute_hash(event_body(event)))


@pytest.mark.parametrize("missing_registry", [False, True])
def test_r7b_a_genuine_activation_of_an_empty_space_still_fails_closed(env, missing_registry):
    activation, = read_events(env.hub_dir)
    assert activation.type == "hub.enable" and activation.payload["manifest"] == {}
    source = env.tmp / "unsigned-source"
    _put(env.hub_dir, EventLog(source, "own", sign=False).append("item.create", {"id": "t", "assignee": "wa"}))
    _put(env.hub_dir, EventLog(source, "wb", sign=False).append("item.claim", {"id": "t"}))
    (env.hub_dir / H.CONFIG).unlink()
    registry = env.tmp / "missing.yaml" if missing_registry else env.registry
    with pytest.raises(H.HubUnavailable):
        H.authoritative_state(env.hub_dir, registry_path=registry, policy=T.TEST_POLICY)
    with pytest.raises(H.HubUnavailable):
        H.strict_problems(env.hub_dir, registry_path=registry)


def test_r7b_a_genuine_empty_activation_keeps_the_void_reader_closed(env, monkeypatch):
    from ledger import voids
    T._with_principals(env, monkeypatch)
    stage = env.tmp / "legacy-source"
    rotation = EventLog(stage, "wa", sign=False).append("key.rotate", {"actor": "wa"})
    _put(env.hub_dir, rotation)
    _put(env.hub_dir, EventLog(stage, "own", sign=False).append("ledger.void", _void(rotation)))
    (env.hub_dir / H.CONFIG).unlink()
    assert not voids.for_events_dir(env.hub_dir / ".datacore/events", registry_path=env.registry).applies("wa", rotation)


@pytest.mark.parametrize("variant", ["stripped-signature", "older-imported-event", "partial-registry",
                                     "whitespace-encoding", "escaped-key", "malformed-manifest"])
def test_r7b_no_recovery_state_reopens_the_plain_fold(env, variant):
    """Round-7 re-check D1-D3: each state that let a finer rule discard a
    genuine activation is read fail-closed by the coarse rule."""
    from ledger import voids
    from ledger.events import Event, body_dict, compute_hash
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t", "assignee": "wa"})
    replica = _replica(env, env.hub_dir)
    (replica / H.CONFIG).unlink()
    owner_log = replica / ".datacore/events" / f"{T.OWNER}.jsonl"
    registry = env.registry
    text = owner_log.read_text()
    if variant == "stripped-signature":
        lines = text.splitlines()
        lines[0] = lines[0].replace('"sig":"', '"sig":"x', 1).replace('"sig":"x', '"sig":"', 1)
        import json as _j
        obj = _j.loads(lines[0]); obj["sig"] = ""; lines[0] = _j.dumps(obj, sort_keys=True, separators=(",", ":"))
        owner_log.write_text("\n".join(lines) + "\n")
    elif variant == "older-imported-event":
        body = body_dict(0, "1600000000000.0000.zz", "zz", "item.create", {"id": "old"}, "GENESIS")
        (replica / ".datacore/events/zz.jsonl").write_text(to_line(Event(**body, hash=compute_hash(body), sig="")) + "\n")
    elif variant == "partial-registry":
        registry = env.tmp / "partial-registry.yaml"
        registry.write_text("actors: {}\n")
    elif variant == "whitespace-encoding":
        owner_log.write_text(text.replace('"type":"hub.enable"', '"type": "hub.\\u0065nable"'))
    elif variant == "escaped-key":
        owner_log.write_text(text.replace('"type":"hub.enable"', '"\\u0074ype":"hub.enable"'))
    else:
        import json as _j
        lines = text.splitlines()
        obj = _j.loads(lines[0]); obj["payload"]["manifest"] = {"wa": {"seq": 0, "head": []}}
        lines[0] = _j.dumps(obj, sort_keys=True, separators=(",", ":"))
        owner_log.write_text("\n".join(lines) + "\n")
    with pytest.raises(H.HubUnavailable):
        H.authoritative_state(replica, registry_path=registry, policy=T.TEST_POLICY)
    assert H.records_a_hub(replica, registry)
    assert voids._hub_space_voids(replica / ".datacore" / "events", registry_path=registry) is not None


def test_r7b_a_writer_opened_before_the_config_was_lost_stops_writing(env):
    from ledger.log import HubSigningRequired
    h = env.hub()
    log = EventLog(env.hub_dir, "wa", sign=True, keys_dir=env.keys_dir, registry_path=env.registry)
    (env.hub_dir / H.CONFIG).unlink()
    with pytest.raises(HubSigningRequired):
        log.append("item.create", {"id": "t"})


def test_r7b_a_held_telemetry_control_record_is_refused_before_appending(env, monkeypatch):
    from ledger.log import NotReversible
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    e, _ = wa.append("metric.attest", {"metric": "job.verify", "job": "j", "ok": True, "failures": []})
    target = next(x for x in h._state()[0] if x.hash == e.hash)
    assert target.log.endswith(".telemetry")
    with pytest.raises(NotReversible):
        EventLog(env.hub_dir, T.OWNER, sign=True, keys_dir=env.keys_dir, registry_path=env.registry).append(
            "ledger.void", {"log": target.log, "seq": target.seq, "hash": target.hash, "reason": "review"})


def test_r7b_a_json_escape_does_not_open_the_writer_gate(env):
    from ledger.log import HubSigningRequired
    env.hub()
    replica = _replica(env, env.hub_dir)
    (replica / H.CONFIG).unlink()
    path = replica / ".datacore/events" / f"{T.OWNER}.jsonl"
    path.write_text(path.read_text().replace('"hub.enable"', '"hub.\\u0065nable"'))
    with pytest.raises(HubSigningRequired):
        EventLog(replica, "wb", sign=False).append("item.claim", {"id": "t"})


def test_r7b_a_signed_writer_also_refuses_once_the_config_is_lost(env):
    from ledger.log import HubSigningRequired
    env.hub()
    replica = _replica(env, env.hub_dir)
    (replica / H.CONFIG).unlink()
    with pytest.raises(HubSigningRequired):
        EventLog(replica, "wa", sign=True, keys_dir=env.keys_dir, registry_path=env.registry).append(
            "item.create", {"id": "t"})


@pytest.mark.parametrize("chain", ["wa-run-2026-10-23", "wa-telemetry"])
def test_r7b_a_held_control_record_in_another_log_is_still_refused(env, chain):
    from ledger.log import NotReversible
    target = EventLog(env.hub_dir, "wa", log_name=chain, sign=True, keys_dir=env.keys_dir,
                      registry_path=env.registry).append("key.rotate", {"actor": "wa", "reason": "review"})
    with pytest.raises(NotReversible):
        EventLog(env.hub_dir, T.OWNER, sign=True, keys_dir=env.keys_dir,
                 registry_path=env.registry).append("ledger.void", dict(_void(target), log=chain))


@pytest.mark.parametrize("cli", [False, True])
def test_r7b_config_loss_does_not_reopen_a_local_control_reversal(env, monkeypatch, cli):
    from ledger.log import HubSigningRequired
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    rotation, _ = wa.append("key.rotate", {"actor": "wa", "reason": "review"})
    T.git_pull(env.hub_dir, owner.space_dir)
    (owner.space_dir / H.CONFIG).unlink()

    def log(space, actor):
        return EventLog(space, actor, sign=True, keys_dir=env.keys_dir, registry_path=env.registry)
    with pytest.raises((HubSigningRequired, SystemExit)):
        if cli:
            import argparse
            import ledger_cli
            monkeypatch.setattr(ledger_cli, "EventLog", log)
            ledger_cli.cmd_void(argparse.Namespace(space=str(owner.space_dir), actor=T.OWNER,
                                                   log="wa", seq=rotation.seq, reason="review"))
        else:
            log(owner.space_dir, T.OWNER).append("ledger.void", _void(rotation))
    assert not any(e.type == "ledger.void" and e.payload.get("hash") == rotation.hash
                   for e in read_events(owner.space_dir))


def test_r7b_ordinary_content_never_blocks_writing_in_a_plain_space(env):
    plain = _plain_space_with_older_history(env, "plain-title")
    log = EventLog(plain, "wa", sign=False)
    log.append("item.update", {"id": "local", "title": "hub.enable"})
    log.append("item.update", {"id": "local", "title": "GitHub hub sync \u263a"})
    log.append("item.update", {"id": "local", "title": "normal update"})
    assert H.authoritative_state(plain, registry_path=env.registry).items["local"].payload["title"] == "normal update"


@pytest.mark.parametrize("marker", ["unsigned_activation", "foreign_activation"])
def test_r7b_a_hub_record_in_a_plain_space_stops_writes_too(env, marker):
    from ledger.log import HubSigningRequired
    plain = _plain_space_with_older_history(env, f"plain-{marker}")
    log = EventLog(plain, "wa", sign=False)
    if marker == "unsigned_activation":
        log.append("hub.enable", {"space": "fake", "hub": "fake"})
    else:
        _put(plain, next(e for e in read_events(env.hub_dir) if e.type == "hub.enable"))
    with pytest.raises(HubSigningRequired):
        log.append("item.update", {"id": "local", "title": "normal update"})
