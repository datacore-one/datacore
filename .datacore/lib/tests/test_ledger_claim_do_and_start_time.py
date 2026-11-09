"""A delegated task can say "do this" and "not before" (2026-10-27, promise AGT-12).

Winston delegated the Datafund X post P1 to Data through the ledger. Every route
framing the claim loop had said "investigate / find / assess / report", so Data's
agent reported on the task instead of posting. And items had no start time, so
a post scheduled for 12:49 could only be delegated at 12:49 by hand.

`route: do` frames the agent to carry the task out exactly as written.
`not_before` (ISO 8601 with offset) keeps an item unclaimed until that moment.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_promise_NS_9_waiting_on_owner_is_not_a_failure import fleet, _events  # noqa: E402,F401


def _capture(monkeypatch):
    import ledger_claim
    calls = []

    def run(title, route, cwd, item_id="", hops=0, actor=""):
        calls.append((item_id, route, title))
        return False, "check failed: nothing done", {}
    monkeypatch.setattr(ledger_claim, "run_task", run)
    return calls


def test_a_do_route_is_honoured_and_frames_the_agent_to_act(fleet, monkeypatch):  # noqa: F811
    import ledger_claim
    root, space, drill = fleet
    calls = _capture(monkeypatch)
    drill.delegate(space, by="winston", to="miles", id="post-p2", title="Post P2 of the order",
                   check="test -f posted.txt", route="do")
    drill.dispatch(space, "miles")
    assert calls and calls[0][1] == "do", calls
    framing = ledger_claim.ROUTE_FRAMING["do"].lower()
    assert "exactly" in framing and "report" not in framing.split(".")[0]


def test_an_item_is_not_claimed_before_its_start_time(fleet, monkeypatch):  # noqa: F811
    root, space, drill = fleet
    calls = _capture(monkeypatch)
    later = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
    drill.delegate(space, by="winston", to="miles", id="post-p3", title="Post P3 of the order",
                   check="test -f posted.txt", route="do", not_before=later)
    drill.dispatch(space, "miles")
    assert calls == [], f"claimed before its start time: {calls}"


def test_an_item_is_claimed_once_its_start_time_has_passed(fleet, monkeypatch):  # noqa: F811
    root, space, drill = fleet
    calls = _capture(monkeypatch)
    earlier = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
    drill.delegate(space, by="winston", to="miles", id="post-p4", title="Post P4 of the order",
                   check="test -f posted.txt", route="do", not_before=earlier)
    drill.dispatch(space, "miles")
    assert [c[0] for c in calls] == ["post-p4"]


def test_an_unreadable_start_time_is_not_claimed(fleet, monkeypatch):  # noqa: F811
    root, space, drill = fleet
    calls = _capture(monkeypatch)
    drill.delegate(space, by="winston", to="miles", id="post-p5", title="Post P5",
                   check="test -f posted.txt", route="do", not_before="tomorrow-ish")
    drill.dispatch(space, "miles")
    assert calls == []
