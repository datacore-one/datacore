"""Winston catches delegation gaps (owner, 2026-11-03).

A delegated task whose agent never runs leaves no failure and no record: if
Data's machine is down, nothing is attempted, so nothing fails. And nothing made
Winston read results. This check reads every ledger space and names:

- a task addressed to an AGENT, not picked up 2 h after its start time;
- a task claimed and not finished for 24 h;
- a task given up after three failed attempts;
- a delegation addressed to nobody.

Tasks addressed to a person (kind: human) are the owner's list, not a gap.
It alerts once per gap and once more when the gap clears, and it never writes
to the ledger. Written before the code.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import delegation_watch as dw  # noqa: E402

H = 3600 * 1000
NOW = 1_800_000_000_000


def item(iid, status="created", assignee="miles", created=NOW - 3 * H, claimed=None,
         not_before=None, reason=None, closed=None, org=False, runner=None):
    payload = {"id": iid, "title": f"task {iid}"}
    if assignee:
        payload["assignee"] = assignee
    if not_before:
        payload["not_before"] = not_before
    if org:
        payload["org"] = {"heading": "x"}
    if runner:
        payload["runner"] = runner
    hist = [f"{created}.0000.mac mac item.create: applied"]
    if claimed:
        hist.append(f"{claimed}.0000.miles miles item.claim: applied")
    return NS(id=iid, title=f"task {iid}", status=status, payload=payload, history=hist,
              closed_reason=reason, closed_at=None if closed is None else f"{closed}.0000.miles")


def kinds(items, **kw):
    state = NS(items={i.id: i for i in items})
    found = dw.gaps_in("2-datacore", state, now_ms=NOW, **kw)
    return {(f["id"], f["kind"]) for f in found}


KIND = {"miles": "agent", "data": "agent", "gregor": "human"}


def _kind(name):
    return KIND.get(name)


def test_an_agent_task_past_its_start_and_not_picked_up_is_named():
    assert kinds([item("a")], kind_of=_kind) == {("a", "not_picked_up")}


def test_within_the_grace_or_before_its_start_time_it_is_not():
    from datetime import datetime, timezone
    future = datetime.fromtimestamp((NOW + H) / 1000, timezone.utc).isoformat()
    assert kinds([item("a", created=NOW - H // 2), item("b", not_before=future)], kind_of=_kind) == set()


def test_the_start_time_counts_from_not_before_not_creation():
    from datetime import datetime, timezone
    nb = datetime.fromtimestamp((NOW - 3 * H) / 1000, timezone.utc).isoformat()
    assert kinds([item("a", created=NOW - 100 * H, not_before=nb)], kind_of=_kind) == {("a", "not_picked_up")}


def test_a_task_for_a_person_or_an_org_mirror_is_not_a_gap():
    assert kinds([item("a", assignee="gregor"), item("b", org=True)], kind_of=_kind) == set()


def test_claimed_for_a_day_unfinished_is_stuck():
    assert kinds([item("a", status="claimed", claimed=NOW - 25 * H),
                  item("b", status="claimed", claimed=NOW - 2 * H)], kind_of=_kind) == {("a", "stuck")}


def test_given_up_after_three_attempts_is_named_with_its_reason():
    state = NS(items={"a": item("a", status="dismissed", reason="gave up after 3 failed attempts; last: boom",
                                closed=NOW - H),
                      "b": item("b", status="dismissed", reason="superseded", closed=NOW - H)})
    found = dw.gaps_in("2-datacore", state, now_ms=NOW, kind_of=_kind)
    assert [(f["id"], f["kind"]) for f in found] == [("a", "gave_up")]
    assert "boom" in found[0]["detail"]


def test_a_delegation_addressed_to_nobody_is_named():
    assert kinds([item("a", assignee=None, runner=None)], kind_of=_kind) == {("a", "no_assignee")}


def test_a_runner_task_counts_as_delegated_to_an_agent():
    assert kinds([item("a", assignee=None, runner="nightshift")], kind_of=_kind) == {("a", "not_picked_up")}


def test_finished_tasks_are_never_gaps():
    assert kinds([item("a", status="completed"), item("b", status="verified")], kind_of=_kind) == set()


def _cycle(tmp_path, found, sent):
    return dw.alert(found, state_path=tmp_path / "s.json", send=lambda t: sent.append(t) or True)


def test_one_alert_per_gap_then_one_clear(tmp_path):
    g = [{"space": "2-datacore", "id": "a", "kind": "stuck", "title": "task a", "assignee": "miles",
          "since_ms": NOW - 25 * H, "detail": "claimed 25 h ago"}]
    sent = []
    r1 = _cycle(tmp_path, g, sent)
    assert r1["alerted"] == ["2-datacore/a"] and len(sent) == 1 and "task a" in sent[0]
    _cycle(tmp_path, g, sent)
    assert len(sent) == 1, "a gap already reported is not sent again"
    r3 = _cycle(tmp_path, [], sent)
    assert r3["cleared"] == ["2-datacore/a"] and len(sent) == 2


def test_an_unsent_alert_is_tried_again(tmp_path):
    g = [{"space": "s", "id": "a", "kind": "stuck", "title": "t", "assignee": "miles",
          "since_ms": 0, "detail": "d"}]
    r = dw.alert(g, state_path=tmp_path / "s.json", send=lambda t: False)
    assert r["unsent"] and not json.loads((tmp_path / "s.json").read_text()).get("open")
    sent = []
    _cycle(tmp_path, g, sent)
    assert len(sent) == 1


def test_many_gaps_go_in_one_message(tmp_path):
    g = [{"space": "s", "id": f"a{n}", "kind": "stuck", "title": f"t{n}", "assignee": "miles",
          "since_ms": 0, "detail": "d"} for n in range(40)]
    sent = []
    _cycle(tmp_path, g, sent)
    assert len(sent) == 1 and "40" in sent[0] and len(sent[0]) < 3500


def test_the_check_writes_no_ledger_event(tmp_path):
    sys.path.insert(0, str(Path(__file__).parent))
    from test_ledger_cli import run_cli
    root = tmp_path / "Data"
    space = root / "2-datacore"
    space.mkdir(parents=True)
    r = run_cli("append", "--space", str(space), "--type", "item.create",
                "--payload", json.dumps({"id": "t1", "title": "x", "state": "TODO", "assignee": "miles"}),
                tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    found, errors = dw.scan(root, now_ms=NOW * 2, kind_of=_kind)
    assert errors == {} and [(f["space"], f["id"], f["kind"]) for f in found] == [("2-datacore", "t1", "not_picked_up")]
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
