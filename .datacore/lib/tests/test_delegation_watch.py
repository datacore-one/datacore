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
    assert sorted(out["reported"]) == ["2-datacore/r1", "2-datacore/r2", "2-datacore/r3", "2-datacore/r4"]
    text = "\n".join(sent)
    assert "task r1" in text and "checked" in text
    assert "task r2" in text and "not yet checked" in text
    assert "task r3" in text and "402" in text
    assert "task r4" in text and "has it" in text               # claimed: the owner hears who has it
    assert "task x1" not in text                                # not an owner request
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


def test_request_reports_go_to_the_owners_own_chat_not_the_alert_group(tmp_path, monkeypatch):
    """The owner asked Winston in their 1:1 chat; the answer goes back there.
    On 2026-11-06 the Tris request was completed and verified, the watch
    recorded it as told -- and the owner never saw it: it went through the
    alert route, which posts only to The Firm group. Gaps stay alerts (group);
    a report on the owner's own request is a reply (1:1, winston_send.py
    without --alert)."""
    calls = []

    class R:
        returncode = 0

    def run(cmd, **kw):
        calls.append((cmd, kw.get("input")))
        return R()
    monkeypatch.setattr(dw.subprocess, "run", run)
    import job_verify
    sender = tmp_path / "winston_send.py"
    sender.write_text("")
    monkeypatch.setattr(job_verify, "WINSTON_SEND", sender)
    folded = {"s": NS(items={"r": req("r", "verified")})}
    out = dw.report_requests(folded, state_path=tmp_path / "s.json")
    assert out["reported"] == ["s/r"], out
    (cmd, text), = calls
    assert any(str(c).endswith("winston_send.py") for c in cmd), cmd
    assert "--alert" not in cmd, cmd
    assert "task r" in text


def test_a_finished_request_says_who_did_it_who_checked_it_and_where_the_result_is(tmp_path, monkeypatch):
    """Owner, 2026-11-06: "It should tell who did it." The report named only the
    assignee field ("nightshift", a writer name), not who did the work, who
    checked it, or where to look."""
    import actor_identity
    monkeypatch.setattr(actor_identity, "principals", lambda path=None: {
        "miles": {"writes_as": ["miles", "nightshift"]}, "winston": {}, "data": {}})
    done = req("d1", "verified")
    done.owner = "nightshift"                       # Miles's hourly run writes as nightshift
    done.payload["check"] = "test -s 1-tracks/research/note.md && grep -qi x 1-tracks/research/note.md"
    done.history.append(f"{NOW}.0000.winston winston item.verify: applied")
    code = req("c1", "verified")
    code.owner = "nightshift"
    code.payload["runner"] = "nightshift"
    code.payload["spec"] = {"properties": {"SURFACE": "datacore-one/datacore"}}
    code.history.append(f"{NOW}.0000.winston winston item.verify: applied")
    waiting = req("w1", "completed")
    waiting.owner = "data"
    sent = []
    dw.report_requests({"2-datacore": NS(items={"d1": done, "c1": code, "w1": waiting})},
                       state_path=tmp_path / "s.json", send=lambda t: sent.append(t) or True)
    text = sent[0]
    d1 = next(l for l in text.splitlines() if "task d1" in l)
    assert "Done by Miles" in d1 and "checked by Winston" in d1, d1
    assert "1-tracks/research/note.md" in d1, d1
    assert "[2-datacore d1]" in d1, d1                      # no writer name in the brackets
    c1 = next(l for l in text.splitlines() if "task c1" in l)
    assert "agent/c1" in c1 and "datacore-one/datacore" in c1, c1
    w1 = next(l for l in text.splitlines() if "task w1" in l)
    assert "Done by Data" in w1 and "not yet checked" in w1, w1


# ── An agent that paused itself (issue #296, owner 2026-11-06) ────────────────
# Tris paused her claiming at 17:21 after OpenRouter said "out of credit"; at
# 21:11 Winston filed the owner's request for her and said "Tris has it", and
# nothing told the owner she could not start. The pause is in the ledger: an
# item.release by the agent whose error is a login, credit or usage-limit
# failure, with no claim or completion after it.

CREDIT = ("hermes: hermes exited 1: hermes_oneshot: incomplete provider result; "
          "Error code: 402 - {'error': {'message': 'This request would exceed your available credits'")


def ev(actor, etype, at, error=None, iid="x"):
    payload = {"id": iid}
    if error is not None:
        payload["error"] = error
    return NS(actor=actor, type=etype, hlc=f"{at}.0000.{actor}", payload=payload)


def test_an_agent_whose_last_attempt_failed_on_credit_is_paused_with_its_retry_time():
    events = [ev("tris", "item.claim", NOW - 5 * H), ev("tris", "item.release", NOW - 4 * H, CREDIT),
              ev("data", "item.release", NOW - 4 * H, "check failed: wc -l says 2")]
    p = dw.pauses_from(events)
    assert set(p) == {"tris"}                               # an ordinary failure is not a pause
    assert p["tris"]["since_ms"] == NOW - 4 * H
    assert p["tris"]["retry_ms"] == NOW - 4 * H + 6 * H     # claim_backoff probes after 6 h
    assert "credit" in p["tris"]["why"]


def test_a_usage_limit_is_a_pause_and_a_later_claim_or_completion_ends_it():
    limit = "claude: You've hit your weekly limit"
    assert "usage limit" in dw.pauses_from([ev("miles", "item.release", NOW - H, limit)])["miles"]["why"]
    assert dw.pauses_from([ev("tris", "item.release", NOW - 4 * H, CREDIT),
                           ev("tris", "item.claim", NOW - H)]) == {}
    assert dw.pauses_from([ev("tris", "item.release", NOW - 4 * H, CREDIT),
                           ev("tris", "item.complete", NOW - H)]) == {}


def test_the_pause_counts_from_the_first_of_the_failures_in_a_row():
    p = dw.pauses_from([ev("tris", "item.release", NOW - 9 * H, CREDIT),
                        ev("tris", "item.release", NOW - 3 * H, CREDIT)])["tris"]
    assert p["since_ms"] == NOW - 9 * H and p["retry_ms"] == NOW - 3 * H + 6 * H


def test_who_has_what_leads_with_the_agent_that_cannot_take_work():
    rows = dw.who_has_what({"2-datacore": NS(items={"a": item("a", assignee="tris")})}, now_ms=NOW)
    paused = {"tris": {"since_ms": NOW - 4 * H, "retry_ms": NOW + 2 * H, "why": "out of model credit"}}
    text = dw.who_text(rows, {}, pauses=paused)
    first = text.splitlines()[0]
    assert "Tris" in first and "cannot take work" in first and "out of model credit" in first
    assert "retries" in first
    assert "Tris" not in dw.who_text(rows, {}).splitlines()[0]      # no pause, no line


def test_the_owner_is_told_once_when_a_request_waits_on_a_paused_agent(tmp_path):
    folded = {"2-datacore": NS(items={"r": req("r", "created", assignee="tris")})}
    paused = {"tris": {"since_ms": NOW - 4 * H, "retry_ms": NOW + 2 * H, "why": "out of model credit"}}
    st, sent = tmp_path / "s.json", []
    out = dw.report_requests(folded, now_ms=NOW, state_path=st, pauses=paused,
                             send=lambda t: sent.append(t) or True)
    assert out["reported"] == ["2-datacore/r"]
    text = "\n".join(sent)
    assert "task r" in text and "Tris" in text and "cannot take work" in text and "out of model credit" in text
    sent.clear()
    dw.report_requests(folded, now_ms=NOW, state_path=st, pauses=paused, send=lambda t: sent.append(t) or True)
    assert not any("cannot take work" in t for t in sent)          # once


# ── Issue #298: a check must test what the result says (2026-11-07) ──────────

WEAK_1106 = ("test -s 1-tracks/research/2026-11-06-tris-data-task-logging-comparison.md && "
             "grep -qi tris 1-tracks/research/2026-11-06-tris-data-task-logging-comparison.md && "
             "grep -qi data 1-tracks/research/2026-11-06-tris-data-task-logging-comparison.md")
REQ = {"id": "r1", "assignee": "tris", "asked_by": "gregor", "route": "do",
       "title": "Write a short note comparing how Tris and Data each log their completed tasks"}


def test_the_check_from_2026_11_06_is_refused_and_told_how_to_fix_it():
    why = dw.check_problem({**REQ, "check": WEAK_1106})
    assert why and "tris" in why and "data" in why
    assert "grep -c" in why or "example" in why.lower()


def test_an_owner_request_without_a_check_is_refused():
    assert dw.check_problem(dict(REQ))


def test_two_specific_greps_pass():
    f = "1-tracks/research/2026-11-07-x.md"
    ok = f"test -s {f} && grep -qi 'item.complete' {f} && grep -qi 'journal' {f}"
    assert dw.check_problem({**REQ, "check": ok}) is None


def test_a_count_compared_with_a_number_is_a_content_assertion():
    f = "1-tracks/research/2026-11-07-x.md"
    ok = (f"test -s {f} && [ \"$(grep -c '^- ' {f})\" -ge 3 ] && grep -q 'ledger event' {f}")
    assert dw.check_problem({**REQ, "check": ok}) is None


def test_one_content_assertion_is_not_enough():
    f = "1-tracks/research/2026-11-07-x.md"
    assert dw.check_problem({**REQ, "check": f"test -s {f} && grep -q 'ledger event' {f}"})


def test_a_pull_request_search_counts_as_proof():
    ok = ("curl -fsS 'https://api.github.com/search/issues?q=repo:o/r+is:pr+is:open+fix-x+in:title' "
          "| grep -q '\"total_count\": [1-9]'")
    assert dw.check_problem({**REQ, "assignee": "nightshift", "check": ok}) is None


def test_items_not_asked_by_the_owner_are_left_alone():
    p = {k: v for k, v in REQ.items() if k != "asked_by"}
    assert dw.check_problem({**p, "check": WEAK_1106}) is None


# ── 2026-11-07 rerun: variants of title words, and HTML-escaped checks ───────

def test_variants_of_title_words_do_not_count_as_content():
    p = {"asked_by": "gregor", "assignee": "miles",
         "title": "Write incident note: agents' server outage (disk full, downtime, two prevention measures)",
         "check": ("f=1-tracks/ops/x.md; grep -qiE \"disk\" \"$f\" && grep -qiE \"(down|downtime|outage|offline)\" \"$f\" "
                   "&& grep -qiE \"(prevent|mitigat|avoid|stop it)\" \"$f\"")}
    assert dw.check_problem(p)


def test_an_html_escaped_check_is_unescaped():
    assert dw.clean_check("test -s f &amp;&amp; grep -q 'a&lt;b' f") == "test -s f && grep -q 'a<b' f"


# ── INS-3: the names this check knows come from the install's registry ───────

def _registry(tmp_path, monkeypatch):
    import actor_identity
    p = tmp_path / "principals.yaml"
    p.write_text("version: 1\nprincipals:\n"
                 "  boss: {kind: human, display: Boss, decision: final}\n"
                 "  aide: {kind: agent, display: Aide, role: chief of staff}\n"
                 "  helper: {kind: agent, display: Helper}\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", p)


def test_a_grep_for_any_registered_agent_name_is_not_content(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch)
    f = "1-tracks/research/x.md"
    p = {"asked_by": "boss", "assignee": "aide", "title": "Write a note on the ledger",
         "check": f"test -s {f} && grep -qi helper {f} && grep -qi boss {f}"}
    assert dw.check_problem(p)


def test_the_request_report_is_headed_with_the_chief_of_staffs_name(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch)
    assert dw._assistant() == "Aide"


def test_without_a_registry_the_report_names_nobody(tmp_path, monkeypatch):
    import actor_identity
    monkeypatch.setattr(actor_identity, "PRINCIPALS", tmp_path / "missing.yaml")
    assert dw._assistant() == "your assistant"
    assert dw._agent_names() == {"nightshift"}
