"""The briefing contracts read the newest DAILY journal, not any dated page.

The pattern `20*-*-*.md` also matched `2026-10-19-weekly-plan.md`, a page the
weekly plan writes into the same folder. Being the newest match, it was the
file checked for '## Daily Briefing', and box-briefing and
cadence-firm-cos-briefing failed on a complete briefing (11 such failures
around 2026-10-19 in the box verifier log).
"""
import os
import time
from pathlib import Path

import yaml

from jobs.checks import expand_path

MANIFEST = Path(__file__).resolve().parent.parent / "jobs" / "manifest.yaml"
JOBS = ("box-briefing", "cadence-firm-cos-briefing")


def _journal_patterns():
    doc = yaml.safe_load(MANIFEST.read_text())
    out = {}
    for job in doc["jobs"]:
        if job["name"] in JOBS:
            pats = [a["path"] for a in job["artifacts"] if "/notes/journals/" in a["path"]]
            assert pats, f"{job['name']} has no journal artifact"
            out[job["name"]] = pats[0]
    assert set(out) == set(JOBS)
    return out


def test_journal_glob_picks_the_daily_page_over_a_newer_weekly_plan(tmp_path, monkeypatch):
    journals = tmp_path / "Data" / "0-personal" / "notes" / "journals"
    journals.mkdir(parents=True)
    daily = journals / "2026-10-19.md"
    daily.write_text("## Daily Briefing\n")
    plan = journals / "2026-10-19-weekly-plan.md"
    plan.write_text("# Weekly plan\n")
    old = time.time() - 600
    os.utime(daily, (old, old))
    monkeypatch.setenv("HOME", str(tmp_path))
    for name, pattern in _journal_patterns().items():
        assert "*" in pattern, f"{name}: the pattern must stay a glob (expand_path globs only on '*')"
        assert expand_path(pattern) == str(daily), f"{name}: {pattern} resolved to the weekly plan"
