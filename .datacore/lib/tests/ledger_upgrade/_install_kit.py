"""A clean machine for the ledger-upgrade Phase 3 evals (I1-I3, profile A).

Profile A (PLAN.md, Phase 3): one host or one shared git remote, humans only,
the ledger, verify and the CLI. No agents, no org projection, no fleet.

The clean machine is built in tmp, without a container (none is required):

  * HOME is an empty directory: no ~/.datacore/identity.env, no keys.
  * DATACORE_ROOT is an empty data root that holds a COPY of this checkout's
    tracked `.datacore/lib` (working-tree contents, tests left out). Code and
    data are one tree, as on a single-host install, so nothing resolves
    against this installation's registry (`principals.yaml`,
    `repositories.yaml`): neither is copied.
  * the environment is scrubbed of every DATACORE_* variable of this machine
    (reusing `_fresh_install.scrubbed_env`), and git reads no global config.
  * `bare_python()` makes a venv with no packages at all (no pip, no PyYAML, no
    cryptography): a machine where nothing was installed yet.

Nothing here writes outside tmp. The real checkout is only read.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent
LIB = TESTS.parent
REAL = LIB.parents[1]
sys.path.insert(0, str(TESTS))
import _fresh_install as F  # noqa: E402

#: This fleet's names. A profile A install must never offer one as a default.
FLEET = re.compile(
    r"\b(?:winston|miles|nightshift|mac|box|tris|gregor|plur-claw|hermes)\b"
    r"|/(?:home|Users)/greg[o]r|/root/Data\b",
    re.I)


@dataclass
class Machine:
    tmp: Path
    root: Path
    home: Path
    env: dict
    python: str = sys.executable

    @property
    def cli_path(self) -> Path:
        return self.root / ".datacore" / "lib" / "ledger_cli.py"

    def cli(self, args: str, *, python: str | None = None, env: dict | None = None,
            timeout: int = 60) -> subprocess.CompletedProcess:
        cmd = f'"{python or self.python}" "{self.cli_path}" {args}'
        return subprocess.run(["bash", "-c", cmd], cwd=self.root, env=env or self.env,
                              capture_output=True, text=True, timeout=timeout)

    def code_digest(self) -> str:
        return tree_digest(self.root / ".datacore" / "lib")

    def scrub(self, text: str) -> str:
        """Output with the tmp location removed: the temp path is the test's own
        (it may hold the OS user's name), not something the install chose."""
        for p in {str(self.tmp), os.path.realpath(self.tmp)}:
            text = text.replace(p, "<tmp>")
        return text

    def bare_python(self) -> str:
        venv = self.tmp / "bare-venv"
        if not venv.exists():
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                           check=True, capture_output=True, timeout=120)
        return str(venv / "bin" / "python")


def tree_digest(top: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(top.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(str(p.relative_to(top)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def _tracked_lib_files() -> list[str]:
    # Tracked files plus new, not-ignored ones: the working tree as it stands.
    out = subprocess.run(["git", "-C", str(REAL), "ls-files", "--cached", "--others",
                          "--exclude-standard", ".datacore/lib"],
                         capture_output=True, text=True, timeout=30, check=True).stdout
    return [f for f in out.splitlines() if "/tests/" not in f and "/__pycache__/" not in f]


def clean_machine(tmp: Path) -> Machine:
    tmp = Path(os.path.realpath(tmp))
    root, home = tmp / "Data", tmp / "home"
    home.mkdir(parents=True)
    for rel in _tracked_lib_files():
        src = REAL / rel
        if not src.is_file():          # tracked but deleted in the working tree
            continue
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    env = F.scrubbed_env(root, home)
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return Machine(tmp, root, home, env)


def files_written(m: Machine) -> dict[str, str]:
    """Every file under HOME and the data root that the install wrote (the
    copied code excluded), relative path -> text."""
    out = {}
    lib = m.root / ".datacore" / "lib"
    for top in (m.home, m.root):
        for p in sorted(top.rglob("*")):
            if not p.is_file() or lib in p.parents or "__pycache__" in p.parts or ".git" in p.parts:
                continue
            try:
                out[str(p.relative_to(m.tmp))] = p.read_text(errors="replace")
            except OSError:
                continue
    return out
