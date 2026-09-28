"""MEM-37: Anything deferred or parked goes back into my inbox under a heading,
never into a side file nobody reads.

Kind: agent behaviour (agent_eval, runs=3) + production contract (read-only).
  * agent: asked to take three tasks out of the overnight queue and "park them
    for now", the agent puts them into 0-personal/org/inbox.org under a
    heading (children of a level-1 heading) and creates no new file holding
    them (ENG-2026-08-19-046/-049).
  * production: no space's org/ directory holds a parked/deferred side file
    (name says park/defer/later/holding/staging) with OPEN tasks in it. Open
    tasks read with org-workspace, never by grepping the org text.

Seeded failure: a parked side file with an open task (the pre-rule
0-personal/org/nightshift-parked-archive-2026-07-01.org convention).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parents[2]
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

SIDE = re.compile(r"park|defer|later|holding|staging", re.I)
OPEN = {"TODO", "NEXT", "WAITING", "REVIEW"}


def _open_tasks(f: Path) -> list[str]:
    from org_workspace import OrgWorkspace
    ws = OrgWorkspace()
    ws.load(f)
    return [n.heading for n in ws.all_nodes() if (n.todo or "") in OPEN]


def _side_files_with_open_tasks(root: Path) -> list[str]:
    out = []
    for org in sorted(root.glob("[0-9]-*/org")):
        for f in sorted(org.glob("*.org")):
            if SIDE.search(f.name):
                try:
                    n = len(_open_tasks(f))
                except Exception as exc:  # noqa: BLE001 -- unreadable is a finding too
                    out.append(f"{f.relative_to(root)} (unreadable: {exc})")
                    continue
                if n:
                    out.append(f"{f.relative_to(root)} ({n} open)")
    return out


def test_seeded_side_file_is_detected(tmp_path):
    org = tmp_path / "0-personal" / "org"
    org.mkdir(parents=True)
    (org / "inbox.org").write_text("* Inbox\n", encoding="utf-8")
    (org / "nightshift-parked-2026-09-26.org").write_text("* Parked\n** TODO Re-run the export\n", encoding="utf-8")
    assert _side_files_with_open_tasks(tmp_path), "detector missed a parked side file with an open task"


@pytest.mark.production
def test_no_space_parks_open_tasks_in_a_side_file():
    found = _side_files_with_open_tasks(ROOT)
    assert not found, f"open tasks parked in side files nobody processes: {found}"


# ── agent behaviour ─────────────────────────────────────────────────────────

TASKS = ["Benchmark the recall latency on the 5k-engram store",
         "Draft the Q4 grant budget table",
         "Re-crawl the competitor pricing pages"]


def _build(d: Path) -> None:
    # The agent gets what a real session gets: CLAUDE.md plus the pinned
    # memories (owner-approved eval revision, 2026-09-28 -- without it the
    # agent ran with no Datacore rules at all, which no fix could reach).
    import agent_context
    agent_context.write_context(d)
    org = d / "0-personal" / "org"
    org.mkdir(parents=True)
    (org / "inbox.org").write_text("#+TITLE: Inbox\n\n* Inbox\n** TODO Call the accountant\n", encoding="utf-8")
    (org / "next_actions.org").write_text("#+TITLE: Next Actions\n\n* Work\n", encoding="utf-8")
    body = "".join(f"** TODO {t} :AI:\n:PROPERTIES:\n:ID: ns-{i}\n:END:\n" for i, t in enumerate(TASKS))
    (org / "nightshift.org").write_text(f"#+TITLE: Nightshift queue\n\n* Queue\n{body}", encoding="utf-8")


def _grade(r):
    inbox = r.file("0-personal/org/inbox.org")
    missing = [t for t in TASKS if t not in inbox]
    if missing:
        return False, f"not parked in inbox.org: {missing}"
    lines = inbox.splitlines()
    for t in TASKS:
        line = next(l for l in lines if t in l)
        if not line.startswith("**"):
            return False, f"parked entry is not under a heading: {line!r}"
    extra = [p for p in r.scaffold.rglob("*") if p.is_file() and p.name not in
             ("inbox.org", "next_actions.org", "nightshift.org")
             and any(t in p.read_text(errors="replace") for t in TASKS)]
    if extra:
        return False, f"parked tasks also written to side files: {[str(p.relative_to(r.scaffold)) for p in extra]}"
    return True, "parked in inbox.org under a heading"


@pytest.mark.agent
def test_agent_parks_into_the_inbox():
    import agent_eval as AE
    AE.require_enabled()
    case = AE.AgentCase(
        name="mem37-park",
        prompt=("Tonight's queue in 0-personal/org/nightshift.org is overloaded. Take its three tasks out "
                "of the queue and park them somewhere for now; I'll decide on them later."),
        build=_build, grade=_grade, runs=3, timeout_s=240,
    )
    verdict = AE.run_case(case)
    assert verdict.passed, verdict.report()
