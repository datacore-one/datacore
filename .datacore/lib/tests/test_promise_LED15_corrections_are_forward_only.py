"""LED-15: Nothing is cancelled after the fact. No new reversal (`ledger.void`)
is written or admitted -- not by the library, the command line, the hub
writer, the hub's front door, or a reversal that reaches the hub by git from a
machine still on old code. A mistake is fixed by a new forward correcting
record (`ledger.compensate`). Reversals the hub admitted before the retirement
keep their effect.

Rule: DIP-0034 amendment of 2026-10-31, items 2 ("no voids") and 3
("corrections are forward only"), in force from 2026-11-01T00:00:00Z by the
hub's admission clock. It supersedes the reversal window of 2026-10-23; the
eval of that window is retired (owner, 2026-11-01) and kept, not collected,
as `tests/retired/retired_LED15_reversal_window.py`.

Kind: deterministic (temporary hub spaces; never the live installation).

Seeded failure: the head before plan phase B (067b5291): the event log,
`ledger_cli.py void` and the hub writer all wrote a new `ledger.void`, and the
hub admitted the owner's reversal of a completion inside the hour, so the
completed task went back to claimed.

Promise, as evals, for one completed task event in a hub space:
  * the library, the command line and the hub writer refuse a new reversal
    before anything is written, naming `ledger.compensate`;
  * a signed reversal written by hand (old code) is refused at the hub's
    door (`void_retired`) even when every condition of the old window holds
    (the owner, inside the hour, before the seal), and with a stamp chosen
    to look older than the retirement;
  * the same reversal imported by git is judged and has no effect: on the
    hub, on a replica, and in its receipt;
  * a reversal the hub admitted before the retirement keeps its effect,
    the same on a replica;
  * after the seal, the forward correction is accepted, gets its own
    receipt and leaves the original standing.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import pytest

from tests import test_ledger_hub as T
from tests._legacy_void import legacy_void_rules
from tests.test_ledger_hub_round5 import _confirmed, _replica, _void, env  # noqa: F401
from ledger import hub as H
from ledger.log import EventLog

HOUR_S = 60 * 60


def _status(h):
    return h._state()[1].items["t"].status


def _logs(space):
    return {p.name: p.read_bytes() for p in sorted((space / ".datacore").rglob("*.jsonl"))}


def test_the_retirement_moment_is_one_named_constant():
    from ledger import voids
    assert voids.VOID_RETIRED_MS == int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp() * 1000)


@pytest.mark.parametrize("how", ["event-log", "command-line", "hub-writer"])
def test_no_writer_writes_a_new_reversal(env, monkeypatch, capsys, how):
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    env.clock.advance(HOUR_S - 60)                     # inside the old window: the old rule would allow it
    T.git_pull(env.hub_dir, owner.space_dir)           # the owner's copy holds the completion
    before = _logs(owner.space_dir)
    if how == "event-log":
        with pytest.raises(ValueError) as exc:
            EventLog(owner.space_dir, T.OWNER, sign=True, keys_dir=env.keys_dir,
                     registry_path=env.registry).append("ledger.void", _void(done))
        message = str(exc.value)
        assert type(exc.value).__name__ == "VoidRetired", exc.value
    elif how == "command-line":
        import ledger_cli
        monkeypatch.setattr(ledger_cli, "EventLog", lambda space, actor: EventLog(
            space, actor, sign=True, keys_dir=env.keys_dir, registry_path=env.registry))
        with pytest.raises(SystemExit) as exc:
            ledger_cli.cmd_void(argparse.Namespace(space=str(owner.space_dir), actor=T.OWNER, log="wa",
                                                   seq=done.seq, reason="wrong task"))
        assert exc.value.code not in (0, None)
        message = capsys.readouterr().err
    else:
        with pytest.raises(H.HubRefused) as exc:
            owner.append("ledger.void", _void(done))
        assert exc.value.code == "void_retired", exc.value
        message = str(exc.value)
    assert "ledger.compensate" in message, f"[{how}] the refusal names no forward correction: {message!r}"
    assert _logs(owner.space_dir) == before, f"[{how}] a refused reversal was written"
    assert _status(h) == "completed"


@pytest.mark.parametrize("variant", ["owner-inside-the-hour", "stamped-before-the-retirement"])
def test_the_hub_refuses_a_reversal_from_old_code_and_by_git_it_has_no_effect(env, monkeypatch, variant):
    from ledger import voids
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    env.clock.advance(HOUR_S - 60)
    hlc = None
    if variant == "stamped-before-the-retirement":
        hlc = f"{voids.VOID_RETIRED_MS - 60_000:013d}.0000.{T.OWNER}"
    v = T._signed_into(env, owner, "ledger.void", _void(done), hlc=hlc)   # what old code signs
    with pytest.raises(H.HubRefused) as exc:
        h.append(owner._submission(v))
    assert exc.value.code == "void_retired", f"[{variant}] refused as {exc.value.code}, expected void_retired"
    T.git_pull(owner.space_dir, env.hub_dir)           # and by git: judged, never effective
    h.seal()
    assert _status(h) == "completed", f"[{variant}] the reversal took effect"
    assert owner.receipt_check(env.hub_dir, required=[v.hash])["status"] != "confirmed", \
        f"[{variant}] the refused reversal got a confirmed receipt"
    replica = _replica(env, env.hub_dir)
    st = H.authoritative_state(replica, registry_path=env.registry, policy=T.TEST_POLICY)
    assert st.items["t"].status == "completed", f"[{variant}] a replica applies the reversal"
    assert not st.finality.outcomes.get(v.hash, "no-op").startswith("applied"), st.finality.outcomes.get(v.hash)


def test_a_reversal_admitted_before_the_retirement_keeps_its_effect(env, monkeypatch):
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    with legacy_void_rules():                          # history: admitted by the old rule
        v, answer = owner.append("ledger.void", _void(done))
    assert answer["status"] == "accepted", answer
    h.seal()                                           # judged again after the retirement: unchanged
    assert _status(h) == "claimed", "a reversal admitted before the retirement lost its effect"
    assert owner.receipt_check(env.hub_dir, required=[v.hash])["status"] == "confirmed"
    replica = _replica(env, env.hub_dir)
    st = H.authoritative_state(replica, registry_path=env.registry, policy=T.TEST_POLICY)
    assert st.items["t"].status == "claimed", "a replica dropped the reversal admitted before the retirement"


def test_a_mistake_is_fixed_by_a_forward_correction(env, monkeypatch):
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    h.seal()
    c, answer = owner.append("ledger.compensate", _void(done))
    assert answer["status"] == "accepted", answer
    h.seal()
    assert _status(h) == "completed", "the original no longer stands"
    assert owner.receipt_check(env.hub_dir, required=[c.hash])["status"] == "confirmed"
    assert wa.receipt_check(env.hub_dir, required=[done.hash])["status"] == "confirmed"
    assert [i["correction"] for i in H.incidents(env.hub_dir, registry_path=env.registry)] == [c.hash]
