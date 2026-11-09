"""V2 (PLAN Phase 2; audit A#7): one verifier. The checkpoint, the relay, the
health check, the seal and the CLI give the same verdict on the same log.

Five consumers ask "is this ledger sound?" today, through four different loops
(ledger.verify, ledger_checkpoint._restore, ledger.seal._chain_issue, and the
health/relay wrappers around verify_chain with their own exemptions). Each case
below plants one defect in a disposable space and asks all five. Every consumer
must say the same thing: ok, broken, or could not tell.

Cases (each from an incident or an audit finding):
  clean              nothing wrong
  tampered           a stored event's body edited without re-hashing it
  forged signature   body and hash intact, signature not by the writer's
                     registered key (6-meridian 2026-09-25 shape)
  voided             a bad event cancelled by an authorised in-ledger void
                     (decision 6): sound, the void is the record
  telemetry          a tampered event in the space's telemetry log (decision 3:
                     telemetry is a separate log per space, still history)
  damaged middle     a malformed line in the middle of one log

Seeded failure: any one consumer keeping its own loop (e.g. seal's
_chain_issue ignoring voids and signatures, the health check calling a bad
signature "unverified", the relay skipping telemetry logs).
"""
from __future__ import annotations

import pytest

import _verify_fixtures as vf


def _space(root):
    space = vf.new_space(root)
    vf.append(space, "alice", 4, sign=True)
    vf.append(space, "bot", 3)
    return space


def _clean(root, space):
    return "ok"


def _tampered(root, space):
    vf.tamper_payload(vf.log_path(space, "bot"), 1)
    return "broken"


def _forged_signature(root, space):
    vf.forge_signature(vf.log_path(space, "alice"), 1)
    return "broken"


def _voided(root, space):
    seq, h, body = vf.hand_append_bad_hash(vf.log_path(space, "bot"), "bot")
    vf.void(space, "owner", "bot.jsonl", seq, h, body)
    return "ok"


def _telemetry(root, space):
    from ledger.log import EventLog
    log = EventLog(space, "bot")
    for i in range(3):
        log.append("metric.attest", {"metric": "probe.latency", "value": i, "unit": "ms"})
    path = vf.log_path(space, "bot", telemetry=True)
    assert path.is_file(), "fixture: metric.attest must land in the per-space telemetry log"
    vf.tamper_payload(path, 1)
    return "broken"


def _damaged_middle(root, space):
    vf.malform_line(vf.log_path(space, "bot"), 1)
    return "broken"


CASES = {"clean": _clean, "tampered": _tampered, "forged signature": _forged_signature,
         "voided": _voided, "telemetry": _telemetry, "damaged middle": _damaged_middle}


@pytest.mark.parametrize("case", list(CASES))
def test_every_consumer_gives_the_same_verdict(sandbox_root, case):
    space = _space(sandbox_root)
    expected = CASES[case](sandbox_root, space)
    got = vf.all_verdicts(sandbox_root, space)
    wrong = {k: v for k, v in got.items() if v[0] != expected}
    assert not wrong, (
        f"case '{case}': every consumer should say '{expected}'. These did not:\n"
        + "\n".join(f"  {k}: said '{v[0]}' ({v[1][:300]})" for k, v in wrong.items())
        + "\nall verdicts: " + ", ".join(f"{k}={v[0]}" for k, v in got.items()))


def test_there_is_one_public_verifier_and_the_private_loop_is_gone():
    """The fix the audit names: one `ledger.verify` entry point used by all callers,
    and seal's private `_chain_issue` loop no longer an independent verifier."""
    import inspect
    from ledger import seal, verify
    assert hasattr(verify, "verify_space"), "ledger.verify has no space-level entry point (verify_space)"
    src = inspect.getsource(seal)
    assert "compute_hash(body) != event.hash" not in src, (
        "ledger.seal still re-implements the chain check (its own hash/prev/seq loop) "
        "instead of asking ledger.verify")
    import ledger_checkpoint
    src = inspect.getsource(ledger_checkpoint._restore)
    assert "compute_hash" not in src, (
        "ledger_checkpoint._restore still re-implements the chain check instead of asking ledger.verify")
