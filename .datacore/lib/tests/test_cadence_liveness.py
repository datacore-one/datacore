"""cadence_liveness counts commitments, not catalogues of parked ventures."""
import importlib.util, pathlib, datetime

ROOT = pathlib.Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("cl", ROOT / ".datacore" / "lib" / "cadence_liveness.py")
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)

VENTURE = """name: {name}
stage: {stage}
roles:
  operator:
    cadences:
      weekly: [competitor-scan]
"""


def test_archived_venture_contributes_no_overdue_rows(tmp_path):
    (tmp_path / "4-forge").mkdir(); (tmp_path / "4-forge" / "venture.yaml").write_text(VENTURE.format(name="forge", stage="archived"))
    (tmp_path / "5-plur").mkdir(); (tmp_path / "5-plur" / "venture.yaml").write_text(VENTURE.format(name="plur", stage="growth"))
    rows = L.collect(tmp_path, grace=3, today=datetime.date(2026, 9, 4))
    spaces = {r[1] for r in rows}
    assert "5-plur" in spaces, "a live venture with a never-run cadence is overdue"
    assert "4-forge" not in spaces, "an archived venture is off, not overdue"
