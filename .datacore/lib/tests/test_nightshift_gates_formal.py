"""Pass implies evidence, for the small verdicts Lean refuted
(DatacoreSpec/NightshiftGates.lean). Each case below is a counterexample
that was replayed against the pre-fix code and passed where it should not.
"""
import datetime
import importlib.util
import pathlib
import sys

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

spec = importlib.util.spec_from_file_location("cl_formal", LIB / "cadence_liveness.py")
CL = importlib.util.module_from_spec(spec); spec.loader.exec_module(CL)

import ai_task_gate  # noqa: E402
import workflow_executor as wf  # noqa: E402
from delegation_requirements import execution_gaps  # noqa: E402

VENTURE = "name: plur\nstage: growth\nroles:\n  cto:\n    cadences:\n      weekly: [release-check]\n"


# ---- cadence_liveness: "0 cadence(s) overdue" must mean every venture was read

def test_an_unreadable_venture_is_not_zero_overdue(tmp_path):
    """Cadence.old_unreadable_passes: one bad YAML line turned a venture with an
    overdue cadence into a green contract."""
    (tmp_path / "5-plur").mkdir()
    (tmp_path / "5-plur" / "venture.yaml").write_text(VENTURE + "  bad: [unclosed\n")
    rows = CL.collect(tmp_path, 3, datetime.date(2026, 9, 21))
    assert len(rows) == 1 and rows[0][1] == "5-plur", rows
    assert "unreadable" in rows[0][4]


def test_an_unreadable_cadence_log_is_not_zero_overdue(tmp_path, monkeypatch):
    (tmp_path / "5-plur").mkdir()
    (tmp_path / "5-plur" / "venture.yaml").write_text(VENTURE)
    import cadence_engine

    def boom(_path):
        raise OSError("unreadable")
    monkeypatch.setattr(cadence_engine, "load_cadence_log_safe", boom)
    rows = CL.collect(tmp_path, 3, datetime.date(2026, 9, 21))
    assert len(rows) == 1 and "unreadable" in rows[0][4], rows


def test_a_space_that_is_not_a_venture_still_counts_nothing(tmp_path):
    (tmp_path / "0-personal").mkdir()
    assert CL.collect(tmp_path, 3, datetime.date(2026, 9, 21)) == []


# ---- ai_task_gate must mirror the executor (nightshift_parser._is_executable)

@pytest.mark.parametrize("surface", ["Unassigned", "UNASSIGNED", " unassigned "])
def test_the_gate_refuses_what_the_executor_will_not_run(surface):
    """AiGate.old_gate_passes_unrunnable: the executor lower-cases SURFACE."""
    props = {"SURFACE": surface, "DONE_WHEN": "x"}
    assert execution_gaps(props) == ["SURFACE"]
    assert ai_task_gate._missing(props, "0-personal"), "gate passed a task the executor skips"


def test_the_gate_accepts_what_the_executor_runs():
    props = {"SURFACE": "datacore", "ACCEPTANCE_CRITERIA": "tests pass"}
    assert execution_gaps(props) == []
    assert ai_task_gate._missing(props, "0-personal") == []


def test_the_gate_checks_every_tag_the_executor_queues(tmp_path):
    """AiGate.old_gate_misses_queued_tag: find_ai_tasks queues any tag starting
    with "AI"; the gate only looked for the exact tag "AI"."""
    org = tmp_path / "next_actions.org"
    org.write_text("* TODO research something :AIresearch:\n")
    assert ai_task_gate.main([str(org)]) == 1


def test_an_untagged_or_inherited_task_is_not_gated(tmp_path):
    org = tmp_path / "next_actions.org"
    org.write_text("* Project :AI:\n** TODO child inherits the tag\n* TODO plain :home:\n")
    assert ai_task_gate.main([str(org)]) == 0


# ---- workflow_executor: LIVE "completed" must not cover phases that never ran

@pytest.fixture
def no_state(monkeypatch):
    seen = []
    monkeypatch.setattr(wf, "_update_phase_state", lambda *a, **k: seen.append(a))
    return seen


def test_live_run_that_executed_nothing_is_not_completed(no_state):
    """Workflow.old_live_reports_completed (DIP-0022: this file is not proof
    that any tool executed)."""
    w = wf.Workflow({"name": "f", "phases": [{"name": "p1", "type": "tool", "invoke": "x"},
                                             {"name": "p2", "type": "agent", "invoke": "y"}]})
    res = wf.execute_workflow(w, {}, dry_run=False)
    assert res["overall"] == "not_executed"
    assert res["phases"] == {"p1": "not_executed", "p2": "not_executed"}


def test_condition_skips_do_not_block_completed(no_state):
    w = wf.Workflow({"name": "f", "phases": [{"name": "p1", "type": "tool", "condition": "absent"}]})
    assert wf.execute_workflow(w, {}, dry_run=False)["overall"] == "completed"


def test_dry_run_is_unchanged(no_state):
    w = wf.Workflow({"name": "f", "phases": [{"name": "p1", "type": "tool", "invoke": "x"}]})
    res = wf.execute_workflow(w, {}, dry_run=True)
    assert res["overall"] == "completed" and res["mode"] == "dry-run"


def test_a_stop_still_wins_over_not_executed(no_state):
    w = wf.Workflow({"name": "f", "phases": [{"name": "p1", "type": "tool"},
                                             {"name": "p2", "type": "tool", "stop_if": "go"}]})
    assert wf.execute_workflow(w, {"go": True}, dry_run=False)["overall"] == "stopped"
