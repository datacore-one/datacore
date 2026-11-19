"""`creds` runs the broker with a Python that can import its dependencies.

2026-11-19: during a release, a virtualenv without PyYAML was first on PATH. The
wrapper's `exec python3` picked it, the broker died on `import yaml`, and inside
`TOKEN="$(creds get ...)"` that left an empty token. PyPI answered 403 "invalid
or non-existent authentication", which was misread as a revoked token and led
to a needless rotation. The wrapper now skips an interpreter that cannot import
yaml and says so when none can.
"""
import os
import stat
import subprocess
from pathlib import Path

WRAPPER = Path(__file__).resolve().parents[2] / "bin" / "creds"


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _root(tmp_path: Path) -> Path:
    lib = tmp_path / "Data/.datacore/lib"
    lib.mkdir(parents=True)
    (lib / "creds.py").write_text("import yaml, sys\nprint('broker ran', sys.argv[1:])\n")
    return tmp_path / "Data"


def _fake_python_without_yaml(bindir: Path) -> None:
    bindir.mkdir()
    # Fails any yaml import the way a bare virtualenv does; anything else would
    # "succeed" silently, which is what made the real failure invisible.
    _exe(bindir / "python3", "#!/bin/sh\necho 'ModuleNotFoundError: yaml' >&2\nexit 1\n")


def test_skips_a_python_without_yaml_on_path(tmp_path):
    root = _root(tmp_path)
    _fake_python_without_yaml(tmp_path / "venvbin")
    env = {**os.environ, "DATACORE_ROOT": str(root), "PATH": f"{tmp_path / 'venvbin'}:{os.environ['PATH']}"}
    out = subprocess.run([str(WRAPPER), "get", "X"], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "broker ran ['get', 'X']" in out.stdout


def test_prefers_the_datacore_venv(tmp_path):
    root = _root(tmp_path)
    vbin = root / ".datacore/venv/bin"
    vbin.mkdir(parents=True)
    _exe(vbin / "python3", "#!/bin/sh\n[ \"$1\" = -c ] && exit 0\necho 'datacore venv ran'\n")
    env = {**os.environ, "DATACORE_ROOT": str(root)}
    out = subprocess.run([str(WRAPPER), "list"], env=env, capture_output=True, text=True)
    assert out.stdout.strip() == "datacore venv ran", out.stderr


def test_no_usable_python_fails_loudly(tmp_path):
    root = _root(tmp_path)
    _fake_python_without_yaml(tmp_path / "venvbin")
    # Only the fake python is reachable: no venv, and PATH holds nothing else.
    env = {"HOME": str(tmp_path), "DATACORE_ROOT": str(root), "PATH": f"{tmp_path / 'venvbin'}:/usr/bin:/bin",
           "CREDS_PYTHON_CANDIDATES": str(tmp_path / "venvbin" / "python3")}
    out = subprocess.run([str(WRAPPER), "get", "X"], env=env, capture_output=True, text=True)
    assert out.returncode != 0
    assert out.stdout == ""
    assert "yaml" in out.stderr
