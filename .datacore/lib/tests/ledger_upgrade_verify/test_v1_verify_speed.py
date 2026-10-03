"""V1 (PLAN Phase 2; audit A#1, A#8, C10): verify of a 20k-event space runs in
under 10 s.

Measured on SANDBOX COPIES of this installation's real spaces that hold at least
20,000 ledger events (task logs + telemetry logs), with the installation's own
principals and verify keys. The live spaces are only read; their checksums are
compared before and after.

Two numbers are judged, both against the claim's 10 s:

  * steady state -- the verify an operator, a probe or the MCP status runs on a
    machine that has verified this space before, after one more event was
    appended. Must finish in under 10 s for every such space, whatever its size.
  * first verify on a machine (cold, nothing remembered) -- normalised to the
    claim's 20,000 events: seconds * 20000 / events < 10.

Seeded failure (must turn this red): keys.verify re-parses principals.yaml
once per signed event (the 09-26 state: 54.6 s on 2-datacore) -> first verify
over 10 s per 20k events, and the steady state with it.

Timings depend on machine load: the 47k-event copy took 6.1 s idle and 13.1 s
while other test suites ran (mac, 2026-10-04); the box took 6.7 s.

Could not tell is red: a machine without a 20k-event space fails this eval
with that reason; it never skips.
"""
from __future__ import annotations

import time

import pytest

import _verify_fixtures as vf

CLAIM_EVENTS = 20_000
CLAIM_SECONDS = 10.0


def _timed(fn):
    t = time.perf_counter()
    out = fn()
    return time.perf_counter() - t, out


def test_verify_of_a_20k_event_space_runs_in_under_10_seconds(sandbox_root, tmp_path):
    lives = vf.live_spaces(CLAIM_EVENTS)
    assert lives, ("could not tell: this machine has no space with 20,000 or more ledger events "
                   "to measure on (a could-not-tell is red, never a pass)")
    before = {p: vf.ledger_digest(p) for p in lives}
    vf.copy_live_registry(sandbox_root)

    rows, failures = [], []
    for live in lives:
        copy = vf.copy_live_space(live, sandbox_root)
        events = vf.count_events(copy)

        cold_s, (cold, cold_out) = _timed(lambda: vf.cli_verdict(copy))
        assert cold == "ok", f"{live.name}: the sandbox copy must verify clean to be measured; got {cold}:\n{cold_out[-2000:]}"

        vf.append(copy, "v1probe", n=1, prefix="steady")
        warm_s, (warm, warm_out) = _timed(lambda: vf.cli_verdict(copy))
        assert warm == "ok", f"{live.name}: verify after one append: {warm}:\n{warm_out[-2000:]}"

        per_20k = cold_s * CLAIM_EVENTS / events
        rows.append(f"{live.name}: {events} events; first verify {cold_s:.1f} s "
                    f"({per_20k:.1f} s per 20k); steady state {warm_s:.1f} s")
        if per_20k >= CLAIM_SECONDS:
            failures.append(f"{live.name}: first verify takes {per_20k:.1f} s per 20,000 events "
                            f"(expected under {CLAIM_SECONDS:.0f} s)")
        if warm_s >= CLAIM_SECONDS:
            failures.append(f"{live.name}: verify after one new event took {warm_s:.1f} s on a machine "
                            f"that had verified it before (expected under {CLAIM_SECONDS:.0f} s)")

    after = {p: vf.ledger_digest(p) for p in lives}
    assert before == after, "LIVE SPACE CHANGED during the eval: a sandbox eval must only read it"
    print("\n".join(rows))
    assert not failures, "\n".join(failures) + "\n\nmeasured:\n" + "\n".join(rows)


def test_the_health_check_answers_within_the_same_budget(sandbox_root):
    """The MCP status and the fleet sweep read ledger_health; it is a verify too."""
    lives = vf.live_spaces(CLAIM_EVENTS)
    assert lives, "could not tell: no space with 20,000 or more ledger events on this machine"
    vf.copy_live_registry(sandbox_root)
    largest = max(lives, key=vf.count_events)
    copy = vf.copy_live_space(largest, sandbox_root)
    vf.cli_verdict(copy)                       # a machine that has verified it before
    vf.append(copy, "v1probe", n=1, prefix="steady")
    took, (verdict, detail) = _timed(lambda: vf.health_verdict(sandbox_root))
    assert verdict == "ok", f"health on a clean copy of {largest.name}: {verdict} {detail}"
    assert took < CLAIM_SECONDS, (f"ledger health of {largest.name} ({vf.count_events(copy)} events) took "
                                  f"{took:.1f} s after one new event (expected under {CLAIM_SECONDS:.0f} s)")
