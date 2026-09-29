"""The nightly triage runs with permissions skipped, so read-only on GitHub is
enforced by a `gh` shim first on PATH, not by instructions alone (owner,
2026-09-29: the triage is read-only; closes and merges wait for him)."""
import os
import subprocess
from pathlib import Path

SHIM = Path(__file__).resolve().parent.parent / "bin" / "gh"


def _run(tmp_path, *args):
    fake = tmp_path / "real-gh"
    fake.write_text("#!/bin/sh\necho REAL \"$@\"\n")
    fake.chmod(0o755)
    env = dict(os.environ, GH_REAL=str(fake))
    return subprocess.run([str(SHIM), *args], capture_output=True, text=True, env=env)


def test_reads_pass_through(tmp_path):
    for args in (["pr", "list", "-R", "a/b"], ["pr", "view", "1"], ["pr", "diff", "1"], ["pr", "checks", "1"],
                 ["issue", "list"], ["issue", "view", "2"], ["run", "list"], ["search", "prs", "x"],
                 ["repo", "view", "a/b"], ["api", "repos/a/b/pulls"], ["api", "user", "--jq", ".login"],
                 ["api", "-X", "GET", "repos/a/b"], ["auth", "status"]):
        r = _run(tmp_path, *args)
        assert r.returncode == 0 and r.stdout.startswith("REAL"), (args, r.stderr)


def test_writes_are_refused(tmp_path):
    for args in (["pr", "merge", "1"], ["pr", "close", "1"], ["pr", "comment", "1", "-b", "x"],
                 ["pr", "review", "1", "--approve"], ["pr", "edit", "1"], ["issue", "close", "2"],
                 ["issue", "create"], ["issue", "comment", "2"], ["label", "create", "x"],
                 ["api", "-X", "POST", "repos/a/b/issues"], ["api", "--method", "PATCH", "x"],
                 ["api", "repos/a/b/issues", "-f", "title=x"], ["api", "graphql", "-f", "query=mutation{x}"],
                 ["repo", "delete", "a/b"], ["workflow", "run", "x"], ["secret", "set", "X"]):
        r = _run(tmp_path, *args)
        assert r.returncode != 0 and "REAL" not in r.stdout, (args, r.stdout)
