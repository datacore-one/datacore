"""One delegation route (plan 2026-10-31, phase E).

Ledger assignment is the only way an agent receives work. Miles has two
executors on his host reading that one route: the claim loop (ledger_claim,
items proved by a `check`) and his hourly run (items carrying a nightshift
task spec). An item names its runner in the payload (`runner`), so exactly one
of the two takes it:

  * the claim loop leaves an item that names a runner to that runner, and says so;
  * `runner_queue` gives the runner the items addressed to its writer, by the
    same rules the claim loop uses (exact writer, not org-mirrored, not given up);
  * `materialize` carries `runner` and `spec` into the item, so a task added by
    hand to nightshift.org keeps its specification when it enters the ledger.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_promise_NS_9_waiting_on_owner_is_not_a_failure import fleet  # noqa: E402,F401
import pytest  # noqa: E402
# Delegates as winston and dispatches as miles: a declared roster, not the
# host's private registry (conftest.install_roster).
pytestmark = pytest.mark.usefixtures("install_roster")


def _capture(monkeypatch):
    import ledger_claim
    calls = []

    def run(title, route, cwd, item_id="", hops=0, actor=""):
        calls.append(item_id)
        return False, "check failed: nothing done", {}
    monkeypatch.setattr(ledger_claim, "run_task", run)
    return calls


def test_materialize_carries_the_runner_and_the_spec(fleet):  # noqa: F811
    from briefing.actions import materialize
    root, space, drill = fleet
    spec = {"tags": ["AI", "research"], "properties": {"DONE_WHEN": "the report exists",
                                                       "SURFACE": "repo"}, "body": "How to work."}
    r = materialize([{"text": "Write the weekly report", "assignee": "miles",
                      "runner": "nightshift", "spec": spec}], space, "miles", transport=None)
    assert r.created, r.blocked
    payload = r.created[0].payload
    assert payload["runner"] == "nightshift"
    assert payload["spec"] == spec
    assert payload["assignee"] == "miles"


def test_the_claim_loop_leaves_a_runner_item_to_its_runner(fleet, monkeypatch):  # noqa: F811
    root, space, drill = fleet
    calls = _capture(monkeypatch)
    drill.delegate(space, by="winston", to="miles", id="ns-1", title="A runner task",
                   runner="nightshift")
    drill.delegate(space, by="winston", to="miles", id="cl-1", title="A claim-loop task",
                   check="test -f done.txt")
    out = drill.dispatch(space, "miles")
    assert calls == ["cl-1"], out
    assert "left to the nightshift runner" in out, out
    assert drill.item(space, "ns-1").status == "created"


def test_runner_queue_is_the_runners_items_for_this_writer(fleet):  # noqa: F811
    from ledger_claim import runner_queue
    root, space, drill = fleet
    drill.delegate(space, by="winston", to="miles", id="ns-1", title="Miles's runner task",
                   runner="nightshift")
    drill.delegate(space, by="winston", to="miles", id="cl-1", title="Miles's claim-loop task")
    drill.delegate(space, by="miles", to="winston", id="ns-2", title="Winston's runner task",
                   runner="nightshift")
    items, state, source = runner_queue(space, "miles", "nightshift", transport=None)
    assert [i.id for i in items] == ["ns-1"]
    assert source == "git"
    assert state.items["ns-2"].status == "created"
