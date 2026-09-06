"""Every hook command wired in Claude Code settings runs a file that exists.

800fd31 (datacore#25) deleted three hook scripts from the repo while the
user-global ~/.claude/settings.json — outside every repository — still ran
them: every session then threw a PostToolUse error and a SessionStart error
at files that were gone. This checks the same thing the v2 checklist row
checks, against fixture settings files and against this repo's own project
settings, so the class of breakage fails a test before it fails a session.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import v2_verify as vv  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def test_hook_script_path_resolves_the_file_a_command_runs():
    assert vv.hook_script_path("python3 /x/y.py") == Path("/x/y.py")
    assert vv.hook_script_path("python3 /x/y.py --flag a") == Path("/x/y.py")
    assert vv.hook_script_path("bash ~/Data/.datacore/hooks/org-date-fix.sh") == \
        Path.home() / "Data" / ".datacore" / "hooks" / "org-date-fix.sh"
    assert vv.hook_script_path("/opt/plur/bin/plur-hook hook-session-guard") == \
        Path("/opt/plur/bin/plur-hook")
    assert vv.hook_script_path("CLAUDE_HOOK_EVENT_NAME=PostToolUseFailure python3 /x/h.py") == \
        Path("/x/h.py")
    # Package runners and PATH binaries are not files we can check.
    assert vv.hook_script_path("npx @plur-ai/cli hook-observe >/dev/null") is None
    assert vv.hook_script_path("some-binary --arg") is None


def _settings(path: Path, hooks: dict) -> Path:
    path.write_text(json.dumps({"hooks": hooks}))
    return path


def test_missing_hook_file_fails_the_row(tmp_path, monkeypatch):
    present = tmp_path / "present.py"
    present.write_text("")
    s = _settings(tmp_path / "settings.json", {
        "PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": f"python3 {present}"},
            {"type": "command", "command": f"python3 {tmp_path}/gone_guard.py"},
        ]},
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "npx something"}]}],
        "SessionStart": [{"hooks": [{"type": "command", "command": f"python3 {tmp_path}/gone_reminder.py"}]}],
    })
    monkeypatch.setattr(vv, "HOOK_SETTINGS", (s,))
    rep = vv.Report()
    vv.check_hooks(rep)
    row = rep.checks[0]
    assert row.name == "hook commands resolve" and row.ok is False
    assert "PreToolUse -> gone_guard.py" in row.detail
    assert "SessionStart -> gone_reminder.py" in row.detail


def test_all_present_passes_and_counts_files(tmp_path, monkeypatch):
    present = tmp_path / "present.py"
    present.write_text("")
    a = _settings(tmp_path / "a.json", {"Stop": [{"hooks": [{"command": f"python3 {present}"}]}]})
    b = _settings(tmp_path / "b.json", {"Stop": [{"hooks": [{"command": "npx x"}]}]})
    monkeypatch.setattr(vv, "HOOK_SETTINGS", (a, b, tmp_path / "absent.json"))
    rep = vv.Report()
    vv.check_hooks(rep)
    assert rep.checks[0].ok is True and rep.checks[0].detail == "2 settings file(s)"


def test_no_settings_at_all_is_not_applicable(tmp_path, monkeypatch):
    monkeypatch.setattr(vv, "HOOK_SETTINGS", (tmp_path / "none.json",))
    rep = vv.Report()
    vv.check_hooks(rep)
    assert rep.checks[0].ok is None and rep.checks[0].skipped is True


def test_this_repos_project_settings_resolve():
    """The repo's own wiring: every path it names under ~/Data exists here."""
    missing = []
    for settings in (ROOT / ".datacore" / "settings.json", ROOT / ".claude" / "settings.json"):
        for event, cmd in vv.hook_commands(settings):
            p = vv.hook_script_path(cmd)
            if p is None:
                continue
            # Project settings say ~/Data/...; map that onto this checkout so
            # the test is about the repo, not about where it is cloned.
            rel = str(p).replace(str(Path.home() / "Data"), str(ROOT), 1)
            if not Path(rel).exists():
                missing.append(f"{settings.name}: {event} -> {p}")
    assert not missing, missing
