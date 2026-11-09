"""A check that cannot run says where it ran, what it ran, and why it failed.

Owner, 2026-10-02: "Errors should say what they are."

The case: every repair on the box failed with

    python3: can't open file '/tmp/check-xxxx/verify/.datacore/lib/jobs/fix_check.py':
    [Errno 2] No such file or directory

A reliability report read that as "the checker runs from an old snapshot". The
real cause was that the claim loop ran the check in a temporary copy of the
2-datacore space, a separate repository with no `.datacore/lib/` at all (fixed
in f5dce8a). The message never said WHICH repository the copy was of, at WHICH
commit, or WHY the file was absent, so the reader guessed.

These tests pin that a failing check is reported with: the repository and the
commit it ran in, the command, and the most likely cause in plain words, both
on the dispatcher's output and in the ledger record a dead-letter escalation is
built from.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
for p in (LIB, LIB / "jobs"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import ledger_claim  # noqa: E402
from check_diagnosis import diagnose  # noqa: E402

FIX_CHECK_CMD = "python3 .datacore/lib/jobs/fix_check.py --job box-x --machine box --contract-sha abc"


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(cwd), *args],
                          check=True, capture_output=True, text=True).stdout


def _space(tmp_path: Path, name: str = "2-datacore") -> Path:
    space = tmp_path / name
    space.mkdir()
    _git(space, "init", "-q")
    _git(space, "config", "user.email", "t@t")
    _git(space, "config", "user.name", "t")
    _git(space, "remote", "add", "origin", f"git@github.com:datacore-one/{name}.git")
    (space / "README.md").write_text("space\n")
    _git(space, "add", "README.md")
    _git(space, "commit", "-qm", "init")
    return space


# -- the observed case, end to end through the isolated check -----------------

def test_the_fix_check_case_names_repo_sha_command_and_cause(tmp_path, capsys):
    space = _space(tmp_path)
    head = _git(space, "rev-parse", "HEAD").strip()

    rc, sha, why = ledger_claim._isolated_check_explained(space, FIX_CHECK_CMD)

    assert rc == 1 and sha == head
    assert "2-datacore" in why, f"the repository is not named: {why}"
    assert "datacore-one/2-datacore" in why, f"the repository's origin is not named: {why}"
    assert head[:10] in why, f"the commit is not named: {why}"
    assert FIX_CHECK_CMD in why, f"the command is not named: {why}"
    assert "has no .datacore/lib" in why, f"the cause is not said: {why}"
    assert "installation" in why, f"the remedy is not said: {why}"
    # The raw /tmp path is not the explanation.
    assert "/tmp/check-" not in why.split("(raw:")[0]
    # And the dispatcher's own output carries the same sentence.
    assert "has no .datacore/lib" in capsys.readouterr().out


def test_the_old_two_tuple_api_still_answers(tmp_path):
    space = _space(tmp_path)
    rc, sha = ledger_claim._isolated_check_rc(space, FIX_CHECK_CMD)
    assert rc == 1 and sha


# -- each detectable cause is said directly -----------------------------------

def test_a_commit_that_predates_the_file_is_said(tmp_path):
    space = _space(tmp_path)
    old = _git(space, "rev-parse", "HEAD").strip()
    (space / "tool.py").write_text("print('ok')\n")
    _git(space, "add", "tool.py")
    _git(space, "commit", "-qm", "add tool")
    new = _git(space, "rev-parse", "HEAD").strip()
    _git(space, "checkout", "-q", old)

    rc, _sha, why = ledger_claim._isolated_check_explained(space, "python3 tool.py")

    assert rc == 1
    assert "predates" in why and "tool.py" in why and new[:10] in why, why


def test_a_relative_path_that_lives_in_the_installation_is_a_wrong_directory(tmp_path):
    space = _space(tmp_path, "3-team")
    install = tmp_path / "Data"
    (install / ".datacore" / "lib").mkdir(parents=True)
    (install / ".datacore" / "lib" / "x.py").write_text("")
    why = diagnose(cmd="python3 .datacore/lib/x.py", rc=2, cwd=space, repo=space, sha="0" * 40,
                   stderr="python3: can't open file '/tmp/check-1/verify/.datacore/lib/x.py': "
                          "[Errno 2] No such file or directory",
                   install_root=install)
    assert "has no .datacore/lib" in why and str(install) in why, why


def test_a_missing_program_is_said(tmp_path):
    space = _space(tmp_path)
    rc, _sha, why = ledger_claim._isolated_check_explained(space, "no-such-program-xyz --flag")
    assert rc == 1
    assert "no-such-program-xyz" in why and "not installed" in why, why


def test_a_timeout_is_said(tmp_path, monkeypatch):
    space = _space(tmp_path)
    monkeypatch.setattr(ledger_claim, "CHECK_TIMEOUT_S", 1)
    rc, _sha, why = ledger_claim._isolated_check_explained(space, "sleep 30")
    assert rc == 1
    assert "timed out after 1s" in why and "sleep 30" in why, why


def test_a_killed_check_is_said_as_memory_or_kill(tmp_path):
    space = _space(tmp_path)
    rc, _sha, why = ledger_claim._isolated_check_explained(space, "kill -9 $$")
    assert rc == 1
    assert "killed" in why and "out of memory" in why, why


@pytest.mark.parametrize("stderr,host", [
    ("ssh: Could not resolve hostname winston: nodename nor servname provided", "winston"),
    ("ssh: connect to host nightshift port 22: Connection refused", "nightshift"),
    ("fatal: unable to access 'https://github.com/o/r/': Could not resolve host: github.com",
     "github.com"),
])
def test_an_unreachable_host_is_said(tmp_path, stderr, host):
    why = diagnose(cmd="ssh x true", rc=255, cwd=tmp_path, repo=None, sha="", stderr=stderr)
    assert "could not reach" in why and host in why, why


def test_an_ordinary_failure_still_shows_what_the_check_said(tmp_path):
    space = _space(tmp_path)
    rc, _sha, why = ledger_claim._isolated_check_explained(
        space, "echo 'not yet: box-x still fails verification' >&2; exit 1")
    assert rc == 1
    assert "not yet: box-x still fails verification" in why, why
    assert "exit 1" in why


def test_a_passing_check_has_nothing_to_explain(tmp_path):
    space = _space(tmp_path)
    rc, _sha, why = ledger_claim._isolated_check_explained(space, "true")
    assert rc == 0 and why == ""


# -- the dispatcher records the cause where the owner's escalation reads it ----

from tests.test_promise_NS_9_waiting_on_owner_is_not_a_failure import fleet  # noqa: E402,F401


def _events(space, iid, kind):
    from ledger.log import read_events
    return [e for e in read_events(space) if e.type == kind and (e.payload or {}).get("id") == iid]


def test_the_release_and_the_give_up_carry_the_cause(fleet, monkeypatch):  # noqa: F811
    from jobs import autofix
    from ledger_claim import MAX_ATTEMPTS
    root, space, drill = fleet
    monkeypatch.setattr(autofix, "_acked", lambda: set())
    iid = drill.delegate(space, by="winston", to="miles", id="autofix-box-x-20261002",
                         title="write K into k.txt", check=f"test -f k.txt && {FIX_CHECK_CMD}",
                         autofix=True, job="box-x", machine="box")

    outs = [drill.dispatch(space, "miles") for _ in range(MAX_ATTEMPTS + 1)]

    releases = _events(space, iid, "item.release")
    assert releases, outs[0][:400]
    err = (releases[0].payload or {}).get("error", "")
    assert "has no .datacore/lib" in err and FIX_CHECK_CMD in err, err
    it = drill.item(space, iid)
    assert it.status == "dismissed", outs[-1][:300]
    assert "has no .datacore/lib" in (it.closed_reason or ""), it.closed_reason
    said = " ".join(autofix.escalations(root))
    assert "gave up" in said and "has no .datacore/lib" in said, said
