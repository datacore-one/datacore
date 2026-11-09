"""Date-cluster defects found by the Lean model (DatacoreSpec/Dates.lean).

Each test replays a counterexample the model produced against the real code.
See specs/datacore-lean/findings/dates.md.
"""
from datetime import date, datetime

import date_utils
import org_date_validator
import validate_org_dates
from decay_and_review import month_keys

# All weekdays below were computed with datetime, never typed from memory:
# 2026-09-24 Thu, 2026-09-23 Wed, 2026-09-28 Mon.


# --- date_utils.fix_day_names / find_mismatches ------------------------------


def test_fix_day_names_leaves_prose_alone():
    text = "2026-09-24 Monitor the queue\n2026-09-24 Monday\n2026-09-24 Sunset\n"
    assert date_utils.fix_day_names(text) == (text, 0)


def test_find_mismatches_does_not_block_prose():
    # find_mismatches drives the PreToolUse hook that BLOCKS a write.
    assert date_utils.find_mismatches("2026-09-24 Monitor the queue") == []


def test_fix_day_names_still_fixes_stamps():
    fixed, n = date_utils.fix_day_names("<2026-09-24 Mon> and 2026-09-24 Mon.")
    assert (fixed, n) == ("<2026-09-24 Thu> and 2026-09-24 Thu.", 2)


def test_fix_day_names_never_joins_lines():
    text = "Due 2026-09-24\nSat with Bob\n"
    assert date_utils.fix_day_names(text) == (text, 0)


def test_fix_day_names_is_idempotent():
    text = "<2026-09-24 Mon>\t2026-09-24  Fri, 2026-09-24 Monitor 2026-13-40 Sun\n"
    once, _ = date_utils.fix_day_names(text)
    assert date_utils.fix_day_names(once) == (once, 0)


# --- date_utils.parse_relative ------------------------------------------------


def test_next_month_is_a_calendar_month_not_next_monday():
    assert date_utils.parse_relative("next month", date(2026, 9, 23)) == date(2026, 10, 23)
    assert date_utils.parse_relative("last month", date(2026, 9, 23)) == date(2026, 8, 23)


def test_month_clamps_to_month_end():
    assert date_utils.parse_relative("next month", date(2026, 1, 31)) == date(2026, 2, 28)
    assert date_utils.parse_relative("last month", date(2024, 3, 31)) == date(2024, 2, 29)
    assert date_utils.parse_relative("next month", date(2026, 12, 15)) == date(2027, 1, 15)
    assert date_utils.parse_relative("last month", date(2026, 1, 15)) == date(2025, 12, 15)


def test_weekday_words_still_parse():
    base = date(2026, 9, 23)
    assert date_utils.parse_relative("next monday", base) == date(2026, 9, 28)
    assert date_utils.parse_relative("next mon", base) == date(2026, 9, 28)
    assert date_utils.parse_relative("next tues", base) == date(2026, 9, 29)
    assert date_utils.parse_relative("last wednesday", base) == date(2026, 9, 16)
    assert date_utils.parse_relative("next week", base) == date(2026, 9, 30)


def test_non_day_words_are_rejected():
    import pytest
    for expr in ("next monthly", "next mongoose", "next sunset"):
        with pytest.raises(ValueError):
            date_utils.parse_relative(expr, date(2026, 9, 23))


# --- org_date_validator -------------------------------------------------------


def test_validator_fix_leaves_prose_alone(tmp_path):
    p = tmp_path / "x.org"
    text = "* TODO x\n  2026-09-24 Monitor it\n  SCHEDULED: <2026-09-24 Mon>\n"
    p.write_text(text)
    days, _ = org_date_validator.validate_file(p, fix=True)
    assert [d[2] for d in days] == ["Mon"] and len(days) == 1
    assert p.read_text() == text.replace("<2026-09-24 Mon>", "<2026-09-24 Thu>")


def test_validator_suspect_year_is_relative(tmp_path):
    p = tmp_path / "x.org"
    p.write_text("* TODO a\n  SCHEDULED: <2026-01-05 Mon>\n* TODO b\n  DEADLINE: <2025-01-06 Mon>\n")
    _, sus = org_date_validator.validate_file(p, today=date(2027, 2, 1))
    assert [s[1] for s in sus] == ["2026-01-05"]
    _, sus = org_date_validator.validate_file(p, today=date(2026, 2, 1))
    assert [s[1] for s in sus] == ["2025-01-06"]


# --- validate_org_dates (pre-commit gate) -------------------------------------


def test_frontmatter_check_ignores_the_body():
    body_only = "# Notes\n\ndate: 2026-09-24\n\nlater\n\nday: Mon\n"
    assert validate_org_dates.check_text(body_only, "n.md") == ([], body_only)
    body_day = "---\ndate: 2026-09-24\n---\n\n```yaml\nday: Mon\n```\n"
    assert validate_org_dates.check_text(body_day, "n.md") == ([], body_day)


def test_frontmatter_check_still_fixes_frontmatter():
    text = "---\ndate: 2026-09-24\nday: Mon\n---\n\nbody\n"
    problems, fixed = validate_org_dates.check_text(text, "n.md")
    assert len(problems) == 1
    assert fixed == "---\ndate: 2026-09-24\nday: Thu\n---\n\nbody\n"


# --- decay_and_review.month_keys ----------------------------------------------


def test_month_keys_on_the_31st_is_three_months():
    assert month_keys(3, now=datetime(2026, 3, 31)) == ["2026-01", "2026-02", "2026-03"]
    assert month_keys(3, now=datetime(2026, 1, 31)) == ["2025-11", "2025-12", "2026-01"]


def test_month_keys_always_n_distinct():
    for m in range(1, 13):
        for d in (1, 28, 31 if m in (1, 3, 5, 7, 8, 10, 12) else 30 if m != 2 else 28):
            keys = month_keys(4, now=datetime(2026, m, d))
            assert len(keys) == 4 == len(set(keys))
