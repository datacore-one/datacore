"""Tests for the restricted-hosts guard.

Uses a fictional `acme` host throughout. No real customer name belongs in this
file — it is tracked in a public repository, which is the exact disclosure the
guard exists to prevent.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

GUARD = Path(__file__).resolve().parents[1] / "lib" / "hooks" / "restricted_hosts_guard.py"

sys.path.insert(0, str(GUARD.parent))
import restricted_hosts_guard as guard  # noqa: E402


@pytest.fixture
def configured(tmp_path, monkeypatch):
    """Guard with a public file carrying no names and a private overlay that does."""
    public = tmp_path / "restricted_hosts.json"
    public.write_text(json.dumps({
        "hosts": [], "networks": ["192.168.253."], "note": "generic",
    }))
    private = tmp_path / "private.yaml"
    private.write_text(yaml.safe_dump({
        "restricted_hosts": {"hosts": ["acme"], "note": "Client agreement."}
    }))
    monkeypatch.setattr(guard, "CONFIG", public)
    monkeypatch.setattr(guard, "PRIVATE_CONFIG", private)
    return guard.load()


def blocked(command, config, cwd=None):
    return guard.offending(command, config, cwd) is not None


# ── the public file must never carry names ───────────────────────────────────

def test_shipped_public_config_names_no_hosts():
    """Regression: a customer host name was once committed to this public file."""
    shipped = json.loads((GUARD.parent / "restricted_hosts.json").read_text())
    assert shipped["hosts"] == [], "public config must list no host names"


def test_private_overlay_is_unioned(configured):
    assert "acme" in configured["hosts"]
    assert configured["note"] == "Client agreement."


# ── the holes this fixes ─────────────────────────────────────────────────────

def test_git_push_is_evaluated_via_the_remote(configured, tmp_path):
    """`git push origin main` names no host — the target is in .git/config."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin",
                    "https://git.acme.si/x/y.git"], cwd=repo, check=True)

    assert blocked("git push origin main", configured, str(repo))
    assert blocked(f"git -C {repo} push", configured)
    assert blocked("git fetch", configured, str(repo))


def test_unrelated_repo_is_not_blocked(configured, tmp_path):
    repo = tmp_path / "other"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin",
                    "https://github.com/x/y.git"], cwd=repo, check=True)
    assert not blocked("git push origin main", configured, str(repo))


def test_host_matches_as_a_domain_label(configured):
    """`acme` must catch git.acme.si — previously it required a word boundary."""
    assert blocked("curl https://git.acme.si/", configured)
    assert blocked("curl https://acme.si/", configured)
    assert blocked("ssh acme", configured)
    assert blocked("scp file user@git.acme.si:/tmp", configured)


def test_similar_names_are_not_caught(configured):
    assert not blocked("curl https://acmecorp.com/", configured)
    assert not blocked("curl https://notacme.io/", configured)


# ── over-matching, which makes a guard get switched off ──────────────────────

def test_mentioning_the_name_without_reaching_anything(configured):
    assert not blocked("grep acme notes.md", configured)
    assert not blocked("ls ~/clients/acme/", configured)
    assert not blocked("cat acme/README.md", configured)


def test_local_ssh_config_read_is_not_blocked(configured):
    """Regression: reading ~/.ssh/config mentioning the host used to block."""
    assert not blocked("awk '/^Host acme/' ~/.ssh/config", configured)


def test_remote_tool_word_in_one_segment_does_not_arm_another(configured):
    """Regression: the word "scp-style" in a commit message armed host-matching
    against an unrelated `grep` token in an &&-chained segment."""
    command = (
        "grep -ic 'acme' file.py && git commit -F - <<'EOF'\n"
        "targets are extracted from URLs, scp-style destinations and remote-tool\n"
        "arguments rather than grepped from raw text\n"
        "EOF"
    )
    assert not blocked(command, configured)


def test_bare_destination_still_read_from_its_own_segment(configured):
    assert blocked("cd /tmp && ssh acme", configured)
    assert blocked("rsync -a src acme:/dst", configured)
    assert not blocked("echo acme && ls -la", configured)


def test_networks_match_anywhere(configured):
    assert blocked("curl http://192.168.253.10/", configured)


# ── failure behaviour ────────────────────────────────────────────────────────

def test_unreadable_overlay_is_fail_closed_for_network_commands(tmp_path, monkeypatch):
    private = tmp_path / "private.yaml"
    private.write_text("restricted_hosts: [unclosed\n")
    monkeypatch.setattr(guard, "PRIVATE_CONFIG", private)
    with pytest.raises(guard.Unevaluable):
        guard.load()


def test_missing_overlay_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "PRIVATE_CONFIG", tmp_path / "absent.yaml")
    monkeypatch.setattr(guard, "CONFIG", tmp_path / "absent.json")
    assert guard.load()["hosts"] == []


@pytest.mark.parametrize("command, reaches", [
    ("git push origin main", True),
    ("git status", False),
    ("ssh acme", True),
    ("ls -la", False),
    ("curl https://example.com", True),
    ("grep acme file.txt", False),
])
def test_can_reach_network_classification(command, reaches):
    assert guard._can_reach_network(command, guard._tokens(command)) is reaches
