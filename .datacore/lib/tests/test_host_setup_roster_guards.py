"""Every guard the roster says a machine needs is wired and verified by host setup.

Canvas finding (2026-10-01): the disclosure guards (redaction, publish, memory,
evals, config protection, client/space policy, context) were wired only in the
Mac's user settings, by install_redaction_guards.py run by hand. The
add-a-machine installer (agent_host_setup.sh) wired and verified only the
tool-policy guard, so another machine got the rest only if someone remembered.

The roster (registry/infrastructure.yaml) now says which guards a machine
needs: servers.<host>.guards. Which guards each machine gets stays the owner's
choice; the installer wires exactly those, and --verify names any missing.
The fixture is the same as test_host_setup_policy_guard.py: a throwaway HOME,
stub system tools, no real machine touched.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
GUARD_SCRIPTS = ["tool_policy_guard.py", "redaction_guard.py", "injection_integrity_guard.py",
                 "publish_guard.py", "memory_guard.py"]


def _host(tmp_path, guards=None):
    home = tmp_path / "home"
    runner = home / "Data"
    lib = runner / ".datacore/lib"
    registry = runner / ".datacore/registry"
    (lib / "hooks").mkdir(parents=True)
    registry.mkdir()
    row = "    access: {actor: fixture}\n"
    if guards is not None:
        row += f"    guards: {json.dumps(guards)}\n"
    registry.joinpath("infrastructure.yaml").write_text("servers:\n  newhost:\n" + row)
    home.joinpath(".datacore").mkdir(exist_ok=True)
    home.joinpath(".datacore/identity.env").write_text("DATACORE_ACTOR=fixture\n")
    lib.joinpath("actor_identity.py").write_text(
        "from pathlib import Path\n"
        f"REGISTRY_DIR = Path({str(registry)!r})\n"
        'if __name__ == "__main__":\n    print("fixture identity.env")\n')
    shutil.copyfile(LIB / "cron_install.py", lib / "cron_install.py")
    shutil.copyfile(LIB / "hooks/install_redaction_guards.py", lib / "hooks/install_redaction_guards.py")
    for name in GUARD_SCRIPTS:
        lib.joinpath("hooks", name).write_text("# guard (fixture)\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    bindir.joinpath("python3").symlink_to(sys.executable)
    stubs = {"systemctl": "exit 0", "sudo": "exit 1", "claude": "echo 'claude (fixture)'",
             "crontab": 'case "$1" in -l) cat "$TEST_CRONTAB" ;; -) cat > "$TEST_CRONTAB" ;; *) exit 2 ;; esac'}
    for name, body in stubs.items():
        bindir.joinpath(name).write_text(f"#!/bin/sh\n{body}\n")
        bindir.joinpath(name).chmod(0o755)
    store = tmp_path / "crontab"
    store.write_text("")
    env = {**os.environ, "HOME": str(home), "DATACORE_RUNNER": str(runner),
           "DATACORE_STATE": str(home / ".datacore/state"), "TEST_CRONTAB": str(store),
           "PATH": os.pathsep.join([str(bindir), "/usr/bin", "/bin", "/usr/sbin", "/sbin"])}
    command = ["bash", str(LIB / "agent_host_setup.sh"), "--host", "newhost"]
    return home / ".claude/settings.json", command, env


def _wired_scripts(settings: Path) -> set[str]:
    doc = json.loads(settings.read_text()) if settings.is_file() else {}
    return {Path(t).name for groups in (doc.get("hooks") or {}).values() for g in groups
            for h in g.get("hooks", []) for t in h.get("command", "").split() if t.endswith(".py")}


def test_setup_wires_every_guard_the_roster_names(tmp_path):
    settings, command, env = _host(tmp_path, guards=["redaction", "publish"])
    out = subprocess.run(command, env=env, capture_output=True, text=True)
    wired = _wired_scripts(settings)
    for script in ("tool_policy_guard.py", "redaction_guard.py", "injection_integrity_guard.py", "publish_guard.py"):
        assert script in wired, f"{script} not wired:\n{out.stdout}\n{out.stderr}"
    assert "memory_guard.py" not in wired, "a guard the roster does not name was wired"
    assert "OK  roster guards" in out.stdout, out.stdout


def test_verify_names_each_roster_guard_that_is_missing(tmp_path):
    settings, command, env = _host(tmp_path, guards=["redaction", "publish", "memory"])
    settings.parent.mkdir(parents=True)
    policy = settings.parent.parent / "Data/.datacore/lib/hooks/tool_policy_guard.py"
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "*", "hooks": [{"type": "command", "command": f"python3 {policy}"}]}]}}))
    before = settings.read_text()
    out = subprocess.run([*command, "--verify"], env=env, capture_output=True, text=True)
    assert out.returncode != 0
    assert "FAIL roster guards" in out.stdout, out.stdout
    for name in ("redaction_guard.py", "publish_guard.py", "memory_guard.py"):
        assert name in out.stdout, f"verify does not name {name}:\n{out.stdout}"
    assert settings.read_text() == before, "verify changed the settings"


def test_a_roster_without_guards_is_said_plainly(tmp_path):
    settings, command, env = _host(tmp_path, guards=None)
    out = subprocess.run(command, env=env, capture_output=True, text=True)
    assert "roster names no disclosure guards for newhost" in out.stdout, out.stdout
    assert _wired_scripts(settings) == {"tool_policy_guard.py"}


def test_an_unknown_guard_in_the_roster_fails(tmp_path):
    settings, command, env = _host(tmp_path, guards=["redaction", "no-such-guard"])
    out = subprocess.run([*command, "--verify"], env=env, capture_output=True, text=True)
    assert out.returncode != 0
    assert "no-such-guard" in out.stdout, out.stdout
