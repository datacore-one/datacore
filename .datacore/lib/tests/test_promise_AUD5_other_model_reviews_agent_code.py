"""AUD-5: Code an agent writes is reviewed by a different model than the one that wrote it before I see the pull request.

Kind: deterministic. Not built yet (spec .datacore/specs/cross-model-audit.md,
"Cross-model review of agent code"); interface in tests/_audit_contract.py.

`cross_model_audit.reviewer_for` picks the reviewing family and
`cross_model_audit.ready_for_owner` is the gate a PR passes before it reaches
me (the morning repair's "needs you", the briefing).

Seeded failure: an agent PR with no review; one reviewed only by its own
family; one "reviewed" by a different family that approved or merged instead
of commenting; and one with a real cross-family review comment. The promise
holds when only the last is ready for me, and no family is ever its own
reviewer.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _audit_contract import AGENTS, audit_module  # noqa: E402


@pytest.mark.parametrize("family", sorted(set(AGENTS.values())))
def test_the_reviewer_is_never_the_author_family(family):
    m = audit_module()
    reviewer = m.reviewer_for(family)
    assert reviewer and reviewer != family and reviewer in set(AGENTS.values())


@pytest.mark.parametrize("reviews,ready", [
    ([], False),
    ([{"family": "claude", "kind": "comment"}], False),
    ([{"family": "gpt", "kind": "approve"}], False),
    ([{"family": "gpt", "kind": "merge"}], False),
    ([{"family": "gpt", "kind": "comment"}], True),
], ids=["unreviewed", "self-reviewed", "approved-not-reviewed", "merged-by-reviewer", "cross-reviewed"])
def test_only_a_cross_family_review_comment_lets_me_see_it(reviews, ready):
    m = audit_module()
    assert m.ready_for_owner({"author_family": "claude", "reviews": reviews}) is ready
