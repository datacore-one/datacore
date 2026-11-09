# Findings: research cluster (2026-09-23)

Model: `DatacoreSpec/Research.lean`, namespace `DatacoreSpec.Research`. It checks
cleanly (`lake env lean DatacoreSpec/Research.lean`) with no `sorry`, `admit`,
`native_decide` or custom axiom. `drain` uses only `propext` and `Quot.sound`.

Python: `.datacore/modules/research/lib/research_orchestrator.py` (changed) and
`research_router.py` (unchanged).
**Repo:** `modules/research` is not its own git repo. It is tracked in the root
`~/Data` repo (`git ls-files .datacore/modules/research` lists it, and it has no
`.git` or submodule entry). The nightshift copy at
`modules/nightshift/lib/research_orchestrator.py` is a forwarding shim, so it
picks up these fixes.

## Summary

| # | Candidate | Verdict | Lean |
|---|---|---|---|
| 3a | An analysis failure never counts, so items block the head of the queue | CONFIRMED+FIXED | `old_head_of_line_blocking`, `old_code_does_not_drain` (refutation of old code); `drain`, `run_lt`, `step_lt`, `pot_le_len` (proof for fixed code) |
| 3b | Items are located by heading TEXT, so duplicate titles misdirect `DONE`/`CLOSED`/`:OUTPUT:` | CONFIRMED+FIXED (worse than the survey said: a prefix title also misdirects, and `note_fetch_failure` can raise `ValueError`) | `old_duplicate_title_misdirects`; `ownerOffset_nearest`, `firstIdx_spec` |
| 3c | The `:END:` search runs past the next heading into the next item's drawer | CONFIRMED+FIXED | `old_end_search_crosses_heading`; `findEndSec_in_section` |
| 3d | Heading not found: silent, unchanged rewrite, and the item is reprocessed | CONFIRMED+FIXED (now reported, and nothing is written) | covered by pytest |
| 3e | Claim "the summary names what is parked" | CONFIRMED+FIXED: nothing named parked items, the journal said "(kept as TODO for retry)" about items it had just parked, and it wrote nothing when no item succeeded | covered by pytest |
| 3f | Claim "URL-less TODOs must not consume limit slots" | REFUTED (the claim holds) | `urlless_consume_no_slots`, `select_sub`, `select_nonempty` |
| 3g | Auto-archive (>60 days) only removes work | REFUTED (the claim holds) | `archive_le` |
| R | research_router bounds: axes 0–3, score ≤ 18, timeliness non-increasing in age, roadmap alone never promotes to an issue | REFUTED (all the claims hold) | `axes_le3`, `timeliness_le3`, `score_le18`, `timeliness_antitone`, `roadmap_necessary_not_sufficient` |

## Details

### 3a: drain and liveness

The model is a queue of items, each with an id, a priority, a has-URL flag, a
state (TODO, WAITING or DONE), fetch and analysis attempt counters, and a
created date. `select` is the three-bucket form of the stable sort by
priority, cut at `limit`. Fetch and analysis are oracles indexed by run and
item, so every theorem holds for any network behaviour and any model
behaviour.

- **Old code, refuted.** Take `limit = 1`, a [#A] item whose analysis always
  fails, and a [#C] item behind it. `runOnce` is the identity, so the [#C]
  item is never processed, whatever the number of runs
  (`old_head_of_line_blocking`, `old_code_does_not_drain`).
- **Fixed code, proved.** `drain`: for every oracle, with `limit > 0`, after
  `pot q` runs no URL-bearing TODO remains. Each one is DONE or parked as
  WAITING. `pot q ≤ 7·|q|` (`pot_le_len`), so the bound is set by the attempt
  counts.
- **Fix.** A new function, `note_failure(item, kind)`, and `note_fetch_failure`
  is kept as a wrapper around it. An analysis failure now increments
  `:ANALYSIS_ATTEMPTS:`. At 3, the item is parked as WAITING with a
  `:RESULT: analysis failed after 3 attempts …` line.
- **Mutation check.** I put the bug back in a scratch copy (`failAna` ignores
  the count), and `step_lt` stops proving, which breaks `run_lt` and `drain`.
- **Replay** (`scratchpad/research/replay.py`, the real `main()` with stubbed
  fetch and analysis). Before the fix, after 5 runs both items were still
  TODO (`b still TODO: True`). After the fix, [#A] is WAITING with
  `:ANALYSIS_ATTEMPTS: 3` and [#C] is DONE.

### 3b, 3c, 3d, 3e: locating an item in the org text

These are the old-code replays before the fix:

- (b) Two items share the heading `** TODO Read: same`. The first failed to
  fetch and the second succeeded. The FIRST item was marked DONE and the
  second stayed TODO.
- (b2) Items `Read: foo bar` (fetch failed) and `Read: foo` (succeeded).
  `str.replace` marked `foo bar` DONE, with no CLOSED line, and `foo` stayed
  TODO.
- (c) An item with no drawer: its `:OUTPUT:` and `:ZETTELS:` were written into
  the NEXT item's `:PROPERTIES:` drawer.
- (d) Heading not found: the file was rewritten byte-identical, with no log line.
- (e) `note_fetch_failure` when the heading occurs only as a substring of a
  longer line: `ValueError: … is not in list`, uncaught in `main()`.

The fix is `_locate_item`. It finds the item by `:ID:` when there is one (a
TODO owner is preferred over a closed duplicate). Without an ID, it uses the
exact heading line when that line is unique. Otherwise it uses the heading
ordinal recorded at parse time, and only if the heading at that ordinal still
matches exactly. If none of these works it returns `None`. The ordinal is safe
because this pipeline's own edits only insert body lines and rewrite headings
in place.

`_section_end` bounds every search at the next real heading, which means
`^\*+\s` in column 0. An indented `*bold*` line no longer ends an item, as it
did in the attempts search. `mark_done` now inserts into the item's own
`:PROPERTIES:` drawer, or creates one. It returns `False` and logs `WARNING …
not marked DONE` instead of rewriting silently. `parse_research_items` now also
returns `id` and `heading_index`.

For the summary, `main()` collects the parked items. The log and the journal
list them by title, and the journal is written when anything was parked, even
if nothing succeeded. "Kept as TODO for retry" now counts only the items that
are really still TODO.

The locating model is flat lines. `findEndSec_in_section` proves that the END
it finds has no heading before it in the section. For the mutation check, I
removed the heading stop in a scratch copy, and the witness
`findEndSec [head, props, endL] = some 2` then contradicts the theorem.
`ownerOffset_nearest` proves that the ID owner is the nearest heading above
the ID line. I did not model the ordinal fallback in Lean. The pytest covers
it.

### Router

Every stated bound holds. On the real code, `timeliness` compares
`age/hl` with 0.5, 1 and 2. For integer ages and a positive half-life this is
exactly `2a ≤ hl`, `a ≤ hl` and `a ≤ 2hl`. The Lean model uses natural-number
ages. A negative age scores 3 in Python, which is consistent with the
monotonicity result. There is no defect and no change. A pytest sweep
(`test_router_bounds_hold`) checks the same bounds on the real `classify`.

## Files changed

- `.datacore/modules/research/lib/research_orchestrator.py`: added
  `_is_heading`, `_section_end`, `_locate_item`, `note_failure`,
  `note_analysis_failure` and `MAX_ANALYSIS_ATTEMPTS`. Rewrote `mark_done`,
  which now returns a bool. `parse_research_items` records `id` and
  `heading_index`. `main()` counts analysis failures and reports parked items.
- New: `.datacore/modules/research/lib/tests/test_research_formal.py` (11 tests).
  Before the fix, 10 of them failed.
- New: `.datacore/specs/datacore-lean/DatacoreSpec/Research.lean`, and this file.

Test: `cd .datacore/modules/research/lib && python3 -m pytest -q tests/` gives
**32 passed**. The existing `test_queue_drain.py` passes unchanged.
`modules/nightshift/tests/test_command_data_boundary.py` gives 11 passed.

## NEEDS-OWNER

1. **Should a run in which every analysis fails count toward parking?** During
   a Claude or API outage lasting 3 nights, the top `limit` items would each
   be parked as WAITING ("analysis failed"), and a human would have to reset
   them to TODO. The coordinator asked for analysis failures to count, and
   liveness needs some bound. One alternative keeps the bound: skip the count
   when all analyses in a run failed and none succeeded. It would give up the
   unconditional `drain` guarantee during outages.
2. **Should the attempt counters live inside the `:PROPERTIES:` drawer?**
   `:FETCH_ATTEMPTS:`, `:ANALYSIS_ATTEMPTS:` and `:RESULT:` are written directly
   under the heading, outside the drawer. The code reads them back as text, so
   they work, but org-workspace `get_property` cannot see them. That placement
   is the existing on-disk format, and I left it unchanged.

## Owner decisions applied (2026-09-23)

**Decision D8 applied (always count):** no code change, as that is the current
behaviour. It is now documented in the `note_failure` docstring and in
`modules/research/CLAUDE.base.md` ("Failures park items"). A model or API outage that
lasts 3 nightly runs parks the top `--limit` items as "analysis failed after 3
attempts", and a human must set them back to TODO. That is the price of the
unconditional `drain` guarantee. The composed `CLAUDE.md` was rebuilt (it is ignored).

**Decision D9 applied (move with a migration):** `:FETCH_ATTEMPTS:`,
`:ANALYSIS_ATTEMPTS:` and `:RESULT:` are now written inside the item's `:PROPERTIES:`
drawer. A drawer is created right after the heading and its planning lines when the
item has none. Readers accept both places, and the drawer wins. `note_failure` moves an
item's old lines into the drawer whenever it touches that item. A re-park replaces
`:RESULT:` instead of stacking a second one. The pure line helpers (`get_item_prop`,
`set_item_prop`, `migrate_item`, `migrate_text`) live in the new
`modules/research/lib/migrate_research_props.py`, which is also the migration CLI: a dry
run by default, and `--apply` rewrites under `org_transaction.serialized` + `watch_file`,
refusing if the file changed since it was read. The orchestrator imports the helpers.
Lean (section 3b): `lk_append`, `migrate_lk`, `mig_preserves_read` (migration changes
no value a reader sees), `mig_idem`, `drawer_value_is_read`. Mutation check: letting the
old value overwrite the drawer's kills `migrate_lk`. Keeping the last old value instead
of the first kills `mig_preserves_read`.
**Dry run on a COPY of the real queue** (scratch `rest/research_learning.copy.org`, from
`0-personal/org/research_learning.org`, which was not written): 17 items touched, 31
properties moved, 0 drawers created, 0 duplicate old lines dropped. The diff is +31/−31
lines and the file stays at 5117 lines. Every moved line lands inside an existing
drawer. The copy was unchanged by the dry run. Applying to a second copy and migrating
again moves 0, so it is idempotent. org-workspace `get_property` finds
FETCH_ATTEMPTS/RESULT on **0** nodes before and **17** after. Before the move, the loose
lines sat between each heading and its drawer, which also hid those items' `:ID:` and
`:SOURCE:` from org readers.
Running `migrate_research_props.py <queue> --apply` on the real file is the owner's call.
Tests: new `lib/tests/test_decisions_research.py` (8 tests; it could not import before
the change, and all 8 exercise new behaviour). Changed:
`tests/test_research_formal.py::test_duplicate_titles_mark_the_item_that_was_processed`.
Its 3-line window below the heading now holds the new drawer, so it was widened to 6
lines. The assertion itself is unchanged.
