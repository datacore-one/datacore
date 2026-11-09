"""MEM-66: The overnight repair hands at most ten items a night to Miles. The rest are
listed for me, not dropped.

Kind: deterministic. The real morning_repair.sweep and morning_repair.recheck, with STATE
and FRAGMENTS in tmp; the collectors (findings), the safe remediation, the fleet pull,
the Miles hand-off (delegate) and the group alert are faked so nothing leaves the test.
  * 13 findings still failing -> exactly 10 handed to Miles;
  * the 03:30 fragment the briefing reads lists all 13 (the 3 over budget included, marked
    as needing a person). Owner decision 2026-09-27: "13 repaired items should be in
    report, Winston gives summary update" -- so the full list lives in the report, and a
    group alert, if one is sent, is a summary that states the count, not a 13-line list;
  * a second sweep the same night (a manual re-run, a restarted unit) does not hand Miles
    another ten: across the night no more than ten distinct items.

Seeded failure: MAX_ITEMS raised to 50 -> 13 delegated; verified red.
"""
import json

import morning_repair as M

N = M.MAX_ITEMS + 3


def _setup(tmp_path, monkeypatch, order=lambda xs: xs):
    monkeypatch.setattr(M, "STATE", tmp_path / "state")
    monkeypatch.setattr(M, "FRAGMENTS", tmp_path / "frag")
    monkeypatch.setattr(M, "pull_latest", lambda: "fleet sync rc 0")
    monkeypatch.setattr(M, "remediate", lambda f: "")
    many = [{"id": f"v2-check-{i:02d}", "kind": "v2", "title": f"v2-verify: check {i:02d}",
             "evidence": "FAIL"} for i in range(N)]
    state = {"order": order}
    monkeypatch.setattr(M, "findings", lambda run_v2=True: [dict(f) for f in state["order"](many)])
    handed = []
    monkeypatch.setattr(M, "delegate", lambda f, day: handed.append(f["id"]) or f"repair-{f['id']}")
    alerts = []
    monkeypatch.setattr(M, "_alert_group", alerts.append)
    return many, handed, alerts, state


def test_at_most_ten_go_to_miles_and_the_rest_are_listed(tmp_path, monkeypatch):
    many, handed, alerts, _ = _setup(tmp_path, monkeypatch)
    M.sweep()
    assert len(handed) <= 10, f"{len(handed)} repair items handed to Miles in one night"
    assert len(handed) == 10, f"only {len(handed)} handed although {N} were failing"
    M.recheck()
    day = M.datetime.now(M.timezone.utc).date().isoformat()
    frag = json.loads((tmp_path / "frag" / day / "repairs.json").read_text())
    listed = {r["title"] for r in frag["still_failing"]}
    missing = {f["title"] for f in many} - listed
    assert not missing, f"over-budget findings dropped from the owner's list: {sorted(missing)}"
    over = [r for r in frag["still_failing"] if not r.get("item")]
    assert len(over) == N - 10 and all(r["needs"].startswith("a person") for r in over), over
    for a in alerts:
        assert str(N) in a, f"the summary does not say how many are still failing: {a!r}"
        assert sum(f["title"] in a for f in many) < N, "the alert lists every item; the list belongs in the report"


def test_a_second_sweep_the_same_night_does_not_hand_over_ten_more(tmp_path, monkeypatch):
    _, handed, _, state = _setup(tmp_path, monkeypatch)
    M.sweep()
    state["order"] = lambda xs: list(reversed(xs))       # same failures, other order
    M.sweep()
    assert len(set(handed)) <= 10, (
        f"{len(set(handed))} distinct repair items handed to Miles in one night: the cap is per "
        "sweep invocation, not per night")
