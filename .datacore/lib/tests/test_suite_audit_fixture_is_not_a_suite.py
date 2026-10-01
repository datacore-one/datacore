"""A test project kept as an eval fixture is not a suite.

The dev module's audit calibration keeps two small practice projects with
planted defects (modules/dev/evals/audit/fixture, fixture2). Their tests belong
to the practice project: they are read by auditors, never run as Datacore's own
tests. The suite audit collected them as suites, so mac-suite-audit reported
4 errors there every day (2026-09-26 .. 10-01) and stayed red for something that
was never broken. A directory declared in config/ungated-test-suites.yaml with
`state: fixture` is not discovered as a suite; anything else still is.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import suite_audit  # noqa: E402


def _tree(tmp_path):
    dc = tmp_path / ".datacore"
    for d in ("lib/tests", "modules/dev/evals/audit/fixture/base/tests", "modules/x/tests"):
        (dc / d).mkdir(parents=True)
        (dc / d / "test_a.py").write_text("def test_a():\n    pass\n")
    (dc / "config").mkdir()
    return dc


def test_a_declared_fixture_is_not_discovered_as_a_suite(tmp_path, monkeypatch):
    dc = _tree(tmp_path)
    (dc / "config" / "ungated-test-suites.yaml").write_text(
        "version: 1\nsuites:\n"
        "  - path: .datacore/modules/dev/evals/audit/fixture\n"
        "    reason: planted-defect practice project\n    state: fixture\n"
        "  - path: .datacore/modules/x/tests\n    reason: not in CI yet\n    state: green\n")
    monkeypatch.setattr(suite_audit, "DATACORE", dc)
    found = {p.relative_to(dc).as_posix() for p in suite_audit.discover_suites()}
    assert found == {"lib/tests", "modules/x/tests"}, found


def test_without_a_declaration_every_test_directory_is_a_suite(tmp_path, monkeypatch):
    dc = _tree(tmp_path)
    monkeypatch.setattr(suite_audit, "DATACORE", dc)
    found = {p.relative_to(dc).as_posix() for p in suite_audit.discover_suites()}
    assert "modules/dev/evals/audit/fixture/base/tests" in found


def test_this_install_declares_the_dev_eval_fixtures():
    found = {p.relative_to(suite_audit.DATACORE).as_posix() for p in suite_audit.discover_suites()}
    assert not [f for f in found if "/evals/audit/fixture" in f], found
