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


VENTURE_WITH_TRIS = """name: plur
stage: growth
roles:
  cto:
    cadences:
      weekly: [release-check]
  cio:
    agent: tris
    cadences:
      weekly: [geo-sov-scan]
"""


def test_a_cadence_owned_by_an_external_agent_is_not_this_fleets_liveness(tmp_path):
    """5-plur's cio is Tris on hermes; its runs never land in our shards, so
    counting them made the box's contract red by construction (2026-09-05)."""
    (tmp_path / "5-plur").mkdir(); (tmp_path / "5-plur" / "venture.yaml").write_text(VENTURE_WITH_TRIS)
    rows = L.collect(tmp_path, grace=3, today=datetime.date(2026, 9, 5))
    names = {(r[2], r[4]) for r in rows}
    assert ("cto", "release-check") in names, "our own never-run weekly cadence is overdue (7 days past a 3-day grace)"
    assert ("cio", "geo-sov-scan") not in names, "Tris's cadence is Tris's liveness"
