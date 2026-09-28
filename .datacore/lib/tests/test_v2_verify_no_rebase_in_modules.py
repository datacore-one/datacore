"""v2_verify's "no rebase in sync paths" check reads the modules too.

On 2026-09-27 the rebase that paused in the box's 0-personal came from
modules/ventures/lib/cadence_run.py (`git pull --rebase --autostash` after a
refused push). The check only read .datacore/lib, so it had been green the whole
time the call was live.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import v2_verify as V  # noqa: E402


def _check(tmp_path, monkeypatch, rel: str, text: str):
    lib = tmp_path / ".datacore" / "lib"
    lib.mkdir(parents=True)
    (lib / "ledger_transport.py").write_text("# merge, never rebase\n")
    f = tmp_path / ".datacore" / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    monkeypatch.setattr(V, "LIB", lib)
    rep = V.Report()
    V.check_transport(rep)
    return next(c for c in rep.checks if c.name == "no rebase in sync paths")


def test_a_rebase_in_a_module_lib_fails_the_check(tmp_path, monkeypatch):
    c = _check(tmp_path, monkeypatch, "modules/ventures/lib/cadence_run.py",
               'git("pull", "-q", "--rebase", "--autostash")\n')
    assert c.ok is False and "cadence_run.py" in c.detail


def test_a_rebase_in_a_module_server_script_fails_the_check(tmp_path, monkeypatch):
    c = _check(tmp_path, monkeypatch, "modules/x/server/lib/sync.sh", "git pull --rebase -q\n")
    assert c.ok is False and "sync.sh" in c.detail


def test_module_tests_and_comments_do_not_count(tmp_path, monkeypatch):
    c = _check(tmp_path, monkeypatch, "modules/x/lib/tests/test_y.py", 'git("pull", "--rebase")\n')
    assert c.ok is True
    c2 = _check(tmp_path / "b", monkeypatch, "modules/x/lib/y.py", "# never `git pull --rebase`\n")
    assert c2.ok is True


def test_python_prose_naming_the_anti_pattern_does_not_count(tmp_path, monkeypatch):
    """nightshift/lib/claim.py's docstring says "This was `git pull --rebase
    --autostash`" -- a record of what was removed, not a call."""
    c = _check(tmp_path, monkeypatch, "modules/nightshift/lib/claim.py",
               '    This was `git pull --rebase --autostash`, and it is the last writer\n')
    assert c.ok is True, c.detail
