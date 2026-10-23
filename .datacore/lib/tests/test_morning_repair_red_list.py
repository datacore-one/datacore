"""W2 (2026-10-23): the morning sweep's red list tells the truth.

The briefing said "in repair: miles is on it" for a rewound ledger log and for a
venture heartbeat refusing an invalid hypothesis board for 21 days -- faults only
the owner can clear -- and "waiting on quota" for runs that failed before any
model call. Nothing alerted although a scoreboard printed an error for 10 days.

Done when: owner-only faults say "needs the owner", never "in repair"; a usage
limit says "waiting on the usage limit" and is not handed out as a repair; the red
list is one list sorted by days red; anything red for more than 24 hours raises one
alert per day through the existing alert path.
"""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

import morning_repair as M

STALE = ("StaleLogError: miles.jsonl ends at seq 22330 but this machine already wrote seq 22332 "
         "(appends stopped since 2026-10-22T06:18:14Z)")

OWNER_ONLY = [
    ("stale-log", {"id": "cadence-meridian-ops-backup", "kind": "cadence",
                   "title": "cadence meridian operations daily state-backup [late: miles (last failed)] is red",
                   "evidence": "2 day(s) late (daily) | run log: " + STALE}),
    ("venture-data", {"id": "heartbeat-meridian", "kind": "heartbeat",
                      "title": "venture heartbeat meridian failed: sense_error",
                      "evidence": "last tick 2026-10-22T07:58:15Z: sense_error"}),
    ("venture-data", {"id": "escalation-venture-heartbeat", "kind": "escalation",
                      "title": "job venture-heartbeat: repair gave up",
                      "evidence": "invalid hypothesis board: hypotheses.yaml line 12"}),
    ("login", {"id": "unit-cos-briefing", "kind": "unit", "title": "unit cos-briefing.service failed",
               "evidence": "claude exited 1: Not logged in · Please run /login"}),
]


def _day():
    return datetime.now(timezone.utc).date().isoformat()


def _prev(day, n):
    return (date.fromisoformat(day) - timedelta(days=n)).isoformat()


@pytest.mark.parametrize("cls,f", OWNER_ONLY, ids=[c for c, _ in OWNER_ONLY])
def test_owner_only_faults_come_from_one_table(cls, f):
    assert M.fault_class(f) == cls
    assert M.FAULT_CLASSES[cls]["owner"] is True


def test_usage_limit_is_its_own_class_and_not_owner_only():
    f = {"id": "unit-x", "kind": "unit", "title": "unit x failed",
         "evidence": "You've hit your weekly limit · resets Oct 27"}
    assert M.fault_class(f) == "usage-limit"
    assert M.FAULT_CLASSES["usage-limit"]["owner"] is False
    assert M.fault_class({"id": "v2-x", "kind": "v2", "title": "v2-verify: egress", "evidence": "2 undeclared"}) == ""


def _sweep(tmp_path, monkeypatch, found):
    monkeypatch.setattr(M, "STATE", tmp_path / "state")
    monkeypatch.setattr(M, "pull_latest", lambda: "fleet sync rc 0")
    monkeypatch.setattr(M, "findings", lambda run_v2=True: [dict(f) for f in found])
    monkeypatch.setattr(M, "remediate", lambda f: "")
    delegated = []
    monkeypatch.setattr(M, "delegate", lambda f, day: delegated.append(f["id"]) or f"repair-{f['id']}")
    M.sweep()
    return delegated


def _recheck(tmp_path, monkeypatch, now_findings):
    monkeypatch.setattr(M, "STATE", tmp_path / "state")
    monkeypatch.setattr(M, "FRAGMENTS", tmp_path / "frag")
    monkeypatch.setattr(M, "_pull_request", lambda iid: (True, ""))
    monkeypatch.setattr(M, "_dead_lettered", lambda iid: False)
    monkeypatch.setattr(M, "findings", lambda run_v2=True: [dict(f) for f in now_findings])
    sent = []
    monkeypatch.setattr(M, "_alert_group", sent.append)
    M.recheck()
    return json.loads((tmp_path / "frag" / _day() / "repairs.json").read_text()), sent


def test_owner_only_and_usage_limit_faults_are_never_delegated_or_in_repair(tmp_path, monkeypatch):
    usage = {"id": "unit-y", "kind": "unit", "title": "unit y failed",
             "evidence": "claude exited 1: You've hit your weekly limit"}
    plain = {"id": "v2-egress", "kind": "v2", "title": "v2-verify: egress", "evidence": "2 undeclared"}
    found = [f for _, f in OWNER_ONLY] + [usage, plain]
    delegated = _sweep(tmp_path, monkeypatch, found)
    assert delegated == ["v2-egress"], "only a fault an agent can repair is handed out as a repair"
    frag, _ = _recheck(tmp_path, monkeypatch, found)
    needs = {s["title"]: s["needs"] for s in frag["still_failing"]}
    for _, f in OWNER_ONLY:
        assert needs[f["title"]].startswith("needs the owner"), needs[f["title"]]
        assert "in repair" not in needs[f["title"]]
    assert needs[usage["title"]].startswith("waiting on the usage limit")
    assert needs[plain["title"]].startswith("in repair")


def test_an_owner_only_fault_with_an_old_repair_item_is_still_not_in_repair(tmp_path, monkeypatch):
    """A repair item filed before the fault was classed does not make it Miles's."""
    (tmp_path / "state").mkdir()
    f = dict(OWNER_ONLY[0][1], item="repair-cadence-meridian-ops-backup-20261022")
    (tmp_path / "state" / f"{_day()}.json").write_text(json.dumps({"swept_at": 0, "findings": [f]}))
    frag, _ = _recheck(tmp_path, monkeypatch, [OWNER_ONLY[0][1]])
    assert frag["still_failing"][0]["needs"].startswith("needs the owner")


def test_a_dead_lettered_repair_is_not_on_it(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    f = {"id": "v2-egress", "kind": "v2", "title": "v2-verify: egress", "item": "repair-v2-egress"}
    (tmp_path / "state" / f"{_day()}.json").write_text(json.dumps({"swept_at": 0, "findings": [f]}))
    monkeypatch.setattr(M, "STATE", tmp_path / "state")
    monkeypatch.setattr(M, "FRAGMENTS", tmp_path / "frag")
    monkeypatch.setattr(M, "_pull_request", lambda iid: (True, ""))
    monkeypatch.setattr(M, "_dead_lettered", lambda iid: True)
    monkeypatch.setattr(M, "findings", lambda run_v2=True: [{k: v for k, v in f.items() if k != "item"}])
    monkeypatch.setattr(M, "_alert_group", lambda text: None)
    M.recheck()
    frag = json.loads((tmp_path / "frag" / _day() / "repairs.json").read_text())
    assert frag["still_failing"][0]["needs"].startswith("needs the owner")


def test_the_red_list_is_one_list_sorted_by_days_red(tmp_path, monkeypatch):
    day = _day()
    st = tmp_path / "state"
    st.mkdir()
    old = {"id": "v2-old", "kind": "v2", "title": "v2-verify: old"}
    mid = {"id": "unit-mid", "kind": "unit", "title": "unit mid failed"}
    new = {"id": "v2-new", "kind": "v2", "title": "v2-verify: new"}
    cad = {"id": "cadence-x", "kind": "cadence", "title": "cadence x is red", "evidence": "5 day(s) late (daily)"}
    for n in (1, 2, 3):
        (st / f"{_prev(day, n)}.json").write_text(json.dumps({"swept_at": 0, "findings": [old] + ([mid] if n == 1 else [])}))
    (st / f"{day}.json").write_text(json.dumps({"swept_at": 0, "findings": [new, mid, old, cad]}))
    frag, _ = _recheck(tmp_path, monkeypatch, [new, mid, old, cad])
    assert "still_failing" in frag and not any(k for k in frag if k.startswith("still_failing_"))
    assert [(s["title"], s["days_red"]) for s in frag["still_failing"]] == [
        ("cadence x is red", 5), ("v2-verify: old", 3), ("unit mid failed", 1), ("v2-verify: new", 0)]


def test_red_over_24_hours_alerts_once_a_day_even_while_in_repair(tmp_path, monkeypatch):
    """Over budget yesterday (no item), handed over tonight: in repair, but red two days."""
    day = _day()
    st = tmp_path / "state"
    st.mkdir()
    f = {"id": "v2-egress", "kind": "v2", "title": "v2-verify: egress"}
    (st / f"{_prev(day, 1)}.json").write_text(json.dumps({"swept_at": 0, "findings": [dict(f, over_budget=True)]}))
    (st / f"{_prev(day, 2)}.json").write_text(json.dumps({"swept_at": 0, "findings": [f]}))
    (st / f"{day}.json").write_text(json.dumps({"swept_at": 0, "findings": [dict(f, item="repair-v2-egress")]}))
    _, sent = _recheck(tmp_path, monkeypatch, [f])
    assert len(sent) == 1 and "v2-verify: egress" in sent[0] and "2 day" in sent[0]
    _, again = _recheck(tmp_path, monkeypatch, [f])
    assert again == [], "a second re-check the same day does not alert again"


def test_a_fault_red_under_24_hours_and_in_repair_does_not_alert(tmp_path, monkeypatch):
    st = tmp_path / "state"
    st.mkdir()
    f = {"id": "v2-egress", "kind": "v2", "title": "v2-verify: egress"}
    (st / f"{_day()}.json").write_text(json.dumps({"swept_at": 0, "findings": [dict(f, item="repair-v2-egress")]}))
    _, sent = _recheck(tmp_path, monkeypatch, [f])
    assert sent == []


def test_a_venture_heartbeat_data_error_is_a_finding(tmp_path, monkeypatch):
    sp = tmp_path / "6-meridian" / ".datacore" / "state"
    sp.mkdir(parents=True)
    (sp / "heartbeat.json").write_text(json.dumps({"venture": "6-meridian", "last_status": "error",
                                                   "last_error": "sense_error", "last_fire": "2026-10-22T07:58:15Z"}))
    ok = tmp_path / "5-plur" / ".datacore" / "state"
    ok.mkdir(parents=True)
    (ok / "heartbeat.json").write_text(json.dumps({"venture": "5-plur", "last_status": "ok", "last_error": None}))
    monkeypatch.setattr(M, "ROOT", tmp_path)
    found = M.venture_heartbeats()
    assert [f["id"] for f in found] == ["heartbeat-6-meridian"]
    assert M.fault_class(found[0]) == "venture-data"


# ---- cadence liveness: "waiting on quota" only while the usage limit is current ----------

def _state(tmp_path, monkeypatch, ends):
    """The duty last ran properly 12 days ago; `ends` are the stops since."""
    import cadence_liveness as L
    from cadence_schedule import slug
    sl = slug("meridian", "operations", "state-backup")
    ok = (datetime.now(timezone.utc) - timedelta(days=12), {"result": "ok", "artifact": "a.md", "sha256": "x"})
    monkeypatch.setattr(L, "_artifact_in_git", lambda space, rel, sha: True)
    monkeypatch.setattr(L, "_attests", lambda space, owner, metric: (
        [] if metric == "cadence.registration" else [(t, {"slug": sl, "phase": "end", **p}) for t, p in [ok, *ends]]))
    return L.scheduled_state(tmp_path, "meridian", "miles", "operations", "daily", "state-backup",
                             date.today(), executing=True)


def test_an_old_usage_limit_with_no_run_since_is_not_waiting_on_quota(tmp_path, monkeypatch):
    """The runs since failed before any model call (a ledger refusal): nothing was recorded."""
    now = datetime.now(timezone.utc)
    st = _state(tmp_path, monkeypatch, [(now - timedelta(days=6), {"result": "quota", "reason": "usage limit"})])
    assert st[0] == "red"
    assert "waiting on" not in st[2] and "no run recorded since" in st[2], st


def test_a_usage_limit_hit_every_day_still_says_waiting_on_quota(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    st = _state(tmp_path, monkeypatch, [(now - timedelta(days=d, hours=2), {"result": "quota", "reason": "usage limit"})
                              for d in range(10, -1, -1)])
    assert st[0] == "red" and "waiting on quota" in st[2], st
