import importlib.util, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("idc", ROOT / ".datacore" / "lib" / "detectors" / "id_churn.py")
C = importlib.util.module_from_spec(spec); spec.loader.exec_module(C)


def test_baseline_hides_acknowledged_damage_but_never_growth_or_duplicates(capsys):
    findings = [
        {"space": "0-personal", "duplicates": 0, "examples": [], "orphaned_ledger_ids": 360},
        {"space": "2-datacore", "duplicates": 0, "examples": [], "orphaned_ledger_ids": 300},
        {"space": "5-plur", "duplicates": 2, "examples": ["a"], "orphaned_ledger_ids": 0},
    ]
    out = C.apply_baseline(findings, {"0-personal": 360, "2-datacore": 271, "_acknowledged": "2026-09-03"})
    by = {r["space"]: r for r in out}
    assert "0-personal" not in by, "exactly the acknowledged amount is not a finding"
    assert by["2-datacore"]["orphaned_ledger_ids"] == 29, "growth above the baseline is"
    assert by["5-plur"]["duplicates"] == 2, "duplicates are the trigger and are never acknowledged"
    assert "acknowledged" in capsys.readouterr().out


def test_no_baseline_changes_nothing():
    f = [{"space": "x", "duplicates": 0, "examples": [], "orphaned_ledger_ids": 5}]
    assert C.apply_baseline(f, {}) == f


def test_acknowledging_one_root_keeps_the_other_roots_baseline():
    # One baseline file per machine; plur-claw scans ~/Data and ~/spaces
    # separately. Acknowledging the second root used to rebuild the file from
    # empty and erase the first root's acknowledged ids (2026-09-23).
    existing = {"1-datacore-space": ["a", "b"], "8-firm": ["c"], "_acknowledged": "2026-09-23"}
    findings = [{"space": "5-plur", "orphaned_ledger_ids": 2, "orphaned_ids": ["x", "y"]}]
    base = C.acknowledged_baseline(existing, findings, scanned=["5-plur"], today="2026-09-24")
    assert base["1-datacore-space"] == ["a", "b"] and base["8-firm"] == ["c"]
    assert base["5-plur"] == ["x", "y"] and base["_acknowledged"] == "2026-09-24"


def test_acknowledging_a_root_resets_only_its_own_spaces():
    existing = {"8-firm": ["c"], "5-plur": ["old"], "_acknowledged": "2026-09-23"}
    base = C.acknowledged_baseline(existing, [], scanned=["5-plur"], today="2026-09-24")
    assert "5-plur" not in base, "a scanned space with no orphans left has nothing acknowledged"
    assert base["8-firm"] == ["c"]


def test_a_legacy_count_baseline_is_not_carried_into_an_id_baseline():
    existing = {"0-personal": 360, "2-datacore": 271, "_acknowledged": "2026-09-03"}
    base = C.acknowledged_baseline(existing, [], scanned=["0-personal"], today="2026-09-24")
    assert not any(isinstance(v, int) for v in base.values())
