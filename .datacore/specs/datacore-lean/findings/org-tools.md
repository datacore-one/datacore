# Findings — OrgTools cluster (2026-09-23)

Model: `DatacoreSpec/OrgTools.lean` (namespace `DatacoreSpec.OrgTools`). It checks
cleanly with `lake env lean DatacoreSpec/OrgTools.lean`. There is no `sorry`,
`admit`, `axiom` or `native_decide`. `#print axioms` on every main theorem gives
only `propext`, `Quot.sound` and (for one theorem) `Classical.choice`.

Python pins: `lib/tests/test_org_tools_formal.py`. It has 615 cases, including
randomised property checks that mirror the theorems: 200 seeds for the union
merge, 300 for inbox_cleanup and 100 for dedup.
Survey leads: `survey/gtd-org.md` items 4, 6, 7, 8, 11, 12.

| # | Tool | Verdict |
|---|---|---|
| 4a | `org_union_merge` substring test drops a theirs block | CONFIRMED+FIXED |
| 4b | `org_union_merge` theirs-only child re-parented at EOF | CONFIRMED+FIXED |
| 6a | `inbox_cleanup` archives DEFERRED | CONFIRMED+FIXED |
| 6b | `inbox_cleanup` star lines inside `#+begin_` blocks | DOWNGRADED |
| 6c | `inbox_cleanup` not idempotent (new, found by the property test) | CONFIRMED+FIXED |
| 7 | `org_dedup_within_file` drops a copy with a different `:ID:` | CONFIRMED+NEEDS-OWNER (report-only fix applied) |
| 8a | `org_resolve_id_conflicts` keeps HEAD, which is local in a merge | CONFIRMED+FIXED |
| 8b | "nothing references the local id" | CONFIRMED+NEEDS-OWNER (docstring corrected) |
| 11 | `org_archive_closed` span selection | REFUTED (the reference property is proved) |
| 12a | `triage_utils._append_task_body` writes into the next task's drawer | CONFIRMED+FIXED |
| 12b | `_append_task_body` duplicate check covers the whole file | CONFIRMED+FIXED |
| 12c | `_CLOSED_STATES` omits DEFERRED | NEEDS-OWNER (not changed) |
| 12d | `dedup.deduplicate` under non-transitive similarity | CONFIRMED+FIXED |

## 4. org_union_merge.reconcile

**(a) Substring test.** For a theirs block with no `:ID:`, the code ran
`block not in a`, which is a substring test. `"** TODO call mom\n"` is a
substring of `"*** TODO call mom\n"`, so theirs' entry was dropped. This
breaks the docstring's promise to lose no item.

- Lean: `old_union_drops_substring_block` is the counterexample.
  `unidExtra_count` and `unid_union_loses_nothing` prove that the fixed
  multiset match keeps every block of either side at least as often as that
  side has it.
- Replay (old code): `reconcile("* Inbox\n*** TODO call mom\n", "* Inbox\n** TODO call mom\n")`
  returned `'* Inbox\n*** TODO call mom\n'`.
- Fix: unidentified blocks are now matched as a multiset on exact bytes. The
  one exception is trailing whitespace: `"* Inbox\n"` and `"* Inbox\n\n"` count
  as one section, so a section heading is not duplicated.

**(b) Re-parenting.** A theirs-only block was appended at EOF. That is wrong
for a child, which ended up under whatever heading came last.

- Lean: `old_union_reparents` is the counterexample: `c` ends up under `q`
  instead of `p`. `insertUnder_places_under_anchor` and `insertUnder_parent`
  prove that the placed block's parent is its theirs-parent.
  `insertUnder_keeps_later_parents` proves that no later block changes parent.
  `insertUnder_mem` proves that nothing is lost or invented.
- Replay (old code): ours `P,Q`, theirs `P,(child c),Q` gave `P,Q,c`.
- Fix: a theirs-only block goes at the end of the subtree of its theirs-parent's
  output entry (found by identity), and a top-level block still goes to EOF.
  Theirs order is kept.
- Stated precondition: the child sits exactly one level below its parent. When
  theirs skips levels (`* P` then `*** x`) and ours has `** y` under `P`, `x`
  lands under `y`. No position keeps every parent in that case.
- Left out of the Lean model: the fold of `insertUnder` over all of theirs
  (the `placed` map). It is induction over the per-step lemmas and is not
  mechanised. The randomised pytest covers it.
- Unchanged: `--apply` writes with plain `write_text` rather than through
  `org_transaction`. That is cross-cutting and belongs to the coordinator.

## 6. inbox_cleanup.clean

**(a) DEFERRED is archived.** `CLOSED` included DEFERRED. DIP-0009 lines
330-337 make DEFERRED closed-but-wakeable, and the wake sweep never reads the
dated archive files. A DEFERRED top-level entry, or one under a DONE parent,
therefore went to `inbox-archive-<date>.org` and could never wake. This runs
nightly through `gtd_hygiene.py`.

- Lean: `old_archives_deferred` is the counterexample.
  `clean_never_archives_live` proves the property for any archive/rescue sets
  where the archivable states are not live and every live state is rescued.
  `new_never_archives_live` instantiates it for the fixed sets. `clean_count`
  proves that every entry lands in exactly one of kept, archive or Inbox.
  `clean_idem` proves idempotence. `detach_count` and `detach_rescues` are
  the lemmas behind them.
- Replay (old code): `* Inbox / ** DEFERRED wake me / * DONE x / ** DEFERRED child`
  gave `archived: 2`, and both DEFERRED entries were in the archive text.
- Fix: `CLOSED = (DONE, CANCELLED)`. A new `LIVE = OPEN + DEFERRED` set is
  what `detach_open_descendants` rescues. This is the same rule
  `org_archive_closed` applies.
- Model scope: top-level blocks with `detach` exactly as in the Python. The
  Inbox section and the level-2 closed children of a live section call the same
  `detach`, so the same lemmas apply. Heading re-levelling on move is not
  modelled, because the properties concern state and identity, not stars.

**(b) Star lines inside `#+begin_` blocks.** DOWNGRADED. The vendored
orgparse, which `org_archive_closed` and `parses()` use, also treats a
`* text` line inside `#+begin_example` as a heading. So does Org syntax, which
is why org-src escapes such lines with a comma. `inbox_cleanup` agrees with
the reference parser. Checked with `loads("* DONE x\n#+begin_example\n* not a heading\n#+end_example\n")`,
which gives the headings `['x', 'not a heading']`.

**(c) Not idempotent (new).** The randomised property test found this in the
old code, with no DEFERRED involved. A subtree cut from the end of the file
carries the file's trailing blank line into the Inbox, so the second run
rewrote whitespace. Example (seed 299): `* Inbox\n* item0\n** CANCELLED item1\n*** TODO item2\n`.
The fix trims trailing blank lines after the arrivals, and `clean(clean(x)) == clean(x)`
now holds over 300 random inboxes.

## 7. org_dedup_within_file

`identity()` strips `:ID:` and `:DISPATCH_ID:` before comparing, so two copies
with different ids are treated as duplicates and the second is deleted. The
ledger may know that second id as its own item.

- Lean: `dd_drops_distinct_id` is the counterexample to "byte-identical".
  `dd_dropped_has_twin` proves what the tool does guarantee: every dropped
  subtree equals a kept copy with the same heading key, modulo the
  generated-id lines.
- Replay: two `* TODO x` subtrees with `:ID: id-1` and `:ID: id-2` gave
  `(1, 4, 0)`. The file kept only `id-1`.
- Why NEEDS-OWNER: ignoring the ids is deliberate. The docstring explains that
  the 2026-08-15 copies differed only in these lines. Requiring equal ids would
  undo the tool's reason to exist.
- Applied (my files, behaviour-neutral): the docstring now states the
  exception instead of claiming byte-identity. The report now names every
  dropped id that the kept copy lacks, for example
  `drop * TODO x (drops id(s) id-2; kept copy has id-1)`, so the ledger side
  can be reconciled. Test: `test_dedup_within_file_names_the_ids_it_drops`.
- Unchanged: plain `write_text` rather than `org_transaction` (coordinator),
  and only maximal subtrees are compared (a limitation, not a defect).

## 8. org_resolve_id_conflicts

**(a) Keeps HEAD.** The docstring says to keep the upstream id, but the code
always kept `<<<<<<< HEAD`. HEAD is upstream only during a rebase.
`git_fleet_sync.py:280-291` merges (`git pull --no-rebase`, DIP-0046 "MERGE,
NEVER REBASE"), and in a merge HEAD is the local side. So on the fleet's own
sync path the tool kept the local id and discarded the upstream one.

- Lean: `old_keeps_local_on_merge` is the counterexample, and
  `new_keeps_upstream` is the proof. The model is shallow by nature: the
  content is git's labelling of the sides, which is encoded as the definition
  `upstream`.
- Replay: a real tmp git repo with an ID-only conflict. In merge mode the old
  code left `LOCAL-ID`. In rebase mode it left `UPSTREAM-ID`.
- Fix: `upstream_side()` asks git (`rev-parse --git-path rebase-merge|rebase-apply|MERGE_HEAD`)
  which operation is in progress. A rebase keeps HEAD and a merge keeps the
  incoming side. When git cannot tell, the tool refuses. `--keep head|other`
  overrides. Tests: `test_resolve_id_conflicts_keeps_the_upstream_id[merge|rebase]`
  and `test_resolve_id_conflicts_refuses_when_it_cannot_tell_the_sides`.

**(b) "Nothing references the local id".** This is false when the local id
was minted by the adapter's `add`, because the ledger holds an `item.create`
for it. Either side's id may be live in the ledger. The docstring is
corrected. Reconciling the ledger is NEEDS-OWNER.

Unchanged: plain `write_text` (coordinator).

## 11. org_archive_closed (positive reference)

REFUTED, meaning the survey's "no suspicion" holds. `archSpans` models
`_spans`: a level-1/2 DONE/CANCELLED node moves with its subtree only when
every descendant is stateless or DONE/CANCELLED, and otherwise the scan
continues at the next node.

- `archSpans_never_live` proves that no open or DEFERRED node is ever
  archived.
- `archSpans_count` proves that every node is either archived or kept, exactly
  once.
- Mutation check: dropping the descendant check breaks `archSpans_never_live`.

## 12. triage_utils._append_task_body

**(a) Writes into the next task's drawer.** The insertion point was the first
`:END:` after the matched heading, with no boundary. When the target had no
drawer of its own, the body went into the next task's drawer.

- Lean: `old_append_writes_into_next_task` is the counterexample.
  `appendBody_local` proves that only the first eligible target's own section
  changes: everything before it is untouched and ineligible, and everything
  after it is untouched.
- Replay: a target `** TODO … repo#1` with no drawer, followed by `repo#2`
  with a drawer. The old code put `context for one` under `repo#2`.
- Fix: the search is scoped to the heading's own section, and a matching
  heading without a drawer is skipped.

**(b) Whole-file duplicate check.** The "already recorded" test was
`first_body_line in content`, over the whole file. A first line that another
task already carried (for example `Source: github`) suppressed the write.

- Lean: `old_append_skips_on_foreign_line` shows the old flat model does not
  write while the fixed model does. `appendBody_effect` proves that after a
  run the first eligible section contains the body. `appendBody_idem` proves
  idempotence.
- Fix: the check looks only at the target section. The four existing tests in
  `.datacore/tests/test_triage_dedup.py` still pass.

**(c) `_CLOSED_STATES` omits DEFERRED.** NEEDS-OWNER, not changed. Whether
fresh triage context should be appended to a benched task is a policy
choice.

## 12. dedup.deduplicate

Jaccard title similarity is not transitive. With A~B, B~C and A≁C, the code
dropped every item that duplicated any earlier item, so C went too. C then
had no kept representative.

- Lean: `old_dedup_loses_representative` is the counterexample.
  `greedy_represents` proves that every item is kept or duplicates a kept
  item. `greedy_pairwise` proves that no two kept items are duplicates.
  `greedy_keeps_it` is the fixed version of the counterexample.
- Replay: titles `a b c d`, `a b c d e` and `b c d e f` at threshold 0.6 gave
  only `a b c d`.
- Fix: greedy keep-first against kept items, with the same pair test as
  `find_duplicates` (hash or title). `find_duplicates` and the CLI dry-run are
  unchanged. The randomised test checks both guarantees on 100 seeds.

## Mutation checks

Each check puts the bug back into a scratch copy of the model
(`scratchpad/orgtools/mutate.py`) and records which theorems stop proving:

| Mutation | Theorems that stop proving |
|---|---|
| dedup: a dropped item still counts as kept for later comparisons (the old rule's effect on later items) | `greedy_represents`, `greedy_pairwise`, `greedy_keeps_it` (and `greedy_extends`, for proof reasons) |
| union: `insertUnder` appends at EOF | `insertUnder_places_under_anchor` (and `insertUnder_mem`, for proof reasons) |
| union: an unidentified match does not consume (set semantics, not multiset) | `unidExtra_count` |
| inbox: `archNew` includes DEFERRED | `new_never_archives_live`, `archSpans_never_live` |
| inbox: rescue only open states (the old `detach`) | `new_never_archives_live` |
| resolve: a merge keeps HEAD | `new_keeps_upstream` |
| triage: the duplicate check also looks at later sections | `appendBody_effect`, `appendBody_idem`, `appendBody_local` |
| archive_closed: no descendant check | `archSpans_never_live` |

`org_dedup_within_file` gets no mutation check: its code was not changed
(NEEDS-OWNER), and the theorem proved describes the current behaviour.

## Files changed

- `.datacore/lib/org_union_merge.py`
- `.datacore/lib/inbox_cleanup.py`
- `.datacore/lib/org_dedup_within_file.py` (docstring and report only)
- `.datacore/lib/org_resolve_id_conflicts.py`
- `.datacore/lib/triage_utils.py`
- `.datacore/lib/dedup.py`
- new: `.datacore/lib/tests/test_org_tools_formal.py`
- new: `.datacore/specs/datacore-lean/DatacoreSpec/OrgTools.lean`
- new: `.datacore/specs/datacore-lean/findings/org-tools.md`

## Direct writes outside org_transaction (for the coordinator)

These write with plain `write_text` and take no lock or journal:
`org_union_merge.py --apply`, `org_dedup_within_file.py --apply` (it does make
a `.bak-dedup` copy), `org_resolve_id_conflicts.py`, and
`triage_utils._append_task_body` / `_set_task_properties`. `inbox_cleanup`
and `org_archive_closed` already use `write_org_text` under `@serialized`.

## Decisions applied (2026-09-23)

Pins: `lib/tests/test_decisions_gtd_b.py` (322 cases, each failing on the
code before its decision). Lean: `DatacoreSpec/OrgTools.lean` sections 2a',
4b, 6; `DatacoreSpec/Dates.lean` section 7.

- **Decision G8 applied** (item 12, and "Direct writes outside org_transaction"):
  `triage_utils._set_task_properties` and `_append_task_body` are each
  `@org_transaction.serialized`, read under `watch_file`, and write through
  `write_org_text`. They are separate transactions because
  `create_triage_task` runs the adapter as a subprocess that takes the same
  lock. The manual repair tools (`org_union_merge --apply`,
  `org_dedup_within_file`, `org_resolve_id_conflicts`) still use plain
  `write_text`: the decision was "live writers now, manual tools later".
  The hook half is in `findings/dates.md` and `findings/org-transaction.md`.
- **Decision G9 applied** (item 7): under `--apply`, each dropped id that the
  kept copy lacks is dismissed in the space's ledger with
  `kind=housekeeping`, reason `duplicate of <kept id>; id dropped by
  org_dedup_within_file`, through `org_workspace_adapter._ledger_emit`. The
  helper, `ledger_dismiss_housekeeping`, does nothing (and says why) when the
  space has no ledger, the ledger never created the id (a dismiss would be
  an orphan event), or the item is already dismissed. A Phase 1 generated
  `next_actions.org` is refused outright when a distinct id would be dropped:
  there `_ledger_emit` reconciles by diff and raises `ProjectionConflict`
  ("removed heading needs explicit archive/deletion evidence"), and the
  projection would regenerate the copy anyway. Lean: `no_known_id_vanishes`,
  `dismissals_sound`, `dd_dismisses_distinct_id`. Mutation (dismiss nothing):
  `no_known_id_vanishes`, `dd_dismisses_distinct_id`,
  `resolve_dismisses_discarded` stop proving.
- **Decision G10 applied** (item 8b): each discarded id that has an
  `item.create` is dismissed the same way, reason `duplicate of <kept id>;
  id dropped by org_resolve_id_conflicts`, paired with the kept id of the
  same hunk position. `--check` and unknown ids touch nothing. Lean:
  `resolve_dismisses_discarded`.
- **Decision G11 applied** (item 12c): `_CLOSED_STATES` now includes
  `DEFERRED`. The Lean `Hd.closed` flag was already abstract; its comment now
  lists DEFERRED, and `appendBody_*` are unchanged.
- **Decision G12 applied** (item 4b, "Stated precondition"): the precondition
  is gone. When nothing in the anchor's subtree is shallower than the block,
  placement is unchanged (`placeTheirs_plain`). Otherwise `place` creates the
  missing heading(s): levels `anchor+1 .. x-1` at the end of the anchor's
  subtree, or, when the anchor sits at or below the block's level (a closure
  won at another depth), levels `1 .. x-1` at end of file. A created heading
  reuses theirs' own ancestor heading at that level, or theirs' parent where
  theirs has none; the last one always has the parent's text. It carries no
  state keyword and no `:ID:` (so it is not a task and no id is duplicated),
  and theirs-only siblings share one (`stats["headings_created"]`). Replay of
  the old code: ours `* P / ** y`, theirs `* P / *** TODO x` gave
  `* P / ** y / *** TODO x` (x under `y`); now `* P / ** y / ** P / *** TODO x`.
  Lean: `old_skip_under_unrelated` (counterexample), `placeTheirs_parent_text`
  (every theirs-only block's parent has its theirs-parent's text),
  `placeTheirs_keeps`, `placeTheirs_new`, `placeTheirs_keeps_later_parents`.
  Mutations: the skip branch inserting plainly, and the end-of-file branch
  without a chain, each break `placeTheirs_parent_text`. The randomised
  pytest (300 seeds, level skips allowed) failed on 12 seeds with the old
  placement. Residual: a theirs block with NO parent but deeper than level 1
  (theirs' file opens with `** x`) still goes to end of file, under whatever
  heading is last; it has no parent text to keep. Not modelled: the fold over
  all of theirs (as before).

## Follow-up decisions applied (2026-09-23, second board)

Pins: `lib/tests/test_followups_org.py`. Lean: `DatacoreSpec/OrgTools.lean`
section 2a'', `DatacoreSpec/Dates.lean` section 7.

- **Decision Q12 applied** ("Direct writes outside org_transaction" above):
  `org_union_merge --apply`, `org_dedup_within_file --apply` and
  `org_resolve_id_conflicts` now watch, read and write under
  `org_transaction.serialized` with `write_org_text`; so do `inbox_dedup
  --apply`, `stamp_seq_todo` and `validate_org_dates --fix`. Details, the
  replay, and the writers left in `lib/` are in `findings/org-transaction.md`.
- **Decision Q13 (keep refusing), no change:** in a Phase 1 generated
  `next_actions.org`, `org_dedup_within_file --apply` still refuses to drop a
  copy whose distinct id the kept copy lacks, and prints "dismiss the
  duplicate item in the ledger instead". A human dismisses it through the
  ledger tools; the tool does not emit a conditional dismissal itself.
- **Decision Q14 applied** (the G12 residual): a theirs-only block with no
  parent in theirs and a level deeper than 1 (theirs opens with `** x`) no
  longer goes to end of file under the last shallower heading. `place` creates
  heading(s) for it: levels `1 .. x-1` at end of file, and later parentless
  blocks go under that same root (a created level-2 heading is shared by
  level-3 siblings, through the same `created` map as G12). **Neutral text,
  decided here:** `UNFILED = "Unfiled by org_union_merge"` for every created
  level; it names the tool so a reader knows where it came from, has no state
  keyword, priority cookie, tags or `:ID:`, and is made unique against every
  heading of both sides (`_unfiled`: compared without stars, state, priority,
  tags and case; on a clash it becomes `... (2)`, `(3)`, ...). One case
  creates nothing: when the output has no heading shallower than `x` (a flat
  file of `**` items, as in `test_org_union_merge.py`), end of file gives `x`
  no parent at all, exactly as in theirs; it cannot land under an unrelated
  heading there. Replay of the old code: ours `* Q`, theirs `** TODO x / * P`
  gave `* Q / ** TODO x / * P` (x under `Q`); now `* Q / * Unfiled by
  org_union_merge / ** TODO x / * P`, `headings_created: 1`.
  Lean: `old_orphan_under_last` (counterexample), `placeOrphan_parent_neutral`
  (the parent, if any, carries the neutral text; the root case reuses
  `placeTheirs_parent_text` with that text), `placeOrphan_never_existing`
  (so, when the text is fresh, never a heading of either side),
  `placeOrphan_keeps` (nothing lost). Axioms: `propext`, `Quot.sound`.
  Mutations (scratch `org-followups/m*.lean`): m1, the fresh case appends
  plainly (the old code): `placeOrphan_parent_neutral` stops proving and
  `mutant_m1_counterexample` proves; m2, the chain's last heading gets another
  text: `placeOrphan_parent_neutral` stops proving; m3, the root case inserts
  plainly at the end of the root's subtree: `placeOrphan_parent_neutral` and
  `placeOrphan_keeps` stop proving and `mutant_m3_counterexample` (x under an
  existing `** y`) proves. The randomised pytest (200 seeds, theirs opening at
  level 2-3 in 80% of them) failed on 139 seeds with the old placement.
  Tests changed: none pin the old behaviour; the comment in
  `test_decisions_gtd_b.py::test_union_every_theirs_only_item_is_under_its_parents_text_randomised`
  that called this a residual is updated.
