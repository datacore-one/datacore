"""A check that cannot look at something reports so; it does not take the harness down.

plur-claw carries a hook command pointing into `/root/.openclaw/workspace/Data`
from when the OpenClaw agent ran as root. `Path.exists()` raises
PermissionError on that path rather than returning False, the exception escaped
`check_hooks`, and the whole of `v2_verify` died with a traceback -- so the
other 32 checks never ran and the host reported nothing at all, for any
question. It had no scheduled verification either, so nobody saw it.

The module's own docstring is about precisely this: "THREE OUTCOMES, NOT TWO...
`n-a` is a real answer: it means the check could not run, which is different
from passing and different from failing."
"""
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import v2_verify  # noqa: E402


class _Report:
    def __init__(self):
        self.rows = []

    def add(self, dip, name, ok, detail="", skipped=False):
        self.rows.append({"dip": dip, "name": name, "ok": ok, "detail": detail, "skipped": skipped})


def _with_hooks(monkeypatch, tmp_path, commands):
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    monkeypatch.setattr(v2_verify, "HOOK_SETTINGS", [settings])
    monkeypatch.setattr(v2_verify, "hook_commands", lambda s: commands)


def test_a_hook_this_user_may_not_stat_does_not_crash_the_run(monkeypatch, tmp_path):
    forbidden = Path("/root/.openclaw/workspace/Data/.datacore/lib/session_bootstrap.py")

    def _raise(self, *a, **kw):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "exists", _raise)
    _with_hooks(monkeypatch, tmp_path, [("SessionStart", str(forbidden))])
    monkeypatch.setattr(v2_verify, "hook_script_path", lambda cmd: forbidden)

    rep = _Report()
    v2_verify.check_hooks(rep)          # must not raise

    row = rep.rows[-1]
    assert row["ok"] is True, "an unreadable path is not evidence the file is missing"
    assert "not readable" in row["detail"], row["detail"]


def test_a_hook_that_really_is_missing_still_fails(monkeypatch, tmp_path):
    gone = tmp_path / "never-existed.py"
    _with_hooks(monkeypatch, tmp_path, [("SessionStart", str(gone))])
    monkeypatch.setattr(v2_verify, "hook_script_path", lambda cmd: gone)

    rep = _Report()
    v2_verify.check_hooks(rep)

    assert rep.rows[-1]["ok"] is False
    assert "never-existed.py" in rep.rows[-1]["detail"]


def test_a_hook_that_is_there_passes(monkeypatch, tmp_path):
    present = tmp_path / "hook.py"
    present.write_text("# hook\n")
    _with_hooks(monkeypatch, tmp_path, [("SessionStart", str(present))])
    monkeypatch.setattr(v2_verify, "hook_script_path", lambda cmd: present)

    rep = _Report()
    v2_verify.check_hooks(rep)

    assert rep.rows[-1]["ok"] is True
    assert "not readable" not in rep.rows[-1]["detail"]
