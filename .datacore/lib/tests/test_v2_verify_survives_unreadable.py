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


# --- Is the code running here the current code? ----------------------------
# Nothing asked. hermes's runner sat 312 commits behind origin and
# nightshift's 5, both on a DETACHED HEAD where `git pull` prints its usage
# and changes nothing: the deploy step succeeded, said nothing, deployed
# nothing, and nightshift's hourly cycle went on running the old code while
# every other check here passed.

def _git_stub(monkeypatch, answers):
    def fake_run(args, timeout=180):
        key = " ".join(a for a in args if not a.startswith("/"))
        for pattern, (rc, out) in answers.items():
            if pattern in key:
                return rc, out
        return 1, ""
    monkeypatch.setattr(v2_verify, "run", fake_run)


def test_a_detached_checkout_is_a_failure_not_a_pass(monkeypatch):
    _git_stub(monkeypatch, {
        "rev-parse --show-toplevel": (0, "/opt/runner\n"),
        "rev-parse --abbrev-ref HEAD": (0, "HEAD\n"),
        "rev-parse --short HEAD": (0, "0135df3\n"),
    })
    rep = _Report()
    v2_verify.check_install_current(rep)

    row = rep.rows[-1]
    assert row["ok"] is False
    assert "detached" in row["detail"] and "0135df3" in row["detail"]
    assert "does nothing" in row["detail"], "it must say why pulling did not help"


def test_being_behind_origin_is_a_failure(monkeypatch):
    _git_stub(monkeypatch, {
        "rev-parse --show-toplevel": (0, "/opt/runner\n"),
        "rev-parse --abbrev-ref HEAD": (0, "main\n"),
        "@{upstream}": (0, "origin/main\n"),
        "rev-list --left-right --count": (0, "312\t0\n"),
    })
    rep = _Report()
    v2_verify.check_install_current(rep)

    assert rep.rows[-1]["ok"] is False
    assert "312 behind" in rep.rows[-1]["detail"]


def test_ahead_of_origin_is_not_stale(monkeypatch):
    # Local commits not yet published are a different question, with its own
    # check ("no stranded commits"). This one asks only whether we are behind.
    _git_stub(monkeypatch, {
        "rev-parse --show-toplevel": (0, "/opt/runner\n"),
        "rev-parse --abbrev-ref HEAD": (0, "main\n"),
        "@{upstream}": (0, "origin/main\n"),
        "rev-list --left-right --count": (0, "0\t3\n"),
    })
    rep = _Report()
    v2_verify.check_install_current(rep)

    assert rep.rows[-1]["ok"] is True


def test_no_upstream_is_could_not_tell_not_broken(monkeypatch):
    _git_stub(monkeypatch, {
        "rev-parse --show-toplevel": (0, "/opt/runner\n"),
        "rev-parse --abbrev-ref HEAD": (0, "main\n"),
    })
    rep = _Report()
    v2_verify.check_install_current(rep)

    assert rep.rows[-1]["ok"] is None
    assert "tracks nothing" in rep.rows[-1]["detail"]


def test_not_a_checkout_at_all_is_could_not_tell(monkeypatch):
    _git_stub(monkeypatch, {})
    rep = _Report()
    v2_verify.check_install_current(rep)

    assert rep.rows[-1]["ok"] is None
