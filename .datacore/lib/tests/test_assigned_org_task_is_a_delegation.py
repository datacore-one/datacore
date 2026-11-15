"""A task becomes a delegation when it is assigned -- whenever that happens.

Owner decision 2026-11-15 (audit report 2026-11-15, section 4.1 item 1): "not
all tasks need to be done immediately; delegation can happen later". Before
this, the claim loop dropped every item that came from an org file (its payload
carries an `org` block), assigned or not, and nothing connected the task's
`:ASSIGNEE:` property (the GTD spec's way to assign) to the ledger. So a task
added the normal way could never reach an agent: six open tasks assigned to one
agent sat unclaimed.

  * an org-origin item WITH an assignee is dispatched by that assignee's loop;
  * an org-origin item with NO assignee is never dispatched, and is counted,
    not listed (the owner's whole backlog is not a queue);
  * the assignee property reaches the ledger from the adapter, on add and on a
    later update, and clearing it un-assigns.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
from tests.test_promise_NS_9_waiting_on_owner_is_not_a_failure import fleet  # noqa: E402,F401
import pytest  # noqa: E402

pytestmark = pytest.mark.usefixtures("install_roster")

ORG = {"priority": None, "properties": {}, "level": 2, "parent": None}


def _capture(monkeypatch):
    import ledger_claim
    calls = []

    def run(title, route, cwd, item_id="", hops=0, actor=""):
        calls.append(item_id)
        return False, "nothing done", {}
    monkeypatch.setattr(ledger_claim, "run_task", run)
    return calls


def test_an_assigned_org_task_is_dispatched_to_its_assignee(fleet, monkeypatch):  # noqa: F811
    root, space, drill = fleet
    calls = _capture(monkeypatch)
    drill.delegate(space, by="winston", to="miles", id="org-1", title="An org task, assigned",
                   check="test -f done.txt", org=dict(ORG))
    out = drill.dispatch(space, "miles")
    assert calls == ["org-1"], out


def test_an_unassigned_org_task_is_never_dispatched_and_only_counted(fleet, monkeypatch):  # noqa: F811
    root, space, drill = fleet
    calls = _capture(monkeypatch)
    for n in range(3):
        drill.delegate(space, by="winston", to=None, id=f"backlog-{n}", title=f"Backlog {n}", org=dict(ORG))
    out = drill.dispatch(space, "miles")
    assert calls == [], out
    assert "3 unassigned task(s)" in out, out
    assert "backlog-0" not in out, "the backlog is counted, never listed"


def test_plan_puts_assigned_org_items_in_ready(fleet):  # noqa: F811
    import ledger_claim
    from ledger.fold import fold
    from ledger.log import read_events
    root, space, drill = fleet
    drill.delegate(space, by="winston", to="miles", id="org-1", title="assigned", org=dict(ORG))
    drill.delegate(space, by="winston", to=None, id="org-2", title="unassigned", org=dict(ORG))
    events = list(read_events(space))
    p = ledger_claim.plan(fold(events), events, "miles")
    assert [i.id for i in p.ready] == ["org-1"]
    assert p.mirrored == 1 and p.unaddressed == []


def test_assignee_of_reads_the_property_in_one_place():
    from ledger.genesis import assignee_of
    assert assignee_of({"ASSIGNEE": " Miles "}) == "miles"
    assert assignee_of({"ASSIGNEE": ""}) is None
    assert assignee_of({}) is None
    assert assignee_of(None) is None


# ── the adapter: the property reaches the ledger ─────────────────────────────
@pytest.fixture
def declared_actor(monkeypatch):
    """The adapter writes as this machine's declared actor and, in a phase-0
    space, drops the ledger event silently when none is declared. The suite
    gives every test an empty identity, so say who is writing."""
    monkeypatch.setenv("DATACORE_ACTOR", "fixture")
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")


def _space(tmp_path):
    space = tmp_path / "5-testspace"
    (space / ".datacore" / "events").mkdir(parents=True)
    (space / "org").mkdir(parents=True)
    (space / "org" / "inbox.org").write_text("#+TITLE: Inbox\n")
    return space


def _payloads(space, task_id):
    out = []
    for f in sorted((space / ".datacore" / "events").glob("*.jsonl")):
        for ln in f.read_text().splitlines():
            if ln.strip():
                e = json.loads(ln)
                if (e.get("payload") or {}).get("id") == task_id:
                    out.append((e["type"], e["payload"]))
    return out


def test_assigning_later_through_the_adapter_reaches_the_ledger(tmp_path, declared_actor):
    from tests.test_org_workspace_adapter import run_adapter
    space = _space(tmp_path)
    org = str(space / "org" / "inbox.org")
    tid = run_adapter("add", "--file", org, "--allow-any-file", "--heading", "Later, maybe",
                      "--state", "TODO")["id"]
    created = _payloads(space, tid)[0][1]
    assert not created.get("assignee"), "a plain task is nobody's work yet"

    run_adapter("update", "--file", org, "--id", tid, "--property", "ASSIGNEE=Miles")
    kind, upd = _payloads(space, tid)[-1]
    assert kind == "item.update" and upd.get("assignee") == "miles", upd

    run_adapter("update", "--file", org, "--id", tid, "--property", "ASSIGNEE=")
    kind, upd = _payloads(space, tid)[-1]
    assert kind == "item.update" and "assignee" in upd and upd["assignee"] is None, upd


def test_adding_with_an_assignee_creates_a_delegation(tmp_path, declared_actor):
    from tests.test_org_workspace_adapter import run_adapter
    space = _space(tmp_path)
    org = str(space / "org" / "inbox.org")
    tid = run_adapter("add", "--file", org, "--allow-any-file", "--heading", "For Miles",
                      "--state", "TODO", "--property", "ASSIGNEE=miles")["id"]
    assert _payloads(space, tid)[0][1].get("assignee") == "miles"


def test_an_update_of_another_property_does_not_touch_the_assignee(tmp_path, declared_actor):
    from tests.test_org_workspace_adapter import run_adapter
    space = _space(tmp_path)
    org = str(space / "org" / "inbox.org")
    tid = run_adapter("add", "--file", org, "--allow-any-file", "--heading", "For Miles",
                      "--state", "TODO", "--property", "ASSIGNEE=miles")["id"]
    run_adapter("update", "--file", org, "--id", tid, "--property", "CONTEXT=why")
    assert "assignee" not in _payloads(space, tid)[-1][1]
