"""MEM-36: A bare link goes to research. A link with my comment becomes an
action, because the comment says I already read it.

Kind: agent behaviour (agent_eval, runs=3). Inbox routing is done by an agent
following the shipped instructions (.datacore/commands/process-inbox.md and
.datacore/agents/gtd-inbox-processor.md -- there is no deterministic URL
router to test). The scaffold is a one-space tmp tree whose inbox holds a bare
link and a link carrying the owner's comment; the agent processes it with the
REAL instruction files copied in.
  pass: the bare link lands in research_learning.org (not next_actions.org);
        the commented link lands in next_actions.org as a task (not in
        research_learning.org, not in someday.org).

Seeded failure: instructions that send every link to research (or to
someday.org as a "captured fragment", as gtd-inbox-processor.md says today).
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
DC = TESTS.parents[1]
sys.path.insert(0, str(TESTS))

BARE = "https://example.org/2026/09/agent-memory-benchmarks"
COMMENTED = "https://example.org/pricing/three-tier-analysis"
INBOX = f"""#+TITLE: Inbox

* Inbox
** {BARE}
:PROPERTIES:
:CREATED: [2026-09-25 Fri 21:10]
:END:
** {COMMENTED}
:PROPERTIES:
:CREATED: [2026-09-25 Fri 21:12]
:END:
Good argument. We move Verity to three pricing tiers before the Dubai pilot.
"""


def _build(d: Path) -> None:
    org = d / "0-personal" / "org"
    org.mkdir(parents=True)
    (org / "inbox.org").write_text(INBOX, encoding="utf-8")
    (org / "next_actions.org").write_text("#+TITLE: Next Actions\n\n* Verity\n* Technology\n", encoding="utf-8")
    (org / "research_learning.org").write_text("#+TITLE: Research\n\n* Verity\n* Technology\n", encoding="utf-8")
    (org / "ideas.org").write_text("#+TITLE: Ideas\n", encoding="utf-8")
    (org / "someday.org").write_text("#+TITLE: Someday\n", encoding="utf-8")
    shutil.copy(DC / "commands" / "process-inbox.md", d / "process-inbox.md")
    shutil.copy(DC / "agents" / "gtd-inbox-processor.md", d / "gtd-inbox-processor.md")


def _grade(r):
    org = "0-personal/org/"
    na, rl, sd = (r.file(org + f) for f in ("next_actions.org", "research_learning.org", "someday.org"))
    problems = []
    if BARE not in rl:
        problems.append("bare link not in research_learning.org")
    if BARE in na:
        problems.append("bare link became an action")
    if COMMENTED not in na and "three pricing tiers" not in na.lower().replace("3", "three"):
        problems.append("commented link did not become an action in next_actions.org")
    if COMMENTED in rl:
        problems.append("commented link was sent to research")
    if COMMENTED in sd:
        problems.append("commented link parked in someday.org")
    return (not problems), "; ".join(problems) or "routed correctly"


@pytest.mark.agent
def test_bare_link_to_research_commented_link_to_action():
    import agent_eval as AE
    AE.require_enabled()
    case = AE.AgentCase(
        name="mem36-link-routing",
        prompt=("Run /process-inbox in nightshift mode for 0-personal/org/inbox.org. The command is "
                "./process-inbox.md and the classification rules are ./gtd-inbox-processor.md. Do the "
                "work yourself (no subagents) and skip the git step."),
        build=_build, grade=_grade, runs=3, timeout_s=300,
    )
    verdict = AE.run_case(case)
    assert verdict.passed, verdict.report()
