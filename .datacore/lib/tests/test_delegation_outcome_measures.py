"""Two outcome measures, read from real events, reported daily.

Audit 2026-11-15, section 5: about 45 promises covered ledger and orchestration
mechanisms and about four covered the outcome, so the area could be all green
while delegation did not work. These two say whether it works:

  * of the work delegated to agents in the last 7 days that has had a day to
    run, what share was finished or closed by a person;
  * does each space's task list match the ledger.

And the watcher follows the same rule as the claim loop (owner, 2026-11-15): a
task from an org file is a delegation once it is assigned.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import delegation_watch as dw

HOUR = 3_600_000
DAY = 24 * HOUR
NOW = int(datetime(2026, 11, 15, 12, tzinfo=timezone.utc).timestamp() * 1000)


def _item(iid, status, created_ago_ms, *, assignee="miles", org=False, reason=None):
    payload = {"title": iid, "assignee": assignee}
    if org:
        payload["org"] = {"properties": {}}
    return SimpleNamespace(id=iid, title=iid, status=status, payload=payload, owner=assignee,
                           closed_reason=reason, closed_at=f"{NOW - HOUR}.0000.x",
                           history=[f"{NOW - created_ago_ms}.0000.winston item.create: applied"])


def _state(*items):
    return SimpleNamespace(items={i.id: i for i in items})


def _agent(name):
    return "human" if name == "owner" else "agent"


def test_the_share_counts_finished_and_closed_work_over_what_had_a_day_to_run():
    folded = {"2-team": _state(
        _item("done-1", "completed", 3 * DAY),
        _item("done-2", "verified", 2 * DAY),
        _item("closed-by-a-person", "dismissed", 4 * DAY, reason="not needed any more"),
        _item("gave-up", "dismissed", 4 * DAY, reason=dw.GAVE_UP + " after 3 failed attempts"),
        _item("still-open", "created", 2 * DAY),
        _item("stuck", "claimed", 5 * DAY),
    )}
    m = dw.outcomes(folded, now_ms=NOW, kind_of=_agent)
    assert (m["total"], m["finished"], m["closed"], m["gave_up"], m["open"]) == (6, 2, 1, 1, 2)
    assert m["share"] == 0.5
    assert [o["id"] for o in m["not_done"]] == ["gave-up", "still-open", "stuck"]


def test_work_younger_than_a_day_or_older_than_the_window_is_not_judged():
    folded = {"2-team": _state(
        _item("too-new", "created", 3 * HOUR),
        _item("too-old", "created", 9 * DAY),
        _item("in-window", "completed", 2 * DAY),
    )}
    m = dw.outcomes(folded, now_ms=NOW, kind_of=_agent)
    assert (m["total"], m["finished"]) == (1, 1) and m["share"] == 1.0


def test_the_owners_own_tasks_and_the_unassigned_backlog_are_not_delegations():
    folded = {"0-own": _state(
        _item("mine", "created", 2 * DAY, assignee="owner"),
        _item("backlog", "created", 2 * DAY, assignee=None, org=True),
        _item("assigned-later", "created", 2 * DAY, assignee="miles", org=True),
    )}
    m = dw.outcomes(folded, now_ms=NOW, kind_of=_agent)
    assert m["total"] == 1 and [o["id"] for o in m["not_done"]] == ["assigned-later"]


def test_no_delegations_is_said_not_scored():
    m = dw.outcomes({"2-team": _state()}, now_ms=NOW, kind_of=_agent)
    assert m["total"] == 0 and m["share"] is None
    assert "no delegated work" in dw.outcome_lines(m, {"ok": [], "differs": {}, "unchecked": {}})[0]


def test_the_watcher_reports_an_assigned_org_task_that_nobody_picked_up():
    """It skipped every org-origin item, so an assigned one could wait forever unseen."""
    state = _state(_item("assigned-later", "created", 2 * DAY, org=True),
                   _item("backlog", "created", 2 * DAY, assignee=None, org=True))
    gaps = dw.gaps_in("2-team", state, now_ms=NOW, kind_of=_agent)
    assert [(g["id"], g["kind"]) for g in gaps] == [("assigned-later", "not_picked_up")]


def test_the_task_list_measure_names_the_spaces_that_differ(tmp_path):
    for name in ("1-a", "2-b", "3-c"):
        (tmp_path / name / ".datacore" / "events").mkdir(parents=True)

    def check(space, out):
        if space.name == "2-b":
            out("lost: x is live in the ledger and missing from the list")
            out("FAIL 2-b 10 task(s), 1 lost, 0 doubled")
            return 1
        if space.name == "3-c":
            raise OSError("list unreadable")
        out(f"OK {space.name} 4 task(s), 0 lost, 0 doubled")
        return 0
    lists = dw.task_lists(tmp_path, check=check, spaces=[tmp_path / n for n in ("1-a", "2-b", "3-c")])
    assert lists["ok"] == ["1-a"]
    assert lists["differs"] == {"2-b": "FAIL 2-b 10 task(s), 1 lost, 0 doubled"}
    assert "3-c" in lists["unchecked"] and "OSError" in lists["unchecked"]["3-c"]


def test_the_report_is_two_plain_lines_a_contract_can_read():
    m = {"total": 40, "finished": 28, "closed": 3, "gave_up": 4, "open": 5, "share": 31 / 40, "days": 7,
         "not_done": []}
    lists = {"ok": ["1-a", "3-c"], "differs": {"2-b": "FAIL 2-b 10 task(s), 1 lost, 0 doubled"}, "unchecked": {}}
    a, b = dw.outcome_lines(m, lists)
    assert a == "delegations 7d: 31 of 40 finished or closed by a person (78%); 4 given up, 5 still open"
    assert b == "task lists: 2 of 3 match the ledger; differs: 2-b (1 lost, 0 doubled)"
