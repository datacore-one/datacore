"""A directory of promise evals collects nothing until its promise is green.

Promise TSK-11's evals (.datacore/evals/ledger-upgrade/phase4) are written red
before any code, and promise_gate.py hides them from an ordinary run until the
promise is green in the committed baseline. So the suite audit ran the directory,
collected nothing, and called it "collected nothing" -- mac-suite-audit went red
on 2026-10-04 for a gate doing exactly its job. A directory declared in
config/ungated-test-suites.yaml with `state: promise-gated` is still run (once the
promise goes green its evals count like any other test), but an empty run there
is not counted against the verdict. An undeclared empty suite still is.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import suite_audit  # noqa: E402


def _declare(tmp_path, monkeypatch):
    dc = tmp_path / ".datacore"
    (dc / "config").mkdir(parents=True)
    (dc / "config" / "ungated-test-suites.yaml").write_text(
        "version: 1\nsuites:\n"
        "  - path: .datacore/evals/p/phase4\n"
        "    reason: promise evals, red by design\n    state: promise-gated\n")
    monkeypatch.setattr(suite_audit, "DATACORE", dc)


def test_an_empty_promise_gated_suite_is_not_counted_empty(tmp_path, monkeypatch):
    _declare(tmp_path, monkeypatch)
    gated = suite_audit.Result(suite="evals/p/phase4")
    other = suite_audit.Result(suite="modules/x/tests")
    assert suite_audit.empty_suites([gated, other]) == [other]


def test_a_promise_gated_suite_that_runs_tests_is_judged_normally(tmp_path, monkeypatch):
    _declare(tmp_path, monkeypatch)
    red = suite_audit.Result(suite="evals/p/phase4", passed=3, failed=1, rc=1)
    assert not red.empty and not red.ok


def test_this_install_declares_the_tsk11_evals_promise_gated():
    assert "evals/ledger-upgrade/phase4" in suite_audit._declared("promise-gated")
