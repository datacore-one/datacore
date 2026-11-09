"""A machine set up by the add-a-machine installer gets the tool-policy guard
in its Claude Code user settings (AGT-11 through INS-7).

Why (fleet week simulator, second run 2026-10-01): an injected `git reset
--hard` in the overnight machine's GitHub triage ran 4 times out of 4. The
script starts `claude -p --dangerously-skip-permissions` with no `--settings`,
so the only thing between that call and the checkout is the guard wired in
~/.claude/settings.json. On the real machines that wiring was added by hand on
2026-09-30 (AGT-11) and no installer writes it, so a new or rebuilt machine
would come up without it. The judging rule is the AGT-11 eval's own.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "agt11", Path(__file__).resolve().parent / "test_promise_AGT11_every_unattended_run_guarded.py")
_agt11 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_agt11)
guard_commands = _agt11._guard_commands


def _host(tmp_path, *, claude=True):
    home = tmp_path / "home"
    runner = home / "Data"
    lib = runner / ".datacore/lib"
    registry = runner / ".datacore/registry"
    (lib / "hooks").mkdir(parents=True)
    registry.mkdir()
    registry.joinpath("infrastructure.yaml").write_text(
        "servers:\n  newhost:\n    access: {actor: fixture}\n")
    home.joinpath(".datacore").mkdir(exist_ok=True)
    home.joinpath(".datacore/identity.env").write_text("DATACORE_ACTOR=fixture\n")
    lib.joinpath("actor_identity.py").write_text(
        "from pathlib import Path\n"
        f"REGISTRY_DIR = Path({str(registry)!r})\n"
        'if __name__ == "__main__":\n    print("fixture identity.env")\n')
    shutil.copyfile(LIB / "cron_install.py", lib / "cron_install.py")
    shutil.copyfile(LIB / "hooks/install_redaction_guards.py", lib / "hooks/install_redaction_guards.py")
    lib.joinpath("hooks/tool_policy_guard.py").write_text("# the guard (fixture)\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    bindir.joinpath("python3").symlink_to(sys.executable)
    stubs = {"systemctl": "exit 0", "sudo": "exit 1",
             "crontab": 'case "$1" in -l) cat "$TEST_CRONTAB" ;; -) cat > "$TEST_CRONTAB" ;; *) exit 2 ;; esac'}
    if claude:
        stubs["claude"] = "echo 'claude (fixture)'"
    for name, body in stubs.items():
        bindir.joinpath(name).write_text(f"#!/bin/sh\n{body}\n")
        bindir.joinpath(name).chmod(0o755)
    store = tmp_path / "crontab"
    store.write_text("")
    # PATH without the real claude: only the fixture bin and the system tools.
    env = {**os.environ, "HOME": str(home), "DATACORE_RUNNER": str(runner),
           "DATACORE_STATE": str(home / ".datacore/state"), "TEST_CRONTAB": str(store),
           "PATH": os.pathsep.join([str(bindir), "/usr/bin", "/bin", "/usr/sbin", "/sbin"])}
    command = ["bash", str(LIB / "agent_host_setup.sh"), "--host", "newhost"]
    return home / ".claude/settings.json", command, env


def _guards(settings: Path) -> list[str]:
    return guard_commands(settings.read_text()) if settings.is_file() else []


def test_a_new_machine_gets_the_guard_in_its_user_settings(tmp_path):
    settings, command, env = _host(tmp_path)
    assert not settings.exists()
    out = subprocess.run(command, env=env, capture_output=True, text=True)
    cmds = _guards(settings)
    assert cmds, f"no tool-policy guard before shell calls in {settings}:\n{out.stdout}\n{out.stderr}"
    target = [t for c in cmds for t in c.split() if t.endswith("tool_policy_guard.py")]
    assert target and all(Path(t).is_file() for t in target), cmds
    assert "OK  safety guard" in out.stdout, out.stdout


def test_existing_settings_are_kept_and_a_rerun_adds_nothing(tmp_path):
    settings, command, env = _host(tmp_path)
    settings.parent.mkdir(parents=True)
    mine = {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 /x/restricted_hosts_guard.py"}]}
    settings.write_text(json.dumps({"model": "keep-me", "hooks": {"PreToolUse": [mine]}}))
    subprocess.run(command, env=env, capture_output=True, text=True)
    subprocess.run(command, env=env, capture_output=True, text=True)
    doc = json.loads(settings.read_text())
    assert doc["model"] == "keep-me"
    assert mine in doc["hooks"]["PreToolUse"]
    assert len(_guards(settings)) == 1, doc


def test_a_guard_wired_by_hand_elsewhere_is_not_doubled(tmp_path):
    settings, command, env = _host(tmp_path)
    settings.parent.mkdir(parents=True)
    other = tmp_path / "elsewhere/tool_policy_guard.py"
    other.parent.mkdir()
    other.write_text("# hand-wired copy\n")
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "*", "hooks": [{"type": "command", "command": f"python3 {other}"}]}]}}))
    out = subprocess.run(command, env=env, capture_output=True, text=True)
    assert _guards(settings) == [f"python3 {other}"], settings.read_text()
    assert "OK  safety guard" in out.stdout, out.stdout


def test_verify_fails_a_machine_without_the_guard_and_changes_nothing(tmp_path):
    settings, command, env = _host(tmp_path)
    settings.parent.mkdir(parents=True)
    settings.write_text("{}")
    out = subprocess.run([*command, "--verify"], env=env, capture_output=True, text=True)
    assert "FAIL safety guard" in out.stdout, out.stdout
    assert out.returncode != 0
    assert settings.read_text() == "{}"


def test_a_machine_without_claude_code_is_not_given_settings(tmp_path):
    settings, command, env = _host(tmp_path, claude=False)
    out = subprocess.run(command, env=env, capture_output=True, text=True)
    assert not settings.exists()
    assert "FAIL safety guard" not in out.stdout, out.stdout
