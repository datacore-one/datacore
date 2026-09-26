"""MEM-68: Install reports failure when any part failed. It never reports success with a
module left broken.

Kind: deterministic. The shipped installer build (3-fds/2-projects/datacore-cli/dist,
what the global `datacore` runs) installing a module offline: `datacore module install
<local bare repo>.git` with DATACORE_ROOT and HOME in tmp, so nothing touches the network
or the real installation.
  * a module whose Python dependencies cannot be installed (requirements.txt names a
    requirement pip cannot satisfy; its tool imports it) is left broken -- the install
    must say so: non-zero exit and a result that is not a success;
  * control: a module with no dependencies installs and reports success (exit 0).
The same defect sits in `datacore init`'s module step (src/lib/init.ts: a failed
post-install is pushed to `warnings` and initSucceeded ignores warnings, ENG-2026-09-23-048);
init needs the network to run, so it is not driven here.

Seeded failure: none needed to show red today; the control proves a real install through
this harness reports success -- verified.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "3-fds" / "2-projects" / "datacore-cli" / "dist" / "index.js"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


def _module_repo(tmp: Path, name: str, requirements: str | None) -> Path:
    src = tmp / f"src-{name}"
    (src / "lib").mkdir(parents=True)
    (src / "module.yaml").write_text(f"name: {name}\nversion: 0.1.0\ndescription: eval fixture\n")
    (src / "lib" / "tool.py").write_text("import not_a_real_dependency_xyz\n" if requirements else "print('ok')\n")
    if requirements:
        (src / "requirements.txt").write_text(requirements)
    env = {**os.environ, **GIT_ENV}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "module"]):
        subprocess.run(["git", *args], cwd=src, env=env, check=True, capture_output=True, timeout=30)
    bare = tmp / f"module-{name}.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], env=env, check=True,
                   capture_output=True, timeout=30)
    return bare


def _install(tmp: Path, repo: Path):
    node = shutil.which("node")
    assert node and CLI.is_file(), "node or the datacore-cli build is missing"
    data, home = tmp / "Data", tmp / "home"
    (data / ".datacore" / "modules").mkdir(parents=True, exist_ok=True)
    home.mkdir(exist_ok=True)
    env = {**os.environ, **GIT_ENV, "DATACORE_ROOT": str(data), "HOME": str(home),
           "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    return subprocess.run([node, str(CLI), "module", "install", str(repo), "--json"], env=env,
                          capture_output=True, text=True, timeout=60)


def _succeeded(r) -> bool:
    if r.returncode != 0:
        return False
    try:
        body = json.loads(r.stdout)
    except ValueError:
        return True          # exit 0 with human output is a success claim
    return not (isinstance(body, dict) and (body.get("success") is False or body.get("error")))


def test_a_module_left_broken_is_reported_as_a_failure(tmp_path):
    repo = _module_repo(tmp_path, "brokendeps", "not_a_real_dependency_xyz==99.99.99\n")
    r = _install(tmp_path, repo)
    assert not _succeeded(r), ("the module's dependencies could not be installed (its tool cannot "
                               f"import them) and the installer reported success: exit {r.returncode}\n"
                               f"{(r.stdout + r.stderr)[-500:]}")


def test_control_a_healthy_module_installs(tmp_path):
    repo = _module_repo(tmp_path, "healthy", None)
    r = _install(tmp_path, repo)
    assert _succeeded(r), f"control: a dependency-free module failed to install:\n{(r.stdout + r.stderr)[-500:]}"
    assert (tmp_path / "Data" / ".datacore" / "modules" / "healthy" / "module.yaml").is_file()
