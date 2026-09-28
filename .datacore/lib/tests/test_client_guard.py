"""The client guard: ``space_policy_guard.py --client`` (owner decision 2026-09-28).

The owner asked for the client guards only -- a session that has worked in a
client space changes nothing outside it (MEM-13), and a person document outside
the personal space asks first (MEM-15). The rest of space_policy_guard.py (the
space-type policy and the cross-space rule) stays off in this mode, so normal
work across the team spaces is not blocked.
"""
from __future__ import annotations

import json
import os
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

import install_redaction_guards as installer  # noqa: E402
import space_policy_guard as guard  # noqa: E402

PERSON_DOC = ("# Ana Kovac -- CTO offer\n\nSalary: EUR 9,000/month.\n"
              "Equity: 2% vesting over 4 years, 1-year cliff.\n")


def _space(root: Path, name: str, kind: str) -> Path:
    sp = root / name
    (sp / ".datacore").mkdir(parents=True)
    (sp / ".datacore" / "config.yaml").write_text(f"space:\n  name: {name}\n  type: {kind}\n")
    return sp


@pytest.fixture
def install(tmp_path, monkeypatch):
    root = tmp_path / "Data"
    root.mkdir()
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv("DATACORE_ROOT", str(root))
    monkeypatch.setenv("DATACORE_STATE", os.path.realpath(state))
    (root / ".datacore" / "lib").mkdir(parents=True)
    return {
        "root": root,
        "personal": _space(root, "0-personal", "personal"),
        "team": _space(root, "2-datacore", "team"),
        "other": _space(root, "3-fds", "team"),
        "client": _space(root / "1-datafund" / "clients", "the-client", "client"),
    }


def _policy(tmp_path: Path) -> Path:
    p = tmp_path / "space-policy.yaml"
    p.write_text("version: 1\npolicies:\n  client:\n    network: deny\n    write: ask\n"
                 "    default: allow\n  team:\n    default: allow\n")
    return p


def _run(payload: dict, root: Path, tmp_path: Path, argv=("--client",)) -> dict:
    """The guard's answer: {} for allow, else hookSpecificOutput."""
    from spaces import find_space as real

    payload = {"session_id": "s1", **payload}
    out = StringIO()
    with (patch("sys.stdin", StringIO(json.dumps(payload))), patch("sys.stdout", out),
          patch.object(guard, "POLICY_PATH", _policy(tmp_path)),
          patch("space_policy_guard.find_space", lambda p, root=root: real(p, root=root))):
        assert guard.main(list(argv)) == 0
    text = out.getvalue().strip()
    return json.loads(text)["hookSpecificOutput"] if text else {}


def _write(path: Path, cwd: Path, content: str = "x", sid: str = "s1") -> dict:
    return {"session_id": sid, "tool_name": "Write", "cwd": str(cwd),
            "tool_input": {"file_path": str(path), "content": content}}


def _bash(command: str, cwd: Path, sid: str = "s1") -> dict:
    return {"session_id": sid, "tool_name": "Bash", "cwd": str(cwd),
            "tool_input": {"command": command}}


# ── one customer per session (MEM-13) ─────────────────────────────────────────

def test_write_outside_after_a_client_write_is_denied(install, tmp_path):
    root, client, team = install["root"], install["client"], install["team"]
    assert _run(_write(client / "notes.md", root), root, tmp_path).get("permissionDecision") != "deny"
    second = _run(_write(team / "README.md", root), root, tmp_path)
    assert second["permissionDecision"] == "deny"
    assert "the-client" not in second["permissionDecisionReason"]   # never names the customer


def test_a_different_session_is_not_affected(install, tmp_path):
    root, client, team = install["root"], install["client"], install["team"]
    _run(_write(client / "notes.md", root), root, tmp_path)
    assert _run(_write(team / "README.md", root, sid="s2"), root, tmp_path) == {}


def test_a_session_started_in_the_client_space_is_a_client_session(install, tmp_path):
    """A shell write by a relative name names no path; the session's cwd is the client."""
    root, client, team = install["root"], install["client"], install["team"]
    _run(_bash("echo follow-up > notes.md", client), root, tmp_path)
    assert _run(_write(team / "README.md", client), root, tmp_path)["permissionDecision"] == "deny"


def test_git_push_outside_after_a_client_write_is_denied(install, tmp_path):
    root, client, team = install["root"], install["client"], install["team"]
    _run(_write(client / "notes.md", root), root, tmp_path)
    assert _run(_bash("git push", team), root, tmp_path)["permissionDecision"] == "deny"


def test_client_session_may_keep_working_in_the_client_space(install, tmp_path):
    root, client = install["root"], install["client"]
    _run(_write(client / "notes.md", root), root, tmp_path)
    assert _run(_write(client / "plan.md", root), root, tmp_path) == {}
    assert _run(_bash(f"git -C {client} status", root), root, tmp_path) == {}


# ── person documents (MEM-15) ──────────────────────────────────────────────────

def test_person_document_into_a_team_space_asks(install, tmp_path):
    root, team = install["root"], install["team"]
    out = _run(_write(team / "people" / "offer.md", root, PERSON_DOC), root, tmp_path)
    assert out["permissionDecision"] == "ask"


def test_person_document_into_the_personal_space_is_allowed(install, tmp_path):
    root, personal = install["root"], install["personal"]
    assert _run(_write(personal / "1-active" / "firm" / "people" / "offer.md", root, PERSON_DOC),
                root, tmp_path) == {}


# ── what the client mode leaves off ────────────────────────────────────────────

def test_cross_space_work_is_not_blocked(install, tmp_path):
    """The cross-space rule is not part of the client guard."""
    root, team, other = install["root"], install["team"], install["other"]
    assert _run(_write(other / "README.md", team), root, tmp_path) == {}
    assert _run(_bash(f"cat {other}/README.md", team), root, tmp_path) == {}


def test_space_type_policy_is_not_applied(install, tmp_path):
    """No space-type warn/deny in client mode: a first write into the client space is silent."""
    root, client = install["root"], install["client"]
    assert _run(_write(client / "notes.md", root), root, tmp_path) == {}


def test_full_guard_still_applies_the_space_type_policy(install, tmp_path):
    root, client = install["root"], install["client"]
    out = _run(_write(client / "notes.md", root), root, tmp_path, argv=())
    assert "additionalContext" in out


@pytest.mark.parametrize("payload", [
    lambda i: _write(i["team"] / "lib" / "x.py", i["root"], "print('hi')\n"),
    lambda i: _write(i["personal"] / "org" / "inbox.org", i["personal"], "* TODO call Ana Kovac\n"),
    lambda i: _write(i["root"] / ".datacore" / "lib" / "y.py", i["personal"], "x = 1\n"),
    lambda i: _bash("git status && git add a.py && git commit -m 'x'", i["team"]),
    lambda i: _bash(f"git -C {i['other']} pull --no-rebase && git -C {i['other']} push", i["team"]),
    lambda i: _bash(f"python3 {i['root']}/.datacore/lib/promise_evals.py --only MEM-13", i["root"]),
    lambda i: {"session_id": "s1", "tool_name": "Read", "cwd": str(i["team"]),
               "tool_input": {"file_path": str(i["other"] / "README.md")}},
])
def test_normal_work_without_a_client_is_silent(install, tmp_path, payload):
    assert _run(payload(install), install["root"], tmp_path) == {}


# ── the installer wires it under its own name ──────────────────────────────────

def test_installer_offers_a_client_guard_with_only_the_client_mode():
    rows = [w for w in installer.WIRING if w[0] == "client"]
    assert rows, "no 'client' guard in the installer"
    assert all(r[3].endswith("space_policy_guard.py --client") for r in rows)
    assert {r[1] for r in rows} == {"PreToolUse"}


def test_installer_keeps_client_and_space_apart():
    """Wiring one does not count as the other being wired (substring match)."""
    client_cmd = next(w[3] for w in installer.WIRING if w[0] == "client")
    space_cmd = next(w[3] for w in installer.WIRING if w[0] == "space")
    wired_client = [{"hooks": [{"command": client_cmd}]}]
    wired_space = [{"hooks": [{"command": space_cmd}]}]
    assert not installer.already(wired_client, space_cmd)
    assert not installer.already(wired_space, client_cmd)
    assert installer.already(wired_client, client_cmd)


def test_person_document_question_never_names_the_client(install, tmp_path):
    root, client = install["root"], install["client"]
    out = _run(_write(client / "people" / "offer.md", root, PERSON_DOC), root, tmp_path)
    assert out["permissionDecision"] == "ask"
    assert "the-client" not in out["permissionDecisionReason"]


def test_spaces_are_discovered_once_per_call(install, tmp_path):
    """Discovery costs ~0.3 s; a hook that runs on every tool call walks the install once."""
    import spaces
    root, team, other = install["root"], install["team"], install["other"]
    real, calls = spaces.discover_spaces, []

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    with patch.object(spaces, "discover_spaces", counting):
        _run(_bash(f"cp {team}/a.md {other}/b.md && ls {team}/x {other}/y", team), root, tmp_path)
    assert len(calls) <= 1, f"discovered spaces {len(calls)} times in one hook call"


# ── only a write makes a client session; only a write is stopped ───────────────

def test_reading_the_client_space_does_not_make_a_client_session(install, tmp_path):
    """Owner's reading of the promise: a session that has WRITTEN in a client space."""
    root, client, team = install["root"], install["client"], install["team"]
    _run({"session_id": "s1", "tool_name": "Read", "cwd": str(root),
          "tool_input": {"file_path": str(client / "journal" / "a.md")}}, root, tmp_path)
    _run(_bash(f"cd {client}/journal && grep -n hook a.md | head -5", root), root, tmp_path)
    assert _run(_write(team / "docs" / "notes.md", root), root, tmp_path) == {}


def test_a_shell_write_into_the_client_space_makes_a_client_session(install, tmp_path):
    root, client, team = install["root"], install["client"], install["team"]
    _run(_bash(f"cat >> {client}/notes.md <<'EOF'\nfollow-up\nEOF", root), root, tmp_path)
    assert _run(_write(team / "README.md", root), root, tmp_path)["permissionDecision"] == "deny"


def test_client_session_may_still_read_elsewhere_by_shell(install, tmp_path):
    root, client, team = install["root"], install["client"], install["team"]
    _run(_write(client / "notes.md", root), root, tmp_path)
    assert _run(_bash(f"sed -n 1,40p {team}/src/server.ts; grep -rn Bearer src/auth 2>/dev/null | head",
                      team), root, tmp_path) == {}


@pytest.mark.parametrize("command", [
    "git add a.py && git commit -m 'x'",
    "echo x > notes.md",
    "gh issue create --title 'x' --body 'y'",
])
def test_client_session_shell_write_outside_is_denied(install, tmp_path, command):
    root, client, team = install["root"], install["client"], install["team"]
    _run(_write(client / "notes.md", root), root, tmp_path)
    assert _run(_bash(command, team), root, tmp_path)["permissionDecision"] == "deny"


def test_client_session_may_cd_into_the_client_space_and_commit(install, tmp_path):
    root, client = install["root"], install["client"]
    _run(_write(client / "notes.md", root), root, tmp_path)
    assert _run(_bash(f"cd {client} && git add notes.md && git commit -m 'x'", root), root, tmp_path) == {}
