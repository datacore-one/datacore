#!/usr/bin/env python3
"""briefing_grounded.py — the grounded-briefing pipeline entry point
(Datacore v2 Phase 4). This is where `briefing.fact_table` (deterministic
facts) and `briefing.render` (the render/validate trust gate) meet the LLM
boundary: `prompt_block` is what the LLM is shown, `finalize` is what a
briefing consumer receives back, and the one property both exist to
guarantee is that NO unvalidated LLM-typed number ever ships.

The motivating incident (`ENG-2026-0728-002`, 2026-07-28): four precise,
individually plausible-sounding claims in a briefing were all wrong, and
three of the four prescribed remedies would have caused active damage
(deleting 639 files that were actually deletions being reported as
uncommitted changes worth committing; pruning 84 "stale" cadences that
were really 28 cadences with no executor scheduled at all; an inverted
awaiting-reply status; a meeting that existed on no calendar). Precision is
exactly what made each wrong claim credible — nothing about the numbers
themselves signaled unreliability. This module exists to make that class
of failure structurally impossible for anything routed through it: an LLM
producing prose for a grounded briefing can ONLY reference numbers via
`{{fact:ID}}` tokens resolved from `briefing.fact_table.Fact` values it did
not choose or compute, and anything it types directly instead is caught by
`briefing.render.validate` before it ever reaches a reader.

Two functions:

- `prompt_block(facts)` -- serializes a fact table into the block shown to
  the LLM: an instruction header mandating token-only use for every
  figure, then one line per fact (sorted by id, for reproducible output)
  in the shape `{{fact:ID}} = <value> <unit> (<source>)` -- literally the
  token syntax `briefing.render.render` substitutes, so the LLM sees
  exactly what it must emit.
- `finalize(llm_text, facts, allow=None)` -- the trust gate itself:
  `render()` then `validate()`. Two distinct failure paths both produce
  the SAME deterministic fallback (never the LLM's own text, in whole or
  in part):
    1. `RenderError` -- the LLM referenced a fact id that doesn't exist.
    2. Nonempty `validate()` errors -- the LLM typed a number directly,
       bypassing tokens entirely (the case `render()` structurally cannot
       catch, because there's no token to reject).
  Only a fully clean round-trip (no unknown tokens, no ungrounded digits)
  returns the actual rendered text alongside an empty errors list.

The fallback text is a plain (non-tokenized) listing of every known fact
-- "plain" specifically to distinguish it from `prompt_block`'s
token-syntax lines: this text is the end of the line, shown to a human,
not a prompt handed back to another LLM turn, so there is no reason for
`{{fact:ID}}` token syntax to appear in it. It carries the same
information content as a successful render would have used, just without
LLM-authored prose wrapped around it -- a briefing consumer still sees
every real number that exists, only without the (unverifiable) sentences
the LLM would have put around them.

CLI:
    python3 briefing_grounded.py --root <dir> --demo

`--demo` runs `briefing.fact_table.build_facts(root)` (the real default
adapters -- `git_status_counts`, `ledger_item_counts` -- both read-only,
safe against any directory) and prints the resulting `prompt_block`
followed by a demo `finalize()` round-trip: an LLM-text stand-in built
entirely from tokens for every fact in the table, so the round-trip's
success path is what's demonstrated end-to-end against real data. Output
is stdout-only; the command always exits 0 (this is a read-only
demonstration, not an assertion -- there is nothing here that should ever
fail a CI gate).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from briefing.fact_table import Fact, build_facts  # noqa: E402
from briefing.render import RenderError, render, validate  # noqa: E402

# Header shown once, above the fact-line listing, in `prompt_block`'s
# output. Deliberately explicit and repetitive ("MUST", "never type a
# number directly") rather than a terse one-liner -- this is the single
# instruction standing between an LLM and the ENG-2026-0728-002 failure
# class, so it says the rule twice, in two different phrasings, rather
# than trusting one clause to land.
_PROMPT_HEADER = (
    "GROUNDED BRIEFING -- every figure, count, or date you state MUST be "
    "inserted as a {{fact:ID}} token from the table below. Never type a "
    "number directly: if a claim is not backed by one of these tokens, "
    "do not make the claim."
)

# Header shown once, above the plain fact listing, whenever `finalize`
# falls back (either failure path). Names what happened (validation
# failed) so a human reading it understands this is NOT the intended
# briefing prose, just the grounded-fact substrate that survived.
_FALLBACK_HEADER = "Briefing (grounded fallback — validation failed)"


def _token_line(fact_id: str, fact: Fact) -> str:
    """`{{fact:ID}} = <value> <unit> (<source>)` -- exactly the token
    syntax `briefing.render.render` substitutes, so a line here is
    literally copy-pasteable into an LLM-authored sentence.
    """
    return f"{{{{fact:{fact_id}}}}} = {fact.value} {fact.unit} ({fact.source})"


def _plain_line(fact_id: str, fact: Fact) -> str:
    """`ID = <value> <unit> (<source>)` -- the same information as
    `_token_line`, without the `{{fact:...}}` wrapper, for the fallback
    listing (which is final output, not a prompt).
    """
    return f"{fact_id} = {fact.value} {fact.unit} ({fact.source})"


def prompt_block(facts: dict[str, Fact]) -> str:
    """Serialize `facts` into the block shown to the LLM: the mandatory
    instruction header, then one `_token_line` per fact, sorted by id (so
    output is reproducible regardless of `facts`' dict insertion order --
    fact tables are built by merging multiple adapters' dicts together,
    per `briefing.fact_table.build_facts`, so insertion order is never
    meaningful here).
    """
    lines = [_PROMPT_HEADER]
    lines.extend(_token_line(fact_id, facts[fact_id]) for fact_id in sorted(facts))
    return "\n".join(lines)


def _fallback_text(facts: dict[str, Fact]) -> str:
    """The deterministic fallback `finalize` returns on EITHER failure
    path: `_FALLBACK_HEADER` plus one `_plain_line` per fact, sorted by
    id. Never includes anything the LLM wrote -- every word in this
    string is either the fixed header or derived directly from `facts`.
    """
    lines = [_FALLBACK_HEADER]
    lines.extend(_plain_line(fact_id, facts[fact_id]) for fact_id in sorted(facts))
    return "\n".join(lines)


def finalize(
    llm_text: str,
    facts: dict[str, Fact],
    allow: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Render then validate `llm_text` against `facts`. NEVER returns
    unvalidated LLM-typed numbers:

    - `render()` raising `RenderError` (an unknown fact id was
      referenced) -- caught here, returns `(_fallback_text(facts),
      [str(exc)])`.
    - `render()` succeeding but `validate()` finding nonempty errors (a
      number the LLM typed directly instead of sourcing from a token --
      the case no token exists to reject) -- returns
      `(_fallback_text(facts), errors)`.
    - Both clean -- returns `(rendered, [])`, the only path where the
      LLM's own text (with tokens substituted) is what's returned.

    `allow` is passed straight through to `validate()` (caller-supplied
    allowlist patterns for numbers that are legitimately not fact-backed,
    e.g. version strings) -- this function has no opinion on it beyond
    forwarding it.
    """
    try:
        rendered = render(llm_text, facts)
    except RenderError as exc:
        return _fallback_text(facts), [str(exc)]

    errors = validate(rendered, facts, allow=allow)
    if errors:
        return _fallback_text(facts), errors

    return rendered, []


def _demo_llm_text(facts: dict[str, Fact]) -> str:
    """Build a stand-in "LLM output" for `--demo`: one clause per fact,
    referencing it ONLY via its `{{fact:ID}}` token -- never a typed
    number -- so the demo round-trip exercises the real success path
    against whatever `facts` real adapters produced for `--root`, rather
    than a canned fixture disconnected from the actual demo data.
    """
    if not facts:
        return "No facts were available for this root."
    clauses = [f"{fact_id} is {{{{fact:{fact_id}}}}}" for fact_id in sorted(facts)]
    return "Grounded demo briefing -- " + "; ".join(clauses) + "."


def _run_demo(root: Path) -> None:
    facts = build_facts(root)

    print("=== PROMPT BLOCK ===")
    print(prompt_block(facts))
    print()

    print("=== DEMO FINALIZE ROUND-TRIP ===")
    llm_text = _demo_llm_text(facts)
    print("LLM text (stand-in, token-only):")
    print(llm_text)
    print()

    rendered, errors = finalize(llm_text, facts)
    print("Result:")
    print(rendered)
    print()
    print(f"Errors: {errors}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Grounded briefing pipeline entry point (prompt_block + finalize)."
    )
    parser.add_argument("--root", required=True, help="Directory to build facts from (read-only).")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Print the prompt block and a demo finalize() round-trip against --root's real facts.",
    )
    args = parser.parse_args(argv)

    if args.demo:
        _run_demo(Path(args.root))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
