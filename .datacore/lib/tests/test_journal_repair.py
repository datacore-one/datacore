"""journal_repair: pages already written with a second page or a split briefing
become one page with one briefing on top, and no line of content is lost."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import journal_repair as J  # noqa: E402

PARTS = ("Good Morning", "The World", "Your Agenda", "Spaces", "Decisions Due", "Horizon")


def _h2(text):
    return [l[3:].strip() for l in text.splitlines() if l.startswith("## ")]


def _lines(text):
    return sorted(l.lstrip("#").strip() for l in text.splitlines() if l.strip())


def _one_briefing_on_top(text):
    h2 = _h2(text)
    return h2.count("Daily Briefing") == 1 and h2[0] == "Daily Briefing" and not set(h2) & set(PARTS)


def test_parts_written_as_h2_move_inside_the_briefing():
    page = ("---\ndate: 2026-09-28\n---\n\n## Daily Briefing\n\n## Good Morning\n\nhi\n\n"
            "## Your Agenda\n\n- a\n\n## Update 09:30\n\nlater\n\n## Data's Observation\n\nobs\n")
    out = J.nest(page)
    assert _one_briefing_on_top(out), out
    assert "### Good Morning" in out and "### Data's Observation" in out
    assert out.index("### Data's Observation") < out.index("## Update 09:30")
    assert _lines(out) == _lines(page)


def test_a_second_run_is_kept_one_level_deeper_under_one_heading():
    page = ("## Daily Summary\n\n- s1\n\n## Daily Briefing\n\n### Good Morning\n\nbox\n\n"
            "## Good Morning\n\nmac\n\n### Vitals\n\nv\n\n## Horizon\n\nh\n\n## Session: x\n\nkeep\n")
    out = J.nest(page)
    assert _one_briefing_on_top(out), out
    assert f"### {J.SECOND_RUN}" in out and "#### Good Morning" in out and "##### Vitals" in out
    assert out.index("## Daily Summary") > out.index("## Daily Briefing")
    assert out.index("## Daily Summary") < out.index("## Session: x")
    assert [l for l in _lines(out) if l != J.SECOND_RUN] == _lines(page)


def test_a_good_page_and_a_page_without_a_briefing_are_left_alone():
    good = "## Daily Briefing\n\n### Good Morning\n\nx\n\n## Session: y\n\nz\n"
    assert J.nest(good) == good
    none = "## Session: y\n\n## Good Morning\n\nz\n"
    assert J.nest(none) == none


def test_a_second_page_folds_into_the_days_page(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "state"))
    extra = tmp_path / "journal" / "2026-09-19.md"
    extra.parent.mkdir()
    extra.write_text("---\ndate: 2026-09-19\n---\n\n# 2026-09-19\n\n## End of Day\n\nreview\n")
    canon = tmp_path / "notes" / "2026-09-19.md"
    canon.parent.mkdir()
    canon.write_text("## Daily Briefing\n\n### Good Morning\n\nx\n")
    assert J.main(["fold", str(extra), str(canon), "--apply"]) == 0
    assert not extra.exists()
    text = canon.read_text()
    assert text.startswith("## Daily Briefing") and text.rstrip().endswith("review")
    assert "# 2026-09-19" not in text and text.count("---") == 0


def test_fold_refuses_two_different_days(tmp_path):
    a, b = tmp_path / "2026-09-19.md", tmp_path / "2026-09-20.md"
    a.write_text("x\n"); b.write_text("y\n")
    assert J.main(["fold", str(a), str(b), "--apply"]) == 2 and a.exists()
