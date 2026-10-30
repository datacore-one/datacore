"""The weekly-plan contract reads the cadence's draft, not any weekly-plan page.

The pattern `weekly-plan-*.md` also matched the Friday close-out page
(`weekly-plan-2026-W44-closeout.md`, heading "# Friday Close-Out — ...") and
the Saturday skeleton. The checker reads a glob's NEWEST match, so from the
Friday close-out until Sunday's run the close-out was the file checked for
'# Weekly Plan — <week>', and cadence-firm-cos-weekly-plan went red on a
plan that had been written (2026-10-30 on the box verifier log).
"""
import os
import time
from pathlib import Path

import yaml

from jobs.checks import expand_path

MANIFEST = Path(__file__).resolve().parent.parent / "jobs" / "manifest.yaml"
JOB = "cadence-firm-cos-weekly-plan"


def _pattern():
    doc = yaml.safe_load(MANIFEST.read_text())
    job = next(j for j in doc["jobs"] if j["name"] == JOB)
    return job["artifacts"][0]["path"]


def test_weekly_plan_glob_picks_the_draft_over_a_newer_closeout_or_skeleton(tmp_path, monkeypatch):
    pages = tmp_path / "Data" / "0-personal" / "notes" / "pages"
    pages.mkdir(parents=True)
    draft = pages / "weekly-plan-2026-W44-draft.md"
    draft.write_text("# Weekly Plan — 2026-W44 (Mon 10-26 → Sun 11-01)\n")
    old = time.time() - 600
    os.utime(draft, (old, old))
    (pages / "weekly-plan-2026-W44-closeout.md").write_text("# Friday Close-Out — 2026-W44\n")
    (pages / "weekly-plan-2026-W45-skeleton.md").write_text("# Weekly Plan — 2026-W45 Pre-Draft Skeleton\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    pattern = _pattern()
    assert "*" in pattern, "the pattern must stay a glob (expand_path globs only on '*')"
    assert expand_path(pattern) == str(draft), f"{pattern} resolved to a page that is not the plan draft"
