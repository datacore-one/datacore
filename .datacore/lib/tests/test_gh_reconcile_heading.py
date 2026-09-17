"""Closing a task must not change its title.

gh_reconcile's substitution kept the space it captured AND added its own, so a
closed task read `* DONE  Title`. In a Phase-1 space the second space arrived as
an authored rename of the item the same run had just closed, and the ledger
refuses edits to terminal items: 13 headings from one 06:50Z pass failed the
ingest of two spaces on every cycle after (2026-09-17).
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import pytest  # noqa: E402

import gh_reconcile  # noqa: E402
from org_workspace._vendor.orgparse import loads  # noqa: E402


@pytest.mark.parametrize("state", ["TODO", "NEXT", "WAITING"])
@pytest.mark.parametrize("stars", ["*", "**"])
def test_closing_rewrites_the_keyword_and_nothing_else(state, stars):
    line = f"{stars} {state} Upgrade @plur-ai to 0.18.0 :work:"
    rewritten = gh_reconcile.RE_HEADING.sub(
        lambda m: m.group(1) + " DONE" + m.group(3), line, count=1)
    assert rewritten == f"{stars} DONE Upgrade @plur-ai to 0.18.0 :work:"


def test_the_title_the_ledger_reads_is_unchanged_by_closing():
    """End to end through the real parser and the real closer, no stand-ins."""
    source = (
        "#+SEQ_TODO: TODO NEXT | DONE\n"
        "* NEXT Upgrade @plur-ai to 0.18.0\n"
        "  :PROPERTIES:\n"
        "  :ID: org-test-1\n"
        "  :END:\n"
    )
    lines = source.splitlines()
    task = next(t for t in gh_reconcile.parse_org_tasks(source) if t.state == "NEXT")
    closed = gh_reconcile.mark_task_done(lines, task, "merged", None)

    after = list(loads("\n".join(closed) + "\n")[1:])  # orgparse slices are iterators
    assert [n.todo for n in after] == ["DONE"]
    assert [n.get_heading(format="raw") for n in after] == ["Upgrade @plur-ai to 0.18.0"]
