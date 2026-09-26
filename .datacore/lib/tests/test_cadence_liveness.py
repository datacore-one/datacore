"""cadence_liveness counts commitments, not catalogues of parked ventures."""
import importlib.util, pathlib, datetime

ROOT = pathlib.Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("cl", ROOT / ".datacore" / "lib" / "cadence_liveness.py")
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)

VENTURE = """name: {name}
stage: {stage}
defaults:
  agent: miles
roles:
  operator:
    cadences:
      weekly: [competitor-scan]
"""


def test_archived_venture_contributes_no_overdue_rows(tmp_path):
    (tmp_path / "4-forge").mkdir(); (tmp_path / "4-forge" / "venture.yaml").write_text(VENTURE.format(name="forge", stage="archived"))
    (tmp_path / "5-plur").mkdir(); (tmp_path / "5-plur" / "venture.yaml").write_text(VENTURE.format(name="plur", stage="growth"))
    rows = L.collect(tmp_path, grace=3, today=datetime.date(2026, 9, 4))
    ventures = {r[1] for r in rows}
    assert "plur" in ventures, "a live venture with a never-run cadence is overdue (keyed by venture name)"
    assert "forge" not in ventures, "an archived venture is off, not overdue"


VENTURE_WITH_TRIS = """name: plur
stage: growth
defaults:
  agent: miles
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
    red, grey = L.collect_states(tmp_path, grace=3, today=datetime.date(2026, 9, 5))
    names = {(r[2], r[4].split(" [", 1)[0]) for r in red}
    assert ("cto", "release-check") in names, "our own never-run weekly cadence is overdue"
    assert not any(r[2] == "cio" for r in red), "Tris's cadence is not red while nothing here runs it"
    assert ("cio", "geo-sov-scan [pending-rollout: tris]") in {(r[2], r[4]) for r in grey}, \
        "but it is VISIBLE, with its owner (DIP-0050 P1: it used to vanish)"


WEEKLY_RAN = """name: plur
stage: growth
defaults:
  agent: miles
roles:
  cto:
    cadences:
      weekly: [sprint-rollover]
"""


def test_the_cadence_log_is_output_only(tmp_path):
    """CAD-3/4: Miles's duties were judged from cadence-log.yaml, which the agent
    writes; one typed line turned a never-run duty green. The log is output only:
    a fresh line with no signed run and artifact is still late. (The window and
    grace rules for signed runs are pinned in test_liveness_scheduled.)"""
    space = tmp_path / "5-plur"
    space.mkdir()
    (space / "venture.yaml").write_text(WEEKLY_RAN)
    log = space / ".datacore" / "state" / "venture" / "cadence-log.yaml"
    log.parent.mkdir(parents=True)
    log.write_text(f"cto.sprint-rollover:\n  last_run: '{datetime.date.today().isoformat()}'\n  result: ok\n")
    rows = L.collect(tmp_path, grace=3, today=datetime.date.today())
    assert [(r[1], r[4].split(" [", 1)[0]) for r in rows] == [("plur", "sprint-rollover")]


# ---- DIP-0050 P1: every assigned cadence has one state, keyed by venture name

def _v(tmp_path, body, members=None):
    sp = tmp_path / "5-plur"; sp.mkdir()
    (sp / "venture.yaml").write_text(body)
    if members is not None:
        (sp / ".datacore").mkdir()
        (sp / ".datacore" / "members.yaml").write_text("members: [" + ", ".join(members) + "]\n")
    return sp


def test_a_role_nobody_owns_is_red_not_silently_miles(tmp_path):
    _v(tmp_path, "name: plur\nstage: growth\nroles:\n  cto:\n    cadences:\n      weekly: [x]\n")
    red, _ = L.collect_states(tmp_path, 3, datetime.date(2026, 9, 5))
    assert len(red) == 1 and red[0][1] == "plur" and "ownership" in red[0][4]


def test_a_human_owned_cadence_is_a_reminder_never_red(tmp_path):
    _v(tmp_path, "name: plur\nstage: growth\nroles:\n  board:\n    agent: human\n    cadences:\n      weekly: [x]\n")
    red, grey = L.collect_states(tmp_path, 3, datetime.date(2026, 9, 5))
    assert red == [] and grey == [(0, "plur", "board", "weekly", "x [reminder: human]")]


def test_an_owner_who_is_not_a_member_of_the_space_is_not_held(tmp_path):
    _v(tmp_path, "name: plur\nstage: growth\nroles:\n  cio:\n    agent: tris\n    cadences:\n      weekly: [x]\n",
       members=["miles", "winston"])
    red, _ = L.collect_states(tmp_path, 3, datetime.date(2026, 9, 5))
    assert len(red) == 1 and "not-held: tris" in red[0][4]


def test_the_executor_alias_counts_as_membership(tmp_path):
    # 6-meridian lists `nightshift`, the identity Miles executes as.
    _v(tmp_path, "name: meridian\nstage: growth\ndefaults:\n  agent: miles\nroles:\n  ops:\n    cadences:\n      weekly: [x]\n",
       members=["nightshift"])
    red, _ = L.collect_states(tmp_path, 3, datetime.date(2026, 9, 5))
    assert not any("not-held" in r[4] for r in red)


