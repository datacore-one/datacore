"""Tests for briefing_grounded -- the grounded-briefing pipeline entry point
(Datacore v2 Phase 4) that ties `briefing.fact_table` (facts) and
`briefing.render` (render/validate) into the two functions an LLM-facing
grounded pipeline needs:

- `prompt_block(facts)` -- what the LLM sees: an instruction header telling
  it every figure MUST be a `{{fact:ID}}` token (never a typed number),
  followed by one line per fact, sorted by id, in the shape
  `{{fact:ID}} = <value> <unit> (<source>)`.
- `finalize(llm_text, facts, allow=None)` -- what a briefing consumer gets
  back. Renders then validates; on EITHER a `RenderError` (an unknown fact
  id referenced) OR nonempty `validate()` errors (a number the LLM typed
  directly, bypassing tokens), it returns a deterministic fallback text
  (a header line plus the same sorted fact listing, in PLAIN -- not
  tokenized -- form) alongside the errors, never the LLM's own text. Only
  a fully clean render+validate round-trip returns the actual rendered
  text with an empty errors list.

The two failure paths are deliberately covered by separate tests because
they exercise different underlying exceptions/paths in `briefing.render`:
`RenderError` (unknown token) vs. a nonempty `validate()` errors list
(fabricated number typed directly, no token involved at all -- the
canonical ENG-2026-0728-002 "639 uncommitted changes" shape).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from briefing.fact_table import Fact
from briefing_grounded import finalize, prompt_block

_COMPUTED_AT = "2026-07-30T00:00:00+00:00"
_MODULE_PATH = Path(__file__).resolve().parent.parent / "briefing_grounded.py"


def _fact(fact_id: str, value: str, unit: str = "count", source: str = "stub") -> Fact:
    return Fact(id=fact_id, value=value, unit=unit, source=source, computed_at=_COMPUTED_AT)


# --- prompt_block: header ------------------------------------------------


def test_prompt_block_on_empty_facts_is_header_only():
    block = prompt_block({})

    lines = block.splitlines()
    assert len(lines) == 1


def test_prompt_block_header_mandates_token_use_never_typed_numbers():
    header = prompt_block({}).splitlines()[0]

    assert "{{fact:" in header
    assert "MUST" in header
    assert "never" in header.lower()


# --- prompt_block: one line per fact, sorted by id ------------------------


def test_prompt_block_one_line_per_fact_sorted_by_id():
    facts = {
        "z.count": _fact("z.count", "9"),
        "a.count": _fact("a.count", "1"),
        "m.branch": _fact("m.branch", "main", unit="name"),
    }

    lines = prompt_block(facts).splitlines()

    assert len(lines) == 4  # header + 3 facts
    assert lines[1] == "{{fact:a.count}} = 1 count (stub)"
    assert lines[2] == "{{fact:m.branch}} = main name (stub)"
    assert lines[3] == "{{fact:z.count}} = 9 count (stub)"


def test_prompt_block_line_format_is_token_equals_value_unit_paren_source():
    facts = {
        "git.branch": _fact("git.branch", "feat/datacore-v2", unit="name", source="git_status_counts"),
    }

    block = prompt_block(facts)

    assert "{{fact:git.branch}} = feat/datacore-v2 name (git_status_counts)" in block


def test_prompt_block_sort_order_independent_of_dict_insertion_order():
    facts_a = {"b.id": _fact("b.id", "2"), "a.id": _fact("a.id", "1")}
    facts_b = {"a.id": _fact("a.id", "1"), "b.id": _fact("b.id", "2")}

    assert prompt_block(facts_a) == prompt_block(facts_b)


# --- finalize: success path (fully token-grounded, no fabrication) --------


def test_finalize_success_renders_and_returns_no_errors():
    facts = {"items.total": _fact("items.total", "12")}
    llm_text = "There are {{fact:items.total}} open tasks as of 2026-07-30."

    rendered, errors = finalize(llm_text, facts)

    assert rendered == "There are 12 open tasks as of 2026-07-30."
    assert errors == []


def test_finalize_success_with_no_tokens_and_no_digits_at_all():
    rendered, errors = finalize("Nothing numeric happened today.", {})

    assert rendered == "Nothing numeric happened today."
    assert errors == []


def test_finalize_allow_param_is_passed_through_to_validate():
    llm_text = "Now shipping v2.0.0."

    rendered, errors = finalize(llm_text, {}, allow=[r"2\.0\.0"])

    assert rendered == llm_text
    assert errors == []


# --- finalize: fabricated number (validate() catches it) ------------------


def test_finalize_fabricated_number_returns_fallback_not_llm_text():
    """THE canonical ENG-2026-0728-002 shape: an LLM typing a precise,
    plausible-sounding number directly instead of sourcing it from a
    fact. render() has no token to reject here; validate() is the net.
    """
    facts = {"git.status.dirty_count": _fact("git.status.dirty_count", "3")}
    llm_text = "There are 639 uncommitted changes, worth a manual commit."

    rendered, errors = finalize(llm_text, facts)

    assert len(errors) == 1
    assert "639" in errors[0]
    # the fabricated number must be structurally absent from shipped text
    assert "639" not in rendered
    # the real fact must still be visible -- fallback is a listing of what
    # IS known, not a bare error message
    assert "3" in rendered
    assert "git.status.dirty_count" in rendered


def test_finalize_fallback_text_starts_with_fallback_header():
    llm_text = "There are 999 widgets."

    rendered, _errors = finalize(llm_text, {"a.count": _fact("a.count", "5")})

    assert rendered.splitlines()[0] == "Briefing (grounded fallback — validation failed)"


def test_finalize_fallback_listing_is_plain_not_tokenized():
    llm_text = "There are 999 widgets."

    rendered, _errors = finalize(llm_text, {"a.count": _fact("a.count", "5")})

    assert "{{fact:" not in rendered
    assert "a.count = 5 count (stub)" in rendered


# --- finalize: unknown token (RenderError) --------------------------------


def test_finalize_unknown_token_returns_fallback_and_errors():
    facts = {"known.id": _fact("known.id", "1")}
    llm_text = "{{fact:known.id}} things, also {{fact:missing.id}}."

    rendered, errors = finalize(llm_text, facts)

    assert len(errors) == 1
    assert "missing.id" in errors[0]
    assert rendered.splitlines()[0] == "Briefing (grounded fallback — validation failed)"
    assert "known.id = 1 count (stub)" in rendered


def test_finalize_unknown_token_fallback_on_empty_facts_is_header_only():
    rendered, errors = finalize("{{fact:missing.id}}", {})

    assert errors  # nonempty
    assert rendered == "Briefing (grounded fallback — validation failed)"


# --- CLI smoke -------------------------------------------------------------


def test_cli_demo_smoke_exits_zero_and_prints_prompt_block(tmp_path):
    result = subprocess.run(
        [sys.executable, str(_MODULE_PATH), "--root", str(tmp_path), "--demo"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert "PROMPT BLOCK" in result.stdout
    assert "{{fact:" in result.stdout
    assert result.stderr == ""


def test_cli_demo_smoke_against_real_root_with_facts(tmp_path):
    """Same smoke test, but --root is a real git repo so build_facts()
    exercises the real git_status_counts adapter (not just the empty-dict
    path) -- still exit 0, stdout only.
    """
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/demo-branch"], cwd=tmp_path, check=True, capture_output=True
    )
    (tmp_path / "file.txt").write_text("hello")

    result = subprocess.run(
        [sys.executable, str(_MODULE_PATH), "--root", str(tmp_path), "--demo"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert "PROMPT BLOCK" in result.stdout
    assert "git.branch" in result.stdout
    assert "demo-branch" in result.stdout
    assert result.stderr == ""
