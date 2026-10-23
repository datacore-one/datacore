"""The capture scan reads the GitHub user from the machine's env file.

2026-10-18..23: the public code lost its default user and no host put
GITHUB_TRIAGE_USERNAME into the cron line's environment, so every 03:15 run
stopped at "GITHUB_TRIAGE_USERNAME is not set" and the scan cache went stale.
The machine's one env file (config_plane, ~/.datacore/datacore.env) is where
a host keeps such a setting; the script reads it when the variable is unset.
"""
import os
import subprocess
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1]
SCRIPT = MODULE / "lib" / "triage_github.sh"
CORE = MODULE.parents[1]  # the .datacore folder this module is installed in


def _install(tmp_path):
    data = tmp_path / "Data"
    space = data / "0-personal" / ".datacore"
    space.mkdir(parents=True)
    (space / "config.yaml").write_text("space: {name: personal, type: personal}\n")
    (data / ".datacore").symlink_to(CORE)
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TRIAGE_USERNAME", "DATACORE_SPACE")}
    env.update(DATA_DIR=str(data), DATACORE_ROOT=str(data),
               DATACORE_ENV_FILE=str(tmp_path / "no-such-datacore.env"))
    return data, env


def _run(env):
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=120)


def test_unset_everywhere_still_refuses(tmp_path):
    _, env = _install(tmp_path)
    r = _run(env)
    assert r.returncode == 1
    assert "GITHUB_TRIAGE_USERNAME is not set" in r.stdout


def test_user_from_the_machine_env_file(tmp_path):
    _, env = _install(tmp_path)
    envfile = tmp_path / "datacore.env"
    envfile.write_text("GITHUB_TRIAGE_USERNAME=octo-owner\n")
    env["DATACORE_ENV_FILE"] = str(envfile)
    r = _run(env)
    # Past the user check: with no repo list it stops at the next step instead.
    assert "GITHUB_TRIAGE_USERNAME is not set" not in r.stdout, r.stdout + r.stderr
    assert "No orgs found" in r.stdout, r.stdout + r.stderr
