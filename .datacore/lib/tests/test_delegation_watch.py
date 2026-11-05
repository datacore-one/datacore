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


# ── Winston's read tool: who has what, across spaces (Winston path step 3, 2026-11-05) ──

def test_who_has_what_groups_open_work_by_assignee_and_status():
    """Winston must see who has what without the owner asking. Same read as the
    gap check: org mirrors and closed work are not listed; the owner's own list
    is listed under the owner, because Winston follows that up too."""
    a = NS(items={i.id: i for i in [
        item("a1", assignee="miles"),
        item("a2", status="claimed", assignee="miles", claimed=NOW - H),
        item("a3", status="completed", assignee="miles"),
        item("a4", assignee="miles", org=True),
    ]})
    b = NS(items={i.id: i for i in [
        item("b1", assignee="data"),
        item("b2", assignee=None),
        item("b3", assignee="gregor"),
    ]})
    rows = dw.who_has_what({"2-datacore": a, "8-firm": b}, now_ms=NOW)
    got = {(r["assignee"], r["status"], r["space"], r["id"]) for r in rows}
    assert got == {("miles", "created", "2-datacore", "a1"), ("miles", "claimed", "2-datacore", "a2"),
                   ("data", "created", "8-firm", "b1"), ("(nobody)", "created", "8-firm", "b2"),
                   ("gregor", "created", "8-firm", "b3")}
    a2 = next(r for r in rows if r["id"] == "a2")
    assert a2["age"] == "1 h" and a2["title"] == "task a2"


def test_who_has_what_text_is_one_block_per_assignee():
    a = NS(items={i.id: i for i in [item("a1"), item("a2", status="claimed", claimed=NOW - H)]})
    text = dw.who_text(dw.who_has_what({"2-datacore": a}, now_ms=NOW), {})
    assert text.splitlines()[0].startswith("miles: 2 open (1 claimed, 1 waiting)")
    assert "2-datacore" in text and "task a1" in text


def test_who_has_what_names_a_space_it_could_not_read():
    text = dw.who_text([], {"5-plur": "OSError: gone"})
    assert "5-plur" in text and "could not be read" in text


def test_who_has_what_says_when_work_is_not_due_yet():
    a = NS(items={"f1": item("f1", not_before="2027-06-01T08:00:00+00:00")})
    rows = dw.who_has_what({"8-firm": a}, now_ms=NOW)
    assert rows[0]["age"].startswith("due in ")


# ── Filing only where the agent listens (Winston path step 4, 2026-11-05) ──
# AGT-12's evidence: a delegated post was filed in a space the agent's dispatcher
# never reads, and waited forever. Winston's filing path now refuses that at the
# moment of filing and names where the agent does listen.

LISTENS = {"data": {"kind": "agent", "listens_in": ["datacore-space", "plur-space"]},
           "miles": {"kind": "agent", "listens_in": ["datacore-space"]},
           "gregor": {"kind": "human"}}


def _entry(name):
    return LISTENS.get({"nightshift": "miles"}.get(name, name))


def test_a_delegation_where_the_agent_does_not_listen_is_refused_with_where_it_does():
    why = dw.listening_problem(Path("/x/8-firm"), {"id": "p1", "assignee": "data"},
                               repo_of=lambda p: "the-firm-space", entry_of=_entry)
    assert why and "data" in why and "the-firm-space" in why
    assert "datacore-space" in why and "plur-space" in why


def test_a_delegation_where_the_agent_listens_passes():
    assert dw.listening_problem(Path("/x/2-datacore"), {"id": "p1", "assignee": "miles"},
                                repo_of=lambda p: "datacore-space", entry_of=_entry) is None
    # a runner name resolves to its principal
    assert dw.listening_problem(Path("/x/2-datacore"), {"id": "p1", "runner": "nightshift"},
                                repo_of=lambda p: "datacore-space", entry_of=_entry) is None


def test_the_owners_items_org_mirrors_and_unknown_spaces_are_not_refused():
    firm = lambda p: "the-firm-space"  # noqa: E731
    assert dw.listening_problem(Path("/x"), {"assignee": "gregor"}, repo_of=firm, entry_of=_entry) is None
    assert dw.listening_problem(Path("/x"), {"assignee": "data", "org": {"h": 1}},
                                repo_of=firm, entry_of=_entry) is None
    assert dw.listening_problem(Path("/x"), {"assignee": "data"},
                                repo_of=lambda p: None, entry_of=_entry) is None


def test_the_registry_template_documents_where_an_agent_listens():
    """principals.yaml is a private per-host overlay; the tracked template carries the field."""
    import yaml
    reg = yaml.safe_load((LIB.parent / "registry" / "principals.yaml.example").read_text())["principals"]
    assert any(e.get("listens_in") for e in reg.values() if e.get("kind") == "agent")


# ── Follow-through on the owner's requests (Winston path step 5, 2026-11-05) ──
# "...and tells the owner the result or why not, without being asked." An owner
# request is an item Winston filed with `asked_by`. Each one that ends is
# reported once: done and checked, done but not yet checked, or not done and why.

def req(iid, status, reason=None, assignee="data"):
    it = item(iid, status=status, assignee=assignee, reason=reason)
    it.payload["asked_by"] = "gregor"
    return it


def test_each_finished_owner_request_is_reported_once_with_its_result(tmp_path):
    folded = {"2-datacore": NS(items={i.id: i for i in [
        req("r1", "verified"), req("r2", "completed"),
        req("r3", "dismissed", reason="gave up after 3 failed attempts: HTTP 402 from openrouter.ai"),
        req("r4", "claimed"), item("x1", status="verified")]})}
    sent = []
    st = tmp_path / "s.json"
    out = dw.report_requests(folded, state_path=st, send=lambda t: sent.append(t) or True)
    assert sorted(out["reported"]) == ["2-datacore/r1", "2-datacore/r2", "2-datacore/r3"]
    text = "\n".join(sent)
    assert "task r1" in text and "checked" in text
    assert "task r2" in text and "not yet checked" in text
    assert "task r3" in text and "402" in text
    assert "task r4" not in text and "task x1" not in text      # still open / not an owner request
    sent.clear()
    assert dw.report_requests(folded, state_path=st, send=lambda t: sent.append(t) or True)["reported"] == []
    assert sent == []                                           # once, never again


def test_a_completed_request_is_reported_again_when_it_becomes_checked(tmp_path):
    st = tmp_path / "s.json"
    sent = []
    dw.report_requests({"s": NS(items={"r": req("r", "completed")})}, state_path=st,
                       send=lambda t: sent.append(t) or True)
    dw.report_requests({"s": NS(items={"r": req("r", "verified")})}, state_path=st,
                       send=lambda t: sent.append(t) or True)
    assert len(sent) == 2 and "checked" in sent[1]


def test_an_unsent_report_is_tried_again_next_time(tmp_path):
    st = tmp_path / "s.json"
    folded = {"s": NS(items={"r": req("r", "verified")})}
    assert dw.report_requests(folded, state_path=st, send=lambda t: False)["unsent"] == ["s/r"]
    assert dw.report_requests(folded, state_path=st, send=lambda t: True)["reported"] == ["s/r"]


def test_the_briefing_line_counts_the_owners_open_requests():
    folded = {"s": NS(items={i.id: i for i in [req("a", "claimed"), req("b", "created"), req("c", "verified")]})}
    line = dw.requests_line(folded)
    assert line.startswith("Your requests: 2 open") and "1 finished" in line
