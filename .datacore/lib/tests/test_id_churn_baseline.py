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
