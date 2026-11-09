# NightshiftLifecycle — findings (2026-09-23)

Survey: `survey/nightshift.md` items 1, 2, 6. Model: `DatacoreSpec/NightshiftLifecycle.lean`
(412 lines, checks cleanly with `lake env lean`; no sorry/admit/axiom/native_decide).

**Repository.** `.datacore/modules/nightshift` is its OWN git repo
(`git -C .datacore/modules/nightshift rev-parse --show-toplevel` →
`~/Data/.datacore/modules/nightshift`). The Python fixes below are
uncommitted changes in that repo, not in the root Datacore repo.

Tests: `tests/test_lifecycle_formal.py` (15 cases), run the module's way
(`tests/conftest.py` puts `lib/` on the path):
`cd .datacore/modules/nightshift && python3 -m pytest -q tests/test_lifecycle_formal.py` → 15 passed.
The whole module suite after the fixes (`python3 -m pytest -q tests lib/tests`, which
includes other agents' concurrent edits to `lib/evaluate.py`): exit 0, 813 passed, 1 skipped.
Before the fixes, 9 of the 15 failed (the 6 that passed were the "still works" controls
and the retry-bound safety cases).

## What the model is

A task is `(org state, NIGHTSHIFT_ATTEMPTS, fence, NIGHTSHIFT_STATUS, output?, live
claim?, NIGHTSHIFT_REQUEUES, queue entry?, generated space?)` plus a ghost counter
`execs` of executions that did work (quota refusals excluded — deferred by design).
The claim stamps are one bit `live` = STARTED > COMPLETED, the only way the queue or
GC reads them; the GC's 6-hour rule is an oracle. One night = `gc`, then `admitted`,
then one exit path of `run_task_mode` (claim refused / pre-hook abort / executed with
outcome {success, known-fail, unknown, quota} × post-check {ok, handoff, write-fail} ×
evaluator decision). Every function takes `fixed : Bool` (code as found vs. after fix);
`fixed = true` theorems hold for every sequence of nights and every oracle.

## Verdicts

| # | Candidate | Verdict | Lean |
|---|---|---|---|
| 1c | GC requeues the second-failure escalation (empty OUTPUT matches "stalled") | CONFIRMED+FIXED | `gc_resurrects_escalation` (as found: 4 runs) / `escalated_review_not_stalled` |
| 1d | Queue entry re-admits a REVIEW/WAITING source under the entry's NEXT, unboundedly | CONFIRMED+FIXED | `queue_entry_unbounded`, `queue_entry_unbounded_general` (n+4 runs for every n) / `review_source_not_admitted` |
| (a)+(b) | Liveness and bounded executions | PROVED after fixes (REFUTED as found) | `executions_bounded` (≤ 2 runs, any outcomes), `twice_run_is_dead`, `dead_never_admitted` |
| 1b | NIGHTSHIFT_ATTEMPTS never resets, so not "consecutive" | CONFIRMED+FIXED | `not_consecutive_escalated_as_found` / `attempts_reset_on_non_failure`, `consecutive_after_fix` |
| 2 | Claim not released on claim-refused / pre-hook-abort paths | CONFIRMED+FIXED | `claim_refused_stays_live`, `refused_claims_cap_a_task_that_never_ran` / `claim_released_on_every_exit`, `refused_claims_do_not_cost_requeues` |
| 6a | Retries honour max_retries | REFUTED (the safety claim holds) | `retries_bounded`: retries ≤ min(1, max_retries) |
| 6b | Quota on the retry recorded as failed, not deferred | CONFIRMED+FIXED | `quota_on_retry_failed_as_found` / `quota_on_retry_defers` |
| 1a | Fenced-NEXT zombies (unknown / post-success failure) | CONFIRMED+NEEDS-OWNER | `dead` (fence-blocked is a terminal class in the proofs) |
| 1e | No supported re-run once a failure is fenced | CONFIRMED+NEEDS-OWNER | — |
| 6c | max_retries > 1 never gives more than one retry; RETRIES never persisted | DOWNGRADED to NEEDS-OWNER (policy) | `retries_bounded` |

### 1c — the GC undid every escalation (FIXED)

`complete_task` escalates the second failure to REVIEW writing `NIGHTSHIFT_STATUS:
failed` and `NIGHTSHIFT_OUTPUT: ''`. `_is_stalled` read "REVIEW without output" as
stalled and requeued it to NEXT (`REQUEUES+1`), up to MAX_REQUEUES=2.
*Replay* (`scratchpad/nightshift-lifecycle/replay.py`, real `gc_stalled` /
`build_queue` / `complete_task` in a tmp dir, a task that always fails, 8 nights):
`direct-pending: executions over 8 nights = 4`, states NEXT, REVIEW, REVIEW(requeued), ….
*Fix* (`lib/requeue_stalled.py`): REVIEW with STATUS `failed` is an escalation, not a
stall. A REVIEW nobody ran (no status) is still requeued — pinned by
`test_review_without_any_run_is_still_stalled`.

### 1d — a queue entry re-ran an escalated task every night (FIXED)

`resolve_queued_task` returns the SOURCE task with the ENTRY's state (NEXT). A source in
REVIEW whose fence is `not-started` — exactly the second-failure escalation — passed
`delegation_status` and ran again. sprint_sync keeps entries for REVIEW sources (it only
drops DONE/DEFERRED/CANCELLED), so this is the normal fleet shape.
*Replay:* `queue-entry: executions over 8 nights = 8` (attempts 1…8).
*Intent:* commit da15cd7 and the comment in build_queue — a REVIEW/WAITING source "is not
blocked work: a person is already on it".
*Fix* (`lib/task_queue.py` build_queue): a resolved entry whose `SOURCE_STATE` is REVIEW
or WAITING is turned away under `review: source is <state>, awaiting a person` (the key
the wedge verdict already ignores), fenced or not. The previous in-gate `review:` branch
became unreachable and was folded in.

### (a)+(b) — liveness and bounded executions (PROVED after fixes)

Invariant `Inv`: `execs ≤ 2 ∧ (¬dead → execs = attempts ≤ 1)`, where `dead` = fence
blocked ∨ (REVIEW ∧ STATUS failed). Preserved by `gc` (`gc_preserves`), by admission
(`admitted_not_dead`) and by every exit path (`runOne_preserves`). Hence
`executions_bounded`: from a fresh task, for every list of nights, `execs ≤ 2`; and
`twice_run_is_dead` + `dead_never_admitted`: after two runs the task is escalated or
fenced and never admitted again until a person acts. Human visibility of `dead`: REVIEW is
a state; a fenced task is listed by `gc_stalled` as awaiting review, by
`reconcile_attempt --list`, and counted in the run's "NO TASKS RAN" verdict.
Test: `test_a_task_that_always_fails_runs_at_most_twice[False|True]` (was 4 and 8).

### 1b — "consecutive" (FIXED)

Nothing reset NIGHTSHIFT_ATTEMPTS. fail → reviewed success → person reopens → one
failure escalated to REVIEW as if it were the second in a row.
*Replay:* `test_a_success_between_two_failures_does_not_escalate` failed with
`assert '1' == '0'`. *Fix* (`lib/claim.py` complete_task): a non-failed completion
writes `NIGHTSHIFT_ATTEMPTS: 0`, only over an existing non-zero count.

### 2 — claims not released (FIXED)

`claim_task` writes STARTED before any refusal can return False; `run.py` then
`continue`d, as it did after a pre-hook abort. The task read as in flight (skipped by
`build_queue`), the GC requeued it after 6 h with REQUEUES+1, and after two such nights it
sat at the cap with a live claim: never admitted, never GC'd — a task that never ran
(`refused_claims_cap_a_task_that_never_ran`).
*Replay:* `test_a_refused_claim_is_released` (real `claim_task`, gitignored file, ledger
receipt refused) and `test_a_pre_hook_abort_releases_the_claim` both failed on `_released`.
*Intent:* the quota path's own comment, "Stamping COMPLETED releases the claim".
*Fix:* new `claim.release_claim(task)` stamps COMPLETED (no state/status/attempt change);
`run.py` calls it on both paths (the pre-hook path also publishes the release, since its
claim was published). Failures are recorded in `run_errors`, never raised — the pre-hook
block's broad `except` would otherwise have fallen through to execution. The claim
function itself is untouched, so `test_rejected_publish_preserves…` (byte-exact file after
a refused publish) still holds.

### 6 — in-run retry

*6a (REFUTED — safety holds):* a single `if` guards the retry with
`NIGHTSHIFT_RETRIES < max_retries`; `retries_bounded` proves retries ≤ min(1, max_retries).
Test `test_in_run_retries_never_exceed_max_retries` (0→1 call, 1/2/5→2 calls).
*6b (FIXED):* the quota check ran only on the FIRST result. A transient failure whose
retry hit the quota was recorded as a failed attempt (counted toward escalation) — the
outcome the deferral exists to prevent. *Replay:*
`test_quota_on_the_retry_defers_like_quota_on_the_first_attempt` failed with the task in
`failed`. *Fix* (`run.py`): the retry now runs before the quota check, which applies to
whichever result the run ends with; the two identical failure blocks became one.

## NEEDS-OWNER

1. **Fenced-NEXT zombies (1a/1e).** A timeout/exception (`unknown:` fence) or a
   post-success failure (background hand-off, output write failure: fence `completed`)
   leaves the task NEXT with attempts=1 behind a fence forever. It never reaches the
   "second consecutive failure", and `reconcile_attempt --to not-started` refuses it
   ("execution RAN before this failure"); `--to completed` keeps it blocked. Replayed
   (`scratchpad/nightshift-lifecycle/replay_fence.py`): both cases → state NEXT, queue
   `[]`, `reconcile … rc = 1`. The hand-off branch's comment says "fail loudly and
   requeue-eligibly", which the `completed` fence makes impossible.
   *Proposal:* in `complete_task`, when `status == 'failed'` and
   `task_attempt.blocked(task)` is true, escalate straight to REVIEW (a fenced failure
   cannot be retried automatically, so NEXT only disguises it as live work); and give
   `reconcile_attempt` an explicit `--reopen` (fence → not-started, ATTEMPTS → 0, state →
   NEXT) that requires `--why` naming the effects checked. *Question:* should a failure
   that leaves the attempt fence closed escalate to REVIEW at once, and should a hand-off
   finish its fence as `not-started` (no deliverable) rather than `completed`?
2. **Lifetime requeue cap.** NIGHTSHIFT_REQUEUES is never reset, so two GC rescues in a
   task's whole life cap it. *Question:* should a successful run (or a person's reopen)
   reset NIGHTSHIFT_REQUEUES as it now resets NIGHTSHIFT_ATTEMPTS?
3. **max_retries semantics (6c).** Docs say "Maximum revision attempts before human
   review, default 2"; the code gives at most one in-run retry whatever the value, keeps
   `NIGHTSHIFT_RETRIES` only on the run's in-memory copy, and escalates at a hard-coded 2
   attempts. *Question:* is `max_retries` meant to be the escalation threshold (replace the
   literal 2 in `complete_task`), the in-run retry count (loop up to it), or should the key
   be retired?

## Files changed (all in the nightshift repo, uncommitted)

- `lib/requeue_stalled.py` — escalation is not a stall (+ docstring rule)
- `lib/task_queue.py` — REVIEW/WAITING sources turned away before the gate
- `lib/claim.py` — `release_claim()`; ATTEMPTS reset on non-failed completion
- `lib/run.py` — claim release on refused-claim and pre-hook-abort paths; retry before quota check
- `tests/test_lifecycle_formal.py` — new, 15 cases

## Mutation check (scratch copies of the model, `lake env lean`)

| Bug put back | Breaks |
|---|---|
| GC stalls the escalation | `gc_preserves` (so `executions_bounded`), `escalated_review_not_stalled` |
| Queue admits REVIEW/WAITING sources | `admitted_not_dead` (so `executions_bounded`), `review_source_not_admitted` |
| Refused claim stays live | `claim_released_on_every_exit`, `refused_claims_do_not_cost_requeues` |
| Pre-hook abort keeps claim | `claim_released_on_every_exit` |
| ATTEMPTS not reset | `attempts_reset_on_non_failure`, `consecutive_after_fix` |
| Quota on retry → failed | `quota_on_retry_defers` |

## Owner decisions applied (2026-09-23)

Tests: `modules/nightshift/tests/test_decisions_nightshift_a.py` (28 cases; 20 failed and 3
errored before the change). No existing test had to change. Model: the `fixed := true`
functions now include the decisions; `runOne`/`night`/`nights` take `m` = max_retries.
The file checks cleanly (636 lines).

- **Decision N1 applied** (NEEDS-OWNER 1a/1e above). `claim.complete_task` escalates a failure
  to REVIEW at once when `task_attempt.blocked(task, plan_only=…)` is true, which covers
  `unknown:`/`pending:`, and `completed`/`proposal-completed` after a post-run failure. It
  writes STATUS `failed` and a reason in `NIGHTSHIFT_ESCALATION`. The max_retries escalation
  gets a reason too. `run.py` passes `plan_only=True` on the plan-only failure path.
  `reconcile_attempt --task ID --reopen --why TEXT [--apply]` resets the fence to not-started,
  sets ATTEMPTS and REQUEUES to 0 and the state to NEXT, and records `NIGHTSHIFT_REOPENED`
  (`<ts> by <actor>`) and `NIGHTSHIFT_RECONCILED` (why). It accepts a fenced or escalated task
  and refuses anything else. It also refuses a REVIEW task in a ledger-phase-1 space, for the
  same reason the GC refuses one. It does not refuse on evidence of effects, because it is the
  human override. Lean: `no_fenced_zombie`, `dead_is_visible` (every dead task is REVIEW or
  DONE), `runOne_executed_visible`, `reopen_restores_inv`, `reopened_bounded`,
  `reopened_is_admitted`, and the refutations `unknown_outcome_zombie_as_found` and
  `handoff_zombie_as_found`.
- **Decision N2 applied.** A non-failed completion writes `NIGHTSHIFT_REQUEUES: 0` over an
  existing non-zero count, and so does `--reopen`. Lean: `requeues_reset_on_success`,
  `reopen_resets_counters`.
- **Decision N3 applied** (NEEDS-OWNER 6c). `claim.escalation_threshold` reads
  `nightshift.max_retries` (default 2; values 0 and 1 mean the first failure). run.py passes
  its own value. The in-run retry is unchanged: at most one, and none when max_retries is 0.
  Lean: `executions_bounded : execs ≤ thr m` with `thr m = max 1 m` (it was `≤ 2`),
  `exhausted_is_dead`, `completeFailed_inv`, the samples `threshold_is_max_retries`
  (m = 0,1,2,3,5 give 1,1,2,3,5), and `max_retries_ignored_as_found`. `calls_bounded`: at
  most 2 `execute_task` calls per run. Docs: module.yaml, CLAUDE.base.md (+ composed
  CLAUDE.md), README.md, commands/nightshift-status.md.
- **Decision N6 applied** (see `nightshift-gates.md` item 7). `Budget.no_paid_call_past_limit`,
  `unlimited_unchanged`, `overshoot_below_one_call` and `paid_call_past_limit_as_found` are in
  this file's `Budget` namespace.

Mutation check (scratch copies, `lake env lean`):

| Change reverted | Breaks |
|---|---|
| Fence ignored in escalation (N1) | `runOne_executed_visible` (hence `dead_is_visible`, `no_fenced_zombie`) |
| Literal 2 instead of `thr m` (N3) | `completeFailed_inv` (hence `executions_bounded`), `threshold_is_max_retries` |
| REQUEUES kept on success (N2) | `requeues_reset_on_success` |
| `--reopen` keeps ATTEMPTS | `reopen_resets_counters`, `reopen_restores_inv` |
| Budget ignored (N6) | `no_paid_call_past_limit`, `overshoot_below_one_call` |

## Owner follow-ups applied (second board, 2026-09-23)

- **Decision Q10 applied: `--reopen` in a ledger-phase-1 space emits the ledger
  event.** The refusal is gone. The write goes through `update_task`, whose
  Phase 1 path is the adapter's own state-change helper (`_ledger_emit` ->
  `sync_generated`): a conditional `item.update` (state NEXT plus the reset
  drawer), with the file rolled back if the ledger refuses. After the write the
  ledger is folded again and must render NEXT with no retained conflict, else
  exit 1 with a pointer to `ledger_resolve_conflict.py`. Still refused, before
  anything is written: an item the ledger holds as `completed`, `verified` or
  `dismissed` (its REVIEW/DONE is derived from the status and no event
  un-completes an item), and an item not in the ledger. Lean: `LStatus`,
  `rendered`, `reopenLedger`, `reopen_phase1_renders_next`,
  `reopen_phase1_escalated_reopens`, `reopen_phase1_refused_as_found`,
  `completed_update_does_not_reopen`. Mutation (drop the completed guard) breaks
  `reopen_phase1_renders_next`.
- **Decision Q11 applied: `budget_exhausted` from execute is a deferral.**
  `run.run_task_mode` (`_budget_wall`): no failure analysis, no retry, state
  restored to the pre-claim state, `NIGHTSHIFT_STATUS: deferred-budget` and
  `NIGHTSHIFT_COMPLETED` stamped (claim released), `skipped` lifecycle, status
  `deferred`, and `continue` (the run goes on, unlike the quota wall's
  `break`). Also on the in-run retry. Lean: `Outcome.budget` added to `runOne`
  (every lifecycle theorem now covers it; `runOne_preserves` got the case),
  `Budget.budget_defer_not_attempt`, `Budget.budget_counted_as_attempt_as_found`.
  Mutation (budget -> `completeFailed`) breaks `runOne_preserves` and
  `budget_defer_not_attempt`.
- Tests: `modules/nightshift/tests/test_followups_nightshift.py` (10 cases; the
  Q10 and Q11 cases failed before the change). No existing test changed.
