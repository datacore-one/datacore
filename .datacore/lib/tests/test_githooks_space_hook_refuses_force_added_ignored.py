"""The space hook refuses a newly added file that the space's .gitignore excludes (2026-10-29).

WHY. 5-plur's org/next_actions.org is the ledger's generated projection: listed in
.gitignore and untracked since c275bf4b. On 2026-10-29 05:49 UTC an agent's GEO
refresh (ec889ce6) force-added it. Every host that pulled got that agent's copy as
its task list, the hourly import refused the space over headings the ledger had
never seen, and 5-plur's tasks stopped updating. A file only gets past .gitignore
with `git add -f`; the hook now asks why. Files tracked before they were ignored
are untouched -- only new additions are refused.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_githooks_credential_store import _install  # noqa: E402

HOOKS_KEY = "core." + "hooksPath"


def _git(repo, *args, env):
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
    return subprocess.run([*g, *args], cwd=repo, env=env, capture_output=True, text=True)


def _space(tmp_path):
    root = _install(tmp_path)
    repo = root / "5-plur"
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_INDEX_FILE", "NIGHTSHIFT_RUN")}
    env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, env=env, capture_output=True)
    _git(repo, "config", HOOKS_KEY, str(root / ".datacore" / "githooks"), env=env)
    (repo / ".gitignore").write_text("org/next_actions.org\n")
    (repo / "org").mkdir()
    _git(repo, "add", ".gitignore", env=env)
    assert _git(repo, "commit", "-q", "-m", "ignore", env=env).returncode == 0
    return repo, env


def test_a_force_added_ignored_file_is_refused(tmp_path):
    repo, env = _space(tmp_path)
    (repo / "org" / "next_actions.org").write_text("* open [#B] Review GEO draft: something\n")
    assert _git(repo, "add", "-f", "org/next_actions.org", env=env).returncode == 0
    r = _git(repo, "commit", "-q", "-m", "geo refresh", env=env)
    assert r.returncode != 0, "a force-added file that .gitignore excludes was committed"
    assert "org/next_actions.org" in r.stdout + r.stderr
    assert "gitignore" in (r.stdout + r.stderr).lower()


def test_an_ordinary_new_file_still_commits(tmp_path):
    repo, env = _space(tmp_path)
    (repo / "README.md").write_text("plain notes\n")
    _git(repo, "add", "README.md", env=env)
    r = _git(repo, "commit", "-q", "-m", "notes", env=env)
    assert r.returncode == 0, (r.stdout + r.stderr)[-400:]


def _gnu_xargs(tmp_path):
    """A PATH shim with GNU xargs' empty-input behaviour: without -r it runs the command once.

    macOS xargs skips empty input, so the bug below never showed on the Mac.
    """
    bin_dir = tmp_path / "gnu-bin"
    bin_dir.mkdir()
    shim = bin_dir / "xargs"
    shim.write_text(
        "#!/bin/sh\n"
        "input=$(cat | tr -d '\\000')\n"
        "skip=0; args=''\n"
        "for a in \"$@\"; do case \"$a\" in -r|--no-run-if-empty) skip=1;; esac; done\n"
        "if [ -z \"$input\" ]; then\n"
        "  [ $skip = 1 ] && exit 0\n"
        "  while [ $# -gt 0 ]; do case \"$1\" in -*) shift;; *) break;; esac; done\n"
        "  exec \"$@\"\n"
        "fi\n"
        "printf '%s' \"$input\" | tr '\\n' '\\000' | /usr/bin/xargs \"$@\"\n"
    )
    shim.chmod(0o755)
    return bin_dir


def test_a_tracked_then_ignored_file_does_not_block_an_unrelated_commit_on_gnu_xargs(tmp_path):
    """2026-10-30: with no file added, GNU xargs ran `git ls-files --ignored` with no paths,
    which lists every tracked-but-ignored file, so every commit in such a space was refused
    on the Linux agent hosts (dayshift: 0-personal and 2-datacore sat out the overnight run,
    cadence triage failed)."""
    repo, env = _space(tmp_path)
    (repo / "org" / "seen-issues.txt").write_text("state written by a job, tracked before it was ignored\n")
    _git(repo, "add", "org/seen-issues.txt", env=env)
    r = _git(repo, "commit", "-q", "-m", "job state", env=env)
    assert r.returncode == 0, (r.stdout + r.stderr)[-400:]
    (repo / ".gitignore").write_text("org/next_actions.org\norg/seen-issues.txt\n")
    _git(repo, "add", ".gitignore", env=env)
    assert _git(repo, "commit", "-q", "-m", "ignore job state", env=env).returncode == 0

    env = {**env, "PATH": f"{_gnu_xargs(tmp_path)}{os.pathsep}{env['PATH']}"}
    (repo / ".gitignore").write_text("org/next_actions.org\norg/seen-issues.txt\n# edited\n")
    _git(repo, "add", ".gitignore", env=env)
    r = _git(repo, "commit", "-q", "-m", "journal edit (nothing added)", env=env)
    assert r.returncode == 0, (r.stdout + r.stderr)[-400:]
