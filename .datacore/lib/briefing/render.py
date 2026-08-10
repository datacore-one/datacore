"""Render + validate: the trust gate for grounded briefings (Datacore v2
Phase 4). This is the module that makes fabricated numbers structurally
impossible -- the LLM emits `{{fact:ID}}` tokens, `render` injects the
`Fact.value` those ids resolve to, and `validate` re-scans the rendered
text afterward to make sure every remaining digit sequence still traces
to something real.

Two halves, two different failure modes:

- `render` is about tokens the LLM *chose* to use. A token naming an id
  that isn't in the fact table is a hard error (`RenderError`) -- ALL
  unknown ids across the whole text are collected into ONE raise, never
  a raise-on-first-unknown and never a partially-substituted string
  returned alongside a warning. A `{{fact:` that never resolves into a
  valid token (bad characters in the id, or no closing `}}`) is simply
  not recognized as a token at all -- the token regex doesn't match it,
  so it passes through untouched. That is NOT an error here: `validate`
  is what would catch any digits such leftover text happens to carry.

- `validate` is about numbers the LLM *typed directly*, bypassing tokens
  entirely (the thing tokens can't stop, because there's no token to
  reject). It extracts every digit sequence from already-rendered text
  and requires each one to be traceable to a `Fact.value` (substring
  match), a built-in allowlist (ISO dates, clock times, a standalone
  `20\\d{2}` year), or a caller-supplied `allow` regex. Anything else is
  reported as an error string naming the number and its context.

Both functions are pure and deterministic: no clock reads, no
randomness, no I/O. "Current year" means the literal `20\\d{2}` pattern,
never `datetime.now().year` -- a real clock read would make `validate`'s
output depend on when it runs, which is exactly the kind of
non-reproducibility this module exists to eliminate everywhere else.
"""

from __future__ import annotations

import re

from briefing.fact_table import Fact

# `{{fact:ID}}` where ID is one or more of [A-Za-z0-9_.-]. Anything that
# doesn't fit this shape (invalid characters in the id, or a missing
# closing `}}`) simply isn't matched -- render() never has to special-case
# "malformed" input, the regex not matching IS the "leave it verbatim"
# behavior.
_TOKEN_RE = re.compile(r"\{\{fact:([A-Za-z0-9_.\-]+)\}\}")

# Every digit-sequence a briefing can contain, per the task spec: either a
# comma/period-joined run of 2+ digits (so "1,234" and "3.14" are each one
# match) or a single lone digit. Note this means "2026-07-30" is NOT one
# match -- the hyphens aren't in the joining class, so it comes out as
# three separate matches ("2026", "07", "30"), which is exactly why the
# ISO-date/clock-time allowances below have to work by inspecting a window
# of surrounding text rather than by matching the extracted text directly.
_DIGIT_SEQUENCE_RE = re.compile(r"\d[\d,.]*\d|\d")

# Built-in allowlist patterns. ISO date and clock time are only ever
# checked against a window of surrounding text (see
# `_grounded_by_date_or_clock_window`), never against the bare extracted
# digit-sequence, because the extraction regex above never captures the
# separators (`-`, `:`) these patterns require. The year pattern is the
# one exception: a standalone `20\d{2}` year IS exactly what the
# extraction regex captures on its own (four contiguous digits, no
# separators), so it's checked directly against the match text.
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CLOCK_TIME_RE = re.compile(r"\d{1,2}:\d{2}")
_STANDALONE_YEAR_RE = re.compile(r"20\d{2}")

# How far to look on either side of a digit-sequence match when checking
# whether it's really a fragment of a date/clock-time written just outside
# the match's own span (e.g. the "07" and "30" fragments of "2026-07-30").
# 11 is generous enough to always contain the rest of either pattern
# (`\d{4}-\d{2}-\d{2}` is 10 chars total, `\d{1,2}:\d{2}` is at most 5)
# regardless of which fragment triggered the check.
_CONTEXT_WINDOW = 11

# How much surrounding text to quote in an error string, on each side of
# the offending number, so a human reviewing the error can find the claim
# in the rendered briefing without re-running validate() themselves.
_ERROR_CONTEXT = 20


class RenderError(ValueError):
    """Raised by `render` when `llm_text` references one or more fact ids
    that aren't present in `facts`. Every unknown id found anywhere in the
    text is collected first; the raise happens once, naming all of them,
    never on the first one found.
    """


def render(llm_text: str, facts: dict[str, Fact]) -> str:
    """Substitute every `{{fact:ID}}` token in `llm_text` with the matching
    `Fact.value`, verbatim (no reformatting, no re-typing).

    If ANY token's id isn't a key in `facts`, nothing is substituted at
    all -- all unknown ids (deduplicated, in first-seen order) are
    collected across the whole text and raised together as one
    `RenderError`, rather than substituting the known tokens and silently
    dropping or partially failing on the rest.

    Text that merely *looks* like a token but doesn't match the strict
    `{{fact:[A-Za-z0-9_.-]+}}` shape (invalid id characters, or no closing
    `}}`) is left exactly as-is in the output. That is not this function's
    problem to flag -- it's ordinary text as far as render() is concerned,
    and `validate` is what will catch any digits it happens to carry.
    """
    unknown_ids: list[str] = []
    seen: set[str] = set()
    for match in _TOKEN_RE.finditer(llm_text):
        fact_id = match.group(1)
        if fact_id not in facts and fact_id not in seen:
            seen.add(fact_id)
            unknown_ids.append(fact_id)

    if unknown_ids:
        raise RenderError(f"unknown fact id(s): {', '.join(unknown_ids)}")

    return _TOKEN_RE.sub(lambda m: facts[m.group(1)].value, llm_text)


def _grounded_by_fact_value(text: str, facts: dict[str, Fact]) -> bool:
    return any(text in fact.value for fact in facts.values())


def _grounded_by_standalone_year(text: str) -> bool:
    return _STANDALONE_YEAR_RE.fullmatch(text) is not None


def _grounded_by_date_or_clock_window(rendered: str, start: int, end: int) -> bool:
    """True if the digit-sequence match at `rendered[start:end]` is a
    fragment of a full ISO date or clock time written in the surrounding
    text. Extraction never captures the separators these patterns need
    (see `_DIGIT_SEQUENCE_RE`'s docstring), so this can't be a direct match
    against the extracted text -- instead it expands the match by
    `_CONTEXT_WINDOW` chars on each side, searches THAT window for a full
    date/clock pattern, and checks that the found pattern's span fully
    contains the original match's span (i.e. the match is genuinely part
    of that date/time, not just near one).
    """
    window_start = max(0, start - _CONTEXT_WINDOW)
    window_end = min(len(rendered), end + _CONTEXT_WINDOW)
    window = rendered[window_start:window_end]

    for pattern in (_ISO_DATE_RE, _CLOCK_TIME_RE):
        for found in pattern.finditer(window):
            found_start = window_start + found.start()
            found_end = window_start + found.end()
            if found_start <= start and end <= found_end:
                return True
    return False


def _grounded_by_allow(text: str, allow_patterns: list[re.Pattern]) -> bool:
    return any(pattern.fullmatch(text) for pattern in allow_patterns)


def validate(rendered: str, facts: dict[str, Fact], allow: list[str] | None = None) -> list[str]:
    """Return one error string per digit-sequence in `rendered` that
    can't be traced to anything real. Empty list means every number in
    the text is grounded.

    Every match of `\\d[\\d,.]*\\d|\\d` must satisfy at least one of:

    1. It's a substring of some `Fact.value` in `facts` (NOT a fact id --
       a number that happens to appear in an id but not any value is
       still flagged).
    2. It full-matches the standalone-year allowlist (`20\\d{2}`), checked
       directly against the match text.
    3. It's a fragment of a full ISO date (`\\d{4}-\\d{2}-\\d{2}`) or clock
       time (`\\d{1,2}:\\d{2}`) written in the surrounding text, checked via
       a windowed search (see `_grounded_by_date_or_clock_window`).
    4. It full-matches one of the caller-supplied `allow` regex strings.

    Anything else becomes an error string naming the offending number
    plus `_ERROR_CONTEXT` characters of surrounding text on each side, so
    a human can locate the unverified claim.
    """
    allow_patterns = [re.compile(pattern) for pattern in (allow or [])]
    errors: list[str] = []

    for match in _DIGIT_SEQUENCE_RE.finditer(rendered):
        text = match.group()
        start, end = match.span()

        if _grounded_by_fact_value(text, facts):
            continue
        if _grounded_by_standalone_year(text):
            continue
        if _grounded_by_date_or_clock_window(rendered, start, end):
            continue
        if _grounded_by_allow(text, allow_patterns):
            continue

        context_start = max(0, start - _ERROR_CONTEXT)
        context_end = min(len(rendered), end + _ERROR_CONTEXT)
        context = rendered[context_start:context_end]
        errors.append(f"ungrounded number {text!r} (context: {context!r})")

    return errors
