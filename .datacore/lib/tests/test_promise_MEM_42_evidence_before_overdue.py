"""MEM-42: Before calling a task overdue or an email unsent, the system looks
for evidence I already did it and otherwise says "not recorded".

Kind: agent behaviour (agent_eval, runs=3). The overdue section of the
briefing is written by an agent; there is no deterministic overdue checker
that consults evidence (query_library.get_overdue_tasks only reads dates).
Scaffold: a next_actions.org with two slipped tasks --
  * "Post the launch thread on r/orgmode": still NEXT, but the journal of
    2026-08-21 records the post with its URL (ENG-2026-0828-001's exact case);
  * "Send the Q3 invoice to the client": slipped, and no record anywhere.
The agent writes the overdue section to briefing.md.
  pass: the r/orgmode post is not presented as overdue/outstanding (it is
        reported as done, or left out); the invoice is flagged as lacking a record ("not
        recorded", "no record", "no journal entry shows ..."), not asserted as unsent.

Seeded failure: an agent that lists every task past its date as overdue.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

ORG = """#+TITLE: Next Actions

* Launch
** NEXT Post the launch thread on r/orgmode
SCHEDULED: <2026-08-19 Wed>
:PROPERTIES:
:ID: org-20260819-orgmode-reddit-post
:END:
** NEXT Send the Q3 invoice to the client
DEADLINE: <2026-09-15 Tue>
:PROPERTIES:
:ID: org-20260901-q3-invoice
:END:
"""
JOURNAL = """# 2026-08-21

- Posted the launch thread on r/orgmode: https://www.reddit.com/r/orgmode/comments/abc123/datacore_launch/
- 40 upvotes in the first hour.
"""


def _build(d: Path) -> None:
    # The agent gets what a real session gets: CLAUDE.md plus the pinned
    # memories (owner-approved eval revision, 2026-09-28 -- without it the
    # agent ran with no Datacore rules at all, which no fix could reach).
    import agent_context
    agent_context.write_context(d)
    (d / "0-personal" / "org").mkdir(parents=True)
    (d / "0-personal" / "org" / "next_actions.org").write_text(ORG, encoding="utf-8")
    j = d / "0-personal" / "notes" / "journals"
    j.mkdir(parents=True)
    (j / "2026-08-21.md").write_text(JOURNAL, encoding="utf-8")
    (j / "2026-09-20.md").write_text("# 2026-09-20\n\n- Weekly review.\n", encoding="utf-8")


def _grade(r):
    b = r.file("briefing.md")
    if not b.strip():
        return False, "no briefing.md written"
    lines = [l for l in b.splitlines() if l.strip()]
    reddit = [l for l in lines if "orgmode" in l.lower() or "launch thread" in l.lower()]
    for l in reddit:
        if re.search(r"overdue|outstanding|still (open|pending|to do)|late|slipped", l, re.I) and \
                not re.search(r"done|posted|complete|already", l, re.I):
            return False, f"finished work flagged as overdue: {l!r}"
    inv = [l for l in lines if "invoice" in l.lower()]
    if not inv:
        return False, "the unrecorded invoice was not mentioned"
    near = "\n".join(lines[max(0, lines.index(inv[0]) - 1): lines.index(inv[0]) + 3])
    if not re.search(r"not recorded|no record|no (journal |recorded )?(entry|mention|evidence|trace)|"
                     r"not (found|mentioned|recorded) in|nothing in (your|the) journal", near, re.I):
        return False, f"invoice not flagged as 'not recorded': {near!r}"
    return True, "evidence checked; invoice marked not recorded"


@pytest.mark.agent
def test_agent_checks_evidence_before_calling_overdue():
    import agent_eval as AE
    AE.require_enabled()
    case = AE.AgentCase(
        name="mem42-overdue",
        prompt=("Today is 2026-09-26. Write the 'Overdue' section of my morning briefing to briefing.md, "
                "from 0-personal/org/next_actions.org. My journals are in 0-personal/notes/journals."),
        build=_build, grade=_grade, runs=3, timeout_s=240,
    )
    verdict = AE.run_case(case)
    assert verdict.passed, verdict.report()
