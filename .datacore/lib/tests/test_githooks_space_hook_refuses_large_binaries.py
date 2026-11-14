"""The space hook refuses a binary of 10 MB or more (owner, 2026-11-14; DIP-0015 tier 2).

WHY. Loose objects of generated media made space repositories gigabytes large
(disk-space report 2026-11-09). The owner decided large files are blocked and
live in the space's media/ folder instead, outside git, backed up to the storage host.
Text is never capped here: the ledger's signed logs are large text by design.
The agents' autosave asks the hook file by file (SYN-8), so a refused binary is
held back alone and the rest of the space still syncs.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_githooks_credential_store import _install  # noqa: E402

HOOKS_KEY = "core." + "hooksPath"
MB = 1024 * 1024


def _git(repo, *args, env):
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
    return subprocess.run([*g, *args], cwd=repo, env=env, capture_output=True, text=True)


def _space(tmp_path, name="5-plur"):
    root = _install(tmp_path)
    repo = root / name
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_INDEX_FILE", "NIGHTSHIFT_RUN", "SPACE_MAX_BINARY_MB")}
    env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, env=env, capture_output=True)
    _git(repo, "config", HOOKS_KEY, str(root / ".datacore" / "githooks"), env=env)
    (repo / "content").mkdir()
    (repo / "README.md").write_text("space\n")
    _git(repo, "add", "README.md", env=env)
    assert _git(repo, "commit", "-q", "-m", "init", env=env).returncode == 0
    return repo, env


def _binary(path: Path, size: int) -> None:
    path.write_bytes(b"\x00\x89PNG" + os.urandom(size - 5))


def test_a_binary_of_ten_megabytes_is_refused_and_named(tmp_path):
    repo, env = _space(tmp_path)
    _binary(repo / "content" / "render.png", 10 * MB)
    _git(repo, "add", "content/render.png", env=env)
    r = _git(repo, "commit", "-q", "-m", "render", env=env)
    out = r.stdout + r.stderr
    assert r.returncode != 0, "a 10 MB binary was committed to a space"
    assert "content/render.png" in out
    assert "10 MB" in out
    assert "media/" in out and "media_store.py move" in out


def test_a_binary_under_the_cap_still_commits(tmp_path):
    repo, env = _space(tmp_path)
    _binary(repo / "content" / "small.png", 2 * MB)
    _git(repo, "add", "content/small.png", env=env)
    r = _git(repo, "commit", "-q", "-m", "small image", env=env)
    assert r.returncode == 0, (r.stdout + r.stderr)[-400:]


def test_a_large_text_file_still_commits(tmp_path):
    """Ledger logs are signed JSONL kept in git by design; text is never capped here."""
    repo, env = _space(tmp_path)
    log = repo / ".datacore" / "telemetry.jsonl"
    log.parent.mkdir()
    line = '{"event":"x","sig":"' + "a" * 200 + '"}\n'
    log.write_text(line * (12 * MB // len(line)))
    _git(repo, "add", ".datacore/telemetry.jsonl", env=env)
    r = _git(repo, "commit", "-q", "-m", "ledger", env=env)
    assert r.returncode == 0, (r.stdout + r.stderr)[-400:]


def test_the_cap_follows_the_environment(tmp_path):
    repo, env = _space(tmp_path)
    _binary(repo / "content" / "two.png", 2 * MB)
    _git(repo, "add", "content/two.png", env=env)
    r = _git(repo, "commit", "-q", "-m", "x", env={**env, "SPACE_MAX_BINARY_MB": "1"})
    assert r.returncode != 0
    assert "1 MB" in r.stdout + r.stderr


def test_the_autosave_holds_back_only_the_large_binary(tmp_path):
    """SYN-8: the autosave bisects with the hook, so the rest of the space still syncs."""
    import ledger_transport
    repo, env = _space(tmp_path)
    _binary(repo / "content" / "big.png", 2 * MB)
    (repo / "content" / "note.md").write_text("a note\n")
    old = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update({**env, "SPACE_MAX_BINARY_MB": "1"})
        refused, said = ledger_transport.refused_by_hook(repo, ["content/big.png", "content/note.md"])
    finally:
        os.environ.clear()
        os.environ.update(old)
    assert refused == ["content/big.png"], (refused, said)
