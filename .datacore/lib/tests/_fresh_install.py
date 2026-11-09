"""A fresh Datacore install, built the way a new team would, in tmp.

`git archive HEAD` of this repository is unpacked into <tmp>/Data (the real
checkout is only read), committed as a baseline so any edit to one of
Datacore's own files shows up in `git status`, and the guide's local steps
(INSTALL.md, "Manual Installation") are run in order with a scrubbed
environment: HOME, DATACORE_ROOT and DATACORE_STATE all point into tmp, and no
DATACORE_* variable of this machine leaks in.

Steps that need a network, an account or a person (fork/clone, pip install,
MCP registration, editing a personal layer, git push) are not run, nor is
install_datacore_path.py (step 1b): it writes into the running interpreter's
site-packages, outside the tmp install (owner OK to update this list, 2026-09-27); every step
that IS run is asserted to still be in INSTALL.md, so the harness cannot drift
from the guide silently.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

REAL = Path(__file__).resolve().parents[3]
GUIDE = REAL / "INSTALL.md"

# (step, command exactly as INSTALL.md writes it)
GUIDE_STEPS = [
    ("2 templates", "cp install.yaml.example install.yaml"),
    ("2 templates", "mkdir -p 0-personal/org"),
    ("2 templates", "cp .datacore/templates/org/inbox.org.example 0-personal/org/inbox.org"),
    ("2 templates", "cp .datacore/templates/org/next_actions.org.example 0-personal/org/next_actions.org"),
    ("2 templates", "cp .datacore/templates/org/someday.org.example 0-personal/org/someday.org"),
    ("2 templates", "cp .datacore/templates/org/habits.org.example 0-personal/org/habits.org"),
    ("2 templates", "python .datacore/lib/context_merge.py rebuild --path ."),
    ("2 templates", "mkdir -p ~/.datacore && echo 'DATACORE_ACTOR=your-name' >> ~/.datacore/identity.env"),
    ("2 templates", "mkdir -p 0-personal/.datacore/events"),
    ("4 mcp", "cp .mcp.json.example .mcp.json"),
    ("4 mcp", "cp .datacore/env/.env.example .datacore/env/.env"),
    ("6 databases", "python .datacore/lib/zettel_db.py init-all"),
    ("7 hooks", "ln -sf ../../.datacore/hooks/pre-commit .git/hooks/pre-commit"),
    ("7 hooks", "ln -sf ../../.datacore/hooks/pre-push .git/hooks/pre-push"),
    ("8 verify", "python3 .datacore/lib/ledger_transport.py status"),
]

GIT = ["git", "-c", "user.name=fresh", "-c", "user.email=fresh@example.invalid",
       "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]


@dataclass
class Install:
    data: Path
    home: Path
    env: dict
    steps: list = field(default_factory=list)   # (step, cmd, rc, output)

    def run(self, cmd: str, timeout: int = 55) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "-c", cmd], cwd=self.data, env=self.env,
                              capture_output=True, text=True, timeout=timeout)

    def modified_tracked(self) -> list[str]:
        out = subprocess.run(GIT + ["-C", str(self.data), "status", "--porcelain",
                                    "--untracked-files=no"], capture_output=True, text=True, timeout=30)
        return [l for l in out.stdout.splitlines() if l.strip()]

    def failed_steps(self) -> list[str]:
        return [f"step {s}: `{c}` -> rc {rc}: {o.strip()[-160:]}" for s, c, rc, o in self.steps if rc != 0]


def scrubbed_env(data: Path, home: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("DATACORE_", "PROMISE_", "PYTEST_", "COS_", "WINSTON_"))}
    env.update(HOME=str(home), DATACORE_ROOT=str(data),
               DATACORE_STATE=str(home / ".datacore" / "state"),
               PATH=f"{Path(sys.executable).parent}:{env.get('PATH', '')}",
               GIT_CONFIG_GLOBAL="/dev/null")
    return env


def unpack(tmp: Path) -> Install:
    data, home = tmp / "Data", tmp / "home"
    data.mkdir(parents=True)
    home.mkdir()
    blob = subprocess.run(["git", "-C", str(REAL), "archive", "HEAD"],
                          capture_output=True, timeout=60, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        tar.extractall(data, filter="tar") if sys.version_info >= (3, 12) else tar.extractall(data)
    subprocess.run(GIT + ["-C", str(data), "init", "-q"], check=True, timeout=30)
    subprocess.run(GIT + ["-C", str(data), "add", "-A"], check=True, timeout=60)
    subprocess.run(GIT + ["-C", str(data), "commit", "-q", "-m", "baseline"], check=True, timeout=60)
    return Install(data, home, scrubbed_env(data, home))


def follow_guide(tmp: Path) -> Install:
    inst = unpack(tmp)
    for step, cmd in GUIDE_STEPS:
        try:
            p = inst.run(cmd)
            inst.steps.append((step, cmd, p.returncode, p.stdout + p.stderr))
        except subprocess.TimeoutExpired:
            inst.steps.append((step, cmd, 124, "timed out"))
    return inst


def guide_text() -> str:
    return GUIDE.read_text()
