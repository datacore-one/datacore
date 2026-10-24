"""Round 5 of the ledger hub: the round-4 independent audit's reproductions,
turned into regression tests (2026-10-23 owner decisions, amendments 2, 5, 7
and the reversal-after-seal decision).

Each test states the behaviour the owner accepted; each was written from the
auditor's reproduction (test_r4_probes.py) and seen failing before the fix.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests import test_ledger_hub as T
from ledger import hub as H
from ledger.events import compute_hash
from ledger.log import read_events


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "runtime"))
    e = T.Env(tmp_path, monkeypatch)
    yield e
    e.close()


def _setup(env):
    h = env.hub(eligibility=None)
    t = H.LocalTransport(h)
    own, wa = env.writer("own", t), env.writer("wa", t)
    own.append("item.create", {"id": "t", "assignee": "wa"})
    grant = wa.request_lease("t")["lease"]["payload"]
    return h, own, wa, grant


# ── amendment 2: an imported result is admitted when the hub first judges it,
# and that judgment must be durable evidence, not a watermark anything can move.

def test_r5_a_later_unrelated_append_does_not_judge_an_imported_late_result(env):
    h, own, wa, g = _setup(env)
    env.clock.advance(901)
    done = T._signed_into(env, wa, "item.complete", {"id": "t", "attempt": g["attempt"], "task_hash": g["task_hash"]})
    T.git_pull(wa.space_dir, env.hub_dir)
    later = T._signed_into(env, wa, "item.create", {"id": "unrelated"}, hlc=f"{T._real_ms() + 1:013d}.0000.wa")
    assert h.append(wa._submission(later))["status"] == "accepted"
    report = wa.receipt_check(T._published(env, h, [wa, own]), required=[done.hash])
    assert h._state()[1].items["t"].status != "completed"
    assert report["status"] != "confirmed" and not report["ok"], report


def test_r5_losing_the_judged_record_never_makes_late_work_timely(env):
    h, own, wa, g = _setup(env)
    h.seal()                                   # the durable evidence: what this seal covers was judged
    env.clock.advance(901)
    done = T._signed_into(env, wa, "item.complete", {"id": "t", "attempt": g["attempt"], "task_hash": g["task_hash"]})
    T.git_pull(wa.space_dir, env.hub_dir)
    h.close()
    h._judged_path.unlink()
    h = env.hub(eligibility=None)
    report = wa.receipt_check(T._published(env, h, [wa, own]), required=[done.hash])
    assert h._state()[1].items["t"].status != "completed"
    assert report["status"] != "confirmed" and not report["ok"], report


def test_r5_losing_the_judged_record_before_any_seal_judges_nothing_as_timely(env):
    h, own, wa, g = _setup(env)
    env.clock.advance(901)
    done = T._signed_into(env, wa, "item.complete", {"id": "t", "attempt": g["attempt"], "task_hash": g["task_hash"]})
    T.git_pull(wa.space_dir, env.hub_dir)
    h.close()
    h._judged_path.unlink()
    h = env.hub(eligibility=None)
    report = wa.receipt_check(T._published(env, h, [wa, own]), required=[done.hash])
    assert report["status"] != "confirmed" and not report["ok"], report


def test_r5_a_timely_result_is_still_confirmed(env):
    """The control: nothing above may turn an honest, in-time result into a failure."""
    h, own, wa, g = _setup(env)
    done, _ = wa.append("item.complete", {"id": "t", "attempt": g["attempt"], "task_hash": g["task_hash"]})
    other = T._signed_into(env, wa, "item.create", {"id": "unrelated"}, hlc=f"{T._real_ms() + 1:013d}.0000.wa")
    assert h.append(wa._submission(other))["status"] == "accepted"
    env.clock.advance(901)
    report = wa.receipt_check(T._published(env, h, [wa, own]), required=[done.hash])
    assert report["status"] == "confirmed", report


# ── amendment 5: every decision reader takes events through admission ───────

def _tampered_seal(env, monkeypatch):
    import ledger.keys as keys
    from ledger.events import to_line  # noqa: F401
    from ledger.seal import build_seal_payload
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)
    monkeypatch.setenv("DATACORE_SEQUENCER", T.GRANTOR)
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t", "assignee": "wa"})
    path = env.hub_dir / ".datacore/events/own.jsonl"
    row = json.loads(path.read_text())
    row["payload"]["assignee"] = "wb"
    row["hash"] = compute_hash({k: v for k, v in row.items() if k not in ("hash", "sig")})
    row["sig"] = ""
    path.write_text(json.dumps(row) + "\n")
    h.log.append("ledger.seal", build_seal_payload(read_events(env.hub_dir)))   # a raw seal over it
    assert "t" not in h._state()[1].items
    return h


def test_r5_seal_status_judges_a_hub_space_through_admission(env, monkeypatch):
    import ledger_seal
    _tampered_seal(env, monkeypatch)
    assert ledger_seal.cmd_status(env.hub_dir) != 0


def test_r5_seal_emit_never_seals_a_hub_space_around_the_hub(env, monkeypatch):
    import ledger_seal
    _tampered_seal(env, monkeypatch)
    before = (env.hub_dir / f".datacore/events/{T.GRANTOR}.jsonl").read_text()
    monkeypatch.setattr(ledger_seal, "_actor", lambda: T.GRANTOR)
    assert ledger_seal.cmd_emit(env.hub_dir, False) == 2
    assert (env.hub_dir / f".datacore/events/{T.GRANTOR}.jsonl").read_text() == before


def test_r5_the_shape_guard_sees_folds_inside_helpers(tmp_path):
    """The round-4 guard missed `verify_seal(read_events(space))`: a helper that
    folds its argument is a fold. Shapes through a fold helper must be reported."""
    lib = tmp_path / "lib"
    lib.mkdir()
    shapes = {
        "status.py": "from ledger.log import read_events\nfrom ledger.seal import verify_seal\n"
                     "def status(space):\n    return verify_seal(read_events(space))\n",
        "emit.py": "from ledger.log import read_events\nfrom ledger.seal import build_seal_payload\n"
                   "def emit(space):\n    events = read_events(space)\n    return build_seal_payload(events)\n",
        "settled.py": "from ledger.log import read_events\nfrom ledger import seal\n"
                      "def s(space):\n    return seal.settled_events(read_events(space))\n",
        "local.py": "def settled(a, b):\n    return a == b\n"
                    "def f(space):\n    from ledger.log import read_events\n    return settled(read_events(space), 1)\n",
    }
    for name, text in shapes.items():
        (lib / name).write_text(text)
    hits = {name: T._raw_folds(lib / name, lib) for name in shapes}
    assert hits["status.py"] and hits["emit.py"] and hits["settled.py"], hits
    assert hits["local.py"] == [], hits          # a same-named local function is not the ledger helper


# ── amendment 7: recovery after prior service runs the owner-cleared barrier ─

def test_r5_a_restore_with_a_task_stamped_before_enable_hits_the_barrier(env):
    own = env.writer("own", None)
    enable = next(e for e in read_events(env.hub_dir) if e.type == "hub.enable")
    ms = int(enable.hlc.split(".")[0]) - 1000
    T._signed_into(env, own, "item.create", {"id": "t", "assignee": "wa"}, hlc=f"{ms:013d}.0000.own")
    T.git_pull(own.space_dir, env.hub_dir)
    h = env.hub(eligibility=None)
    assert not h.barrier
    stale = env.tmp / "before-grant"
    T.git_pull(env.hub_dir, stale)
    wa = env.writer("wa", H.LocalTransport(h))
    wa.request_lease("t")
    h.close()
    restored = env.hub(space=stale, state="restored", eligibility=None)
    assert restored.barrier.get("state_lost"), restored.barrier
    req = H.sign_request("wa", "lease.request", {"space": T._space_id(stale), "hub": T.HUB_ID, "item": "t"},
                         keys_dir=env.keys_dir, clock=env.clock)
    with pytest.raises(H.HubError):
        restored.request_lease(req)


def test_r5_a_genuine_first_start_needs_no_owner_cleared_recovery(env):
    own = env.writer("own", None)
    T._signed_into(env, own, "item.create", {"id": "t", "assignee": "wa"}, hlc=f"{T._real_ms() + 1000:013d}.0000.own")
    T.git_pull(own.space_dir, env.hub_dir)
    h = env.hub(eligibility=None)
    assert not h.barrier.get("state_lost"), h.barrier
    wa = env.writer("wa", H.LocalTransport(h))
    assert wa.request_lease("t")["status"] == "granted"


def test_r5_a_restart_with_its_private_state_is_not_a_recovery(env):
    h = env.hub(eligibility=None)
    h.close()
    again = env.hub(eligibility=None)
    assert not again.barrier, again.barrier


# ── reversals only inside the correction window; after the seal, a forward
# correction (owner decision 2026-10-23, superseding "withdrawn by owner";
# blockchain-compatible: append only, decided from the ledger's own records).

def _confirmed(env, monkeypatch):
    T._with_principals(env, monkeypatch)
    h = env.hub()
    t = H.LocalTransport(h)
    own, wa, owner = env.writer("own", t), env.writer("wa", t), env.writer(T.OWNER, t)
    own.append("item.create", {"id": "t", "assignee": "wa"})
    wa.append("item.claim", {"id": "t"})
    done, _ = wa.append("item.complete", {"id": "t"})
    return h, own, wa, owner, done


def _void(done, reason="completion recorded against the wrong task"):
    return {"log": "wa", "seq": done.seq, "hash": done.hash, "reason": reason}


def _replica(env, src: Path) -> Path:
    """A replica rebuilt line by line from the published logs (no hub private state)."""
    dst = env.tmp / f"replica-{len(list(env.tmp.glob('replica-*')))}"
    (dst / ".datacore" / "events").mkdir(parents=True)
    for f in sorted((src / ".datacore" / "events").glob("*.jsonl"), reverse=True):
        with open(dst / ".datacore" / "events" / f.name, "w", encoding="utf-8") as out:
            for line in f.read_text().splitlines():
                out.write(line + "\n")
    (dst / H.CONFIG).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src / H.CONFIG, dst / H.CONFIG)
    return dst


def test_r5_a_reversal_inside_the_window_takes_effect(env, monkeypatch):
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    owner.append("ledger.void", _void(done))
    assert h._state()[1].items["t"].status == "claimed"
    report = wa.receipt_check(T._published(env, h, [wa, own, owner]), required=[done.hash])
    assert report["status"] == "failed" and not report["ok"], report      # amendment 3, inside the window


def test_r5_a_reversal_after_the_seal_is_refused_at_write(env, monkeypatch):
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    first = T._published(env, h, [wa, own])
    assert wa.receipt_check(first, required=[done.hash])["status"] == "confirmed"
    v = T._signed_into(env, owner, "ledger.void", _void(done))
    with pytest.raises(H.HubRefused) as exc:
        h.append(owner._submission(v))
    assert exc.value.code == "reversal_closed", exc.value
    assert h._state()[1].items["t"].status == "completed"


def test_r5_a_reversal_after_the_seal_imported_is_ignored_the_same_on_a_replica(env, monkeypatch):
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    first = T._published(env, h, [wa, own])
    assert wa.receipt_check(first, required=[done.hash])["status"] == "confirmed"
    v = T._signed_into(env, owner, "ledger.void", _void(done))
    T.git_pull(owner.space_dir, env.hub_dir)                 # it arrives anyway, by git
    second = T._published(env, h, [wa, own, owner])
    assert v.hash in {x for (_, _, x) in h._rejected()}       # recorded as a rejected proposal
    assert h._state()[1].items["t"].status == "completed"
    # A sealed confirmed receipt never flips.
    assert wa.receipt_check(second, required=[done.hash])["status"] == "confirmed"
    # The same on a replica, decided from the logs alone -- even with the
    # grantor's rejection record taken out, the window rule decides.
    replica = _replica(env, second)
    assert H.authoritative_state(replica, registry_path=env.registry).items["t"].status == "completed"
    from ledger.fold import fold
    events, kw = H.authoritative_events(replica, registry_path=env.registry)
    bare = [e for e in events if e.type != "ledger.reject"]
    assert fold(bare, **kw).items["t"].status == "completed"
    assert wa.receipt_check(replica, required=[done.hash])["status"] == "confirmed"


def test_r5_a_reversal_past_the_maximum_age_is_refused_even_before_a_late_seal(env, monkeypatch):
    # Round 6 (owner decision H1): the age is measured on the HUB's clock, from
    # the hub's admission of the reversed event -- not from the writers' HLCs.
    from ledger.voids import REVERSAL_MAX_AGE_MS
    h, own, wa, owner, done = _confirmed(env, monkeypatch)      # no seal yet: the seal is late
    env.clock.advance(REVERSAL_MAX_AGE_MS / 1000 + 1)
    v = T._signed_into(env, owner, "ledger.void", _void(done))
    with pytest.raises(H.HubRefused) as exc:
        h.append(owner._submission(v))
    assert exc.value.code == "reversal_closed", exc.value
    T.git_pull(owner.space_dir, env.hub_dir)                 # and by git: no effect either
    published = T._published(env, h, [wa, own, owner])
    assert h._state()[1].items["t"].status == "completed"
    assert wa.receipt_check(published, required=[done.hash])["status"] == "confirmed"
    replica = _replica(env, published)
    assert H.authoritative_state(replica, registry_path=env.registry).items["t"].status == "completed"


def test_r5_a_reversal_just_inside_the_maximum_age_still_works(env, monkeypatch):
    from ledger.voids import REVERSAL_MAX_AGE_MS
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    env.clock.advance(REVERSAL_MAX_AGE_MS / 1000 - 1)
    v = T._signed_into(env, owner, "ledger.void", _void(done))
    assert h.append(owner._submission(v))["status"] == "accepted"
    assert h._state()[1].items["t"].status == "claimed"


def test_r5_after_the_seal_a_correction_is_a_forward_event_with_its_own_receipt(env, monkeypatch):
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    first = T._published(env, h, [wa, own])
    c, answer = owner.append("ledger.compensate", _void(done))
    assert answer["status"] == "accepted", answer
    second = T._published(env, h, [wa, own, owner])
    assert wa.receipt_check(second, required=[done.hash])["status"] == "confirmed"     # the original stands
    assert owner.receipt_check(second, required=[c.hash])["status"] == "confirmed"     # its own receipt
    rows = [json.loads(x) for x in h._incidents_path.read_text().splitlines() if x.strip()]
    assert [(r["original"], r["correction"]) for r in rows] == [(done.hash, c.hash)], rows
    assert "write gate" in rows[0]["gate"] and "item.complete" in rows[0]["gate"]
    h.seal()
    assert len([x for x in h._incidents_path.read_text().splitlines() if x.strip()]) == 1     # once
    replica = _replica(env, second)
    strip = lambda rs: [{k: v for k, v in r.items()} for r in rs]  # noqa: E731
    assert strip(H.incidents(second, registry_path=env.registry)) == strip(H.incidents(replica, registry_path=env.registry))


def test_r5_a_correction_of_an_unsealed_event_is_refused(env, monkeypatch):
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    with pytest.raises(H.HubRefused) as exc:
        owner.append("ledger.compensate", _void(done))
    assert exc.value.code == "bad_compensate", exc.value
