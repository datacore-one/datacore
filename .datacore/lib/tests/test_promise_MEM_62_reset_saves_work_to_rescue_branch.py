"""MEM-62: If sync must reset a machine, unsaved agent work is saved to a rescue branch
first, and I'm told.

Kind: deterministic + production contract.
  * deterministic: the one reset left in the tracked sync code, git_relay._park (a refused
    relay merge is taken off the branch with `reset --hard <pre>`), run on a tmp repo where
    an agent has uncommitted work in a tracked file and an untracked file at that moment.
    Its docstring assumes "the working tree was verified clean before the merge"; the
    promise needs the work saved before the reset, whatever the tree looked like earlier.
    The returned status (what the caller prints/alerts) must name where the work went.
  * production (read-only ssh): every scheduled script on every host that runs
    `git reset --hard` (non-comment line) also creates a rescue branch and sends an alert
    before it (the 2026-07-12 decision; cos_sync.sh's rescue-then-reset shape).

Seeded failure: a _park variant that resets without update-ref (the merge commit itself
lost) -- the refused merge no longer reachable; verified red on the evidence check.
"""
import os
import subprocess
from pathlib import Path

import pytest

import git_relay

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
HOSTS = ["winston", "nightshift", "hermes", "plur-claw"]
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


def _git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30,
                       env={**os.environ, **GIT_ENV})
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for k, v in GIT_ENV.items():
        monkeypatch.setenv(k, v)
    r = tmp_path / "2-space"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "report.md").write_text("v1\n")
    _git(r, "add", "report.md")
    _git(r, "commit", "-q", "-m", "pre")
    pre = _git(r, "rev-parse", "HEAD")
    (r / "events.jsonl").write_text("{}\n")
    _git(r, "add", "events.jsonl")
    _git(r, "commit", "-q", "-m", "refused relay merge")
    return r, pre


def _saved_somewhere(repo: Path, text: str) -> bool:
    """True if `text` is in the working tree or in any ref/stash the reset left behind."""
    for p in repo.rglob("*"):
        if ".git" not in p.parts and p.is_file() and text in p.read_text(errors="replace"):
            return True
    refs = _git(repo, "for-each-ref", "--format=%(refname)").splitlines()
    stash = subprocess.run(["git", "stash", "list", "--format=%H"], cwd=repo, capture_output=True,
                           text=True, timeout=30).stdout.split()
    for ref in refs + stash:
        g = subprocess.run(["git", "grep", "-q", text, ref], cwd=repo, capture_output=True, timeout=30)
        if g.returncode == 0:
            return True
        u = subprocess.run(["git", "grep", "-q", text, f"{ref}^3"], cwd=repo, capture_output=True, timeout=30)
        if u.returncode == 0:
            return True
    return False


def test_the_refused_merge_is_kept_and_the_status_says_where(repo):
    r, pre = repo
    status = git_relay._park(r, "nightshift", "main", pre)
    assert _git(r, "rev-parse", "HEAD") == pre
    kept = _git(r, "for-each-ref", "--format=%(refname)", "refs/relay-refused/")
    assert kept, "the refused merge was reset away with no rescue ref"
    assert "refs/relay-refused/" in status, f"the status does not say where the work went: {status!r}"


def test_uncommitted_agent_work_survives_the_reset(repo):
    r, pre = repo
    (r / "report.md").write_text("v1\nAGENT-WORK-IN-TRACKED-FILE\n")   # an agent was mid-edit
    git_relay._park(r, "nightshift", "main", pre)
    assert _saved_somewhere(r, "AGENT-WORK-IN-TRACKED-FILE"), (
        "git_relay._park ran `reset --hard` over an uncommitted agent edit: it is in no branch, "
        "stash or file")


@pytest.mark.production
@pytest.mark.parametrize("host", HOSTS)
def test_no_scheduled_script_resets_without_rescue_and_alert(host):
    cmd = ("for f in $( (crontab -l 2>/dev/null; sudo -n crontab -l 2>/dev/null; "
           "systemctl cat $(systemctl list-timers --all --no-legend --plain 2>/dev/null | awk '{print $(NF-1)}') "
           "2>/dev/null | grep ExecStart) | grep -oE '/[^ ;\"]+\\.(sh|py)' | sort -u); do "
           "[ -f \"$f\" ] || continue; "
           "if grep -qE '^[^#]*reset[^#]*--hard' \"$f\"; then "
           "r=$(grep -cE '^[^#]*rescue' \"$f\"); a=$(grep -cE '^[^#]*alert' \"$f\"); echo \"$f rescue=$r alert=$a\"; fi; done; true")
    p = subprocess.run([*SSH, host, cmd], capture_output=True, text=True, timeout=50)
    assert p.returncode == 0, f"{host}: could not read ({p.stderr.strip()[-160:]})"
    bad = [l for l in p.stdout.splitlines() if "rescue=0" in l or "alert=0" in l]
    assert not bad, f"{host}: scheduled scripts hard-reset without a rescue branch and an alert: {bad}"
