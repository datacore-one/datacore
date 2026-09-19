"""A test nobody runs protects nothing, so make that state fail a PR.

CI listed ~100 test FILES by name in validate-pr.yml. Nothing connected that list
to the tree, so 421 of 531 test files were gated by nobody -- and the point is
not the ratio, it is that the gap was silent. The suite stayed green while a
stale assertion asked for an undeclared writer to become its own policy
principal, while chief-of-staff's manifest schema moved without its tests, and
while sixteen X-poster tests raised on a live kill switch instead of testing.

CI now names directories. This test asserts the remainder is declared rather than
merely absent: every directory holding test files is either run by CI or written
down in config/ungated-test-suites.yaml with a reason. Declaring one is a
decision to accept that risk. Forgetting one is now a failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parent.parent
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import suite_audit as audit  # noqa: E402

DECLARED = LIB.parent / "config" / "ungated-test-suites.yaml"


def _declared() -> list[dict]:
    if not DECLARED.exists():
        return []
    return yaml.safe_load(DECLARED.read_text()).get("suites") or []


def _ungated_dirs() -> set[str]:
    gated = audit.ci_gated_paths()
    out = set()
    for p in audit.DATACORE.rglob("test_*.py"):
        rel = p.relative_to(audit.DATACORE)
        if not audit.is_source(rel):
            continue
        repo_rel = p.relative_to(audit.DATACORE.parent).as_posix()
        if not audit.is_gated(repo_rel, gated):
            out.add(str(Path(repo_rel).parent))
    return out


def test_every_test_directory_is_gated_or_declared():
    declared = {d["path"].rstrip("/") for d in _declared()}
    undeclared = sorted(
        d for d in _ungated_dirs()
        if not any(d == p or d.startswith(p + "/") for p in declared)
    )
    assert not undeclared, (
        "these directories hold test files that no CI job runs and that "
        "config/ungated-test-suites.yaml does not mention -- gate them in "
        ".github/workflows/validate-pr.yml, or declare them with the reason:\n  "
        + "\n  ".join(undeclared)
    )


def test_every_declaration_carries_a_reason_and_a_review_date():
    """A declaration without a reason is the silent gap wearing a hat."""
    for entry in _declared():
        assert entry.get("reason", "").strip(), f"{entry.get('path')} declared with no reason"
        assert entry.get("reviewed"), f"{entry.get('path')} declared with no review date"


def test_no_declaration_names_a_directory_that_is_gone():
    """A stale entry silently re-opens the hole it was written to describe."""
    stale = [d["path"] for d in _declared()
             if not (audit.DATACORE.parent / d["path"]).exists()]
    assert not stale, f"declared but no longer present: {stale}"


def test_a_declared_directory_is_not_also_run_by_ci():
    """Contradictory bookkeeping means one of the two is being read wrong."""
    gated = audit.ci_gated_paths()
    both = [d["path"] for d in _declared()
            if audit.is_gated(d["path"] + "/x/test_a.py", gated)]
    assert not both, f"declared as ungated but CI runs them: {both}"


@pytest.mark.parametrize("path", ["lib/tests", "tests"])
def test_the_core_suites_are_gated(path):
    """The regression this whole file exists to prevent, stated directly."""
    gated = audit.ci_gated_paths()
    assert audit.is_gated(f".datacore/{path}/test_anything.py", gated), (
        f".datacore/{path} is not run by CI"
    )
