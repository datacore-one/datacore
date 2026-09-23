# GtdState — findings (2026-09-23)

Model: `DatacoreSpec/GtdState.lean` (namespace `DatacoreSpec.GtdState`). It checks
cleanly with `lake env lean` and contains no sorry, axiom or native_decide.
Tests: `lib/tests/test_gtd_state_formal.py`, 15 tests. All 14 of the original tests
failed before the fix. The 15th (the task_cleanup repeater test) was replayed
against the HEAD copy and confirmed. All 15 pass now.

## Summary

| # | Candidate | Verdict |
|---|---|---|
| 1a | `complete` on a repeater dismisses the ledger item | CONFIRMED+FIXED (authored files, any phase) |
| 1b | `update --state DONE/CANCELLED` never dismisses | CONFIRMED+FIXED (authored files, any phase) |
| 1c | Same two paths on the Phase 1 generated next_actions.org | REFUTED: `sync_generated` diffs the file, so these paths were already correct |
| 1d | `complete` on an ID-less heading appends `{"id": null}` | CONFIRMED+FIXED |
| 2 | Code transition relation ⊆ DIP-0009 v2.0 table | REFUTED as a claim (the code is looser), so CONFIRMED+NEEDS-OWNER |
| 2b | "REVIEW exits: owner only" | CONFIRMED+NEEDS-OWNER. The adapter has no identity notion to enforce it with |
| 2c | env_keys puts DEFERRED in the todo class | CONFIRMED, cosmetic. With the v2 header, DEFERRED ends up in both lists. This is in the external org_workspace package, so NEEDS-OWNER |
| 9a | `retire_duplicate` on a repeater | CONFIRMED+FIXED |
| 9b | "lowest id" tie-break is dead code | CONFIRMED+FIXED |
| 9c | No ledger event, so the Phase 1 projection reverts | DOWNGRADED |
| 9d | Open children left under a CANCELLED parent | Not fixed. Low impact: fingerprint equality means the children are identical duplicates too |
| 10a | dup-id `<id>-copy<N>` is not a UUID and not unique across reruns | CONFIRMED+FIXED |
| 10b | The `close` action on a repeater (the same class as 9a) | CONFIRMED+FIXED |
| 10c | The meridian swap reassigns the canonical copy, and there is no ledger notification | NEEDS-OWNER |

## 1. Adapter org state → ledger events

**Model.** `wsTransition` models workspace.py:257-318 branch for branch: the no-op
on the same state, `can_transition`, and the repeater branch that reopens the task
as TODO. `syncEvents` models `projection_state.sync_generated`. `oldComplete` and
`oldUpdate` model the pre-fix code. `newComplete`, `newUpdate` and `newEvents`
model the fix.

**Counterexamples** (Lean `old_complete_dismisses_repeater` and
`old_update_never_dismisses`), replayed with
`scratchpad/gtdstate/replay_adapter.py` on an inbox.org in both a Phase 0 and a
Phase 1 tmp space:

```
phase1 (a) complete repeater: org={'STATE': 'TODO'} ledger=('dismissed', 'TODO')
phase1 (b) update --state DONE: org=DONE ledger=('created', 'DONE')
phase1 (b) update --state CANCELLED: ledger=('created', 'CANCELLED')
```

**The generated-file path is correct.** `replay_generated.py`, run with
ledger-edit-protocol=1:

```
complete repeater (generated): {'STATE': 'TODO'} ('created', 'TODO', '<2026-09-28 Mon +1w>')
update DONE / CANCELLED (generated): ('dismissed', ...)
```

The bug therefore hits every authored file: inbox.org, nightshift.org, habits.org
and someday.org. All 10 spaces are Phase 1, and their next_actions.org was safe.
An inbox capture closed by `update` stayed live in the ledger, and the projector
rendered it into next_actions.org as a live DONE item forever. That is exactly the
drift ruling 5 was made to end.

The real habits.org holds only 1 ID, and that ID is not in the ledger. Real-data
impact of 1a is therefore mostly orphan `{"id": null}` dismiss events (1d).

**Fix** (`lib/org_workspace_adapter.py`):
- New helper `_generated_target()`.
- New helper `_ledger_emit_close()`: it dismisses iff the RESULTING `node.todo` is
  DONE (kind done) or CANCELLED (kind dropped), the node has an id, and the path
  is not the generated one.
- `cmd_complete`: when the repeater reopened the task, it now emits `item.update`
  (state, scheduled, properties) instead of a dismissal, and returns `repeated`.
  It emits nothing when the node has no id.
- `cmd_update`: it adds the dismissal after the update. When a repeater reopened
  the task, the update also carries scheduled and properties.

**Proved** (fixed code, every start state, target, repeater flag, id flag and
path):
- `dismissed_implies_terminal`: ledger dismissed ⇒ org DONE or CANCELLED.
- `terminal_implies_dismissed`: a task with an id that ends DONE or CANCELLED is
  dismissed, with the right kind.
- `deferred_never_dismisses`.
- `repeater_stays_live`.

**Mutation check.**
- Put back "complete always dismisses": `dismissed_implies_terminal` and
  `deferred_never_dismisses` fail, and a counterexample theorem proves by `decide`.
- Drop the close event from update: `terminal_implies_dismissed` fails.

## 2. DIP-0009 v2.0 table vs `can_transition`

The code relation is `code a b := ¬terminal a ∧ a ≠ b` over all 10 states in the
single "gtd" sequence of `StateConfig.default()`. That config still lists
QUEUED/WORKING/FAILED, and FAILED is non-terminal.

**Proved:**
- `spec_sub_code`: every spec move is allowed by the code.
- `code_not_sub_spec`: code ⊆ spec is false. The counterexamples are
  DEFERRED→DONE, DEFERRED→NEXT, REVIEW→TODO, REVIEW→WAITING, TODO→QUEUED and
  NEXT→FAILED.
- `excess_exact`: the complete characterisation of the excess.
- `canonical_excess`: among the seven v2.0 states there are exactly 7 illegal
  moves: REVIEW→TODO/WAITING and DEFERRED→NEXT/WAITING/REVIEW/DONE/CANCELLED.

**Replay** (`envcheck.py`): the code allows 72 moves and the spec 23. The code
admits 49 moves the spec does not, and every spec move is allowed by the code.
All six survey pairs are confirmed.

**Not fixed in the adapter.** Current callers rely on spec-illegal moves:
- `gtd_decision_board._ops`, when a REVIEW row is sent to "someday" in a
  non-generated file. It runs `move` and then `update --state TODO`, which is
  REVIEW→TODO. Its `defer` choice also maps a non-WAITING row to TODO.
- `delegation_gate` moves REVIEW→DEFERRED and REVIEW→NEXT as an agent.

The DIP's "self-contradiction" at :462-466 is the v1.1 table, which is marked
superseded (it allowed DEFERRED→CANCELLED). Under v2.0, dropping a benched task
takes DEFERRED→TODO→CANCELLED.

**Owner-only.** `owner_only_not_enforced` shows the gap. The identity notions that
exist today:
- `actor_identity.this_actor()`: the per-machine writer identity (DIP-0044), for
  example `mac`. It is the same for the owner's session and for agents on the
  same host.
- `principals.yaml`: binds writers to principals.
- `:OWNER:` (ruling 9): no GTD code reads it.
- The ledger's `item.owner` is the claim holder, not the accountable owner.

Nothing can tell "owner" from "agent" at `transition()`.

## 9. dedup_tasks

**9a.** Replay (`replay_cleanup.py`): `retired=True state=TODO sched=<2026-09-28
Mon +1w> DEDUPE_OF=keep-id`. The Lean counterexample is
`old_retire_lies_on_repeater`.

The fix: `has_repeater` refuses a repeater, and a post-transition check refuses
to stamp DEDUPE_OF on anything that is not CANCELLED. `main` prints
"retain: repeating task". Proved as `retire_true_implies_cancelled`. The mutation
check (drop the guard) fails it.

**9b.** The first sort was overwritten by the second. The fix is `rank_group()`:
sort by id first, then do a stable reverse sort on (number of properties,
scheduled). Pinned by two tests.

**9c.** DOWNGRADED. next_actions.org is generated in every space. The hourly
`ledger_ingest_org` runs `sync_generated` before projecting, and
`guard_projection` refuses to project over unreconciled authored edits. An
authored CANCELLED therefore becomes `item.dismiss(dropped)`. This is the same
function the generated-path replay above exercised: CANCELLED→dismissed.

## 10. task_cleanup

**10a.** Replay: run 1 gives the inbox copy `X-copy1`. A new routing copy of X
arrives, and run 2 mints `X-copy1` again, so the inbox holds `['X-copy1',
'X-copy1']`. `SafeOrgWorkspace.load` then refuses: "duplicate Org IDs require
explicit identity reconciliation". `scan()` silently skips unloadable files, so
the tool cannot see the damage it caused. Lean: `old_mint_collides`.

The fix: `_fresh_id(taken)` returns a `new_org_id()` UUID4, and loops until the
id is absent from every scanned id and every id minted this run. Proved as
`fresh_mint_unique` and `fresh_mint_no_new_dup`. The mutation check (drop the
freshness hypothesis) fails it.

**10b.** Replay on the HEAD copy: `close` on a `+1w` task returned
`(1, [])`. The date advanced, the task stayed TODO, and CLOSED_REASON was stamped.
The fix: `close` and `also_close` skip repeaters, and `close` reports an error.

**10c.** NEEDS-OWNER.
- The meridian swap reassigns the canonical copy. For [A (sp1 next_actions),
  M (meridian), B (sp1 inbox)], A and B get new ids and only M keeps X.
- A Phase 1 generated next_actions.org whose id is reassigned makes the projection
  refuse the space.
- The by-construction pair of inbox.org and generated next_actions.org
  (`ledger_ingest_org._projected_duplicates`) is treated as a duplicate.
- No ledger event is emitted for any of this.

## Files changed

- `.datacore/lib/org_workspace_adapter.py`
- `.datacore/lib/dedup_tasks.py`
- `.datacore/lib/task_cleanup.py`
- new: `.datacore/lib/tests/test_gtd_state_formal.py`,
  `specs/datacore-lean/DatacoreSpec/GtdState.lean`, this file

## Tests

```
cd .datacore/lib && python3 -m pytest -q tests/test_gtd_state_formal.py   -> 15 passed
```

Regression set: test_org_workspace_adapter, test_org_transactions,
test_decision_board_safety, test_ledger_phase1_tools,
test_projection_reconciliation, test_phase1_cycle_safety,
test_org_ledger_roundtrip, test_plain_heading_is_admitted, test_org_space and
test_ledger_ingest_org all pass. So do chief-of-staff test_delegation_gate and
test_delegation_integrity, and nightshift test_routing_preservation.

One unrelated failure: `test_ledger_attest::test_undecodable_identity_file_never_raises`
raises UnicodeDecodeError in `actor_identity._parse_env_file`. That is not a file
this cluster owns, and none of these changes touch it.

## Out of scope, reported

`modules/nightshift/lib/nightshift_parser._persist_task` has the same defect as
1b. On nightshift.org and other authored files, a state change to DONE or
CANCELLED emits only `item.update`, so the ledger item stays live. It could call
the new `org_workspace_adapter._ledger_emit_close(fp, node, reason)`.

## Owner decisions applied (2026-09-23)

Tests: `lib/tests/test_decisions_gtd_a.py` (18 tests; 10 failed on the pre-change
code, the 8 "silent" cases are guards). Lean: `DatacoreSpec/GtdState.lean`
section 5. Mutation checks (scratch copies under `scratchpad/gtd-a/`): dropping
DEFERRED→CANCELLED from `specG2` breaks `deferred_cancelled_no_warn`,
`specG2_exact`, `canonical_warned`, `codeV2_warn_exact`; making `warn` ignore
the spec breaks `warn_iff_not_spec`; making the adapter refuse on a warning
breaks `warn_performs_as_before`; dropping the generated-file skip breaks
`plan_skips_generated` and `plan_never_touches_generated`; dropping the
retired-state restriction from `codeV2` breaks `codeV2_no_retired` and
`codeV2_warn_exact`.

**Decision G1 applied: warn only.** `org_workspace_adapter.DIP0009_V2_TRANSITIONS`
and `dip0009_allows()` hold the adapter's relation. `_transition_warning()` runs
before `ws.transition` in `cmd_complete` and `cmd_update`; a real move outside
the relation prints `org_workspace_adapter: WARNING: transition A→B is not in
the DIP-0009 v2.0 table …` to stderr (the adapter has no other log) and adds it
to a `warnings` list in the JSON result. The move is then performed exactly as
before; the exit code and every other field are unchanged. A same-state
request is a no-op, not a move, and never warns. Lean: `warn_iff_not_spec`
(warn ⇔ a ≠ b ∧ move ∉ spec), `warn_performs_as_before`, `spec_never_warns`,
`canonical_warned` (among the seven v2.0 states exactly six performed moves
warn: REVIEW→TODO/WAITING, DEFERRED→NEXT/WAITING/REVIEW/DONE). Callers that will
now see warnings: `gtd_decision_board` (REVIEW→TODO on the someday path) and
`delegation_gate` only for moves outside the table (its REVIEW→DEFERRED and
REVIEW→NEXT are in the table and stay silent).

**Decision G2 applied: DEFERRED→CANCELLED allowed.** It is in the adapter's
relation and does not warn (`deferred_cancelled_no_warn`, `specG2_exact`: the
relation is the v2.0 table plus exactly this move). The DIP-0009 table lives
in the dips repo, which this area may not edit. The edit needed, in
`.datacore/dips/DIP-0009-gtd-specification.md`, "Valid transitions (v2.0)":

```
- - `DEFERRED` → `TODO` (wake: past-due `SCHEDULED:`, lane back on, or human)
+ - `DEFERRED` → `TODO` (wake: past-due `SCHEDULED:`, lane back on, or human),
+   `CANCELLED` (drop a benched task without waking it)
```

**Decision G3 recorded: not until principals exist.** No code. Requirement:
"REVIEW exits are owner only" (DIP-0009 v2.0) cannot be enforced until the
adapter can tell the accountable owner from an agent. That needs a principal
identity at `transition()` time, distinct from `actor_identity.this_actor()`
(per machine: the owner's session and agents on the same host share it) and
from the ledger's `item.owner` (the claim holder). Once `principals.yaml` binds
a writer to a principal and the call site can present that principal, the
adapter should check REVIEW→{DONE,NEXT,DEFERRED,CANCELLED} against the task's
`:OWNER:` (ruling 9, defaulting per space). Until then `owner_only_not_enforced`
stands, and REVIEW exits by agents are neither warned nor refused on identity.

**Decision G4 applied: skip generated pairs.** `task_cleanup.scan()` marks each
task `generated` when its file is a Phase 1 space's `org/next_actions.org`
(`ledger_project_org.phase(space) == 1`; an unreadable or invalid marker counts
as generated, so the id is left alone). `plan_dup_ids` skips every id group
with a copy in a generated file, which covers the inbox.org / generated
next_actions.org pair that shares ids by design, and cross-space groups that
touch a generated file. Phase 0 (authored) pairs are still reassigned, as
`test_dup_id_reassignment_mints_fresh_uuids_across_reruns` pins. Lean:
`plan_skips_generated`, `plan_never_touches_generated`. The 6-meridian hold is
unchanged (not part of G4).

**Decision G5 applied (repo only; release is out of bounds).** In
`2-datacore/2-projects/org-workspace`, `StateConfig.default()` is now
`TODO NEXT WAITING REVIEW | DONE DEFERRED CANCELLED`, with terminal
{DONE, CANCELLED} and DEFERRED in the done class. `nightshift()` stays an alias.
Its pytest: 263 passed. Tests changed there: `test_types.py`
(`test_nightshift_states`, `test_nightshift_terminal`, new
`TestDefaultIsDip0009V2`), `test_state_vocabulary.py`
(`test_headerless_file_recognizes_overlay_states` → `…_v2_states` plus
`test_headerless_file_does_not_recognize_retired_states` and
`test_header_declared_retired_state_still_parses`;
`test_query_by_state_finds_overlay_states` → `…_header_declared_legacy_state`;
`test_default_config_is_canonical_union` → `…_is_v2`;
`test_default_terminal_is_done_and_cancelled_only`;
`test_default_config_allows_overlay_transitions` → `…_transitions`;
`test_env_keys_split_todo_and_done_class`;
`test_nightshift_factory_aligned_no_executing`;
`test_transition_and_save_roundtrip_overlay_state` → `…_review_state`),
`test_workspace.py` (`test_transition_nightshift_states` now passes a legacy
config explicitly). README and OVERVIEW updated. Lean: `codeV2_no_retired`,
`codeV2_warn_exact`, `deferred_done_class_wakes`.

Release check: the installed package is 0.5.1 (site-packages, not editable);
the repo's pyproject says 0.5.2. Every .datacore test file that mentions a
task state (40 files, 6 directories) was run against the installed package
and against the repo's `src` (`scratchpad/gtd-a/g5_compare.sh`). Exactly one
test changes result:
`lib/tests/test_intent_input_integrity.py::test_review_and_retry_states_still_count_as_unfinished_work`.
`intent_sources.org_nodes` seeds its parser with
`StateConfig.default().env_keys()`, so headerless QUEUED/WORKING/FAILED
headings stop counting as open intent work. By inspection, the same class
applies to every `SafeOrgWorkspace()` reader with the default config
(`ledger.genesis.scan`, `ledger_ingest_org`, the adapter, `task_cleanup`,
`dedup_tasks`): a retired keyword in a file whose header does not declare it
becomes heading text. `genesis.scan` would then import it as a section, not
as an overlay task. nightshift is unaffected (`NIGHTSHIFT_STATE_CONFIG` is
its own). The generated next_actions.org header already omits the retired
states. The release is safe only once live authored files carry no retired
keyword (the DIP-0009 v2.0 migration and `org_state_lint` cover this) and
`intent_sources` / that test are updated. Version bump needed: a minor bump
(a behaviour change to the public default), i.e. 0.6.0. Versions disagree
today: the repo's pyproject says 0.5.2, the installed and `dist/` builds are
0.5.1, and the auto-memory says 0.5.4 is on PyPI. Check PyPI before bumping.

**Decision G7 applied.** `org_transaction.write_org_text` now documents that a
caller must `watch_file` (or `SafeOrgWorkspace.load`) before reading or
deciding to overwrite a path, because a first-touch write compares the file
with itself. `ledger_project_org._with_org_header` watches the header copy
before its `exists()` check when it may write it. `project_space` already
watched it, so production behaviour is unchanged; the function is now safe
for any serialized caller. Test:
`test_header_copy_first_write_refuses_a_racing_writer` (a writer that creates
the copy between the check and the write now raises `RecoveryRequired`, and
its content survives; before, it was silently overwritten).

## Decision G6 applied (2026-09-23)

`nightshift_parser._persist_task` now closes the ledger item through
`org_workspace_adapter._ledger_emit_close`, decided from the state the task ENDS
in. A write to DONE or CANCELLED loads the file under `NIGHTSHIFT_CLOSE_CONFIG`
(the nightshift sequence plus CANCELLED, with DONE and CANCELLED terminal), so
`transition` behaves as the adapter's: a SCHEDULED repeater advances and reopens
as TODO, and its `item.update` carries state, scheduled and properties; anything
else closes with a CLOSED stamp and is dismissed (`done` / `dropped`). Every
other nightshift move still uses the permissive config. `update_task` records the
state the task ended in. Before, the nightshift config had no terminal states, so
closing a repeater wrote a literal DONE and ended the recurrence, and CANCELLED
raised `InvalidTransitionError`. Tests:
`modules/nightshift/tests/test_decisions_nightshift_b.py` (DONE/CANCELLED dismiss
in Phase 0 and 1 on an authored nightshift.org, REVIEW never dismisses, a
repeater reopens and stays live). The generated Phase-1 next_actions.org path is
unchanged (`_ledger_emit_close` returns None there; `sync_generated` diffs it).

## Decision G5 release preparation (2026-09-23; prepared, not released)

**intent_sources.** `org_nodes` now seeds its parser with the pinned constant
`intent_sources.DIP0009_V2_KEYS` (`TODO NEXT WAITING REVIEW | DONE DEFERRED
CANCELLED`) instead of `StateConfig.default().env_keys()`. An intent review then
reads a file the same way whichever org-workspace release a host has installed.
Headerless QUEUED, WORKING and FAILED are heading text. A file whose header
declares them keeps them as open work, because `intent_tasks.OPEN_STATES` still
lists them. Test changed:
`test_review_and_retry_states_still_count_as_unfinished_work`. The headerless
case now expects 4, and a header-declared case expects 7. Test added:
`test_intent_vocabulary_matches_a_v2_package_default`, a drift guard that is
skipped while the installed package predates v2.0. No Lean model covers this
module.

**Live-file scan.** A read-only scan of all 232 files matching
`~/Data/*/org/**/*.org`, including hidden `.archive/` directories (script at
`scratchpad/g5-release/scan_retired.py`), found 0 live files with a headerless
retired state. Two archive files have 17 such headings between them. One
parked archive file declares WORKING and FAILED in its header, so it parses
correctly. `org_state_lint` reports 0 violations.

**Test comparison.** The 40 state-touching .datacore test files were run
against the installed package (0.5.1) and against the repo's `src`. The only
difference is that the drift guard is skipped under 0.5.1 (lib/tests: 1259
passed and 1 skipped, against 1260 passed).

**Version.** The repo is bumped to 0.6.0 (pyproject, `__version__`, a new
CHANGELOG.md, and OVERVIEW.md). Release blocker: PyPI already has 0.5.2
(2026-08-19), 0.5.3 (2026-08-28) and 0.5.4 (2026-09-05). The local clone was
last fetched on 2026-08-10. Its HEAD `a013566` is 0.5.2, and nothing in it
matches 0.5.3 or 0.5.4. Building 0.6.0 from this tree could drop whatever those
two releases shipped.
