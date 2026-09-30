"""/today must read the market-phase report and the news where their producers put them.

2026-09-30: the market-phase job on nightshift ran at 05:31 UTC and published
6-meridian/reports/market-phase/market-phase-2026-09-30.md, which was on the Mac
by 07:04 UTC. /today step 11 still said to look in 0-personal/0-inbox/ (where the
job wrote before it got its adapter on 2026-09-17), found nothing, and the
briefing had no market phase.

The same morning step 10 refreshed headlines.json correctly -- all 500 items
scored -- but the briefing read keys the store does not have (`score`,
`ai_score`), got None for every item and reported the news as unscored. Step 10
now reads through news_briefing.py, which knows the store's fields.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
TODAY = ROOT / ".datacore" / "commands" / "today.md"
NIGHTSHIFT_LIB = ROOT / ".datacore" / "modules" / "nightshift" / "lib"


def _step(n: int) -> str:
    text = TODAY.read_text(encoding="utf-8")
    m = re.search(rf"^## Step {n}:.*?(?=^## Step )", text, re.S | re.M)
    assert m, f"today.md has no Step {n}"
    return m.group(0)


def _market_phase_dir() -> str:
    sys.path.insert(0, str(NIGHTSHIFT_LIB))
    import market_phase_orchestrator as mpo
    return f"6-meridian/{mpo.REPORT_DIR.as_posix()}"


def test_step_11_reads_the_market_phase_report_where_the_job_publishes_it():
    step = _step(11)
    assert _market_phase_dir() in step
    assert "0-personal/0-inbox" not in step


def test_step_11_says_plainly_when_the_report_is_missing():
    step = _step(11)
    assert "nightshift-market-phase" in step  # where to look for why
    assert re.search(r"no market-phase report", step, re.I)


def test_quick_reference_points_at_the_same_place():
    text = TODAY.read_text(encoding="utf-8")
    row = next(l for l in text.splitlines() if l.startswith("| Market phase?"))
    assert _market_phase_dir() in row


def test_step_10_refreshes_then_reads_through_the_briefing_helper():
    step = _step(10)
    assert "feed_fetcher.py" in step
    assert "news_briefing.py" in step
    # the store's field is relevance_score; `score` / `ai_score` do not exist
    assert "relevance_score" in step
