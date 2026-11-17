"""A number the task itself gives a repository to is one reference, not two.

WHY. "Issue #1031" or "PR #2288" in a title names no repository, so the
reconciler looks the number up in the space's default one. When the same task
also names that number WITH its repository (a full URL, or owner/repo#1031 in a
tracking property), the bare mention is that reference said short. Looked up in
the default repository as well, it is a second, unrelated item: an open one
keeps the task open for ever, and one closed as "not planned" makes the
references "part done, part not planned", which is left for a human.

Seen 2026-11-16: twenty review tasks whose issue and pull request lived in a
second repository each carried the title "Issue #N: ..." and the property
ISSUE: owner/other#N. All twenty picked up item N of the default repository as
well, and six of them could not close because of it. Eight open tasks had the
same shape that day, four of them with the title "PR #N" and the pull request's
URL in the body. In seven of the eight the default repository had no pull
request under that number, so the lookup failed, and a failed lookup blocks a
close for good.

What stays as it was: a bare number that nothing on the task gives a repository
to is looked up in the default repository, and an open item there still blocks.
An issue does not name a pull request: the same number under the other kind of
URL is a different item.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gh_reconcile  # noqa: E402
from test_reconcile_formal import MERGED, _issue, _run, gh  # noqa: E402,F401

OPEN_ISSUE = {"state": "open", "closed_at": None, "state_reason": None, "pull_request": None}


def _refs(text):
    """What the reconciler would look up for the one task in `text`, default repository o/r."""
    task = gh_reconcile.parse_org_tasks(text)[0]
    return sorted((r.full_repo, r.num, r.kind) for r in gh_reconcile.extract_github_refs(task, "o", "r"))


def test_an_issue_number_in_the_title_is_the_issue_the_tracking_property_names():
    assert _refs("* REVIEW Issue #1031: importer drops the last row\n"
                 "  :PROPERTIES:\n"
                 "  :ISSUE: o/other#1031\n"
                 "  :END:\n") == [("o/other", 1031, "unknown")]


def test_a_pull_request_number_in_the_title_is_the_pull_request_the_url_names():
    assert _refs("* REVIEW Review PR #2288\n"
                 "  :PROPERTIES:\n"
                 "  :NIGHTSHIFT_PR: https://github.com/o/other/pull/2288\n"
                 "  :END:\n") == [("o/other", 2288, "pr")]


def test_a_bare_number_in_a_tracking_property_is_the_one_a_url_names():
    assert _refs("* TODO Land the fix\n"
                 "  :PROPERTIES:\n"
                 "  :PR: PR #2288\n"
                 "  :LINK: https://github.com/o/other/pull/2288\n"
                 "  :END:\n") == [("o/other", 2288, "pr")]


def test_a_pull_request_number_in_the_title_is_the_pull_request_a_url_in_the_body_names():
    assert _refs("* TODO Review PR #1965\n"
                 "  Opened as https://github.com/o/other/pull/1965 last week.\n") == [("o/other", 1965, "pr")]


def test_a_bare_number_nothing_gives_a_repository_to_goes_to_the_default_repository():
    assert _refs("* TODO Fix PR #77\n") == [("o/r", 77, "pr")]


def test_an_issue_elsewhere_with_the_same_number_does_not_name_a_pull_request():
    assert _refs("* TODO Fix PR #77\n"
                 "  Reported in https://github.com/o/other/issues/77\n") == [("o/other", 77, "issue"), ("o/r", 77, "pr")]


def test_another_number_in_another_repository_changes_nothing():
    assert _refs("* TODO Issue #5: tidy up\n"
                 "  :PROPERTIES:\n"
                 "  :ISSUE: o/other#9\n"
                 "  :END:\n") == [("o/other", 9, "unknown"), ("o/r", 5, "issue")]


TASK = ("* REVIEW Issue #1031: importer drops the last row\n"
        "  :PROPERTIES:\n"
        "  :ISSUE: o/other#1031\n"
        "  :NIGHTSHIFT_PR: https://github.com/o/other/pull/2288\n"
        "  :END:\n")


@pytest.mark.parametrize("same_number_in_the_default_repository", [
    pytest.param(OPEN_ISSUE, id="open"),
    pytest.param(_issue("not_planned"), id="closed-as-not-planned"),
])
def test_a_settled_task_closes_whatever_the_default_repository_holds_under_that_number(
        gh, tmp_path, same_number_in_the_default_repository):  # noqa: F811
    """End to end through the real closer, GitHub stubbed (no network)."""
    gh["o/other/pulls/2288"] = MERGED
    gh["o/other/issues/1031"] = _issue("completed")
    gh["o/r/issues/1031"] = same_number_in_the_default_repository
    n, text = _run(tmp_path, TASK)
    assert n == 1
    assert text.startswith("* DONE Issue #1031: importer drops the last row")


def test_an_open_item_still_blocks_when_nothing_gives_the_number_a_repository(gh, tmp_path):  # noqa: F811
    """The boundary: a merged pull request with ANOTHER number names nothing for #1031."""
    gh["o/other/pulls/2288"] = MERGED
    gh["o/r/issues/1031"] = OPEN_ISSUE
    n, text = _run(tmp_path, "* REVIEW Issue #1031: importer drops the last row\n"
                             "  https://github.com/o/other/pull/2288\n")
    assert n == 0
    assert text.startswith("* REVIEW Issue #1031")
