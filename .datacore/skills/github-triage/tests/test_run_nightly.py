"""The nightly runner: two separate runs (public, then enterprise), the
read-only gh shim first on PATH, full sweep on Sundays, each board validated,
and only the two output files per scope committed to the personal space."""
import json
import os
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
RUNNER = SKILL / "run_nightly.sh"

STUB = r'''#!/usr/bin/env bash
prompt="$(cat)"
scope=$(printf '%s' "$prompt" | sed -n 's/.*SCOPE=\([a-z]*\).*/\1/p' | head -1)
mode=$(printf '%s' "$prompt" | sed -n 's/.*MODE=\([a-z]*\).*/\1/p' | head -1)
date=$(printf '%s' "$prompt" | sed -n 's/.*DATE=\([0-9-]*\).*/\1/p' | head -1)
owner=$(printf '%s' "$prompt" | sed -n 's/.*OWNER=\([A-Za-z0-9-]*\).*/\1/p' | head -1)
echo "OWNER $owner" >> "$STUB_LOG.owner"
echo "$scope $mode $(command -v gh)" >> "$STUB_LOG"
out="$DATA_DIR/0-personal/notes/github-triage"; mkdir -p "$out"
echo "# report $scope" > "$out/$date-$scope.md"
cat > "$out/$date-$scope.board.json" <<JSON
{"meta":{"slug":"github-triage-$date-$scope","title":"GitHub triage — $scope","h1":"GitHub triage","eyebrow":"x","lede":"x","asOf":"$date 03:40 UTC","path":[["a","b"]]},
 "sections":[{"key":"waiting","label":"Waiting on you","rows":[{"id":"W1","area":"a/b #1","title":"t","context":"c","suggested":"keep",
 "options":[{"value":"keep","label":"Keep","consequence":"x"},{"value":"close","label":"Close","consequence":"y"}],"meta":["m","https://github.com/a/b/pull/1"]}]}],"prefill":{}}
JSON
echo "unrelated" > "$DATA_DIR/0-personal/stray.txt"
'''


def _setup(tmp_path):
    data = tmp_path / "Data"
    personal = data / "0-personal"
    personal.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(personal)], check=True)
    subprocess.run(["git", "-C", str(personal), "commit", "-q", "--allow-empty", "-m", "init",
                    "-c", "user.email=t@t", "-c", "user.name=t"], check=False)
    subprocess.run(["git", "-C", str(personal), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "claude").write_text(STUB)
    (bindir / "claude").chmod(0o755)
    env = dict(os.environ, DATA_DIR=str(data), CLAUDE_BIN=str(bindir / "claude"),
               STUB_LOG=str(tmp_path / "calls.log"), TRIAGE_NO_PUSH="1",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    return data, personal, env


def test_two_separate_runs_readonly_gh_and_commit_only_outputs(tmp_path):
    data, personal, env = _setup(tmp_path)
    r = subprocess.run(["bash", str(RUNNER), "--date", "2026-09-30"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert [c.split()[0] for c in calls] == ["public", "enterprise"]
    assert all(c.split()[1] == "incremental" for c in calls)  # 2026-09-30 is a Wednesday
    assert all(c.split()[2] == str(SKILL / "bin" / "gh") for c in calls)
    committed = subprocess.run(["git", "-C", str(personal), "show", "--name-only", "--format=", "HEAD"],
                               capture_output=True, text=True).stdout.split()
    assert sorted(committed) == sorted([
        "notes/github-triage/2026-09-30-public.md", "notes/github-triage/2026-09-30-public.board.json",
        "notes/github-triage/2026-09-30-enterprise.md", "notes/github-triage/2026-09-30-enterprise.board.json"])
    assert "stray.txt" not in committed
    # The owner is named explicitly: on the nightshift host gh is logged in as the bot, not the owner.
    owners = set((tmp_path / "calls.log.owner").read_text().split()) - {"OWNER"}
    assert owners == {"plur9"}


def test_sunday_is_a_full_sweep(tmp_path):
    data, personal, env = _setup(tmp_path)
    r = subprocess.run(["bash", str(RUNNER), "--date", "2026-10-04"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert all(c.split()[1] == "full" for c in calls)
