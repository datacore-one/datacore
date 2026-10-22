"""Runtime state under .datacore/state/ is not a suite.

.datacore/state/ is gitignored runtime state. On 2026-10-22 it held a trial
checkout from the history rewrite (state/leak-cleanup-2026-10-16/trial), and the
suite audit ran its 11 test directories as if they were Datacore's own: about a
quarter of the on-arrival run's CPU spent re-testing a stale copy, right after
boot. A directory named `state` elsewhere is still discovered.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import suite_audit  # noqa: E402


def test_tests_under_runtime_state_are_not_discovered(tmp_path, monkeypatch):
    dc = tmp_path / ".datacore"
    for d in ("lib/tests", "state/backup/trial/.datacore/lib/tests", "modules/x/state/tests"):
        (dc / d).mkdir(parents=True)
        (dc / d / "test_a.py").write_text("def test_a():\n    pass\n")
    monkeypatch.setattr(suite_audit, "DATACORE", dc)
    found = {p.relative_to(dc).as_posix() for p in suite_audit.discover_suites()}
    assert found == {"lib/tests", "modules/x/state/tests"}, found
