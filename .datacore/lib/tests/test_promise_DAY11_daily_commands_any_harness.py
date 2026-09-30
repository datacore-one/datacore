"""DAY-11: The daily commands (/today, /wrap-up, /tomorrow, /continue) finish the same
way whichever AI tool runs them: Claude Code, Codex, Hermes or OpenClaw.

Kind: deterministic. The wrap-up mechanics (`wrap_up_mechanics.py`) and the session
archive (`session_archive.py`, also called by /tomorrow and /continue) run as real
subprocesses against a throwaway install: three git repos with bare remotes, no
Claude-specific environment at all (no CLAUDE_* variables, an empty home with no
~/.claude/projects transcript) and, for the Codex case, the variable Codex really sets
(CODEX_THREAD_ID, observed in Codex 0.159 on 2026-09-30).

What the owner observes, and what is graded:
- a step that does not need Claude passes in any harness: the session's own files
  are committed and pushed with explicit paths, today's journals are checked, the
  audit comes back with no failures;
- a step that inherently needs a Claude transcript (the transcript archive, the token
  counts) is marked "not applicable here", never "fail" and never "error";
- when the harness cannot say which files are this session's, the checks that
  depend on it say "cannot tell" (not applicable), and nothing is committed on a guess;
- the relaxation is for other harnesses only: a Claude Code session whose transcript
  archive is missing still FAILS "session archived".

Seeded failure: the 2026-09-30 Codex wrap-up. Its audit passed 6 of 11: "no
CLAUDE_CODE_SESSION_ID" failed space journals and the commit/push check, "run
preflight" failed the archive check, the session's journals were never committed,
and a registry file another session had edited failed "context in sync".
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
MECH = LIB / "wrap_up_mechanics.py"
ARCHIVE = LIB / "session_archive.py"
TODAY = date.today().isoformat()


# --------------------------------------------------------------------------- fixture

def _env(tmp: Path, root: Path, **extra: str) -> dict:
    """A harness with nothing Claude-specific: no CLAUDE_* variables, a home with no
    ~/.claude, and git isolated from this machine's global config."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE", "CODEX_", "DATACORE_", "GIT_"))}
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    gitconfig = tmp / "gitconfig"
    if not gitconfig.exists():
        gitconfig.write_text("[user]\n\tname = t\n\temail = t@t\n[init]\n\tdefaultBranch = main\n")
    env.update(HOME=str(home), DATACORE_ROOT=str(root), GIT_CONFIG_GLOBAL=str(gitconfig),
               GIT_CONFIG_NOSYSTEM="1")
    env.update(extra)
    return env


def _git(cwd: Path, env: dict, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"fixture git {args} failed: {r.stderr}"
    return r.stdout


def _install(tmp: Path) -> tuple[Path, dict]:
    """Root repo + two space repos, each pushed to its own bare remote. Then a
    session's writes and another session's uncommitted edits."""
    root = tmp / "Data"
    env = _env(tmp, root)
    seeds = {
        root: {".gitignore": "/[0-9]-*/\n/.datacore/state/\n",
               ".datacore/registry/ventures.yaml": "ventures: []\n"},
        root / "0-personal": {f"journal/{TODAY}.md": "# today\n\n## Daily Briefing\n\n- morning\n",
                              "org/inbox.org": "* inbox\n"},
        root / "2-datacore": {"notes/other.md": "other session's note\n",
                              "journal/.keep": ""},
    }
    for repo, files in seeds.items():
        remote = tmp / "remotes" / f"{repo.name}.git"
        remote.mkdir(parents=True)
        _git(remote, env, "init", "-q", "--bare", "-b", "main")
        repo.mkdir(parents=True, exist_ok=True)
        _git(repo, env, "init", "-q", "-b", "main")
        for rel, body in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(body)
        _git(repo, env, "add", "-A")
        _git(repo, env, "commit", "-qm", "seed")
        _git(repo, env, "remote", "add", "origin", str(remote))
        _git(repo, env, "push", "-q", "-u", "origin", "main")

    # This session's work: a note, a space journal, its entry in the personal journal.
    (root / "2-datacore" / "notes" / "idea.md").write_text("this session's idea\n")
    (root / "2-datacore" / "journal" / f"{TODAY}.md").write_text("# today\n\n## Session: work\n\n- did it\n")
    pj = root / "0-personal" / "journal" / f"{TODAY}.md"
    pj.write_text(pj.read_text() + "\n## Session: work\n\n- did it\n")
    # Another session's uncommitted work, which must be left exactly as it is.
    (root / "2-datacore" / "notes" / "other.md").write_text("other session, still editing\n")
    (root / ".datacore" / "registry" / "ventures.yaml").write_text("ventures: [edited elsewhere]\n")
    return root, env


SESSION_FILES = ["2-datacore/notes/idea.md", f"2-datacore/journal/{TODAY}.md",
                 f"0-personal/journal/{TODAY}.md"]


def _mech(env: dict, root: Path, *args: str) -> tuple[int, dict | str]:
    r = subprocess.run([sys.executable, str(MECH), *args], cwd=root, env=env,
                       capture_output=True, text=True, timeout=300)
    try:
        return r.returncode, json.loads(r.stdout)
    except ValueError:
        return r.returncode, (r.stdout + r.stderr)[-800:]


def _status(check: dict) -> str:
    return check.get("status") or ("pass" if check.get("pass") else "fail")


def _checks(audit: dict) -> dict[str, dict]:
    return {c["check"]: c for c in audit["checks"]}


def _on_remote(tmp: Path, env: dict, repo: str, path: str) -> bool:
    remote = tmp / "remotes" / f"{repo}.git"
    r = subprocess.run(["git", "cat-file", "-e", f"main:{path}"], cwd=remote, env=env,
                       capture_output=True, timeout=60)
    return r.returncode == 0


def _dirty(repo: Path, env: dict) -> set[str]:
    out = _git(repo, env, "status", "--porcelain", "-uall")
    return {line[3:] for line in out.splitlines() if line.strip()}


def _record_files(env: dict, root: Path) -> None:
    rc, out = _mech(env, root, "files", "--add", *SESSION_FILES)
    assert rc == 0 and isinstance(out, dict), (
        "outside Claude Code there is no transcript to read the session's files from, so the "
        "harness must be able to name them itself (`wrap_up_mechanics.py files --add <path>...`); "
        f"that call failed: {out}")


def _full_wrap_up(tmp: Path, root: Path, env: dict) -> tuple[dict, dict, dict]:
    _record_files(env, root)
    rc, pre = _mech(env, root, "preflight", "--dry-run")
    assert rc == 0 and isinstance(pre, dict), f"preflight did not finish: {pre}"
    rc, fin = _mech(env, root, "finalize")
    assert rc == 0 and isinstance(fin, dict), f"finalize did not finish: {fin}"
    rc, aud = _mech(env, root, "audit")
    assert rc == 0 and isinstance(aud, dict), f"audit did not finish: {aud}"
    return pre, fin, aud


HARNESSES = {
    "codex": {"CODEX_THREAD_ID": "01a0f17e-f407-7c11-a963-0d4a951aab8b"},
    "hermes-or-openclaw (no session id at all)": {},
    "any harness that sets DATACORE_SESSION_ID": {"DATACORE_SESSION_ID": "run-20260930-x"},
}


# --------------------------------------------------------------------------- evals

@pytest.mark.parametrize("harness", list(HARNESSES))
def test_wrap_up_commits_pushes_and_audits_clean_without_claude(tmp_path, harness):
    root, env = _install(tmp_path)
    env.update(HARNESSES[harness])
    pre, fin, aud = _full_wrap_up(tmp_path, root, env)

    # The session's own files reached the remote, by explicit path.
    missing = [f for f in SESSION_FILES
               if not _on_remote(tmp_path, env, f.split("/", 1)[0], f.split("/", 1)[1])]
    assert not missing, (
        f"[{harness}] the wrap-up did not commit and push this session's own files {missing}; "
        f"finalize said: {json.dumps({k: fin.get(k) for k in ('error', 'note', 'pushes')})[:600]}")

    # Another session's work was left exactly as found.
    assert "notes/other.md" in _dirty(root / "2-datacore", env), \
        f"[{harness}] another session's uncommitted note was committed or discarded"
    assert not _on_remote(tmp_path, env, "2-datacore", "notes/other.md") or \
        "still editing" not in _git(tmp_path / "remotes" / "2-datacore.git", env, "show", "main:notes/other.md"), \
        f"[{harness}] another session's half-finished edit was pushed"
    assert ".datacore/registry/ventures.yaml" in _dirty(root, env), \
        f"[{harness}] another session's registry edit was committed or discarded"

    # Nothing the session was responsible for failed.
    failed = [(c["check"], c["detail"]) for c in aud["checks"] if _status(c) == "fail"]
    assert not failed, (
        f"[{harness}] the wrap-up audit failed checks that do not need Claude Code: {failed}")

    # The step that inherently needs a Claude transcript says so, and is not a pass either.
    arch = _checks(aud)["session archived"]
    assert _status(arch) == "n/a", (
        f"[{harness}] 'session archived' needs a Claude transcript, so it must read "
        f"'not applicable here', and it read {_status(arch)!r}: {arch['detail']}")
    sa = pre["session_archive"]
    assert sa.get("status") == "not-applicable", (
        f"[{harness}] preflight's transcript archive must say 'not-applicable' outside Claude "
        f"Code, and said {sa}")

    # Another session's registry edit is reported, not scored against this one.
    ctx = _checks(aud)["context in sync"]
    assert _status(ctx) == "pass" and "ventures.yaml" in ctx["detail"], (
        f"[{harness}] a registry file this session never touched must be reported and not "
        f"scored: {_status(ctx)} — {ctx['detail']}")


def test_token_counts_are_marked_unavailable_not_an_error(tmp_path):
    root, env = _install(tmp_path)
    env.update(HARNESSES["codex"])
    _full_wrap_up(tmp_path, root, env)
    rc, meta = _mech(env, root, "meta")
    assert rc == 0 and isinstance(meta, dict)
    assert meta.get("not_applicable") is True and "preflight" not in str(meta.get("error", "")), (
        "outside Claude Code the token counts have no transcript to come from; `meta` must say "
        f"'not applicable here', not send the agent to re-run preflight: {meta}")


def test_without_a_file_list_the_audit_says_cannot_tell_and_nothing_is_committed(tmp_path):
    root, env = _install(tmp_path)
    env.update(HARNESSES["codex"])
    before = {r: _git(tmp_path / "remotes" / f"{r}.git", env, "rev-parse", "main")
              for r in ("Data", "0-personal", "2-datacore")}
    rc, _ = _mech(env, root, "preflight", "--dry-run")
    rc, fin = _mech(env, root, "finalize")
    rc, aud = _mech(env, root, "audit")
    after = {r: _git(tmp_path / "remotes" / f"{r}.git", env, "rev-parse", "main")
             for r in ("Data", "0-personal", "2-datacore")}
    assert before == after, "with no list of this session's files, finalize pushed something on a guess"
    checks = _checks(aud)
    for name in ("space journals", "session work committed and pushed", "session archived"):
        assert _status(checks[name]) == "n/a", (
            f"'{name}' depends on knowing this session's files, which this harness cannot "
            f"tell; it must read 'not applicable here' and read "
            f"{_status(checks[name])!r}: {checks[name]['detail']}")
    assert not [c for c in aud["checks"] if _status(c) == "fail"], (
        f"a non-Claude harness failed checks it cannot be judged on: "
        f"{[(c['check'], c['detail']) for c in aud['checks'] if _status(c) == 'fail']}")


def test_a_claude_session_with_no_archive_still_fails(tmp_path):
    """The relaxation is for other harnesses only. In Claude Code the transcript
    exists, so a missing archive is a real failure and must stay one."""
    root, env = _install(tmp_path)
    env["CLAUDE_CODE_SESSION_ID"] = "0b0b0b0b-1111-2222-3333-444444444444"
    rc, aud = _mech(env, root, "audit")
    assert rc == 0 and isinstance(aud, dict)
    arch = _checks(aud)["session archived"]
    assert _status(arch) == "fail", (
        f"a Claude Code session whose transcript was never archived must fail "
        f"'session archived'; it read {_status(arch)!r}: {arch['detail']}")


def test_two_codex_sessions_on_one_day_keep_separate_records(tmp_path):
    """Each session's step results are filed under its own id, so one session's
    wrap-up never reports another's numbers."""
    root, env = _install(tmp_path)
    for tid in ("thread-a", "thread-b"):
        rc, _ = _mech({**env, "CODEX_THREAD_ID": tid}, root, "preflight", "--dry-run")
        assert rc == 0
    state = root / ".datacore" / "state" / "wrap_up"
    dirs = sorted(p.name for p in state.iterdir()) if state.is_dir() else []
    assert {"thread-a", "thread-b"} <= set(dirs), (
        f"two Codex sessions' wrap-ups were filed together (found {dirs}); each needs its own "
        "record, taken from the id the harness exposes")


@pytest.mark.parametrize("command", ["/tomorrow", "/continue"])
def test_the_session_archive_step_is_not_applicable_outside_claude(tmp_path, command):
    """/tomorrow and /continue call the archive and are told to say so when the status
    is not 'archived'. Outside Claude Code there is no transcript: that is 'not
    applicable', not a failure to report."""
    root, env = _install(tmp_path)
    env.update(HARNESSES["codex"])
    r = subprocess.run([sys.executable, str(ARCHIVE), "--json"], cwd=root, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"{command}: the archive step crashed: {r.stderr[-400:]}"
    out = json.loads(r.stdout)
    assert out.get("status") == "not-applicable" and out.get("reason"), (
        f"{command}: outside Claude Code the archive step must say 'not-applicable' with a "
        f"reason, and said {out}")
