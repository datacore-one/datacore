"""Finality seals (DIP-0042).

The properties that matter are: an unsealed ledger has NO settled state, a seal
is reproducible by any reader, and tampering after a seal is detected.
"""
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from ledger.log import EventLog, read_events  # noqa: E402
from ledger.seal import (  # noqa: E402
    build_seal_payload, latest_seal, settled, settled_events, verify_seal, watermarks,
)


@pytest.fixture
def space(tmp_path):
    (tmp_path / ".datacore" / "events").mkdir(parents=True)
    return tmp_path


def _work(space, actor="mac", n=3):
    log = EventLog(space, actor)
    for i in range(n):
        log.append("item.create", {"id": f"org-{actor}-{i}", "title": f"t{i}"})


class TestUnsealed:
    def test_no_seal_means_no_settled_state(self, space):
        """Deliberate: falling back to "the tip is settled" would make an
        unsealed system indistinguishable from a sealed one."""
        _work(space)
        evs = read_events(space)
        assert latest_seal(evs) is None
        assert settled(evs) is None
        assert settled_events(evs) == []

    def test_unsealed_is_not_a_failure(self, space):
        _work(space)
        ok, detail = verify_seal(read_events(space))
        assert ok is None            # not False — nothing to check
        assert "no seal" in detail


class TestSealing:
    def test_seal_verifies_against_a_recomputed_root(self, space):
        _work(space)
        payload = build_seal_payload(read_events(space))
        EventLog(space, "winston").append("ledger.seal", payload)
        ok, detail = verify_seal(read_events(space))
        assert ok is True, detail

    def test_settled_excludes_work_after_the_seal(self, space):
        _work(space, "mac", 2)
        EventLog(space, "winston").append(
            "ledger.seal", build_seal_payload(read_events(space)))
        # DISTINCT id: re-running _work would replay org-mac-0, which is a
        # duplicate create (a no-op by design), not new work — and would prove
        # nothing about the settled/tip split.
        EventLog(space, "mac").append(
            "item.create", {"id": "org-mac-late", "title": "after the seal"})
        evs = read_events(space)
        assert len(settled_events(evs)) == 2
        assert len([e for e in evs if e.type != "ledger.seal"]) == 3
        # The tip sees the late item; settled state does not. That split is the
        # entire purpose of a seal.
        from ledger.fold import fold
        assert "org-mac-late" in fold(
            [e for e in evs if e.type != "ledger.seal"]).items
        assert "org-mac-late" not in settled(evs).items

    def test_a_seal_never_seals_a_seal(self, space):
        """Otherwise the root would depend on sealing history rather than on
        the work, and two runs over identical work would disagree."""
        _work(space)
        p1 = build_seal_payload(read_events(space))
        EventLog(space, "winston").append("ledger.seal", p1)
        p2 = build_seal_payload(read_events(space))
        assert p1["state_root"] == p2["state_root"]
        assert "winston" not in p2["watermarks"]

    def test_watermarks_name_an_exact_event_set(self, space):
        _work(space, "mac", 3)
        _work(space, "miles", 2)
        wm = watermarks(read_events(space))
        assert wm == {"mac": 2, "miles": 1}


class TestDetection:
    def test_a_wrong_root_is_detected_by_any_reader(self, space):
        """The property that makes a designated sequencer safe: its claim is
        reproducible, so a bad seal is detectable rather than authoritative."""
        _work(space)
        payload = build_seal_payload(read_events(space))
        payload["state_root"] = "0" * 64
        EventLog(space, "winston").append("ledger.seal", payload)
        ok, detail = verify_seal(read_events(space))
        assert ok is False
        assert "MISMATCH" in detail

    def test_being_behind_a_seal_is_not_a_failure(self, space):
        """A seal naming an actor this machine has not synced yet means we are
        behind, not that the seal is wrong."""
        _work(space, "mac", 2)
        payload = build_seal_payload(read_events(space))
        payload["watermarks"]["tris"] = 7        # an actor we have never seen
        EventLog(space, "winston").append("ledger.seal", payload)
        ok, detail = verify_seal(read_events(space))
        assert ok is None
        assert "behind" in detail
