"""AUD-2: An audit only reads. It never changes code, tasks or history, and never merges anything.

Kind: deterministic + production. Not built yet (spec
.datacore/specs/cross-model-audit.md, boundaries: "The audit tool policy
blocks writes outside the findings file").

Deterministic: the real in-flight tool policy (`tool_policy.decide`, with the
real config/approvals_policy.yaml and config/tool_effects.yaml) for the audit
run's principal `auditor`: reading is allowed, writing its own findings file
is allowed, and every write to code, tasks or history and every merge is
refused.

Production (@production): git history of the 2-datacore repo -- every commit
that adds audit findings touches nothing outside the nightly findings folder,
and at least one such commit exists (a boundary nobody exercised is not held).

Seeded failure: an audit that edits a library file, rewrites next_actions.org,
appends to the ledger, commits, force-pushes, merges a pull request, or writes
a findings file somewhere else.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _audit_contract import NIGHTLY, ROOT  # noqa: E402

import tool_policy  # noqa: E402

PRINCIPAL = "auditor"
FINDINGS = str(NIGHTLY / "2026-09-27" / "tris.yaml")

ALLOWED = [
    ("Read", {"file_path": str(ROOT / ".datacore/lib/job_verify.py")}),
    ("Grep", {"pattern": "def ", "path": str(ROOT / ".datacore/lib")}),
    ("Bash", {"command": "git -C ~/Data log --oneline -5"}),
    ("Write", {"file_path": FINDINGS, "content": "agent: tris\n"}),
]
REFUSED = [
    ("Edit", {"file_path": str(ROOT / ".datacore/lib/job_verify.py"), "old_string": "a", "new_string": "b"}),
    ("Write", {"file_path": str(ROOT / "2-datacore/org/next_actions.org"), "content": "* TODO x\n"}),
    ("Write", {"file_path": str(ROOT / "2-datacore/1-tracks/dev/notes.yaml"), "content": "x\n"}),
    ("Bash", {"command": "python3 .datacore/lib/org_workspace_adapter.py add --file org/next_actions.org"}),
    ("Bash", {"command": "echo '{}' >> 2-datacore/.datacore/events/mac.jsonl"}),
    ("Bash", {"command": "git commit -am 'audit fix'"}),
    ("Bash", {"command": "git push --force origin main"}),
    ("Bash", {"command": "gh pr merge 123 --squash"}),
    ("Bash", {"command": "sed -i '' 's/a/b/' .datacore/lib/job_verify.py"}),
]


def _decide(tool, args):
    try:
        return tool_policy.decide(PRINCIPAL, tool, args)
    except ValueError as exc:
        pytest.fail(f"no audit policy: {exc} (declare principal {PRINCIPAL!r} "
                    "in config/approvals_policy.yaml)")


@pytest.mark.parametrize("tool,args", ALLOWED, ids=[f"{t}:{list(a.values())[0][-40:]}" for t, a in ALLOWED])
def test_reading_and_its_own_findings_file_are_allowed(tool, args):
    assert _decide(tool, args).allow


@pytest.mark.parametrize("tool,args", REFUSED, ids=[f"{t}:{list(a.values())[0][-40:]}" for t, a in REFUSED])
def test_every_write_and_merge_is_refused(tool, args):
    d = _decide(tool, args)
    assert not d.allow, f"the audit policy let {tool} {args} through ({d.reason})"


@pytest.mark.production
def test_audit_commits_touched_only_the_findings_folder():
    repo = ROOT / "2-datacore"
    rel = NIGHTLY.relative_to(repo).as_posix()
    shas = subprocess.run(["git", "-C", str(repo), "log", "--since=30 days ago", "--format=%H", "--", rel],
                          capture_output=True, text=True, timeout=30).stdout.split()
    assert shas, "no audit has committed findings in 30 days: the read-only boundary is unexercised"
    leaks = {}
    for sha in shas:
        files = subprocess.run(["git", "-C", str(repo), "show", "--name-only", "--format=", sha],
                               capture_output=True, text=True, timeout=30).stdout.split()
        outside = [f for f in files if not f.startswith(rel + "/")]
        if outside:
            leaks[sha[:10]] = outside
    assert not leaks, f"audit commits wrote outside the findings folder: {leaks}"
