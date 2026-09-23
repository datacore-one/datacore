# Findings: Reconcile cluster (2026-09-23)

Survey: `survey/sync-git-publication.md` items 5 (gh_reconcile) and 10 (sync/conflict.py).
Model: `DatacoreSpec/Reconcile.lean` (namespace `DatacoreSpec.Reconcile`). It checks
cleanly with `lake env lean` and uses no `sorry`, `axiom` or `native_decide`.
Replays: `scratchpad/reconcile/replay_gh.py` runs the real `reconcile_file` on a tmp
org file, with `subprocess.run` stubbed to act as `gh api` (it applies the caller's
`--jq` keys to a fixture table). No network was used.

Claim under test (gh_reconcile.py docstring): "Only transitions to DONE when state is
provably terminal … Never a false DONE."

| # | Candidate | Verdict |
|---|-----------|---------|
| 5a | A failed lookup (`None`) is skipped, so a merged PR plus an unreachable ref closes the task | CONFIRMED+FIXED |
| 5b | The archive check matches by basename only (lines 514, 518) | DOWNGRADED |
| 5c | An issue closed "not planned" becomes DONE | CONFIRMED+FIXED (becomes CANCELLED, per DIP-0009) |
| 5d | Read-modify-write with no lock and no atomic replace | CONFIRMED+FIXED |
| 5e | (new) Path B closes a task through the archived output even when its tracked PR is open | CONFIRMED+NEEDS-OWNER |
| 10 | The two-way conflict detector ignores `last_sync`, so ORG_WINS reopens an issue a human closed | CONFIRMED (dormant)+NEEDS-OWNER |

## 5a: an unknown ref no longer counts as proof

- **Old behaviour.** In `reconcile_file`, `if result is None: continue` drops the ref
  entirely. A task with refs {merged PR, issue whose lookup failed} therefore closed
  DONE, even though the issue may still be open.
- **Lean.** `old_false_done_unknown` is a concrete refutation. It uses the oracle
  `cexLookUnknown`, which `cexLookUnknown_sound` proves truthful. The fixed code is
  covered by `new_done_sound`: every Path A DONE implies every ref is truly done, for
  every sound oracle. `new_blocks_unknown` covers the concrete case, and
  `new_closes_when_proven` shows the model is not vacuous.
- **Replay before the fix:** `R1_unknown_ref: closed=1 headings=['* DONE Ship']`.
  After the fix: `closed=0 headings=['* TODO Ship']`.
- **Fix.** Failed lookups are collected in `unknown_refs`. If there are terminal refs
  alongside any open or unknown ref, the task is skipped, exactly as it already was for
  terminal plus open.
- **Mutation check (M1).** Unknown was made non-blocking, and the close kind was taken
  over the terminal refs only, as the old code did. `new_done_sound` and
  `new_cancelled_sound` stop proving, `new_blocks_unknown` is refuted by `decide`, and
  `newDecide cexLookUnknown [0,1] = .done ∧ cexTruth 1 ≠ .done` now holds.

## 5b: archive match by basename (DOWNGRADED)

Output names come from `modules/nightshift/lib/output.py:212-214`
(`generate_exec_id`, "Independent identity even for simultaneous executions on
several hosts"). The name is `nightshift-exec-YYYY-MM-DD-<uuid4 hex>-…md`, so the
basename is a unique identity. Legacy names carry a timestamp to the second plus type
and space, so they collide only if the same type in the same space ran in the same
second. Basename matching is how the check works across hosts: the stored path is
often another host's path. There was no fix. In the model, the archive check is an
opaque Bool.

## 5c: not planned becomes CANCELLED

- **DIP-0009.** CANCELLED means "Will not do", with `:CANCEL_REASON:`. Its ledger
  dismissal has kind `dropped`, "so cancelled work must not be allowed to inflate
  completion stats". `ledger_ingest_org.TERMINAL_KINDS` already maps
  CANCELLED→dropped, so Phase-1 spaces need no change.
- **Old behaviour.** The jq filter did not even fetch `state_reason`, so every closed
  issue became DONE.
- **Replay before the fix:** `R3_not_planned: closed=1 headings=['* DONE Do the thing']`.
  After the fix: `['* CANCELLED Do the thing']`, with `:CANCEL_REASON:`.
- **Fix.** The jq filter now fetches `state_reason`, and `_issue_result` maps it:
  - `completed` or `null` (older issues) → DONE;
  - `not_planned` → CANCELLED;
  - `duplicate` → not terminal, so the task is not closed.

  If a task's refs mix DONE and CANCELLED, it is left for a human.
  `mark_task_done(..., state=)` writes the keyword and adds `:CANCEL_REASON:` for
  CANCELLED.
- **Lean.** `old_false_done_not_planned` is the refutation. `new_not_planned_cancelled`,
  `new_cancelled_sound` (CANCELLED ⇒ a non-empty ref list, all truly dropped) and
  `new_done_sound` cover the fixed code.
- **Mutation check (M2).** `isDone` was made to accept dropped. `isDone_sound` fails, and
  `new_not_planned_cancelled` is refuted by `decide`.

## 5d: lost update

- **Old behaviour.** `content = read_text()`, then minutes of `gh api` calls, then
  `org_file.write_text(...)`: a plain truncate-and-write, with no lock and no check.
- **Replay before the fix.** A stub appends `* TODO captured by human mid-run` during the
  lookup. Result: `R4 human line survives: False`. After the fix: `closed=0`, the human
  line survives, and a warning is logged. The next run closes the task.
- **Fix.** New `_write_if_unchanged`, decorated with `@serialized` from
  `org_transaction`, the house Org writer: one lock, a durable undo journal and an
  atomic replace. It writes only if `watch_file(org_file)["before"]` still equals the
  text the decisions were made on. `write_org_text` then re-checks the digest just
  before `os.replace` and raises `RecoveryRequired` if the file changed; that is
  caught, and the file is not written. The GitHub lookups stay outside the lock, so
  the lock is never held for minutes.
- **Lean.** `cas_preserves_concurrent_edit`, `cas_applies_when_unchanged`, and the
  refutation `old_write_loses_edit`.
- **Mutation check (M3).** With `casWrite := f s0`, `cas_preserves_concurrent_edit`
  fails.
- **Residual.** Emacs and other editors that do not take the lock are protected only
  by the digest check. The window between check and replace is microseconds, not
  minutes.

## 5e: Path B ignores open refs (NEEDS-OWNER)

- **Behaviour.** When no ref answered terminal (every ref is open or unknown), control
  falls through to Path B. An archived NIGHTSHIFT_OUTPUT then closes the task DONE.
- **Replay:** `R2_open_ref_archived_output: closed=1 headings=['* DONE Review PR']`,
  with PR 3 open. This is the same before and after the fix; it was deliberately left
  unchanged.
- **Lean.** `pathB_closes_over_open_ref` records it for both the old and the new code.
- **Why it was not fixed.** `router.py:338-345` says the archive is "the
  artifact-landed signal" for Review tasks, and PR_URL is only set "additionally". So
  archiving may be intended to override an open PR. That is a policy question.

## 10: sync/conflict.py uses two-way detection (NEEDS-OWNER, dormant)

- **Code.** `ConflictDetector.detect(org, ext, last_sync)` never reads `last_sync`.
  Every field that differs is reported as a conflict. The default strategy for STATE
  is ORG_WINS.
- **Replay (in-memory).** Org is TODO and unchanged since the last sync; the external
  issue is closed by a human. The detector reports `conflict fields: ['state']`, the
  resolver gives `external_changes: {'state': 'TODO'}`, and `needs_review: False`. The
  resolver would reopen the issue. This is dormant, because `SyncEngine.sync` is a stub
  (engine.py:318-332) and nothing calls `detect` in production.
- **Spec (Lean).** `resolve3 st base org ext` works as follows:
  - if org = ext, take that value;
  - else if org = base, take ext;
  - else if ext = base, take org;
  - otherwise it is a true conflict, and only then does the strategy apply.

  `resolve3_respects_one_sided` proves that no strategy ever overrides a change made on
  only one side. `resolve3_strategy_only_on_conflict` proves the strategy matters only
  when both sides changed and they disagree. `resolve2_reopens_human_close` refutes
  the current two-way rule.
- **Mutation check (M4).** Dropping the two one-sided branches makes
  `resolve3_respects_one_sided` fail.
- **Why it was not fixed.** The data model has no base. `OrgTask` and `ExternalTask`
  carry no last-synced per-field snapshot, and `sync/history.py` stores only sync
  timestamps. Timestamps can show that the external side changed
  (`updated_at > last_sync`), but nothing records whether the org side changed.
  Adding a base changes the on-disk format of the sync state DB.
- **Executable spec:** `tests/test_reconcile_formal.py::test_a_close_made_only_on_github_is_not_overridden`
  (xfail, strict).

## Files changed

- `.datacore/lib/gh_reconcile.py`: unknown refs block closing; `state_reason` is
  fetched and classified; CANCELLED path with `:CANCEL_REASON:`; serialized
  compare-and-swap write; docstring updated to the proven policy.
- `.datacore/lib/tests/test_reconcile_formal.py` (new): 9 tests plus 1 strict xfail.
- `.datacore/specs/datacore-lean/DatacoreSpec/Reconcile.lean` (new).
- `.datacore/specs/datacore-lean/findings/reconcile.md` (this file).
- `sync/conflict.py`: unchanged (NEEDS-OWNER).

## Tests

`cd .datacore/lib && python3 -m pytest -q tests/test_reconcile_formal.py tests/test_gh_reconcile_heading.py sync/tests/test_conflict.py`
→ **34 passed, 1 xfailed**. Before the fix, 5 of the new tests failed.

## Owner questions

1. Should an archived nightshift output close a Review task whose tracked PR or issue
   is still open, or whose lookup failed (5e)? Today it does.
2. An issue closed as a duplicate now leaves the task open, where before it was marked
   DONE. Should a duplicate instead become CANCELLED, or should the task follow the
   canonical issue?
3. Should sync conflict detection gain a per-task base snapshot, stored at each
   successful sync, so the three-way rule `resolve3` can be wired in? Or should the
   detector be removed until `SyncEngine.sync` is implemented?

## Decisions applied (2026-09-23)

**Decision P5 applied: require refs terminal too (was 5e).** An archived
NIGHTSHIFT_OUTPUT now closes a task only if none of its tracked refs is open or
unknown. Once Path A has not decided, that means the task has no GitHub ref.
A Review task whose PR is open, or whose lookup failed, stays open.
- Lean: `newDecide` gains the branch `refs.any isBlk → skip` before Path B.
  - `new_done_sound_all`: every DONE, from either path, leaves no ref that is
    not truly done. The old `hterm` hypothesis is gone.
  - `pathB_open_ref_kept_open` replaces `pathB_closes_over_open_ref`, and
    `preP5Decide` keeps the old rule as the counterexample.
  - `pathB_closes_without_refs` shows the model is not vacuous.
- Mutation M5 (drop the branch) breaks `new_done_sound_all` and
  `pathB_open_ref_kept_open`.
- Tests: `test_p5_archived_output_does_not_close_over_an_open_pr` and
  `..._over_an_unknown_ref` failed before the change.
  `test_p5_archived_output_still_closes_a_task_with_no_refs` and
  `test_p5_merged_pr_with_archived_output_closes` pass.
- Note for the nightshift module (not this cluster's file):
  `modules/nightshift/lib/router.py:338-345` calls the archive "the
  artifact-landed signal" for Review tasks. Since P5, a Review task with a
  tracked PR closes when the PR merges.

**Decision P6 applied: CANCELLED with reason.** In `_ISSUE_CLOSE_AS`,
`duplicate` now maps to CANCELLED. `:CANCEL_REASON:` reads
`Issue <owner>/<repo>#<n> closed as a duplicate`, which names the issue.
Duplicate refs mixed with done refs are still left for a human.
- Lean: `dropped` now also covers closed-as-duplicate. `new_cancelled_sound`
  is unchanged and still proved.
- Test changed: `test_reconcile_formal.py::test_a_duplicate_close_is_not_proof_of_either`
  became `test_a_duplicate_close_cancels`, because the decision reverses it.
- Tests added: `test_p6_an_issue_closed_as_duplicate_cancels_the_task` and
  `test_p6_duplicate_and_merged_together_are_left_for_a_human`.

**Decision P7 applied: removed until the engine exists.** Nothing live used
ConflictDetector or ConflictResolver. The only references outside tests were
`SyncEngine.detect_conflicts` / `resolve_conflict`, which nothing calls, since
`SyncEngine.sync` is a stub. Both were removed:
- `ConflictDetector`, `ConflictResolver` and their result type
  `ConflictResolution` from `sync/conflict.py`;
- the same names from `sync/__init__.py`;
- `detect_conflicts` / `resolve_conflict` and the two attributes from
  `sync/engine.py`.

What stays:
- `ConflictQueue` and its CLI, and `Conflict` / `ConflictField` /
  `ConflictType` / `ConflictStrategy`;
- `load_conflict_config`, which the engine still calls to validate the config.

The module docstring gives the `resolve3` rule as the specification. In
`Reconcile.lean`, Part 3 keeps `resolve3` with its theorems, noted as the
spec for when the engine is built. `resolve2` is kept as the removed
baseline.

Tests:
- Removed: the strict xfail `test_a_close_made_only_on_github_is_not_overridden`,
  and in `sync/tests/test_conflict.py` the classes `TestConflictDetector`,
  `TestConflictResolver` and `TestConflictIntegration`.
- Rewritten: `tests/test_conflict_preservation.py`. It now builds `Conflict`
  directly, so the queue round-trip checks are kept and the resolver asserts
  are dropped.
- Added: `test_p7_two_way_detector_and_resolver_are_removed` and
  `test_p7_engine_still_loads_and_reports`.

Still out of date, and out of bounds for this change: DIP-0010 (lines
605-617, 904, 941) still lists ConflictDetector/Resolver as done.

**Decision Q9 applied: look up the canonical issue (second board, 2026-09-23).**
For an issue closed with `state_reason == "duplicate"`, `_issue_result` calls
the new `canonical_of_duplicate(ref)`.
- The lookup is one `gh api graphql` call: the same CLI and auth as the REST
  lookups. It reads the issue's last `MarkedAsDuplicateEvent` /
  `UnmarkedAsDuplicateEvent` timeline item and returns `canonical` as
  `owner/repo#n`.
- It is cached per `owner/repo/num` in `_duplicate_cache`, like `_api_cache`.
- With a canonical issue, `:CANCEL_REASON:` reads `Issue o/r#4 closed as a
  duplicate of o/other#9`.
- If there is no event, the last event was an unmark, the call fails, or the
  output is malformed, it falls back to the P6 wording, which names the
  duplicate.
- The lookup only words the reason. It never changes a verdict.

Lean: `cancelNames`, `q9_names_canonical`, `q9_fallback_names_duplicate`, and
`decideWithReason` with `q9_verdict_independent` (so `new_cancelled_sound` and
`new_done_sound_all` still apply unchanged). Mutation M-Q9 (always name the
duplicate) breaks `q9_names_canonical`.

Tests (`lib/tests/test_followups_sync.py`, API stubbed):
- `test_q9_the_reason_names_the_canonical_issue`;
- `test_q9_a_failed_lookup_names_the_duplicate`;
- `test_q9_no_event_names_the_duplicate`;
- `test_q9_one_lookup_per_duplicate_and_cached`;
- `test_q9_no_lookup_for_other_close_reasons`.

The existing P6 tests in `test_decisions_sync.py` pass unchanged. Their stub
does not answer graphql, so they exercise the fallback.

**Decision Q15 applied: comment fixed.** The comment at
`modules/nightshift/lib/router.py:338-343` now says that an archived output
closes the Review task only when none of its tracked GitHub refs is open or
unknown (P5), and that with a PR tracked, the task closes when the PR merges.
This is a comment-only change.
