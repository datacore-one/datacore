# Findings: OrgTransaction (`lib/org_transaction.py`), 2026-09-23

Survey lead: `survey/gtd-org.md` item 3. Model: `DatacoreSpec/OrgTransaction.lean`
(namespace `DatacoreSpec.OrgTransaction`). It checks cleanly with
`lake env lean DatacoreSpec/OrgTransaction.lean`. Axioms used: `propext` and
`Quot.sound` only.

## Model

- The filesystem is `Path → Option Nat`, a content hash per path, with `none`
  meaning absent. Each `Entry` holds `before`, `versions` and `current`, as in
  the Python. The journal is `persistOf mem`: every entry with more than one
  version, reduced to `(before, versions)`, which are the only fields
  `recover` reads.
- `recover` is the relation `Recover`. It has three outcomes: clean (no
  journal), restored (every owned hash is in its version list, so every
  owned path gets `before` back and the journal is deleted), and retained
  (nothing changes).
- Each durable effect is one micro-step: `watch`, `prep` (append a version,
  then `persist`) and `apply` (the atomic file effect). The `apply` step is
  deliberately broader than the Python: it may apply **any** recorded version
  at any time, and a move's two effects happen separately. `Reach` is the set
  of states reachable from the start of a serialized call. A crash can
  therefore happen between any two steps.
- Left out: the directory fsyncs, the symlink refusals, and journal format
  validation. A residual that no cooperative model can cover: `recover`
  checks every file and then restores them, and an uncooperative writer who
  edits a file in the gap between those two loops is overwritten.
  `file_lock` only serializes writers that take it.

## Candidates

| # | Claim | Verdict | Lean | Replay |
|---|---|---|---|---|
| 1 | After `recover`, the files equal the pre-transaction state, OR the journal is retained and nothing was restored | **REFUTED as a bug; the claim holds and is proved** | `crash_recovers_pre_state` (every crash point, no external writers: `fs' = fs0` and the journal is removed, never retained); `recover_dichotomy` (any filesystem: either nothing changed and the journal is retained, or every changed path was owned and gets exactly its `before`) | `test_every_crash_point_recovers_the_pre_transaction_state[1..6]`: the process dies at each durable effect of write, write, rewrite, move, delete, then `recover` runs. Pre-state restored and journal gone in all 6 cases. |
| 2 | No restore over a foreign hash | **REFUTED as a bug; the claim holds and is proved** | `recover_dichotomy`: a changed path had `fs p ∈ vs`. Existing test: `test_recovery_never_overwrites_an_external_edit` | Existing tests pass. The only exception is the check-to-restore gap described under Model. |
| 3 | The stale-overwrite check detects an external change | **CONFIRMED+NEEDS-OWNER**. The check is vacuous when `write()` is the first touch of a path. | `stale_check_vacuous_first_touch` (always passes), `stale_check_detects_when_watched`, `lost_update_example` | `test_stale_check_is_vacuous_when_the_path_is_first_touched_by_write`: when the caller reads without watching, the external edit is silently lost. When the caller watches first, the write raises `RecoveryRequired('…refusing stale overwrite')`. Replay script `scratchpad/org-transaction/replay.py`, output: `(b) final: '* TODO original\n* TODO added\n' -> external edit lost: True`. |
| 4 | A watched destination that loses the rename race strands the journal, and every later serialized call raises `RecoveryRequired` (a global stuck lock) | **CONFIRMED+FIXED** | `race_shipped_strands` (shipped code: `recover` retains the journal and keeps retaining it while the competitor's file stays, together with `retained_is_stuck`); `race_fixed_recovers` (fixed code: the journal is removed, every other path returns to `fs0`, and the competitor's file is left alone) | Before the fix: `move -> RecoveryRequired … journal retained`, and then `later unrelated call -> RecoveryRequired`. After the fix: `move -> FileExistsError`, `journal retained: False`, `later call OK`. |
| 5 | Tools that write org files outside the transaction | **NEEDS-OWNER (cross-cutting, not owned here)** | none; this is outside the model by definition | See below. The lost update is confirmed with the real `org_date_hook`. |

### Candidate 4: the defect and the fix

`Transaction.move` appends `None` to the source's versions and the content
hash to the destination's versions, then persists the journal and calls
`rename_noreplace`. On `FileExistsError` (a competitor created the
destination first; a no-replace rename guarantees nothing was mutated), the
code undid its appends **only if the destination had not been watched
before**. When the caller had watched the destination first (with
`watch_file` or `SafeOrgWorkspace.load`), the journal still owned it with
versions `[None, content]`. The competitor's hash is in neither, so
`recover` retained the journal, and it kept retaining it on every later
`@serialized` call, in every space, until someone repaired it by hand. The
original `FileExistsError` was also masked by `RecoveryRequired`.

Fix (`org_transaction.py`, the `except FileExistsError` branch): always pop
both appended versions, drop the destination entry if `move` itself watched
it, then persist. If the destination had already been written by this
transaction, the competitor's file really does sit on an owned path. That
case still retains the journal, by design, because `race_fixed_recovers`
assumes the destination is not owned.

Production exposure was latent. The only production caller of `move_file`
is `archive_files.archive_file`, and it does not watch the destination, so
it took the branch that already worked. Any future caller that watches
before it moves would have hit the stuck lock.

Mutation checks (scratch copies in `scratchpad/org-transaction/`). Each
mutant file also proves a concrete counterexample that has no `sorryAx`:

- m1: the race handler does not undo the destination's append. Result:
  `race_fixed_recovers` stops proving, and `mutant_m1_counterexample` (a
  reachable state where `recover` retains the journal) proves.
- m2: the journal is persisted after the effect instead of before. Result:
  `inv_step` and `crash_recovers_pre_state` stop proving, and
  `mutant_m2_counterexample` (a reachable crash point that recovers to a
  modified filesystem) proves.
- m3: `recover` restores without its check loop. Result: `recover_dichotomy`
  stops proving, and `mutant_m3_counterexample` (`before` is written over a
  foreign hash) proves.

### Candidate 3: why this is NEEDS-OWNER

`write()` calls `watch()`, and on first touch `watch()` reads the file at
that moment. The check `digest(read_text(path)) != entry["current"]` then
compares the file with itself. The protection is real only when the caller
watched the path before reading it. In-tree callers mostly do:
`SafeOrgWorkspace.load` watches before it reads, and so do
`registry_gc._apply_serialized`, `credential_store`, `writeback_store`,
`inbox_cleanup`, `ledger_project_org`, `ledger_phase1_flip`, and
`gtd_decision_board`. The exception is blind first writes such as
`ledger_project_org.py:127` (the header copy, guarded only by
`not copy.exists()`). Making `write()` refuse an existing, unwatched path
would enforce the contract, but it changes the API for every caller, and
existing tests write without watching (for example
`test_change_receipts_exclude_reads_noops_and_reverted_writes`).

**Question for the owner:** should `write_org_text` refuse to overwrite an
existing file that was not watched in the same transaction (a strict
read-before-write contract), or should the vacuous first-touch check be
documented as the caller's responsibility?

### Candidate 5: tools that bypass the transaction (NEEDS-OWNER)

Each of these writes with a plain `Path.write_text` (truncate, then write;
not atomic), with no `file_lock` and no journal:

| Tool | Write | How it runs |
|---|---|---|
| `org_date_hook.py:45` | `p.write_text` | **Live PostToolUse hook** (`~/.claude/settings.json:67`), after every Edit/Write on .org/.md |
| `triage_utils.py:144,201` | `org_file.write_text` | Imported by `modules/mail/lib/task_creator.py` and `modules/github/lib/task_creator.py` (automated triage) |
| `validate_org_dates.py:200` | `--fix` | Manual; the pre-commit hook suggests it |
| `org_dedup_within_file.py:177`, `org_resolve_id_conflicts.py:112`, `inbox_dedup.py:171`, `stamp_seq_todo.py:60`, `org_union_merge.py:187` (`--apply`) | `write_text` | Manual repair tools (none found wired into cron or git merge drivers; `.gitattributes` has no org merge driver) |

Evidence of whether it matters:

1. **A lost update is possible against a committed adapter transaction.**
   Replay `scratchpad/org-transaction/replay_bypass.py`: `org_date_hook.fix_dates`
   reads the file, a serialized adapter call appends a task and commits, and
   then the hook writes back its fixed copy of the stale read. Output:
   `adapter commit lost: True`. `org_transaction` cannot see or prevent this.
   The window is the few milliseconds of a read-modify-write, and it needs an
   adapter call (the hourly Phase-1 cycle, nightshift, or the app) running at
   the same moment.
2. **Safe, but it blocks.** A bypass write to a file an adapter transaction
   has already written, followed by a failure of that transaction, makes
   `recover` see a foreign hash. The journal is retained (proved safe by
   `recover_dichotomy`), and every serialized call raises until someone
   repairs it by hand. This is the same global stuck lock as candidate 4, but
   caused by an uncooperative writer.
3. **Refused, correctly.** A bypass write to a file a transaction has watched
   but not yet written is caught by the stale check, and the adapter's write
   fails.
4. A plain `write_text` is not atomic. A crash, or a concurrent adapter read
   in the middle of a write, can see a truncated file. The adapter's shrink
   guard catches large shrinks only.

**Question for the owner:** should the live and automated writers
(`org_date_hook`, `triage_utils`) route through `@serialized` +
`watch_file`/`write_org_text`, or at least take the same `file_lock` and use
`atomic_write_text`, with the manual repair tools following later? Or is the
millisecond lost-update window acceptable for them?

## Residuals (not fixed, documented)

- The gap between `recover`'s check loop and its restore loop (see Model).
- The same stuck-lock shape applies to any failed effect that did not mutate
  (for example `atomic_write_text` failing before its `os.replace`) and is then
  followed by an external edit before the in-process `recover` runs. The
  window is microseconds, and the result is safe (the journal is retained).

## Files changed

- `.datacore/lib/org_transaction.py`: the `move` `FileExistsError` branch
  (candidate 4).
- New: `.datacore/lib/tests/test_org_transaction_formal.py` (10 tests).
- New: `DatacoreSpec/OrgTransaction.lean`, and this file.

## Tests

`cd .datacore/lib && python3 -m pytest -q tests/test_org_transaction_formal.py tests/test_org_transactions.py tests/test_closed_archive_safety.py`
gives **48 passed**. Before the fix, the new file had 2 failures, both from
candidate 4. The existing 38 tests passed before and after the fix.

Decision G7 applied (2026-09-23, area gtd-a): candidate 3 is documented as the
caller's responsibility, not enforced. `write_org_text` now carries a docstring
saying callers must `watch_file` (or load through `SafeOrgWorkspace.load`)
before reading or deciding to overwrite a path. The blind caller,
`ledger_project_org._with_org_header` (the header copy), now watches the copy
before its `exists()` check. Test: `lib/tests/test_decisions_gtd_a.py::
test_header_copy_first_write_refuses_a_racing_writer`. See findings/gtd-state.md.

## Decision G8 applied (2026-09-23)

Candidate 5, live writers: `org_date_hook.fix_dates` and
`triage_utils._set_task_properties` / `_append_task_body` now re-read and
write inside `org_transaction.serialized` with `watch_file` +
`write_org_text`, as `inbox_cleanup` does. The hook takes the lock only when
its unlocked first read finds something to fix, waits at most
`LOCK_TIMEOUT` (2 s), and on a busy lock or any transaction error skips with
one stderr line and exit 0. `serialized` has no timeout parameter, so the hook
caps it by wrapping `org_transaction.file_lock` for the one call
(`_lock_timeout`); a `timeout=` argument on `serialized` would remove that
wrapper (question for the owner of `org_transaction.py`).

Re-replay of `scratchpad/org-transaction/replay_bypass.py` against the new
hook: `hook fixes: 1 | journal: False`, file
`'* TODO Call back\nSCHEDULED: <2026-09-24 Thu>\n* TODO Added by adapter\n'`,
**`adapter commit lost: False`** (was `True`). Pinned by
`test_hook_does_not_lose_an_adapter_commit`. Lean:
`Dates.HookLock.locked_no_lost_update` (every interleaving ends `f (g s0)` or
`g (f s0)`), `unlocked_loses_adapter_commit` (the old schedule). Mutation (the
adapter ignores the lock): `inv_step` stops proving and a lost-update
counterexample proves. The manual repair tools remain unlocked, per the
decision ("manual tools later").

## Decision Q12 applied (2026-09-23, follow-up board)

**(a) `serialized(timeout=)`.** `org_transaction.serialized` now takes a
keyword `timeout` (default `DEFAULT_LOCK_TIMEOUT = 30`, the old value), usable
as `@serialized`, `serialized(fn)` or `@serialized(timeout=2)`. A nested call
joins the running transaction and never waits. `org_date_hook` now uses
`@org_transaction.serialized(timeout=LOCK_TIMEOUT)`; its `_lock_timeout`
wrapper, which swapped `org_transaction.file_lock` for one call, is gone.

**(b) The manual repair tools write under the lock.** Each of these now
watches its target, reads it and writes it with `write_org_text` inside one
`serialized` call, as `inbox_cleanup` does. Dry runs and checks take no lock.

| Tool | Transaction |
|---|---|
| `org_union_merge --apply` | `_run`: the working-tree file is watched before the refs are read |
| `org_dedup_within_file --apply` | `_dedup`, one per file; the ledger dismissal (G9) runs inside the same transaction, as adapter commands do |
| `org_resolve_id_conflicts` (not `--check`) | `_resolve`, one per file; G10 dismissals inside |
| `inbox_dedup --apply` | `_run`: the inbox is watched; the destinations are only read |
| `stamp_seq_todo` (not `--dry-run`) | `_stamp`, one short transaction per file |
| `validate_org_dates --fix` | `_fix_file`, one per file; `org_transaction` is imported only on `--fix`, so the pre-commit check never needs it |

The `.bak` / `.bak-dedup` backup copies are unchanged (they are not org
files and are not journalled). What `org_union_merge --apply` overwrites by
design is unchanged: the file becomes the union of the two refs, whatever
uncommitted content the working tree had (a commit that lands during the run
now waits for the lock and is then applied on top; see below).

**Lost-update replay** (the candidate-5 pattern, made concurrent: the adapter
commit runs in its own thread and context, so it contends for the real lock
instead of joining the tool's transaction). Script
`scratchpad/org-followups/replay_tools.py <old copies>`:

```
old org_dedup_within_file: '* TODO buy milk\nbody\n' -> adapter commit lost: True
old stamp_seq_todo: '#+TITLE: x\n#+SEQ_TODO: ...\n* TODO a\n' -> adapter commit lost: True
old validate_org_dates --fix: '* TODO a\nSCHEDULED: <2026-09-24 Thu>\n' -> adapter commit lost: True
new org_dedup_within_file: '* TODO buy milk\nbody\n* TODO Added by adapter\n' -> adapter commit lost: False
new stamp_seq_todo: '...* TODO a\n* TODO Added by adapter\n' -> adapter commit lost: False
new validate_org_dates --fix: '...<2026-09-24 Thu>\n* TODO Added by adapter\n' -> adapter commit lost: False
journal left: False
```

Pinned for all six tools by `lib/tests/test_followups_org.py::
test_*_does_not_lose_a_racing_adapter_commit` (each failed on the old code
with the adapter line missing). Lean: `Dates.HookLock.repair_tool_no_lost_update`
(the G8 lock model with `f` = the tool's rewrite); the G8 mutation (adapter
ignores the lock) breaks `inv_step`, on which it rests. The timeout is noted
as out of model in `OrgTransaction.lean`.

**Direct org writes left in `lib/`** (grep of `write_text`, `open(..., 'w'|'a')`,
`os.replace` in non-test files that mention org files; none are in the files
this change owns, so none were changed):

| File | Write | Kind |
|---|---|---|
| `org_date_validator.py:100` | `filepath.write_text` (fix mode) | manual/validator on live org files |
| `org_conformance.py:215` | `f.write_text` (`--mode fix`) | manual, live org files |
| `org_tag_normalize.py:109` | `path.write_text` (`--apply`) | manual; the space pre-commit hook suggests it |
| `migrate_org_tag.py:99` | `path.write_text` | one-off migration |
| `state_loop_rollout.py:142` | `fp.write_text` (execute) | one-off rollout |
| `task_surface.py:119` | `path.write_text` (`--apply`, `:SURFACE:` properties) | manual, live org files |
| `tag_validator.py:417` | `open(file_path, 'w')` (fix; `*.org` and notes `*.md`) | manual |
| `org_parser.py:854` | `open(target_path, 'a')` archive append (own `fcntl` lock on the file, not the org transaction lock) | library, archive path |
| `sprint_sync.py:146` | `qfile.write_text` creates a missing `nightshift.org` | only when absent |
| `review_sweep_one_time.py:238`, `repair_nightshift_org.py:84`, `restore_ledger_ids.py:114`, `restore_research.py:88`, `recover_org_from_index.py:98` | `write_text` | one-off repair/recovery scripts |
| `ledger/projection_state.py:69`, `ledger_checkpoint.py:296,334`, `ledger_roundtrip_diff.py:29`, `phase1_drill.py`, `ledger_chaos_drill.py` | scratch/tmp copies or drills | not live files |

Question for the owner: which of the first seven (live org files, manual)
should follow the same pattern?
