"""V3 (PLAN Phase 2; audit A#15, D8): a malformed line in one log is reported,
and the rest of the space stays readable.

A hand-written or torn-and-overwritten line in the MIDDLE of one writer's log
used to raise CorruptLogError from read_events, so fold, projection, checkpoint,
seal and policy stopped for the whole space (A#15). The verifier, per decision 7,
judges events, not whole logs: it names the bad line, it does not condemn the
events before it, and it does not condemn the other logs.

Seeded failure: read_events re-raises CorruptLogError for a malformed middle
line (the 09-26 state), or verify stops at the first bad line.
"""
from __future__ import annotations

import re
import warnings

import _verify_fixtures as vf


def _damaged(root, name="0-alpha", telemetry=False):
    space = vf.new_space(root, name)
    vf.append(space, "alice", 4)
    vf.append(space, "bot", 4)
    if telemetry:
        from ledger.log import EventLog
        log = EventLog(space, "bot")
        for i in range(4):
            log.append("metric.attest", {"metric": "probe.latency", "value": i, "unit": "ms"})
    vf.malform_line(vf.log_path(space, "bot", telemetry=telemetry), 1)     # line 2 of 4
    return space


def _flagged(out: str, log: str) -> set[int]:
    return {int(m.group(1)) for m in re.finditer(rf"{re.escape(log)}: line (\d+):", out)}


def test_the_bad_line_is_named_and_nothing_else_is_condemned(sandbox_root):
    space = _damaged(sandbox_root)
    verdict, out = vf.cli_verdict(space)
    assert verdict == "broken", f"a malformed line must fail verify; got {verdict}:\n{out}"
    flagged = _flagged(out, "bot.jsonl")
    assert 2 in flagged and "malformed" in out, f"verify must name bot.jsonl line 2 as malformed:\n{out}"
    assert 1 not in flagged, f"the event before the bad line is sound and must not be flagged:\n{out}"
    assert not _flagged(out, "alice.jsonl"), f"another writer's log is sound and must not be flagged:\n{out}"


def test_a_malformed_telemetry_line_is_reported_too(sandbox_root):
    space = _damaged(sandbox_root, telemetry=True)
    verdict, out = vf.cli_verdict(space)
    assert verdict == "broken", f"a malformed telemetry line must fail verify; got {verdict}:\n{out}"
    assert "telemetry/bot.jsonl: line 2" in out and "malformed" in out, out


def test_the_rest_of_the_space_is_still_read_and_folded(sandbox_root):
    from ledger.fold import fold
    from ledger.log import read_events
    space = _damaged(sandbox_root)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        events = read_events(space)
    assert any("bot.jsonl" in str(w.message) for w in caught), "the damage must be flagged to the reader"
    by_log = {}
    for e in events:
        by_log.setdefault(e.log, []).append(e.seq)
    assert by_log.get("alice") == [0, 1, 2, 3], f"alice's log must be read in full; got {by_log}"
    assert by_log.get("bot") == [0], f"bot's events before the bad line must be read; got {by_log}"
    items = fold(events).items
    assert {f"t-alice-{i}" for i in range(4)} <= set(items), "the other writer's items must still fold"


def test_one_damaged_space_does_not_hide_the_others(sandbox_root):
    _damaged(sandbox_root, "0-alpha")
    good = vf.new_space(sandbox_root, "1-beta")
    vf.append(good, "alice", 3)
    import ledger_health
    result = ledger_health.check(sandbox_root)
    assert result["spaces_broken"] == 1 and result["spaces_verified"] == 1, (
        f"one damaged space must be reported broken and the other still verified: {result}")
