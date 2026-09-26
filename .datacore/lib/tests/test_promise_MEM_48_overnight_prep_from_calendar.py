"""MEM-48: Overnight, tomorrow's calendar and commitments are read and prep
tasks are ready for me in the morning.

Kind: agent behaviour (agent_eval, runs=3) + production contract (read-only).
The overnight evening job is box-tomorrow (lib/jobs/manifest.yaml ->
lib/cos_tomorrow.sh), which asks an agent to "Run the /tomorrow evening
review" (.datacore/commands/tomorrow.md). No deterministic code reads the
calendar and writes prep tasks, so:
  * agent: a tiny scaffold with the REAL tomorrow.md, a calendar stand-in
    command (`calendar-events`) that lists tomorrow's two meetings, and a
    commitment due tomorrow in next_actions.org. Run as cos_tomorrow.sh asks.
    pass: 0-personal/org/inbox.org gains a prep task naming each meeting
    (the investor call and the Dubai pilot review) and the commitment.
  * production: in the last 7 days, at least one prep task was created
    overnight (CREATED between 18:00 and 08:00) in a space's inbox.org or
    next_actions.org -- read with org-workspace.

Seeded failure: an evening review that only PREVIEWS tomorrow (today's
tomorrow.md step 9 "Tomorrow's Preview") and creates no prep task.
"""
from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parents[2]
DC = ROOT / ".datacore"
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

PREP = re.compile(r"\bprep(are|aration)?\b", re.I)
STAMP = re.compile(r"(\d{4}-\d{2}-\d{2})(?:\s+\w{3})?\s+(\d{2}):(\d{2})")


def _overnight_prep_tasks(root: Path, days: int = 7) -> list[str]:
    from org_workspace import OrgWorkspace
    since = datetime.now() - timedelta(days=days)
    found = []
    for f in sorted(root.glob("[0-9]-*/org/*.org")):
        if f.name not in ("inbox.org", "next_actions.org"):
            continue
        ws = OrgWorkspace()
        try:
            ws.load(f)
        except Exception:  # noqa: BLE001 -- an unreadable file holds no evidence
            continue
        for n in ws.all_nodes():
            if not PREP.search(n.heading or ""):
                continue
            m = STAMP.search(str((n.properties or {}).get("CREATED", "")))
            if not m:
                continue
            at = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}:{m.group(3)}")
            if at >= since and (at.hour >= 18 or at.hour < 8):
                found.append(f"{f.relative_to(root)}: {n.heading}")
    return found


def test_detector_sees_an_overnight_prep_task(tmp_path):
    org = tmp_path / "0-personal" / "org"
    org.mkdir(parents=True)
    at = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    (org / "inbox.org").write_text(f"* Inbox\n** TODO Prep for the investor call\n:PROPERTIES:\n"
                                   f":CREATED: [{at} 21:05]\n:END:\n", encoding="utf-8")
    assert _overnight_prep_tasks(tmp_path)


@pytest.mark.production
def test_prep_tasks_were_made_overnight_this_week():
    found = _overnight_prep_tasks(ROOT)
    assert found, "no prep task was created overnight in the last 7 days in any space"


# ── agent behaviour ─────────────────────────────────────────────────────────

EVENTS = """2026-09-27 10:00-10:45  Investor call — Northwind Ventures (seed follow-up)
2026-09-27 15:00-16:00  Dubai pilot review with the DMCC team
"""


def _build(d: Path) -> None:
    from agent_eval import plant_stub
    org = d / "0-personal" / "org"
    org.mkdir(parents=True)
    (org / "inbox.org").write_text("#+TITLE: Inbox\n\n* Inbox\n", encoding="utf-8")
    (org / "next_actions.org").write_text(
        "#+TITLE: Next Actions\n\n* Work\n** NEXT Send the signed NDA to Northwind\n"
        "DEADLINE: <2026-09-27 Sun>\n", encoding="utf-8")
    (d / "0-personal" / "notes" / "journals").mkdir(parents=True)
    shutil.copy(DC / "commands" / "tomorrow.md", d / "tomorrow.md")
    plant_stub(d, "calendar-events", stdout=EVENTS)


def _grade(r):
    inbox = r.file("0-personal/org/inbox.org") + r.file("0-personal/org/next_actions.org")
    heads = [l for l in inbox.splitlines() if l.startswith("*")]
    need = {"investor call": r"investor|northwind", "Dubai pilot review": r"dubai|dmcc"}
    missing = [k for k, pat in need.items()
               if not any(PREP.search(h) and re.search(pat, h, re.I) for h in heads)
               and not any(re.search(pat, h, re.I) and "Send the signed NDA" not in h for h in heads)]
    if missing:
        return False, f"no prep task for: {missing}"
    return True, "prep tasks created"


@pytest.mark.agent
def test_evening_review_leaves_prep_tasks_for_tomorrow():
    import agent_eval as AE
    AE.require_enabled()
    case = AE.AgentCase(
        name="mem48-prep",
        prompt=("Today is 2026-09-26. Run the /tomorrow evening review (the command is ./tomorrow.md). "
                "Be concise. Tomorrow's calendar: run `calendar-events --date 2026-09-27`. Tasks live in "
                "0-personal/org/. Skip steps that need tools you do not have."),
        build=_build, grade=_grade, runs=3, timeout_s=360,
        allowed_tools=("Read", "Write", "Edit", "Glob", "Grep", "Bash(calendar-events:*)"),
    )
    verdict = AE.run_case(case)
    assert verdict.passed, verdict.report()
