"""MEM-61: Sync never deletes a file just because one machine lacks it. A large deletion or
shrinking file stops the sync and alerts me.

Kind: deterministic. The real fleet sync (git_fleet_sync.py <data_dir> --execute --pull,
the timer every agent host runs; exit 1 turns the unit red and fires fleet_sync_alert.sh
via OnFailure=) against a tmp data dir holding one space checkout whose origin is a local
bare repo (no GitHub gate consulted). Another clone plays "the other machine".
  * this machine lacks a tracked file (deleted in the working tree): the sync does not
    publish the deletion -- origin keeps the file;
  * the other machine lacked 25 files and published their deletion: pulling it here must
    stop the sync (files kept on this machine, exit non-zero), not delete them;
  * a tracked file shrank from 400 lines to 1 on this machine: the sync must stop (not
    publish it) and exit non-zero.

Seeded failure: a sweep that stages deletions (git add -A before the run) publishes the
missing file's deletion; verified red on the first case.
"""
import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
ENV = {**os.environ, **GIT_ENV}


def _git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30, env=ENV)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _setup(tmp: Path):
    origin, data = tmp / "origin.git", tmp / "data"
    work, other = data / "2-space", tmp / "other"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, timeout=30)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, timeout=30, env=ENV)
    _git(work, "checkout", "-q", "-b", "main")
    notes = work / "notes"
    notes.mkdir()
    for i in range(25):
        (notes / f"n{i:02d}.md").write_text(f"# note {i}\n")
    (work / "journal.md").write_text("".join(f"line {i}\n" for i in range(400)))
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "seed")
    _git(work, "push", "-q", "-u", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, timeout=30, env=ENV)
    return origin, data, work, other


def _sync(data: Path, tmp: Path):
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    return subprocess.run([sys.executable, str(LIB / "git_fleet_sync.py"), str(data), "--execute", "--pull"],
                          capture_output=True, text=True, timeout=60, env={**ENV, "HOME": str(home)})


def _origin_has(origin: Path, path: str) -> bool:
    r = subprocess.run(["git", "--git-dir", str(origin), "cat-file", "-e", f"main:{path}"],
                       capture_output=True, timeout=30)
    return r.returncode == 0


def test_a_file_missing_here_is_not_deleted_everywhere(tmp_path):
    origin, data, work, _ = _setup(tmp_path)
    (work / "notes" / "n03.md").unlink()
    (work / "new-work.md").write_text("agent output\n")   # something real to land
    r = _sync(data, tmp_path)
    assert _origin_has(origin, "notes/n03.md"), f"the sync published a deletion:\n{r.stdout[-600:]}"


def test_a_large_deletion_from_another_machine_stops_the_sync(tmp_path):
    origin, data, work, other = _setup(tmp_path)
    for i in range(25):
        (other / "notes" / f"n{i:02d}.md").unlink()
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "machine without the notes")
    _git(other, "push", "-q", "origin", "main")
    r = _sync(data, tmp_path)
    kept = len(list((work / "notes").glob("*.md")))
    assert kept == 25, (f"pulling another machine's mass deletion removed {25 - kept} files here:\n"
                        f"{r.stdout[-600:]}")
    assert r.returncode != 0, f"a 25-file deletion did not stop the sync (exit 0, no alert):\n{r.stdout[-600:]}"


def test_a_shrinking_file_stops_the_sync(tmp_path):
    origin, data, work, _ = _setup(tmp_path)
    (work / "journal.md").write_text("line 0\n")
    r = _sync(data, tmp_path)
    shown = subprocess.run(["git", "--git-dir", str(origin), "show", "main:journal.md"],
                           capture_output=True, text=True, timeout=30).stdout
    assert len(shown.splitlines()) == 400, (f"a file shrunk from 400 lines to {len(shown.splitlines())} "
                                            f"was published:\n{r.stdout[-600:]}")
    assert r.returncode != 0, f"a shrinking file did not stop the sync (exit 0, no alert):\n{r.stdout[-600:]}"
