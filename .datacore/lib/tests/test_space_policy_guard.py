"""Tests for space_policy_guard.py (datacore#43).

Covers:
- Session outside a space reaching in by absolute path
- git -C <client-space> from a team-space session
- Unrecognised space type → allow (no enumeration gap)
- Policy file missing → allow (fail open for non-deny path; policy unreadable)
- deny rule → JSON permissionDecision=deny
- ask rule → JSON additionalContext warning
- allow rule → exit 0, no JSON output
- Read tool → always allow (default: allow in policy)
"""
from __future__ import annotations

import json
import sys
import types
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

import space_policy_guard as guard


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _client_space(tmp_path: Path, name: str = "acme") -> Path:
    sp = tmp_path / name
    sp.mkdir()
    marker = sp / ".datacore"
    marker.mkdir()
    (marker / "config.yaml").write_text(
        f"space:\n  name: {name}\n  type: client\n", encoding="utf-8"
    )
    return sp


def _team_space(tmp_path: Path, name: str = "datacore") -> Path:
    sp = tmp_path / name
    sp.mkdir()
    marker = sp / ".datacore"
    marker.mkdir()
    (marker / "config.yaml").write_text(
        f"space:\n  name: {name}\n  type: team\n", encoding="utf-8"
    )
    return sp


def _policy(tmp_path: Path) -> Path:
    p = tmp_path / "space-policy.yaml"
    p.write_text(
        "version: 1\n"
        "policies:\n"
        "  client:\n"
        "    network: deny\n"
        "    write: ask\n"
        "    default: allow\n"
        "  team:\n"
        "    default: allow\n",
        encoding="utf-8",
    )
    return p


def _run(payload: dict, policy_path: Path, root: Path) -> tuple[int, dict | None]:
    """Run main() with the given payload; return (exit_code, parsed_json | None)."""
    stdin = StringIO(json.dumps(payload))
    stdout = StringIO()
    with (
        patch("sys.stdin", stdin),
        patch("sys.stdout", stdout),
        patch.object(guard, "POLICY_PATH", policy_path),
    ):
        from spaces import find_space as real_find_space

        def _patched_find_space(path, root=root):  # noqa: ANN001
            return real_find_space(path, root=root)

        with patch("space_policy_guard.find_space", _patched_find_space):
            code = guard.main()

    out = stdout.getvalue().strip()
    parsed = json.loads(out) if out else None
    return code, parsed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hso(parsed: dict | None) -> dict:
    assert parsed is not None
    return parsed["hookSpecificOutput"]


# ---------------------------------------------------------------------------
# deny tests
# ---------------------------------------------------------------------------

class TestNetworkDeny:
    def test_git_push_in_client_space(self, tmp_path):
        client = _client_space(tmp_path)
        policy = _policy(tmp_path)
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {client} push origin main"},
            "cwd": str(tmp_path),
        }
        code, parsed = _run(payload, policy, tmp_path)
        assert code == 0
        hso = _hso(parsed)
        assert hso["permissionDecision"] == "deny"
        assert "network" in hso["permissionDecisionReason"]
        assert "client" in hso["permissionDecisionReason"]

    def test_git_push_cwd_is_client_space(self, tmp_path):
        client = _client_space(tmp_path)
        policy = _policy(tmp_path)
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
            "cwd": str(client),
        }
        code, parsed = _run(payload, policy, tmp_path)
        assert code == 0
        hso = _hso(parsed)
        assert hso["permissionDecision"] == "deny"

    def test_git_fetch_in_client_space(self, tmp_path):
        client = _client_space(tmp_path)
        policy = _policy(tmp_path)
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {client} fetch"},
            "cwd": str(tmp_path),
        }
        code, parsed = _run(payload, policy, tmp_path)
        assert code == 0
        assert _hso(parsed)["permissionDecision"] == "deny"

    def test_session_outside_reaches_in_by_absolute_path(self, tmp_path):
        """Session cwd is NOT in the client space; git -C reaches into it."""
        team = _team_space(tmp_path)
        client = _client_space(tmp_path, name="acme-client")
        policy = _policy(tmp_path)
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {client} push"},
            "cwd": str(team),
        }
        code, parsed = _run(payload, policy, tmp_path)
        assert code == 0
        assert _hso(parsed)["permissionDecision"] == "deny"


# ---------------------------------------------------------------------------
# ask tests
# ---------------------------------------------------------------------------

class TestWriteAsk:
    def test_edit_in_client_space_warns(self, tmp_path):
        client = _client_space(tmp_path)
        policy = _policy(tmp_path)
        file_path = client / "notes.md"
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(file_path)},
            "cwd": str(tmp_path),
        }
        code, parsed = _run(payload, policy, tmp_path)
        assert code == 0
        hso = _hso(parsed)
        assert "additionalContext" in hso
        assert "write" in hso["additionalContext"].lower()
        assert "client" in hso["additionalContext"].lower()

    def test_write_in_client_space_warns(self, tmp_path):
        client = _client_space(tmp_path)
        policy = _policy(tmp_path)
        file_path = client / "output.txt"
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": str(file_path)},
            "cwd": str(tmp_path),
        }
        code, parsed = _run(payload, policy, tmp_path)
        assert code == 0
        hso = _hso(parsed)
        assert "additionalContext" in hso


# ---------------------------------------------------------------------------
# allow tests
# ---------------------------------------------------------------------------

class TestTeamSpaceAllow:
    def test_git_push_in_team_space_allowed(self, tmp_path):
        team = _team_space(tmp_path)
        policy = _policy(tmp_path)
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {team} push"},
            "cwd": str(tmp_path),
        }
        code, parsed = _run(payload, policy, tmp_path)
        assert code == 0
        assert parsed is None  # silent allow

    def test_edit_in_team_space_allowed(self, tmp_path):
        team = _team_space(tmp_path)
        policy = _policy(tmp_path)
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(team / "notes.md")},
            "cwd": str(tmp_path),
        }
        code, parsed = _run(payload, policy, tmp_path)
        assert code == 0
        assert parsed is None


class TestReadAlwaysAllow:
    def test_read_in_client_space_allowed(self, tmp_path):
        client = _client_space(tmp_path)
        policy = _policy(tmp_path)
        payload = {
            "tool_name": "Read",
            "tool_input": {"file_path": str(client / "doc.md")},
            "cwd": str(tmp_path),
        }
        code, parsed = _run(payload, policy, tmp_path)
        assert code == 0
        # Read resolves to `read` category; default: allow in client → no block
        assert parsed is None


class TestPathOutsideAnySpace:
    def test_path_not_in_space_allows(self, tmp_path):
        policy = _policy(tmp_path)
        # tmp_path itself has no space marker
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(tmp_path / "random.txt")},
            "cwd": str(tmp_path),
        }
        code, parsed = _run(payload, policy, tmp_path)
        assert code == 0
        assert parsed is None


# ---------------------------------------------------------------------------
# Unknown / unrecognised space type
# ---------------------------------------------------------------------------

class TestUnknownSpaceType:
    def test_unknown_type_allows(self, tmp_path):
        """A space type not in the policy → allow (no enumeration gap)."""
        sp = tmp_path / "exotic"
        sp.mkdir()
        marker = sp / ".datacore"
        marker.mkdir()
        (marker / "config.yaml").write_text(
            "space:\n  name: exotic\n  type: partner\n", encoding="utf-8"
        )
        policy = _policy(tmp_path)
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {sp} push"},
            "cwd": str(tmp_path),
        }
        code, parsed = _run(payload, policy, tmp_path)
        assert code == 0
        assert parsed is None  # unknown type → allow


# ---------------------------------------------------------------------------
# Policy file missing
# ---------------------------------------------------------------------------

class TestMissingPolicy:
    def test_missing_policy_allows(self, tmp_path):
        client = _client_space(tmp_path)
        missing = tmp_path / "no-such-policy.yaml"
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {client} push"},
            "cwd": str(tmp_path),
        }
        code, parsed = _run(payload, missing, tmp_path)
        assert code == 0
        assert parsed is None  # policy unreadable → allow
