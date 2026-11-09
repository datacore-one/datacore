"""One failing check is one line in the morning briefing (2026-11-01).

The 2026-11-01 briefing listed "v2-verify: every machine has an actor" 18 times and
"v2-verify: install is current" 17 times, in groups that grew down the list: the
v2 log the sweep read held many runs, every FAIL line of every run became a
finding, and neither the sweep nor the 03:30 re-check ever asked whether a finding
id was already in the list. Each copy was also delegated on its own, so the
night's ten-item budget went on copies of one or two findings and everything
after it read "a person: nothing could be delegated".
"""
from __future__ import annotations

import json

import morning_repair as M


def _run_block(stamp: str, fails: list[str]) -> str:
    lines = [f"=== 2026-10-{stamp} UTC — v2 verify on box ===", "", "  DIP-0044"]
    lines += [f"    \x1b[31mFAIL\x1b[0m {name:<32} detail of {name}" for name in fails]
    lines += ["    \x1b[32mok  \x1b[0m VERSION                          2.0.0", "v2-verify: exit 1"]
    return "\n".join(lines) + "\n"


def test_a_log_holding_many_runs_gives_each_check_once_from_the_last_run(tmp_path, monkeypatch):
    log = tmp_path / "v2.log"
    log.write_text(_run_block("30 18:00:01", ["every machine has an actor"])
                   + _run_block("31 06:00:01", ["every machine has an actor", "registry hygiene"])
                   + _run_block("31 18:00:01", ["every machine has an actor", "install is current"]))
    monkeypatch.setattr(M, "V2_LOG", log)
    found = M.v2_checklist(run=False)
    assert [f["id"] for f in found] == ["v2-every-machine-has-an-actor", "v2-install-is-current"], (
        "only the newest run is today's state, and each check appears once")


def test_the_same_fail_line_twice_in_one_run_is_one_finding(tmp_path, monkeypatch):
    log = tmp_path / "v2.log"
    log.write_text("    FAIL egress declared                  2 undeclared\n" * 3)
    monkeypatch.setattr(M, "V2_LOG", log)
    assert [f["id"] for f in M.v2_checklist(run=False)] == ["v2-egress-declared"]


def test_the_sweep_records_and_delegates_a_duplicated_finding_once(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "STATE", tmp_path / "state")
    monkeypatch.setattr(M, "pull_latest", lambda: "fleet sync rc 0")
    monkeypatch.setattr(M, "fault_class", lambda f: "")
    ids = ["v2-actor"] * 12 + ["v2-db", "v2-lens"]
    monkeypatch.setattr(M, "findings", lambda run_v2=True: [{"id": i, "kind": "v2", "title": i} for i in ids])
    monkeypatch.setattr(M, "remediate", lambda f: "")
    delegated = []
    monkeypatch.setattr(M, "delegate", lambda f, day: delegated.append(f["id"]) or f"repair-{f['id']}")
    M.sweep()
    state = json.loads(next((tmp_path / "state").glob("*.json")).read_text())
    assert [f["id"] for f in state["findings"]] == ["v2-actor", "v2-db", "v2-lens"]
    assert delegated == ["v2-actor", "v2-db", "v2-lens"], (
        "copies of one finding spent the night's budget, so the rest could not be delegated")


def test_the_recheck_lists_each_finding_once_even_from_a_state_with_copies(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "STATE", tmp_path / "state")
    monkeypatch.setattr(M, "FRAGMENTS", tmp_path / "frag")
    monkeypatch.setattr(M, "_alert_group", lambda text: None)
    monkeypatch.setattr(M, "_pull_request", lambda iid: (True, ""))
    monkeypatch.setattr(M, "_dead_lettered", lambda iid: False)
    monkeypatch.setattr(M, "fault_class", lambda f: "")
    day = M.datetime.now(M.timezone.utc).date().isoformat()
    (tmp_path / "state").mkdir()
    actor = {"id": "v2-actor", "kind": "v2", "title": "v2-verify: every machine has an actor"}
    hygiene = {"id": "v2-hygiene", "kind": "v2", "title": "v2-verify: registry hygiene"}
    (tmp_path / "state" / f"{day}.json").write_text(json.dumps({"findings": [
        {**actor, "item": "repair-v2-actor"}, dict(actor), dict(actor), dict(hygiene), dict(hygiene)]}))
    monkeypatch.setattr(M, "findings", lambda run_v2=True: [dict(actor, evidence="6/7"), dict(actor)])
    M.recheck()
    frag = json.loads((tmp_path / "frag" / day / "repairs.json").read_text())
    assert [f["title"] for f in frag["still_failing"]] == ["v2-verify: every machine has an actor"]
    assert frag["still_failing"][0]["item"] == "repair-v2-actor", "the delegated copy is the one kept"
    assert [r["title"] for r in frag["repaired"]] == ["v2-verify: registry hygiene"]
