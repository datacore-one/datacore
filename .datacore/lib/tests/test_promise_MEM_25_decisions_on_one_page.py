"""MEM-25: When I have several decisions, I get one decision page with a suggested answer on each,
asked as a few plain principles.

Kind: agent behaviour (tests/agent_eval.py, runs=3; context = the real CLAUDE.md + every pinned
engram, tests/agent_context.py, and the real decision-board skill copied into the scaffold's
.claude/skills/ as it is installed in ~/.claude/skills/). `open` is a logging stub.

Seeded failure: four open decisions in a notes file and "help me decide these" -- the chat-list
answer is the temptation. The promise holds when exactly one local page (.html) is produced that
carries all four decisions and a suggested answer on each. "Asked as a few plain principles" is
not machine-gradable and is not graded here.
"""
import re
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_context  # noqa: E402
import agent_eval as AE  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / ".datacore" / "skills" / "decision-board"
TOPICS = (("newsletter", "cadence"), ("hosting", "vps"), ("hire", "designer"), ("pricing", "free tier"))


def _build(d: Path):
    agent_context.write_context(d)
    shutil.copytree(SKILL, d / ".claude" / "skills" / "decision-board")
    (d / "open-decisions.md").write_text(
        "# Open decisions\n\n"
        "1. Newsletter cadence: weekly or monthly?\n"
        "2. Hosting: stay on the current VPS or move to managed hosting?\n"
        "3. Hire: a part-time designer now, or wait until after the grant?\n"
        "4. Pricing: keep the free tier or cap it at 100 notes?\n")
    AE.plant_stub(d, "open")


def _grade(r: AE.RunResult):
    if r.is_error or r.timed_out:
        return False, f"run failed (error={r.is_error}, timeout={r.timed_out})"
    # Anywhere the run could write: the repo, $DATACORE_STATE, or ~/.datacore/state under its HOME.
    root = r.run_dir or r.scaffold
    pages = [p for p in root.rglob("*.html") if ".claude" not in p.parts]
    if len(pages) != 1:
        return False, f"expected one decision page, found {len(pages)}"
    html = pages[0].read_text(errors="replace").lower()
    missing = [t[0] for t in TOPICS if not any(k in html for k in t)]
    if missing:
        return False, f"page lacks decisions: {missing}"
    # A per-row suggestion shows either as rendered text or as a per-item data field.
    if len(re.findall(r"suggest|recommend|\b(?:default|pick|rec)\b[\"']?\s*[:=]", html)) < len(TOPICS):
        return False, "not every decision carries a suggested answer"
    return True, f"one page: {pages[0].name}"


CASE = AE.AgentCase(
    name="MEM-25", runs=3, timeout_s=180, max_budget_usd=0.8,
    prompt="I have to make the calls in open-decisions.md this week. Help me decide them.",
    build=_build, grade=_grade,
    # The skill needs Bash (owner-only dir, atomic write, open); the run is sandboxed to a tmp
    # HOME/DATACORE_STATE and `open` is a stub, so Bash is allowed whole.
    allowed_tools=("Read", "Write", "Edit", "Glob", "Grep", "Skill", "Bash"),
    disallowed_tools=("WebFetch", "WebSearch", "Agent"),
)


def test_grader_catches_a_chat_only_answer(tmp_path):
    r = AE.RunResult(run=0, scaffold=tmp_path, text="1. weekly (suggested) 2. stay 3. wait 4. keep",
                     exit_code=0)
    assert _grade(r)[0] is False


@pytest.mark.agent
def test_agent_puts_decisions_on_one_page(tmp_path):
    AE.require_enabled()
    v = AE.run_case(CASE, workdir=tmp_path)
    assert v.passed, v.report()
