"""process_views: the processes page (owner, 2026-10-30, second round).

Asked: a left sidebar listing every flow, type one size smaller, the ledger's
processes (fold, seal, corrections), where signing and provenance receipts
happen, and how cadences and scheduled jobs run.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import process_views as pv  # noqa: E402


def test_every_flow_is_in_the_sidebar():
    html = pv.page({})
    side = re.search(r'<nav class="side".*?</nav>', html, re.S)
    assert side, "no sidebar"
    for f in pv.FLOWS:
        assert f'href="#{f["slug"]}"' in side.group(0), f["slug"]
    assert 'href="#overlaps"' in side.group(0)


def test_the_ledger_and_schedule_flows_exist():
    slugs = {f["slug"] for f in pv.FLOWS}
    assert {"ledger-finality", "ledger-corrections", "ledger-daily",
            "scheduled-job", "cadence-run"} <= slugs


def test_marks_render_with_hover_text_and_optional_ones_are_dashed():
    f = next(f for f in pv.FLOWS if f["slug"] == "task-lifecycle")
    svg = pv.svg_flow(f)
    assert 'class="mark optional"' in svg          # co-sign only for side effects, receipts where the hook is on
    assert "<title>" in svg.split('class="mark', 1)[1][:400]
    led = pv.svg_flow(next(f for f in pv.FLOWS if f["slug"] == "ledger-finality"))
    assert 'class="mark always"' in led


def test_every_mark_code_is_known():
    for f in pv.FLOWS:
        for st in f["steps"]:
            for c in (st[7] if len(st) > 7 else ""):
                assert c in pv.MARKS, (f["slug"], st[0], c)


def test_type_is_one_size_smaller():
    assert "svg .name{font-family:'Outfit',sans-serif;font-size:18px" in pv.CSS
    assert "svg .sub{font-family:'JetBrains Mono',monospace;font-size:14px" in pv.CSS
