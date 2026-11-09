"""The morning's journal page has one judge: the briefing cadence (plan 2026-10-31, phase H, morning 0).

box-briefing and cadence-firm-cos-briefing both asserted '## Daily Briefing' on
the newest journal page, so one missing page opened two repair tasks. The
cadence (DIP-0050) is what runs the briefing and judges it by its template's
evidence, so it stays the judge; box-briefing keeps judging what only it
declares (the briefing artifact, its log and the voice message) and stays the
one declared producer (the one-audio-briefing promise counts it).

The morning check (cos_verify_morning) reads the page through the cadence's own
contract, so the two cannot disagree (its own journal check was retired, datacore#54).
"""
from __future__ import annotations

from pathlib import Path

import yaml

MANIFEST = Path(__file__).resolve().parent.parent / "jobs" / "manifest.yaml"
JUDGE = "cadence-firm-cos-briefing"


def _jobs() -> dict:
    return {j["name"]: j for j in yaml.safe_load(MANIFEST.read_text())["jobs"]}


def test_only_the_cadence_judges_the_journal_page():
    judges = sorted(name for name, j in _jobs().items()
                    for a in j.get("artifacts") or []
                    if "/notes/journals/" in str(a.get("path", "")) and "Daily Briefing" in str(a.get("arg", "")))
    assert judges == [JUDGE], f"jobs judging the journal page: {judges}"


def test_box_briefing_still_judges_what_only_it_declares():
    arts = [str(a["path"]) for a in _jobs()["box-briefing"]["artifacts"]]
    assert any(p.endswith("app-briefing.json") for p in arts)
    assert any(p.endswith("audio-script.json") for p in arts)
    assert "cos_morning.sh" in _jobs()["box-briefing"]["cmd"]
