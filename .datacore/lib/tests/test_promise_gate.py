"""The promise gate: CI runs a promise's evals only once the promise is green in
the committed baseline, so a red-by-design eval (evals first, not fixed yet)
does not break unrelated work, while a promise that regresses from green to
red fails the gate. The scoreboard (PROMISE_EVALS_ALL=1) always runs all.
"""
import json
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import promise_gate as G  # noqa: E402


def _baseline(tmp_path, monkeypatch, ids):
    b = tmp_path / "baseline.json"
    b.write_text(json.dumps({"green": ids}))
    monkeypatch.setattr(G, "BASELINE", b)
    monkeypatch.delenv("PROMISE_EVALS_ALL", raising=False)


def test_a_green_promise_is_gated(tmp_path, monkeypatch):
    _baseline(tmp_path, monkeypatch, ["TSK9"])
    assert not G.should_ignore(Path("tests/test_promise_TSK_9_decision_age_limit.py"))


def test_a_red_by_design_promise_is_left_to_the_scoreboard(tmp_path, monkeypatch):
    _baseline(tmp_path, monkeypatch, ["TSK9"])
    assert G.should_ignore(Path("tests/test_promise_NS6_one_problem_not_the_night.py"))


def test_the_scoreboard_runs_everything(tmp_path, monkeypatch):
    _baseline(tmp_path, monkeypatch, [])
    monkeypatch.setenv("PROMISE_EVALS_ALL", "1")
    assert not G.should_ignore(Path("tests/test_promise_NS6_one_problem_not_the_night.py"))


def test_ordinary_tests_are_never_touched(tmp_path, monkeypatch):
    _baseline(tmp_path, monkeypatch, [])
    assert not G.should_ignore(Path("tests/test_ledger_log.py"))


def test_no_baseline_means_no_promise_eval_is_gated(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "BASELINE", tmp_path / "missing.json")
    monkeypatch.delenv("PROMISE_EVALS_ALL", raising=False)
    assert G.should_ignore(Path("tests/test_promise_TSK_9_decision_age_limit.py"))
