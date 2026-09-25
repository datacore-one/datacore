"""The morning repair sweep: what it finds, what it may do, and what it reports."""
import json
from datetime import date

import morning_repair as M


def test_v2_fail_lines_become_findings(tmp_path, monkeypatch):
    log = tmp_path / "v2.log"
    log.write_text("  \x1b[32mok  \x1b[0m VERSION     2.0.0\n"
                   "    \x1b[31mFAIL\x1b[0m egress declared                  2 undeclared in opted-in modules\n")
    monkeypatch.setattr(M, "V2_LOG", log)
    found = M.v2_checklist(run=False)
    assert [f["id"] for f in found] == ["v2-egress-declared"]
    assert "2 undeclared" in found[0]["evidence"]


def test_mail_triage_is_judged_by_todays_clean_run(tmp_path, monkeypatch):
    log = tmp_path / "triage.log"
    monkeypatch.setattr(M, "TRIAGE_LOG", log)
    log.write_text("[email-triage] Completed at 2026-09-25T07:30:52Z — totals: scanned=117 archived=19 "
                   "processed=48 errors=0\n")
    assert M.mail_triage(date(2026, 9, 25)) == []
    log.write_text("[email-triage] ERROR: MAIL_TRIAGE_ACCOUNTS is not set -- no inbox was scanned\n")
    assert M.mail_triage(date(2026, 9, 25))[0]["id"] == "input-mail-triage"
    log.write_text("[email-triage] Completed at 2026-09-25T03:30:00Z — totals: scanned=0 archived=0 "
                   "processed=0 errors=2\n")
    assert M.mail_triage(date(2026, 9, 25)), "errors or an empty scan are not a clean run"


def test_sweep_remediates_then_delegates_only_what_is_still_failing(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "STATE", tmp_path / "state")
    monkeypatch.setattr(M, "pull_latest", lambda: "fleet sync rc 0")
    a = {"id": "unit-x", "kind": "unit", "unit": "x.service", "title": "unit x failed"}
    b = {"id": "v2-egress", "kind": "v2", "title": "v2-verify: egress"}
    calls = {"n": 0}

    def fake_findings(run_v2=True):
        calls["n"] += 1
        return [dict(a), dict(b)] if calls["n"] == 1 else [dict(b)]   # the re-run cleared x
    monkeypatch.setattr(M, "findings", fake_findings)
    monkeypatch.setattr(M, "remediate", lambda f: "re-ran x.service (rc 0)" if f["kind"] == "unit" else "")
    delegated = []
    monkeypatch.setattr(M, "delegate", lambda f, day: delegated.append(f["id"]) or f"repair-{f['id']}")
    assert M.sweep() == 0
    assert delegated == ["v2-egress"], "only what the safe remediation did not clear goes to miles"
    state = json.loads(next((tmp_path / "state").glob("*.json")).read_text())
    by = {f["id"]: f for f in state["findings"]}
    assert by["unit-x"]["cleared_by_sweep"] and not by["v2-egress"]["cleared_by_sweep"]


def test_the_budget_caps_delegations(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "STATE", tmp_path / "state")
    monkeypatch.setattr(M, "pull_latest", lambda: "fleet sync rc 0")
    many = [{"id": f"v2-{i}", "kind": "v2", "title": f"t{i}"} for i in range(M.MAX_ITEMS + 3)]
    monkeypatch.setattr(M, "findings", lambda run_v2=True: [dict(f) for f in many])
    monkeypatch.setattr(M, "remediate", lambda f: "")
    monkeypatch.setattr(M, "delegate", lambda f, day: f"repair-{f['id']}")
    M.sweep()
    state = json.loads(next((tmp_path / "state").glob("*.json")).read_text())
    assert sum(1 for f in state["findings"] if f.get("item")) == M.MAX_ITEMS
    assert sum(1 for f in state["findings"] if f.get("over_budget")) == 3


def test_recheck_reports_repaired_and_still_failing_and_alerts_only_for_the_latter(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "STATE", tmp_path / "state")
    monkeypatch.setattr(M, "FRAGMENTS", tmp_path / "frag")
    day = M.datetime.now(M.timezone.utc).date().isoformat()
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / f"{day}.json").write_text(json.dumps({"findings": [
        {"id": "unit-x", "kind": "unit", "title": "unit x failed", "tried": "re-ran x.service (rc 0)"},
        {"id": "v2-egress", "kind": "v2", "title": "v2-verify: egress", "item": "repair-v2-egress"}]}))
    monkeypatch.setattr(M, "findings", lambda run_v2=True: [{"id": "v2-egress", "kind": "v2",
                                                             "title": "v2-verify: egress", "evidence": "still 2"}])
    sent = []
    monkeypatch.setattr(M, "_alert_group", sent.append)
    M.recheck()
    frag = json.loads((tmp_path / "frag" / day / "repairs.json").read_text())
    assert [r["title"] for r in frag["repaired"]] == ["unit x failed"]
    assert frag["still_failing"][0]["item"] == "repair-v2-egress"
    assert frag["still_failing"][0]["needs"] == "review the pull request"
    assert len(sent) == 1 and "v2-verify: egress" in sent[0]


def test_a_sweep_that_did_not_run_is_the_first_thing_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "STATE", tmp_path / "state")
    monkeypatch.setattr(M, "FRAGMENTS", tmp_path / "frag")
    monkeypatch.setattr(M, "findings", lambda run_v2=True: [])
    monkeypatch.setattr(M, "_alert_group", lambda text: None)
    M.recheck()
    day = M.datetime.now(M.timezone.utc).date().isoformat()
    frag = json.loads((tmp_path / "frag" / day / "repairs.json").read_text())
    assert frag["still_failing"][0]["title"] == "the 02:00 sweep did not run"


def test_repairs_that_gave_up_are_findings_once_per_job(tmp_path, monkeypatch):
    log = tmp_path / "esc.log"
    log.write_text("autofix: 3 repair(s) need a person\n  old-job: miles gave up\n"
                   "autofix: 13 repair(s) need a person\n"
                   "  nightshift-overnight: miles gave up — gave up after 3 failed attempts\n"
                   "  nightshift-overnight: miles gave up — gave up after 3 failed attempts\n"
                   "  box-deploy-drift: miles gave up — gave up after 3 failed attempts\n")
    monkeypatch.setattr(M, "ESCALATIONS_LOG", log)
    found = M.escalations()
    assert [f["id"] for f in found] == ["escalation-box-deploy-drift", "escalation-nightshift-overnight"]
    assert all(f["kind"] == "escalation" for f in found)


def test_an_escalation_is_not_handed_back_to_miles(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "STATE", tmp_path / "state")
    monkeypatch.setattr(M, "pull_latest", lambda: "fleet sync rc 0")
    esc = {"id": "escalation-x", "kind": "escalation", "title": "job x: repair gave up"}
    monkeypatch.setattr(M, "findings", lambda run_v2=True: [dict(esc)])
    monkeypatch.setattr(M, "remediate", lambda f: "")
    delegated = []
    monkeypatch.setattr(M, "delegate", lambda f, day: delegated.append(f["id"]) or "x")
    M.sweep()
    assert delegated == []
