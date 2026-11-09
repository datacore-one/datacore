"""Script-built Telegram text follows the formatting skill and is never cut mid-word."""
from tg_format import clip, html_safe, normalize


def test_clip_cuts_at_a_word_boundary():
    s = "Review the enterprise sprint close for plur-ai/enterprise development"
    got = clip(s, 40)
    assert len(got) <= 40 and got.endswith("…")
    assert got[:-1] in s and s[len(got) - 1] == " ", "the cut falls between words"
    assert clip("short", 40) == "short"


def test_clip_joins_lines_it_is_given():
    assert "\n" not in clip("an error\nwith a traceback line", 100)


def test_normalize_applies_the_skill_bullets_and_drops_markdown():
    got = normalize("## Status\n\n\n\n- **done** item\n* other\n  - nested\n```\n- code stays\n```")
    assert got.splitlines() == ["Status", "", "• done item", "• other", "  • nested",
                                "```", "- code stays", "```"]


def test_html_safe_escapes_text_but_keeps_formatting_tags():
    assert html_safe("<b>a < b & c</b>") == "<b>a &lt; b &amp; c</b>"
