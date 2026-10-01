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


def test_the_fleet_simulators_fault_injector_does_not_count(tmp_path, monkeypatch):
    """lib/sim/ is the fleet week simulator's stand-in model: it runs the bad
    command on purpose, to prove the guard stops it (2026-09-30: its stand_in.py
    turned the box's 18:00 verification red). A real sync path beside it still counts."""
    c = _check(tmp_path, monkeypatch, "lib/sim/stand_in.py", '_bash("git pull --rebase --autostash")\n')
    assert c.ok is True, c.detail
    c2 = _check(tmp_path / "b", monkeypatch, "lib/simple_sync.py", '_bash("git pull --rebase")\n')
    assert c2.ok is False and "simple_sync.py" in c2.detail


GUARD = '''_FLAGS = {
    "pull": {"-q", "--quiet", "-f", "--force", "--ff-only", "--rebase", "-r", "--no-rebase",
             "--no-edit"},
}
_BAD = re.compile(r"git pull --rebase")
def refused(argv):
    return "--rebase" in argv or argv[-1] == "--rebase"
'''


def test_a_guard_that_only_recognises_the_pattern_does_not_count(tmp_path, monkeypatch):
    """lib/hooks/restricted_hosts_guard.py (2026-09-30, 3adb97e) lists `--rebase`
    in a set of git flags it parses, and matches against it: data it inspects,
    never a command it runs. It turned the box's verification red."""
    c = _check(tmp_path, monkeypatch, "lib/hooks/restricted_hosts_guard.py", GUARD)
    assert c.ok is True, c.detail


def test_a_real_rebase_call_in_a_hooks_script_still_counts(tmp_path, monkeypatch):
    """No folder exemption: a hook that RUNS the rebase is a sync path like any other."""
    c = _check(tmp_path, monkeypatch, "lib/hooks/sync_hook.py",
               'subprocess.run(["git", "pull", "--rebase"])\n')
    assert c.ok is False and "sync_hook.py" in c.detail
    c2 = _check(tmp_path / "b", monkeypatch, "lib/hooks/sync_hook2.py",
                'subprocess.run([\n    "git", "pull",\n    "--rebase",\n])\n')
    assert c2.ok is False and "sync_hook2.py" in c2.detail
    c3 = _check(tmp_path / "c", monkeypatch, "lib/hooks/pull.sh", "git pull --rebase -q\n")
    assert c3.ok is False and "pull.sh" in c3.detail
    c4 = _check(tmp_path / "d", monkeypatch, "lib/hooks/g.py", 'CMD = "git pull --rebase"\nos.system(CMD)\n')
    assert c4.ok is False and "g.py" in c4.detail
