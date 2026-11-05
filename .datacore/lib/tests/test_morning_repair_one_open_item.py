"""One fault, one open item (owner, 2026-11-05).

"A check reports and alerts. It does not create a new task per failure per day,
and a repair closes itself when its re-check passes. Repair work must not crowd
out the owner's requests in an agent's queue."

Seen on 2026-11-05: Miles held 14 claimed repair items from the 04 Nov sweep
alone, each named repair-<fault>-<date>, beside the copies from earlier nights.
The sweep only avoided a second item within the same night.
"""
import json
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
import morning_repair as M  # noqa: E402

V2 = {"id": "v2-hash-chains", "kind": "v2", "title": "v2-verify: hash chains", "evidence": "FAIL"}


def run_sweep(tmp_path, monkeypatch, existing, findings=(V2,)):
    monkeypatch.setattr(M, "STATE", tmp_path / "state")
    monkeypatch.setattr(M, "FRAGMENTS", tmp_path / "frag")
    monkeypatch.setattr(M, "pull_latest", lambda: "ok")
    monkeypatch.setattr(M, "remediate", lambda f: "")
    monkeypatch.setattr(M, "findings", lambda run_v2=True: [dict(f) for f in findings])
    monkeypatch.setattr(M, "_repair_items", lambda: existing)
    monkeypatch.setattr(M, "_alert_group", lambda t: None)
    wrote = []
    import ledger.policy
    monkeypatch.setattr(ledger.policy, "guarded_append", lambda log, kind, payload: wrote.append((kind, payload)))
    import actor_identity
    monkeypatch.setattr(actor_identity, "this_actor", lambda: "winston")
    import jobs.autofix
    monkeypatch.setattr(jobs.autofix, "_space", lambda root: tmp_path / "space")
    return wrote


def test_a_fault_with_an_open_repair_item_gets_no_new_one(tmp_path, monkeypatch):
    existing = {"v2-hash-chains": [("repair-v2-hash-chains-20261104", "claimed")]}
    wrote = run_sweep(tmp_path, monkeypatch, existing)
    M.sweep()
    assert [k for k, _ in wrote] == [], wrote
    day = M.datetime.now(M.timezone.utc).date().isoformat()
    swept = json.loads((tmp_path / "state" / f"{day}.json").read_text())["findings"]
    assert swept[0]["item"] == "repair-v2-hash-chains-20261104"


def test_a_new_fault_gets_one_item_with_a_stable_id_and_its_fault(tmp_path, monkeypatch):
    wrote = run_sweep(tmp_path, monkeypatch, {})
    M.sweep()
    [(kind, payload)] = wrote
    assert kind == "item.create" and payload["id"] == "repair-v2-hash-chains"
    assert payload["fault"] == "v2-hash-chains"


def test_a_reused_item_does_not_spend_the_nights_budget(tmp_path, monkeypatch):
    many = [dict(V2, id=f"f{n}", title=f"fault {n}") for n in range(M.MAX_ITEMS + 3)]
    existing = {f"f{n}": [(f"repair-f{n}", "created")] for n in range(M.MAX_ITEMS + 3)}
    existing.pop("f0")
    wrote = run_sweep(tmp_path, monkeypatch, existing, findings=many)
    M.sweep()
    assert [p["fault"] for k, p in wrote if k == "item.create"] == ["f0"]


def test_the_recheck_closes_the_open_items_of_a_fault_that_cleared(tmp_path, monkeypatch):
    existing = {"v2-hash-chains": [("repair-v2-hash-chains-20261103", "claimed"),
                                   ("repair-v2-hash-chains-20261104", "created"),
                                   ("repair-v2-hash-chains-20261101", "dismissed")]}
    wrote = run_sweep(tmp_path, monkeypatch, existing)
    M.sweep()
    monkeypatch.setattr(M, "findings", lambda run_v2=True: [])      # cleared by 03:30
    M.recheck()
    closed = sorted(p["id"] for k, p in wrote if k == "item.dismiss")
    assert closed == ["repair-v2-hash-chains-20261103", "repair-v2-hash-chains-20261104"]
    assert all(p["kind"] == "done" and "re-check" in p["reason"] for k, p in wrote if k == "item.dismiss")


def test_a_fault_still_red_keeps_its_item_open(tmp_path, monkeypatch):
    existing = {"v2-hash-chains": [("repair-v2-hash-chains", "claimed")]}
    wrote = run_sweep(tmp_path, monkeypatch, existing)
    M.sweep()
    M.recheck()
    assert [k for k, _ in wrote if k == "item.dismiss"] == []


def test_legacy_dated_ids_are_read_as_their_fault():
    assert M.fault_of("repair-v2-hash-chains-20261104", {}) == "v2-hash-chains"
    assert M.fault_of("repair-v2-hash-chains", {"fault": "v2-hash-chains"}) == "v2-hash-chains"
    assert M.fault_of("repair-cadence-x-y-20261103", {}) == "cadence-x-y"
