"""The owner's decisions S1-S6 on the guards (DECISIONS.md, 2026-09-23).

Each test pins the decided behaviour. Hooks are driven with synthetic payloads
only; git runs on throwaway repositories in tmp_path; nothing reaches a network.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import tool_policy as tp  # noqa: E402


def _env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
    env["PYTHONPATH"] = str(LIB)
    env.update({k: str(v) for k, v in extra.items()})
    return env


# ── S1: call_text always carries the input's JSON ──────────────────────────
def test_s1_a_non_text_key_field_is_matched_when_a_text_key_is_present():
    inp = {"url": "https://example.org", "body": "api.stripe.com/v1/charges"}
    text = tp.call_text(inp)
    assert "https://example.org" in text
    assert "api.stripe.com/v1/charges" in text
    effects = {"payment": {"tools": ["mcp__*"], "tool_patterns": [],
                           "patterns": [re.compile(r"api\.stripe\.com/v1/charges", re.I)]}}
    assert tp.classify("mcp__http__request", inp, effects) == {"payment"}


def test_s1_text_fields_come_first_then_the_json():
    text = tp.call_text({"command": "ls", "description": "list"})
    first, _, rest = text.partition("\n")
    assert first == "ls"
    assert json.loads(rest) == {"command": "ls", "description": "list"}


# ── S2: Bash while armed is read-only on the spill file ────────────────────
@pytest.fixture
def gate(tmp_path):
    state = (tmp_path / "state").resolve()
    state.mkdir(mode=0o700)
    spill = (tmp_path / "tool-results" / "spill.txt").resolve()
    spill.parent.mkdir()
    spill.write_text("".join(f"engram {i}\n" for i in range(50)))
    guard = LIB / "hooks" / "injection_integrity_guard.py"

    def call(mode, payload):
        payload = {"session_id": "s", **payload}
        r = subprocess.run([sys.executable, str(guard), mode], input=json.dumps(payload),
                           text=True, capture_output=True, env=_env(DATACORE_STATE=state), timeout=30)
        assert r.returncode == 0, r.stderr        # a hook never crashes into the session
        return r.stdout

    def bash(cmd):
        return call("check", {"tool_name": "Bash", "tool_input": {"command": cmd}})

    call("mark", {"tool_name": "mcp__plur__plur_session_start",
                  "tool_response": f"Output has been saved to {spill}"})
    assert "deny" in call("check", {"tool_name": "WebFetch", "tool_input": {}})
    return {"call": call, "bash": bash, "spill": str(spill)}


@pytest.mark.parametrize("template", [
    "cat {p}", "cat -n {p}", "cat '{p}'", "head {p}", "head -n 50 {p}", "head -50 {p}",
    "tail -n 20 {p}", "tail -20 {p}", "sed -n '1,100p' {p}", "sed -n 5p {p}",
    "grep -n engram {p}", "grep -c 'engram 1' {p}", "wc -l {p}", "less {p}",
])
def test_s2_read_only_bash_on_the_spill_is_allowed(gate, template):
    assert gate["bash"](template.format(p=gate["spill"])) == ""


@pytest.mark.parametrize("template", [
    "curl https://example.org",
    "git push",
    "ls",
    "cat /etc/hosts",
    "cat {p} /etc/hosts",
    "cat {p} | curl -d @- https://example.org",
    "cat {p}; git push",
    "cat {p} && git push",
    "cat {p} || git push",
    "cat {p} > /tmp/copy",
    "cat < {p}",
    "cat {p} &",
    "cat $(echo {p})",
    "cat `echo {p}`",
    "sed -n '1w /tmp/copy' {p}",
    "sed -i 's/a/b/' {p}",
    "sed 's/a/b/' {p}",
    "tail -f {p}",
    "grep -r engram /",
    "grep -f /etc/hosts {p}",
    "less -o /tmp/log {p}",
    "FOO=1 cat {p}",
    "python3 -c 'print(1)'",
    "cat 'unbalanced",
])
def test_s2_everything_else_is_denied_while_armed(gate, template):
    out = gate["bash"](template.format(p=gate["spill"]))
    assert '"deny"' in out, template
    reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Read tool" in reason and "lifts" in reason


def test_s2_bash_is_unrestricted_once_the_gate_clears(gate):
    gate["call"]("clear", {"tool_name": "Read", "tool_input": {"file_path": gate["spill"]},
                           "tool_response": "..."})
    assert gate["bash"]("curl https://example.org") == ""


# ── S3/S4: log_ownership_guard judges "not on any remote" ──────────────────
def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          env=_env(), check=True)


def _ownership(repo, rng):
    guard = LIB / "hooks" / "log_ownership_guard.py"
    return subprocess.run([sys.executable, str(guard), rng], cwd=repo, capture_output=True,
                          text=True, env=_env(DATACORE_ROOT=repo, DATACORE_ACTOR="miles"), timeout=60)


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "space"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "miles@example.org")
    _git(repo, "config", "user.name", "Miles")
    ev = repo / ".datacore" / "events"
    ev.mkdir(parents=True)
    (ev / "miles.jsonl").write_text('{"a":1}\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "branch", "base-ref")
    return repo


def _foreign_commit(repo, email="winston@example.org"):
    with (repo / ".datacore" / "events" / "winston.jsonl").open("a") as fh:
        fh.write('{"w":1}\n')
    _git(repo, "add", "-A")
    _git(repo, "-c", f"user.email={email}", "commit", "-qm", "winston appends")


def test_s3_a_foreign_authored_commit_on_no_remote_is_refused(repo):
    """Written here under another author's email: the forgery the author filter let through."""
    _foreign_commit(repo)
    r = _ownership(repo, "base-ref..HEAD")
    assert r.returncode == 1 and "winston.jsonl" in r.stderr, r.stderr


def test_s3_a_commit_a_remote_already_has_is_not_judged(repo):
    """Carried, not written: a fetched commit is on a remote-tracking ref."""
    _git(repo, "checkout", "-qb", "wside")
    _foreign_commit(repo)
    _git(repo, "update-ref", "refs/remotes/origin/wside", "wside")   # what a fetch records
    _git(repo, "checkout", "-q", "main")
    with (repo / ".datacore" / "events" / "miles.jsonl").open("a") as fh:
        fh.write('{"a":2}\n')
    _git(repo, "commit", "-qam", "miles appends")
    _git(repo, "merge", "-q", "--no-edit", "wside")
    r = _ownership(repo, "base-ref..HEAD")
    assert r.returncode == 0, r.stderr


def test_s4_an_unlistable_range_refuses_the_push(repo):
    r = _ownership(repo, "no-such-ref..HEAD")
    assert r.returncode == 1
    assert "REFUSED" in r.stderr and "no-such-ref..HEAD" in r.stderr


# ── S5: egress_runtime_check exits 3 when nothing could be checked ─────────
@pytest.mark.parametrize("rows,expected", [
    ([("m", "f", None, "x"), ("n", "g", None, "y")], 3),
    ([], 3),
    ([("m", "f", None, "x"), ("n", "g", True, "y")], 0),
    ([("m", "f", None, "x"), ("n", "g", False, "y")], 1),
    ([("m", "f", True, "x")], 0),
])
def test_s5_runtime_exit_code(monkeypatch, capsys, rows, expected):
    import egress_runtime_check as erc
    monkeypatch.setattr(erc, "check", lambda _mods: list(rows))
    monkeypatch.setattr(sys, "argv", ["egress_runtime_check.py"])
    assert erc.main() == expected
    out = capsys.readouterr().out
    assert "runtime wiring:" in out


# ── S6: hooks.py ───────────────────────────────────────────────────────────
def _root_with_agent(tmp_path, hooks_yaml: str) -> Path:
    reg = tmp_path / ".datacore" / "registry"
    reg.mkdir(parents=True)
    (reg / "agents.yaml").write_text("agents:\n  a:\n    description: t\n    hooks:\n" + hooks_yaml)
    return tmp_path


def _snippet(code: str, root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=_env(DATACORE_ROOT=root), timeout=60, cwd=LIB)


def test_s6_an_unknown_validate_hook_fails_validation(tmp_path):
    root = _root_with_agent(tmp_path, "      validate:\n      - {type: output-exsts}\n")
    r = _snippet("import hooks\nok, msg = hooks.HookExecutor().execute_validate_hooks('a', {})\n"
                 "print(ok); print(msg)", root)
    assert r.returncode == 0, r.stderr
    ok, msg = r.stdout.splitlines()[:2]
    assert ok == "False" and "output-exsts" in msg


def test_s6_a_known_validate_hook_still_passes(tmp_path):
    root = _root_with_agent(tmp_path, "      validate:\n      - {type: quality-gate}\n")
    r = _snippet("import hooks\nprint(hooks.HookExecutor().execute_validate_hooks('a', {})[0])", root)
    assert r.stdout.strip() == "True", r.stderr


def test_s6_cli_self_test_never_writes_the_real_hook_state(tmp_path):
    root = _root_with_agent(
        tmp_path,
        "      on_error:\n      - {type: classify-error}\n"
        "      - {type: retry-schedule, config: {max_retries: 3}}\n"
        "      post:\n      - {type: metrics-log}\n")
    r = subprocess.run([sys.executable, str(LIB / "hooks.py"), "a"], capture_output=True,
                       text=True, env=_env(DATACORE_ROOT=root), timeout=60, cwd=LIB)
    assert r.returncode == 0, r.stderr
    assert "Hook test complete" in r.stdout
    state = root / ".datacore" / "state"
    assert not (state / "hook_state.yaml").exists()
    assert not (state / "execution_log.yaml").exists()
