"""SPC-2: Work started inside a space or one of its projects is filed,
journalled and tasked in that space, not in personal or elsewhere.

Kind: deterministic + agent behaviour.

Deterministic -- what a session started there is told (`focus_mode.detect`,
the context every session in a space reads):
  * started in `<space>/2-projects/<project>/src`: the space, its journal and
    its org directory are that space's;
  * started elsewhere inside a space (`<space>/1-tracks/ops`): the session is
    still told which space it is in -- today it gets "full" mode with no space,
    so its journal and tasks default to personal;
  * the project context file written by `apply_focus_context` names the same
    space's journal and org.

Agent (@agent, `agent_eval.py`): a headless session is asked to log work done
on a 1-datafund project and to add a follow-up; the journal entry must land in
1-datafund/journal, the follow-up in 1-datafund/org, and 0-personal must stay
untouched. Fails (never skips) unless DATACORE_AGENT_EVALS=1.

Seeded failure: journal and tasks routed to 0-personal whenever the session is
not in a 2-projects directory (the full-mode default).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import apply_focus_context
import focus_mode


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "Data"
    (root / ".datacore" / "registry").mkdir(parents=True)
    for s in ("0-personal", "1-datafund"):
        for d in ("journal", "org", "1-tracks/ops", "2-projects/verity/src"):
            (root / s / d).mkdir(parents=True)
    return root


def test_a_project_session_is_told_its_space(root, monkeypatch):
    monkeypatch.chdir(root / "1-datafund" / "2-projects" / "verity" / "src")
    info = focus_mode.detect()
    assert info.get("space_dir") == "1-datafund", info
    assert Path(info["journal_path"]) == (root / "1-datafund" / "journal").resolve()
    assert Path(info["org_path"]) == (root / "1-datafund" / "org").resolve()


def test_a_session_elsewhere_in_a_space_is_told_its_space(root, monkeypatch):
    monkeypatch.chdir(root / "1-datafund" / "1-tracks" / "ops")
    info = focus_mode.detect()
    assert info.get("space_dir") == "1-datafund", (
        f"a session started inside 1-datafund is not told its space: {info} -- its journal and "
        "tasks fall back to personal")


def test_the_project_context_names_the_same_space():
    section = apply_focus_context.generate_section("1-datafund")
    assert "~/Data/1-datafund/journal/" in section and "~/Data/1-datafund/org/" in section
    assert "0-personal" not in section


# ── agent part ─────────────────────────────────────────────────────────────

def _build(d: Path) -> None:
    for s in ("0-personal", "1-datafund"):
        (d / s / "journal").mkdir(parents=True)
        (d / s / "org").mkdir(parents=True)
        (d / s / "org" / "inbox.org").write_text("#+TITLE: Inbox\n")
    (d / "CLAUDE.md").write_text(
        "# Datacore\n\nSpaces: 0-personal (personal), 1-datafund (team). Each space has "
        "journal/YYYY-MM-DD.md and org/inbox.org. Follow-ups are captured to a space's "
        "org/inbox.org.\n")
    proj = d / "1-datafund" / "2-projects" / "verity"
    proj.mkdir(parents=True)
    (proj / "parser.py").write_text("def parse(x):\n    return x.strip()\n")
    (proj / "CLAUDE.md").write_text("# verity\n\n" + apply_focus_context.generate_section("1-datafund") + "\n")


def _grade(r) -> tuple[bool, str]:
    today = date.today().isoformat()
    journal = r.file(f"1-datafund/journal/{today}.md")
    inbox = r.file("1-datafund/org/inbox.org")
    personal = r.file(f"0-personal/journal/{today}.md") + r.file("0-personal/org/inbox.org").replace("#+TITLE: Inbox\n", "")
    ok = "parser" in journal.lower() and "review" in inbox.lower() and not personal.strip()
    return ok, f"journal={bool(journal)} inbox_followup={'review' in inbox.lower()} personal_touched={bool(personal.strip())}"


@pytest.mark.agent
def test_an_agent_files_project_work_in_its_space():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_eval import AgentCase, require_enabled, run_case
    require_enabled()
    case = AgentCase(
        name="spc2-filing",
        prompt=("I just fixed the whitespace bug in the parser of the verity project "
                "(1-datafund/2-projects/verity/parser.py). Write today's journal entry for this work "
                "and capture a follow-up task to review the parser tests."),
        build=_build, grade=_grade, runs=2, timeout_s=240, max_budget_usd=0.5)
    verdict = run_case(case)
    assert verdict.passed, verdict.report()
