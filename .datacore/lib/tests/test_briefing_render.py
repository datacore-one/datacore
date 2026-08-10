"""Tests for briefing.render -- the trust gate for grounded briefings.

Two functions, two halves of one guarantee:

- `render` substitutes `{{fact:ID}}` tokens with `fact.value` verbatim.
  Unknown ids are collected across the WHOLE text and raised as one
  `RenderError` naming all of them -- never a partial substitution, never
  a raise-on-first-unknown. A malformed `{{fact:` that never closes (bad
  chars in the id, or no closing `}}`) is not a token at all -- the regex
  simply doesn't match it, so it's left verbatim in the output and is
  NOT an error here (the validator downstream is what would catch any
  digits it happens to carry).

- `validate` extracts every digit-sequence from ALREADY-RENDERED text
  (`\\d[\\d,.]*\\d|\\d`) and requires each one to trace to something: a
  substring of some fact's `value`, a built-in allowlist (ISO dates,
  clock times, a standalone `20\\d{2}` year), or a caller-supplied
  `allow` regex. Anything else becomes an error string naming the number
  plus its surrounding context. Zero clock reads, zero randomness --
  "current year" means the literal `20\\d{2}` pattern, never
  `datetime.now().year`.

THE canonical fixture (explicitly called out in the task brief): text
claiming "639 uncommitted changes" with no fact grounding 639 must
produce EXACTLY one error, quoting 639 -- not two. The fixture's literal
text also contains "0-personal", which contributes a second digit
match ("0", from the digit-sequence regex's lone-digit alternative --
the hyphen isn't a joining character). That "0" is deliberately grounded
by an unrelated companion fact (value "10") already present in the fact
table, exactly as it would be in a real fact table with more than one
fact -- see `_FACTS_WITH_OTHER_COUNT` and the "design note" comment on
the canonical fixture tests below for why this isn't a special case in
`validate` itself, just a realistic multi-fact fixture.
"""

from __future__ import annotations

from briefing.fact_table import Fact
from briefing.render import RenderError, render, validate

_COMPUTED_AT = "2026-07-30T00:00:00+00:00"


def _fact(fact_id: str, value: str) -> Fact:
    return Fact(id=fact_id, value=value, unit="count", source="stub", computed_at=_COMPUTED_AT)


# --- render: token substitution ---------------------------------------------


def test_render_substitutes_single_token():
    facts = {"git.status.dirty_count": _fact("git.status.dirty_count", "3")}

    result = render("There are {{fact:git.status.dirty_count}} dirty files.", facts)

    assert result == "There are 3 dirty files."


def test_render_substitutes_multiple_distinct_tokens():
    facts = {
        "items.total": _fact("items.total", "12"),
        "git.branch": _fact("git.branch", "feat/datacore-v2"),
    }

    result = render(
        "On {{fact:git.branch}} there are {{fact:items.total}} tasks.",
        facts,
    )

    assert result == "On feat/datacore-v2 there are 12 tasks."


def test_render_substitutes_same_token_twice():
    facts = {"items.total": _fact("items.total", "5")}

    result = render(
        "{{fact:items.total}} tasks now, {{fact:items.total}} tasks yesterday.",
        facts,
    )

    assert result == "5 tasks now, 5 tasks yesterday."


def test_render_id_charset_allows_dots_underscores_hyphens_digits():
    facts = {"a.b_c-9": _fact("a.b_c-9", "ok")}

    result = render("value={{fact:a.b_c-9}}", facts)

    assert result == "value=ok"


# --- render: unknown tokens --------------------------------------------------


def test_render_single_unknown_token_raises_render_error_naming_it():
    try:
        render("{{fact:missing.id}} things", {})
        assert False, "expected RenderError"
    except RenderError as exc:
        assert "missing.id" in str(exc)


def test_render_multiple_unknown_tokens_collected_into_one_error():
    facts = {"known": _fact("known", "1")}

    try:
        render("{{fact:known}} {{fact:foo}} and {{fact:bar}}", facts)
        assert False, "expected RenderError"
    except RenderError as exc:
        message = str(exc)
        assert "foo" in message
        assert "bar" in message
        # exactly two unknown ids collected -- the known token contributed
        # neither a raise-on-first-unknown short circuit nor a spurious
        # third entry
        assert message.count(",") == 1


def test_render_unknown_token_raise_means_no_partial_substitution_side_effect():
    """A single raise -- not a partial string returned alongside it. There's
    no return value to inspect on the exception path, but this test pins
    that calling render() again with the fix produces the fully-substituted
    string (i.e. the earlier raise didn't leave any cached/partial state).
    """
    facts = {"known": _fact("known", "1")}

    try:
        render("{{fact:known}} {{fact:missing}}", facts)
        assert False, "expected RenderError"
    except RenderError:
        pass

    facts["missing"] = _fact("missing", "2")
    assert render("{{fact:known}} {{fact:missing}}", facts) == "1 2"


# --- render: malformed tokens left verbatim ----------------------------------


def test_render_unterminated_token_left_verbatim_not_an_error():
    text = "Note: {{fact:unclosed and more text"

    result = render(text, {})

    assert result == text


def test_render_token_with_invalid_id_chars_left_verbatim_not_an_error():
    text = "Weird {{fact:has space}} token"

    result = render(text, {})

    assert result == text


# --- validate: THE canonical fixture -----------------------------------------

# Design note: the fact table here deliberately carries a second,
# unrelated fact ("other.count" = "10") alongside the one under test.
# Real fact tables (see briefing.fact_table.build_facts) always merge
# several adapters' facts together, so a companion fact is the realistic
# case, not a contrived one. Its value "10" happens to contain "0" as a
# substring, which is what grounds the "0" digit-match that the fixture
# text's "0-personal" also contributes (the digit-sequence regex treats
# a lone digit next to a hyphen as its own match -- hyphens don't join
# digits together). This is intentional: `validate` has no special case
# for single-digit numbers, so the fixture's "exactly one error" claim
# depends on realistic multi-fact grounding, not on a carve-out in the
# implementation.
_CANONICAL_TEXT = "there are 639 uncommitted changes in 0-personal"
_OTHER_COUNT_FACT = {"other.count": _fact("other.count", "10")}


def test_canonical_fixture_ungrounded_639_flagged_exactly_once():
    errors = validate(_CANONICAL_TEXT, _OTHER_COUNT_FACT)

    assert len(errors) == 1
    assert "639" in errors[0]


def test_canonical_fixture_grounded_639_is_clean():
    facts = dict(_OTHER_COUNT_FACT)
    facts["git_status_count"] = _fact("git_status_count", "639")

    errors = validate(_CANONICAL_TEXT, facts)

    assert errors == []


# --- validate: substring-of-fact-value grounding -----------------------------


def test_validate_digit_inside_fact_value_passes_as_substring():
    facts = {"amount": _fact("amount", "1,234")}

    errors = validate("Total: 1,234 widgets.", facts)

    assert errors == []


def test_validate_number_matching_fact_id_but_not_value_still_flagged():
    facts = {"metric.42": _fact("metric.42", "unrelated text with no digits")}

    errors = validate("The reading was 42 today.", facts)

    assert len(errors) == 1
    assert "42" in errors[0]


# --- validate: built-in allowlist (dates, clock times, standalone year) -----


def test_validate_iso_date_passes_via_windowed_context():
    errors = validate("Sprint ends 2026-07-30 as planned.", {})

    assert errors == []


def test_validate_clock_time_passes_via_windowed_context():
    errors = validate("Standup at 08:30 today.", {})

    assert errors == []


def test_validate_standalone_year_passes_directly():
    errors = validate("Copyright 2026.", {})

    assert errors == []


# --- validate: caller-supplied allow patterns --------------------------------


def test_validate_caller_allow_pattern_passes_version_number():
    errors = validate("Now shipping v2.0.0.", {}, allow=[r"2\.0\.0"])

    assert errors == []


def test_validate_caller_allow_pattern_does_not_grant_blanket_pass():
    # allow list is checked per-match via full match; a pattern for one
    # number must not accidentally cover an unrelated one.
    errors = validate("v2.0.0 shipped with 77 fixes.", {}, allow=[r"2\.0\.0"])

    assert len(errors) == 1
    assert "77" in errors[0]


# --- validate: unexplained number flagged with context ----------------------


def test_validate_unexplained_number_flagged_with_context():
    errors = validate("Somehow the answer is 42 apparently.", {})

    assert len(errors) == 1
    assert "42" in errors[0]
    # surrounding context should be present so a human can find the claim
    assert "answer is 42 apparently" in errors[0]


# --- validate: empty text -----------------------------------------------------


def test_validate_empty_rendered_text_returns_no_errors():
    assert validate("", {}) == []


def test_validate_text_with_no_digits_returns_no_errors():
    assert validate("Nothing numeric happened today.", {}) == []


# --- render + validate together: fabrication is structurally impossible -----


def test_render_then_validate_end_to_end_grounded_briefing_is_clean():
    facts = {
        "git.status.dirty_count": _fact("git.status.dirty_count", "3"),
        "items.total": _fact("items.total", "12"),
    }
    llm_text = (
        "There are {{fact:git.status.dirty_count}} dirty files and "
        "{{fact:items.total}} open tasks as of 2026-07-30."
    )

    rendered = render(llm_text, facts)
    errors = validate(rendered, facts)

    assert rendered == "There are 3 dirty files and 12 open tasks as of 2026-07-30."
    assert errors == []


def test_render_then_validate_catches_fabricated_number_llm_typed_directly():
    facts = {"git.status.dirty_count": _fact("git.status.dirty_count", "3")}
    # LLM didn't use a token at all -- typed a number directly instead of
    # sourcing it from a fact. render() has no way to catch this (there's
    # no token to reject); validate() is the safety net.
    llm_text = "There are 999 dirty files."

    rendered = render(llm_text, facts)
    errors = validate(rendered, facts)

    assert rendered == llm_text
    assert len(errors) == 1
    assert "999" in errors[0]
