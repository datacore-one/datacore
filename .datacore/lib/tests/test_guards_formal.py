"""Defects the Lean model DatacoreSpec/Guards.lean found in the guards.

Each test replays a counterexample from the model against the real script,
piping synthetic hook JSON in exactly as Claude Code does. Nothing here
reaches a network: the restricted-host guard only runs `git remote -v` on
throwaway repositories, and every host name is fictional.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

GUARD = Path(os.environ.get("GUARDS_UNDER_TEST", LIB / "hooks")) / "restricted_hosts_guard.py"


# ── restricted_hosts_guard ─────────────────────────────────────────────────
@pytest.fixture
def world(tmp_path):
    """A fake HOME whose private list restricts `acme`, a repo whose origin is
    on acme, and a repo whose origin is not."""
    home = tmp_path / "home"
    (home / ".datacore" / "private").mkdir(parents=True)
    (home / ".datacore" / "private" / "customer-denylist.yaml").write_text(
        "restricted_hosts:\n  hosts: [acme]\n")
    bad, ok = tmp_path / "bad", tmp_path / "ok"
    for repo, url in ((bad, "https://git.acme.example/x.git"), (ok, "https://github.com/ok/x.git")):
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", url], check=True)
    return {"home": home, "bad": bad, "ok": ok, "tmp": tmp_path}


def run_guard(world, command: str, cwd: Path) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    env = {**os.environ, "HOME": str(world["home"])}
    return subprocess.run([sys.executable, str(GUARD)], input=payload, text=True,
                          capture_output=True, env=env, timeout=30).returncode


@pytest.mark.parametrize("template", [
    "git push",                              # the baseline the guard always caught
    "cd {bad} && git push",                  # git not the first token of the command
    "FOO=1 git push",                        # env assignment prefix
    "env git push",                          # wrapper
    "env -i PATH=/usr/bin git push origin main",
    "git -c k=v push",                       # -c's value read as the subcommand
    "git --git-dir {bad}/.git push",
    "sudo -u me git push",
    "timeout 30 git push",
    "(cd {bad}; git push)",                  # subshell
    "true; git fetch",
    "sleep 1 & git push",
    "bash -c 'git push'",
    "sh -c \"cd {bad} && git pull\"",
    "eval git push",
    "echo $(git ls-remote)",
])
def test_network_git_with_restricted_remote_is_blocked(world, template):
    command = template.format(bad=world["bad"])
    assert run_guard(world, command, world["bad"]) == 2, command


@pytest.mark.parametrize("template", [
    "cd {bad} && git push",
    "(cd {bad}; git push)",
    "git -C {bad} push",
])
def test_cd_into_restricted_repo_from_safe_cwd_is_blocked(world, template):
    assert run_guard(world, template.format(bad=world["bad"]), world["ok"]) == 2


@pytest.mark.parametrize("command", [
    "sudo ssh acme", "timeout 5 ssh acme", "nohup ssh acme", "(ssh acme)", "env ssh acme",
])
def test_wrapped_remote_tool_to_restricted_host_is_blocked(world, command):
    assert run_guard(world, command, world["ok"]) == 2


@pytest.mark.parametrize("command", [
    "git push", "git status", "cd {bad} && git status", "git log --oneline",
    "grep acme notes.md", "ls -la && echo done", "git commit -m 'fix (scp-style) parsing'",
    "cd {ok} && git fetch", "FOO=1 git pull",
])
def test_safe_commands_still_pass(world, command):
    assert run_guard(world, command.format(bad=world["bad"], ok=world["ok"]), world["ok"]) == 0


def test_cd_to_runtime_directory_before_network_git_fails_closed(world):
    assert run_guard(world, "cd $REPO && git push", world["ok"]) == 2
    assert run_guard(world, "cd $REPO && git status", world["ok"]) == 0


@pytest.mark.parametrize("overlay", [
    "- acme\n",                               # a list, not a mapping
    "restricted_hosts:\n  - acme\n",          # restricted_hosts as a list
    "restricted_hosts:\n  hosts: acme\n",     # hosts as a bare string
])
def test_malformed_private_list_fails_closed_for_network_commands(world, overlay):
    (world["home"] / ".datacore" / "private" / "customer-denylist.yaml").write_text(overlay)
    assert run_guard(world, "ssh acme", world["ok"]) == 2
    assert run_guard(world, "ls -la", world["ok"]) == 0      # cannot reach: fail open


def test_git_failure_other_than_not_a_repo_fails_closed(world):
    cfg = world["bad"] / ".git" / "config"
    cfg.write_text(cfg.read_text() + "\n[broken\n")
    assert run_guard(world, "git push", world["bad"]) == 2


def test_non_repository_directory_is_nothing_to_reach(world):
    plain = world["tmp"] / "plain"
    plain.mkdir()
    assert run_guard(world, "git fetch", plain) == 0


# ── shared: run a lib script (or a snippet) from the lib under test ───────
LIBDIR = Path(os.environ.get("GUARDS_LIB_UNDER_TEST", LIB))


def _env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
    env["PYTHONPATH"] = str(LIB)          # dependencies always from the real lib
    env.update({k: str(v) for k, v in extra.items()})
    return env


def _snippet(code: str, **env) -> subprocess.CompletedProcess:
    prelude = f"import sys; sys.path.insert(0, {str(LIBDIR)!r}); sys.path.insert(1, {str(LIB)!r})\n"
    return subprocess.run([sys.executable, "-c", prelude + code], capture_output=True,
                          text=True, env=_env(**env), timeout=60)


# ── injection_integrity_guard ──────────────────────────────────────────────
@pytest.fixture
def gate(tmp_path):
    state = (tmp_path / "state").resolve()
    state.mkdir(mode=0o700)
    spill = (tmp_path / "tool-results" / "spill.txt").resolve()
    spill.parent.mkdir()
    spill.write_text("".join(f"engram {i}\n" for i in range(5000)))
    guard = LIBDIR / "hooks" / "injection_integrity_guard.py"

    def call(mode, payload):
        payload = {"session_id": "s", **payload}
        r = subprocess.run([sys.executable, str(guard), mode], input=json.dumps(payload),
                           text=True, capture_output=True, env=_env(DATACORE_STATE=state), timeout=30)
        return r.stdout

    def armed():
        return "deny" in call("check", {"tool_name": "WebFetch", "tool_input": {}})

    call("mark", {"tool_name": "mcp__plur__plur_session_start",
                  "tool_response": f"Output has been saved to {spill}"})
    assert armed(), "precondition: the spill armed the gate"
    return {"call": call, "armed": armed, "spill": str(spill)}


def _read(gate, offset=None, limit=None):
    ti = {"file_path": gate["spill"]}
    if offset is not None:
        ti["offset"] = offset
    if limit is not None:
        ti["limit"] = limit
    gate["call"]("clear", {"tool_name": "Read", "tool_input": ti, "tool_response": "..."})


def test_partial_read_does_not_clear_the_gate(gate):
    _read(gate, 1, 10)
    assert gate["armed"](), "a 10-line peek of a 5000-line spill cleared the gate"


def test_mentioning_the_file_does_not_clear_the_gate(gate):
    gate["call"]("clear", {"tool_name": "Bash", "tool_input": {"command": "ls tool-results"},
                           "tool_response": {"stdout": "spill.txt"}})
    gate["call"]("clear", {"tool_name": "Grep", "tool_input": {"pattern": "x", "path": gate["spill"]},
                           "tool_response": "1 match"})
    assert gate["armed"]()


def test_paged_full_read_clears_the_gate(gate):
    _read(gate)                    # lines 1-2000 (the Read default)
    assert gate["armed"]()
    _read(gate, 2001, 2000)
    assert gate["armed"]()
    _read(gate, 4001, 1000)
    assert not gate["armed"](), "every line was read, the gate must lift"


def test_gap_in_pages_keeps_the_gate(gate):
    _read(gate, 1, 2000)
    _read(gate, 2002, 4000)        # line 2001 never read
    assert gate["armed"]()


# ── log_ownership_guard ────────────────────────────────────────────────────
def _git(repo, *args, **kw):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          env=_env(), check=kw.get("check", True))


def _ownership(repo, rng):
    guard = LIBDIR / "hooks" / "log_ownership_guard.py"
    return subprocess.run([sys.executable, str(guard), rng], cwd=repo, capture_output=True,
                          text=True, env=_env(DATACORE_ROOT=repo, DATACORE_ACTOR="miles"), timeout=60)


@pytest.fixture
def merge_repo(tmp_path):
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
    _git(repo, "checkout", "-qb", "wside")
    (ev / "winston.jsonl").write_text('{"w":1}\n')
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=winston@example.org", "commit", "-qm", "winston appends")
    # Winston's commit arrived by fetch, so a remote-tracking ref holds it
    # (decision S3: the guard judges only commits no remote has).
    _git(repo, "update-ref", "refs/remotes/origin/wside", "wside")
    _git(repo, "checkout", "-q", "main")
    with (ev / "miles.jsonl").open("a") as fh:
        fh.write('{"a":2}\n')
    _git(repo, "commit", "-qam", "miles appends")
    return repo


def test_clean_union_merge_is_allowed(merge_repo):
    _git(merge_repo, "merge", "-q", "--no-edit", "wside")
    assert _ownership(merge_repo, "base-ref..HEAD").returncode == 0


def test_evil_merge_writing_a_foreign_log_is_refused(merge_repo):
    _git(merge_repo, "merge", "-q", "--no-commit", "wside")
    with (merge_repo / ".datacore" / "events" / "winston.jsonl").open("a") as fh:
        fh.write('{"forged":1}\n')
    _git(merge_repo, "add", "-A")
    _git(merge_repo, "commit", "-qm", "merge wside")
    r = _ownership(merge_repo, "base-ref..HEAD")
    assert r.returncode == 1 and "winston.jsonl" in r.stderr, r.stderr


def test_unlistable_range_is_reported_not_silent(merge_repo):
    r = _ownership(merge_repo, "no-such-ref..HEAD")
    assert r.returncode == 1                     # decision S4: an unlistable range refuses
    assert "NOT checked" in r.stderr


# ── hooks.py retry budget ──────────────────────────────────────────────────
def test_retry_budget_is_per_task(tmp_path):
    reg = tmp_path / ".datacore" / "registry"
    reg.mkdir(parents=True)
    (tmp_path / ".datacore" / "state").mkdir(parents=True)
    (reg / "agents.yaml").write_text(
        "agents:\n  a:\n    description: t\n    hooks:\n      on_error:\n"
        "      - {type: classify-error, config: {transient_patterns: [timeout]}}\n"
        "      - {type: retry-schedule, config: {max_retries: 3, backoff: [1]}}\n")
    r = _snippet(
        "import hooks\n"
        "for _ in range(3):\n"
        "    assert hooks.HookExecutor().execute_error_hooks('a', Exception('timeout'), task_id='A')['retry']\n"
        "print(hooks.HookExecutor().execute_error_hooks('a', Exception('timeout'), task_id='A')['retry'],\n"
        "      hooks.HookExecutor().execute_error_hooks('a', Exception('timeout'), task_id='B')['retry'])\n"
        "e = hooks.HookExecutor(); e.reset_retries('a', 'A')\n"
        "print(hooks.HookExecutor().execute_error_hooks('a', Exception('timeout'), task_id='A')['retry'])\n",
        DATACORE_ROOT=tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["False", "True", "True"]


# ── pre_push_scan exit-code contract ───────────────────────────────────────
@pytest.mark.parametrize("policy", [
    "forbidden_paths: [unclosed\n",              # yaml.YAMLError
    "- just\n- a list\n",                         # not a mapping
    "forbidden_paths: 5\n",                       # wrong type deep inside
])
def test_malformed_policy_is_a_scanner_error_not_a_violation(tmp_path, policy):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.org")
    _git(repo, "config", "user.name", "T")
    (repo / "a.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    deny = tmp_path / "deny.yaml"
    deny.write_text(policy)
    r = subprocess.run([sys.executable, str(LIBDIR / "pre_push_scan.py"), "--repo", "o/r",
                        "--denylist", str(deny)], input=sha, cwd=repo, capture_output=True,
                       text=True, env=_env(), timeout=60)
    assert r.returncode == 2, r.stderr


# ── egress_scan --enforce ──────────────────────────────────────────────────
def _egress(mods: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(LIBDIR / "egress_scan.py"), "--enforce",
                           "--modules", str(mods)], capture_output=True, text=True,
                          env=_env(), timeout=120)


def test_enforce_fails_on_an_unreadable_manifest(tmp_path):
    mod = tmp_path / "m" / "sender"
    (mod / "lib").mkdir(parents=True)
    (mod / "module.yaml").write_text("egress: [unclosed\n")
    (mod / "lib" / "s.py").write_text("import requests\ndef go():\n    requests.post('u')\n")
    assert _egress(tmp_path / "m").returncode == 1


def test_enforce_fails_on_an_unparseable_file_in_an_opted_in_module(tmp_path):
    mod = tmp_path / "m" / "sender"
    (mod / "lib").mkdir(parents=True)
    (mod / "module.yaml").write_text("exempt:\n  - fn: lib/other.py:*\n    reason: reads\n")
    (mod / "lib" / "s.py").write_text("import requests\ndef go(:\n    requests.post('u')\n")
    assert _egress(tmp_path / "m").returncode == 1


def test_enforce_still_passes_a_clean_opted_in_module(tmp_path):
    mod = tmp_path / "m" / "sender"
    (mod / "lib").mkdir(parents=True)
    (mod / "module.yaml").write_text("exempt:\n  - fn: lib/s.py:*\n    reason: reads\n")
    (mod / "lib" / "s.py").write_text("import requests\ndef go():\n    requests.post('u')\n")
    assert _egress(tmp_path / "m").returncode == 0
