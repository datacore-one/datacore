"""AUD-7: Each audit states the exact code version it read, so its findings can be checked and repeated.

Kind: deterministic + production. Not built yet (spec
.datacore/specs/cross-model-audit.md: findings carry "evidence (file:line at
the pinned commit)"; a night is valid only when each file is "pinned to a
commit that exists"); interface in tests/_audit_contract.py.

Deterministic: `cross_model_audit.validate_findings` against this repo: a file
pinned to HEAD whose evidence lines exist at HEAD is valid; each seeded
variant is refused with a reason.

Production (@production): every findings file of the last seven nights
validates.

Seeded failure: no commit; a branch name ("main") instead of a sha; a short
sha; a sha that does not exist; evidence naming a file absent at that commit;
evidence naming a line past the end of the file at that commit.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _audit_contract import NIGHTLY, ROOT, audit_module, finding, findings_file, head_sha  # noqa: E402

GOOD_EVIDENCE = ".datacore/lib/promise_evals.py:10"


def _lines_at(sha, path):
    return len(subprocess.run(["git", "-C", str(ROOT), "show", f"{sha}:{path}"], capture_output=True,
                              text=True, timeout=30).stdout.splitlines())


def test_a_file_pinned_to_a_real_commit_is_valid(tmp_path):
    m = audit_module()
    f = findings_file(tmp_path / "miles.yaml", agent="miles", capability="tasks", commit=head_sha(),
                      findings=[finding(evidence=GOOD_EVIDENCE)])
    assert m.validate_findings(f, repo=ROOT) == []


@pytest.mark.parametrize("variant", ["no-commit", "branch", "short", "missing-sha", "missing-file", "past-eof"])
def test_an_unpinned_or_unrepeatable_file_is_refused(tmp_path, variant):
    m = audit_module()
    sha = head_sha()
    commit, evidence = sha, GOOD_EVIDENCE
    if variant == "no-commit":
        commit = None
    elif variant == "branch":
        commit = "main"
    elif variant == "short":
        commit = sha[:7]
    elif variant == "missing-sha":
        commit = "0" * 40
    elif variant == "missing-file":
        evidence = ".datacore/lib/no_such_module_anywhere.py:3"
    elif variant == "past-eof":
        evidence = f".datacore/lib/promise_evals.py:{_lines_at(sha, '.datacore/lib/promise_evals.py') + 50}"
    f = findings_file(tmp_path / "miles.yaml", agent="miles", capability="tasks", commit=commit,
                      findings=[finding(evidence=evidence)])
    errors = m.validate_findings(f, repo=ROOT)
    assert errors, f"{variant}: a findings file that cannot be checked or repeated was accepted"


@pytest.mark.production
def test_every_recent_findings_file_is_pinned_and_checkable():
    m = audit_module()
    files = [p for d in range(1, 8) for p in (NIGHTLY / (date.today() - timedelta(days=d)).isoformat()).glob("*.yaml")]
    assert files, "no findings files in the last seven nights"
    bad = {str(p.relative_to(ROOT)): e for p in files if (e := m.validate_findings(p, repo=ROOT))}
    assert not bad, bad
