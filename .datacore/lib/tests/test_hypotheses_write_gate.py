"""A commit of a venture's hypotheses board the heartbeat would refuse is refused.

WHY. Agents edit hypotheses.yaml with editors as well as through the store.
On 2026-10-02 one board got a lane `active_candidate`; on 2026-10-12 another
got unescaped double quotes inside a double-quoted string (not YAML any more).
The venture heartbeat then refused both ventures on every tick for weeks
(ops report 2026-10-23). The pre-commit dispatcher now runs the ventures
store's own write check (`hypothesis_store.check_text`) on a staged board, so
a broken board never lands, whatever wrote it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
DATACORE = LIB.parent


def _ventures() -> Path:
    for root in (DATACORE, Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data")) / ".datacore"):
        module = root / "modules" / "ventures"
        if (module / "lib" / "hypothesis_store.py").is_file():
            return module
    pytest.skip("the ventures module is not installed")


GOOD = "board:\n  backlog:\n    - id: H-1\n      statement: Agents keep the board current\n  paused: []\n"
BROKEN_QUOTES = ('hypotheses:\n  - id: H001\n'
                 '    statement: "Teams adopt "one memory" when it saves a day a week"\n')
UNKNOWN_LANE = "board:\n  backlog: []\n  active_candidate:\n    - id: H-7\n      statement: A candidate\n"


@pytest.fixture
def space(tmp_path):
    root = tmp_path / "Data"
    (root / ".datacore" / "modules").mkdir(parents=True)
    shutil.copytree(DATACORE / "githooks", root / ".datacore" / "githooks")
    shutil.copytree(DATACORE / "hooks", root / ".datacore" / "hooks")
    (root / ".datacore" / "lib").symlink_to(LIB)
    (root / ".datacore" / "modules" / "ventures").symlink_to(_ventures())
    repo = root / "2-venture"
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_INDEX_FILE", "NIGHTSHIFT_RUN")}
    env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
    subprocess.run([*g, "init", "-q", "-b", "main", str(repo)], check=True, env=env, capture_output=True)
    subprocess.run([*g, "config", "core.hooksPath", str(root / ".datacore" / "githooks")],
                   cwd=repo, check=True, env=env)

    def commit(files: dict, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        for name, text in files.items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(text)
        subprocess.run([*g, "add", "--", *files], cwd=repo, check=True, env=env)
        return subprocess.run([*g, "commit", "-q", "-m", "x"], cwd=repo, env={**env, **(extra_env or {})},
                              capture_output=True, text=True)

    def landed(name: str) -> bool:
        return subprocess.run(["git", "cat-file", "-e", f"HEAD:{name}"], cwd=repo, env=env,
                              capture_output=True).returncode == 0

    return commit, landed


def test_a_valid_board_lands(space):
    commit, landed = space
    r = commit({"hypotheses.yaml": GOOD})
    assert r.returncode == 0, (r.stdout + r.stderr)[-600:]
    assert landed("hypotheses.yaml")


def test_invalid_yaml_is_refused_before_it_lands(space):
    commit, landed = space
    r = commit({"hypotheses.yaml": BROKEN_QUOTES})
    assert r.returncode != 0
    assert "not valid YAML" in r.stderr and "hypotheses.yaml" in r.stderr
    assert not landed("hypotheses.yaml")


def test_an_unknown_lane_is_refused_before_it_lands(space):
    commit, landed = space
    r = commit({"hypotheses.yaml": UNKNOWN_LANE})
    assert r.returncode != 0
    assert "active_candidate" in r.stderr
    assert not landed("hypotheses.yaml")


def test_the_board_named_by_venture_yaml_is_the_one_checked(space):
    commit, landed = space
    r = commit({"venture.yaml": "hypotheses_file: content/board.yaml\n",
                "content/board.yaml": UNKNOWN_LANE})
    assert r.returncode != 0 and "active_candidate" in r.stderr
    assert not landed("content/board.yaml")


def test_other_yaml_files_are_not_this_gates_business(space):
    commit, landed = space
    r = commit({"content/notes.yaml": BROKEN_QUOTES})
    assert r.returncode == 0, (r.stdout + r.stderr)[-600:]


def test_a_board_is_checked_on_a_host_without_pydantic(space, tmp_path):
    """Two agent hosts have PyYAML but not pydantic; one of them writes boards."""
    commit, landed = space
    blocker = tmp_path / "no-pydantic" / "pydantic"
    blocker.mkdir(parents=True)
    (blocker / "__init__.py").write_text("raise ModuleNotFoundError(\"No module named 'pydantic'\")\n")
    without = {"PYTHONPATH": str(blocker.parent)}
    r = commit({"hypotheses.yaml": UNKNOWN_LANE}, without)
    assert r.returncode != 0 and "active_candidate" in r.stderr, r.stderr[-600:]
    assert "cannot load" not in r.stderr
    r = commit({"hypotheses.yaml": GOOD}, without)
    assert r.returncode == 0, (r.stdout + r.stderr)[-600:]
    assert landed("hypotheses.yaml")
