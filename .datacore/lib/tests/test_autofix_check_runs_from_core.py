"""A repair item's done-check finds fix_check.py when the claim loop runs it.

ledger_claim runs an item's check inside a fresh worktree of the SPACE the
item lives in (`_isolated_check_rc`). Repair items live in the system space
(2-datacore on the box), which is its own git repository and has no
`.datacore/lib/`. autofix wrote the check as the relative path
`python3 .datacore/lib/jobs/fix_check.py ...`, so in that worktree the file
never exists. Measured on winston, 2026-10-02 (ledger-claim.log):

    python3: can't open file '/tmp/check-u6lpeqmz/verify/.datacore/lib/jobs/fix_check.py'

and every repair of a box job (box-ledger-ingest, box-phase1-cycle,
morning-repair-*) was dead-lettered after three such "failures". The worktree
was not an old snapshot that predates the file; it is a different repository.

The checker is the installation's code, not the space's content, so the check
names it through DATACORE_ROOT (the root ledger_claim_run.sh exports), and the
check still runs with the space worktree as its working directory.
"""
from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parent.parent
for p in (LIB, LIB / "jobs"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def _space_repo(tmp_path: Path) -> Path:
    """A system space as it is on the box: its own repository, a ledger, no lib."""
    space = tmp_path / "2-datacore"
    (space / ".datacore" / "events").mkdir(parents=True)
    (space / "README.md").write_text("space\n")
    _git(space, "init", "-q")
    _git(space, "add", "README.md")
    _git(space, "commit", "-q", "-m", "init")
    return space


def _delegated_check(monkeypatch, tmp_path, space, machine="box", assignee_host="box"):
    import autofix
    import ledger.policy
    import actor_identity
    appended = []
    monkeypatch.setattr(ledger.policy, "guarded_append",
                        lambda log, kind, payload: appended.append((kind, payload)))
    monkeypatch.setattr(actor_identity, "this_actor", lambda: "winston")
    monkeypatch.setattr(autofix, "contract_sha", lambda name, manifest: "abc")
    monkeypatch.setattr(autofix, "_space", lambda root: space)
    monkeypatch.setattr(autofix, "host_of", lambda who, roster=None: assignee_host)
    monkeypatch.setattr(autofix, "repo_for", lambda job, root: "datacore-one/datacore")
    job = types.SimpleNamespace(name="box-x", machine=machine, delegate=True, cmd="x",
                                schedule="x", artifacts=[])
    state, why = autofix.delegate(job, ["f"], {"first_failed": "2026-10-02"}, root=tmp_path,
                                  assignee="winston")
    assert state == "delegated", why
    return appended[0][1]["check"]


def _run_isolated(space, check):
    """ledger_claim's own isolated check, in a child process: importing
    ledger_claim here would leave its module state behind for later tests."""
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import ledger_claim; "
            "from pathlib import Path; "
            "rc, _ = ledger_claim._isolated_check_rc(Path(sys.argv[2]), sys.argv[3]); "
            "print('RC', rc)")
    p = subprocess.run([sys.executable, "-c", code, str(LIB), str(space), check],
                       capture_output=True, text=True, timeout=180,
                       env={**os.environ, "DATACORE_ROOT": str(ROOT)})
    out = p.stdout + p.stderr
    rc = int(out.rsplit("RC ", 1)[1].split()[0]) if "RC " in out else -1
    return rc, out


def test_the_repair_check_finds_fix_check_in_a_space_without_a_lib(tmp_path, monkeypatch):
    space = _space_repo(tmp_path)
    check = _delegated_check(monkeypatch, tmp_path, space)

    rc, said = _run_isolated(space, check)

    assert "can't open file" not in said, \
        f"the repair check cannot find fix_check.py from the space worktree: {said.strip()}"
    # The real checker ran: contract sha "abc" is not box-x's, so it refuses --
    # a judged failure, not a missing file.
    assert rc != 0


def test_the_cross_host_repair_check_finds_fix_check_too(tmp_path, monkeypatch):
    space = _space_repo(tmp_path)
    check = _delegated_check(monkeypatch, tmp_path, space, machine="box", assignee_host="nightshift")
    assert "--stage merged" in check

    _rc, said = _run_isolated(space, check)

    assert "can't open file" not in said, said.strip()
