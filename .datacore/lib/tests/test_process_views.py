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
    # third round (owner, 2026-10-31): one more size down, 16px names and 13px technical lines
    assert "svg .name{font-family:'Outfit',sans-serif;font-size:16px" in pv.CSS
    assert "svg .sub{font-family:'JetBrains Mono',monospace;font-size:13px" in pv.CSS


# -- third round (owner, 2026-10-31): every process gets a Current / Upgrade switch -------

NEEDS = {"your decision", "DIP amendment", "code fix", "install"}


def test_every_flow_has_an_upgrade_entry():
    for f in pv.FLOWS:
        up = pv.UPGRADES.get(f["slug"])
        assert up, f"{f['slug']}: no upgrade entry"
        assert up.get("summary"), f["slug"]
        if up.get("keep"):
            continue
        assert up["changes"], f["slug"]
        for what, why, needs in up["changes"]:
            assert what and why, f["slug"]
            assert needs in NEEDS, (f["slug"], needs)
        pv.svg_flow(pv.upgrade_flow(f))   # raises on long labels and unknown marks


def test_upgrade_edges_go_down_and_cells_are_unique():
    for f in pv.FLOWS:
        if pv.UPGRADES[f["slug"]].get("keep"):
            continue
        u = pv.upgrade_flow(f)
        assert len(u["lanes"]) <= 4, u["slug"]
        pos = {s[0]: (s[1], s[2]) for s in u["steps"]}
        assert len(pos) == len(u["steps"]), u["slug"]
        assert len({(s[1], s[2]) for s in u["steps"]}) == len(u["steps"]), u["slug"]
        for a, b, _ in u["edges"]:
            assert pos[b][1] > pos[a][1], (u["slug"], a, b)


def test_page_has_a_switch_per_flow_and_one_for_the_page():
    html = pv.page({})
    for f in pv.FLOWS:
        assert f'name="view-{f["slug"]}"' in html, f["slug"]
        assert f'id="{f["slug"]}-up-pane"' in html, f["slug"]
    assert 'data-all="up"' in html and 'data-all="cur"' in html
    assert html.count("Proposed, not running") >= len(pv.FLOWS)


def test_no_duplicate_ids_on_the_page():
    html = pv.page({})
    ids = re.findall(r'\sid="([^"]+)"', html)
    dup = {i for i in ids if ids.count(i) > 1}
    assert not dup, sorted(dup)


def test_sidebar_can_be_collapsed():
    html = pv.page({})
    assert 'id="side-toggle"' in html and 'aria-controls="side"' in html
    assert '<nav class="side" id="side"' in html
    assert "body.side-off" in pv.CSS


def test_every_diagram_has_zoom_and_fit():
    html = pv.page({})
    drawn = html.count('<svg viewBox=')
    for act in ("out", "in", "fit", "full"):
        assert html.count(f'data-zoom="{act}"') == drawn, act
