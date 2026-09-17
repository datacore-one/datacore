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


WEEKLY_RAN = """name: plur
stage: growth
roles:
  cto:
    cadences:
      weekly: [sprint-rollover]
"""


def _ran_on(tmp_path, day):
    space = tmp_path / "5-plur"
    space.mkdir()
    (space / "venture.yaml").write_text(WEEKLY_RAN)
    log = space / ".datacore" / "state" / "venture" / "cadence-log.yaml"
    log.parent.mkdir(parents=True)
    log.write_text(f"cto.sprint-rollover:\n  last_run: '{day}'\n  result: ok\n")


def test_a_weekly_cadence_is_not_overdue_on_the_day_it_falls_due(tmp_path):
    """2026-09-17: last run 09-10, due 09-17, reported "7d overdue" at 07:40Z.

    The engine's days_overdue is days since the last run. The grace is days
    PAST DUE, so on the due date there is nothing to alert on yet.
    """
    _ran_on(tmp_path, "2026-09-10")
    assert L.collect(tmp_path, grace=3, today=datetime.date(2026, 9, 17)) == []
    assert L.collect(tmp_path, grace=3, today=datetime.date(2026, 9, 20)) == [], "3 past due is within grace"


def test_a_weekly_cadence_past_its_grace_is_still_overdue(tmp_path):
    _ran_on(tmp_path, "2026-09-10")
    rows = L.collect(tmp_path, grace=3, today=datetime.date(2026, 9, 21))
    assert [(r[0], r[4]) for r in rows] == [(4, "sprint-rollover")], "reported as days past due"
