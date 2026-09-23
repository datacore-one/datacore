# NightshiftGates — pass ⇒ evidence (2026-09-23)

Model: `DatacoreSpec/NightshiftGates.lean` (namespace `DatacoreSpec.NightshiftGates`),
591 lines. Checks clean with `lake env lean`. It has no `sorry`, `admit`, `axiom` or `native_decide`.
The axiom audit (`#print axioms` in a scratch copy) shows only `propext`, `Quot.sound` and
`Classical.choice`.
Survey items: 3, 4, 5, 7, 8, 9, 10, 11, 12 of `survey/nightshift.md`.

| # | Candidate | Verdict | Lean theorems |
|---|---|---|---|
| 3 | evaluator consensus (`evaluate.py`) | **CONFIRMED+FIXED** (threshold part NEEDS-OWNER) | `pass_implies_three_real_scores`, `vote_is_written_score`, `pass_implies_mean_ge_070`; refutations `old_score_8_approves`, `old_score_6_approves`, `old_two_real_plus_garbage_approves`, `old_two_real_plus_unknown_persona_approves`, `old_violates_pass_implies_evidence` |
| 4 | delegation canary | **CONFIRMED+NEEDS-OWNER** (blocked); unknown and `git add` DOWNGRADED | `abandoned_canary_fails` (proved), `blocked_forever` (refutation) |
| 5 | gstack gate (`postprocess.py`) | **REFUTED** (property holds, proved) | `Gstack.proceeds_iff`; `legacy_passes_without_evidence` records the pre-2026-09-08 defect |
| 7 | daily budget cap | **CONFIRMED+NEEDS-OWNER** (latent: budget 0) | `Budget.spend_past_limit` |
| 8 | jobs/recurrence reset | **CONFIRMED+NEEDS-OWNER** | `locked_reset_holds` (proved), `swallowed_save_loses_reset`, `unlocked_interleaving_loses_reset` |
| 9 | ai_task_gate vs executor | **CONFIRMED+FIXED** (ROADMAP clause NEEDS-OWNER) | `gate_eq_executor_plus_roadmap`, `gate_mirrors_executor`, `gate_never_weaker`; refutations `old_gate_passes_unrunnable`, `old_gate_misses_queued_tag`; `roadmap_clause_is_stricter` |
| 10 | cadence_liveness contract | **CONFIRMED+FIXED** | `pass_implies_all_checked`; refutation `old_unreadable_passes` |
| 11 | workflow_executor LIVE | **CONFIRMED+FIXED** | `overall_cons`, `completed_means_nothing_owed`; refutation `old_live_reports_completed` |
| 12 | jobs/awake accounting | **REFUTED** (property holds, proved) | `asleepLoop_nonneg`, `asleep_bounds`, `awake_le_wall`, `awake_between` |

## 3. Evaluator consensus — CONFIRMED+FIXED

Pre-fix, three kinds of output counted as votes that nobody had cast:
- a number above 1.0 was clamped to 1.0, so "score: 8" or "score: 6" (a 0–10
  scale) became a unanimous approval;
- an exit-0 answer with no score voted 0.5;
- an unknown persona voted 0.5, because its feedback did not start with `Error:`.
The last two let **two** real votes reach `MIN_REAL_EVALUATORS = 3` and approve.

Replay (`scratchpad/NightshiftGates/replay_eval.py`, with the real `evaluate_output` and `_run_claude` stubbed):
```
A score:8 x3 -> ('approved', 1.0, …)      A' score:6 x3 -> ('approved', 1.0, …)
B 2 real + unparseable -> ('approved', 0.833, {'a':1.0,'b':1.0,'c':0.5})
C 2 real + unknown persona -> ('approved', 0.833, {…,'ghost':0.5})
```
Fix (`modules/nightshift/lib/evaluate.py`):
- `parse_evaluator_output` returns `score=None` when there is no score in [0, 1]. It no
  longer clamps, and it does not rescale.
- `run_evaluator` turns `None` into a non-vote (`Error: evaluator gave no score on the 0.0-1.0 scale`).
- An unknown persona's feedback now starts with `Error:`.
- The regex is `\bscore:` with IGNORECASE, so `raw_score: 9` is no longer read as the score.

The quorum rule and the thresholds are unchanged.
Proved: an approving decision means ≥ 3 outcomes each wrote an in-range score, and every
counted vote is such a score. The mean of the real votes is then ≥ 0.70.
Tests: `modules/nightshift/tests/test_evaluator_formal.py` (8 cases, 7 failed before the fix).
Mutation: putting back the 0.5 vote for an unparseable answer, or the clamp, breaks
`voteNew_isSome` / `vote_is_written_score` / `new_garbage_needs_review`.

Not fixed (NEEDS-OWNER):
- `quality_threshold` (settings.yaml 0.80; module.yaml: "Minimum score for auto-approval")
  is never read. The 0.80 / 0.70 / 0.85 values are hard-coded. Today the configured value
  equals the hard-coded one, so behaviour is unaffected.
- `recommendation: reject` with a high score still approves, because recommendation is
  parsed but never used in the decision.

## 4. Delegation canary — CONFIRMED+NEEDS-OWNER

The live contract is `box-delegation-canary`: `--run`, then an artifact regex
`dispatched|completed|blocked` that must be < 26 h old.

Proved: an open canary past its budget is written `failed`, which the regex rejects.

Refuted: when the **local** `git commit` of the input fails, every run writes `blocked`,
exits 0 and passes the contract. It keeps passing for as long as the commit keeps failing.
Replay (`replay_rest.py`, a space that is not a git repo): `CANARY runs exit codes: [0, 0, 0] verdict: blocked`.

The module docstring justifies `blocked` as "an unreachable remote is a condition". But the
code writes `blocked` only on a local commit failure, and nothing pushes. The test
`test_a_canary_that_could_not_publish_is_blocked_not_failed` pins "blocked 99 h old passes".
That is pinned intent, so no fix was made.

Downgraded:
- `unknown` never passes forever: it is written only by `--check`, which is retired from
  the manifest. `--run` treats it as "no previous" and seeds a new canary, and `unknown`
  does not match the contract regex.
- A `git add` failure makes the following `commit -- src` fail, which is the blocked path
  above.

## 5. gstack gate — REFUTED (holds)

`proceeds_iff`: output passes the gate ⇔ review = `verdict true` ∧ cso = `verdict true`.
`_parse_gate_verdict` only yields a verdict when `passed` is a real bool. The mutation
"unavailable reads as passed" (the 2026-09-08 defect, `gateLegacy`) breaks `proceeds_iff`.
No code change.

## 7. Daily budget — CONFIRMED+NEEDS-OWNER (latent)

`check_budget` feeds `api_fallback_allowed`, and `execute._execute_task` ignores it: its
docstring says "accepted for call compatibility". The batch API (`auth='api'`) is used
whenever `NIGHTSHIFT_BATCH_API` is on, whatever the budget. `spend_past_limit` shows this.
With `budget_daily_usd: 0` (unlimited) there is no effect today. The fix would be in files
this cluster does not own (`run.py`, `execute.py`).

## 8. jobs/recurrence — CONFIRMED+NEEDS-OWNER

The file's own comment: "A counter that can be off by one is fine; a reset that can be lost is not".
Two paths lose a reset:
- `_save` swallows OSError. The pass returns `consecutive 0`, the disk keeps 3, and the
  next single failure reads 4 and is `recurring`.
  Replay (state dir made read-only for the pass):
  `after 3 fails: 3 True / pass returned: 0 False / next single fail: 4 recurring = True`.
- The lock-unavailable fallback allows fail-load / pass / fail-save. That ends at n+1,
  which no serial order produces (`unlocked_interleaving_loses_reset`).

With the lock held and saves working, the reset holds (`locked_reset_holds`).
Not fixed: the fallback is an explicit, commented trade-off ("an off-by-one counter, not an
aborted run"). Choosing between it and a guaranteed reset is the owner's call.

## 9. ai_task_gate vs executor — CONFIRMED+FIXED (+NEEDS-OWNER)

The gate claims to "mirror nightshift_parser._is_executable exactly". Differing inputs:
1. SURFACE `Unassigned` / `UNASSIGNED`: the gate passed it, but the executor lower-cases
   and never runs it. **Fixed**: `_missing` now calls `delegation_requirements.execution_gaps`
   itself.
2. Tag `:AIresearch:` (any tag starting with "AI"): the executor queues it, but the gate
   only looked for the exact tag `AI`. **Fixed**: the gate uses the same `startswith("AI")`
   predicate on the heading's own (shallow) tags.
3. ROADMAP, required in spaces with a `roadmap.yaml`: the gate is stricter than the
   executor. The docstring states both "mirror exactly" and this clause, so it is
   **NEEDS-OWNER**. Proved: after the fix, gate = executor ∧ ROADMAP clause, exactly
   (`gate_eq_executor_plus_roadmap`).
4. Not changed: the executor skips hidden / `archive` / `2-projects` paths that the gate
   still checks. That makes the gate stricter only on files nightshift never reads.

Replay: `SURFACE=Unassigned gate-missing: [] executor-gaps: ['SURFACE']`;
`tag AIresearch: gate selects False executor selects True`.
Tests are in `lib/tests/test_nightshift_gates_formal.py`: 3 gate cases failed before the
fix, and 3 more guard the unchanged behaviour. Mutation: putting back the case-sensitive
comparison breaks `gate_never_weaker` / `gate_mirrors_executor`.

## 10. cadence_liveness — CONFIRMED+FIXED

An unreadable `venture.yaml` or cadence log was skipped with `continue`, so the result was
"0 cadence(s) overdue" and a green contract. An engine error, by contrast, already added a
row.
Replay: `readable, never-run weekly -> 1 overdue`, but `same venture, one bad line -> 0 overdue`.

Fix: both now add a row `(-1, space, '?', '?', '<file> unreadable: <ExcType>')`, like the
engine-error row. It records the exception type only, never the content. A non-mapping
`venture.yaml` is also a row now; before, it crashed.

Proved: a passing contract means every venture space was read and has 0 overdue
(`pass_implies_all_checked`). Mutation: skipping unreadable spaces again breaks it.
Real-data check (read-only YAML parse of all 8 `venture.yaml` files on the mac): all parse,
so no change today.

## 11. workflow_executor — CONFIRMED+FIXED

In LIVE mode the tool, agent and output handlers only log and return `skipped`, and the
run reported `overall: completed`. DIP-0022 says "This file is not proof that any tool executed".
Replay: `LIVE overall: completed {'p1': 'skipped', 'p2': 'skipped'}`.

Fix: those handlers now return `not_executed` (a new constant `NOT_EXECUTED`), and the run's
overall is `not_executed` unless it stops, pauses or errors. The state-file record is
unchanged (`skipped`, so the on-disk format is untouched). Dry-run is unchanged.
Proved: overall `completed` ⇒ no phase was due and left unexecuted
(`completed_means_nothing_owed`). Mutation: the old fold breaks `overall_cons`.
Tests: 4 cases in `test_nightshift_gates_formal.py`; 1 failed before the fix, and the
other 3 guard the unchanged behaviour.

## 12. awake — REFUTED (holds)

Proved: `awake_age ≤ wall_age` always, and `0 ≤ awake_age ≤ wall_age` whenever
wall_age ≥ 0, including the exception branch. The model is over Int. For floats the same
holds, because `min(total, age) ≤ age` and IEEE subtraction is monotone.
A fuzz check of the real `asleep_seconds_since` (3000 random logs) found 0 violations.
Mutation: dropping the `min` clip and the floor breaks `asleep_bounds`.

## Files changed
- `.datacore/modules/nightshift/lib/evaluate.py`
- `.datacore/lib/cadence_liveness.py`
- `.datacore/lib/ai_task_gate.py`
- `.datacore/lib/workflow_executor.py`
- new: `.datacore/modules/nightshift/tests/test_evaluator_formal.py`,
  `.datacore/lib/tests/test_nightshift_gates_formal.py`,
  `specs/datacore-lean/DatacoreSpec/NightshiftGates.lean`, this file.

## Targeted tests
```
cd .datacore/lib && python3 -m pytest -q tests/test_nightshift_gates_formal.py tests/test_cadence_liveness.py \
  tests/test_workflow_state_preservation.py tests/test_private_state_boundary.py tests/test_delegation_canary.py \
  tests/test_jobs_recurrence.py tests/test_awake_age.py tests/test_python_source_binding.py      -> 128 passed
cd .datacore/modules/nightshift && python3 -m pytest -q tests/test_evaluator_formal.py tests/test_evaluator_nonvotes.py \
  tests/test_launch_boundaries.py tests/test_gstack_gate_verdict.py lib/tests/test_run_triage_barrier.py -> 62 passed
```

## Decision N6 applied (2026-09-23)

The budget is now enforced when `budget_daily_usd > 0`.
- `check_budget` and its helpers moved to `modules/nightshift/lib/execute.py`, next to the
  call they guard. `run.py` re-exports the same names.
- `_execute_task` starts no batch-API call once the day's paid spend has reached the limit.
  It returns a known not-started result with `budget_exhausted=True`. This check applies
  when `uses_paid_api` holds: tier-1 model, `NIGHTSHIFT_BATCH_API` set, a key present, SDK
  importable. `paid_api_allowed=None` makes it read the budget itself, so no caller can skip
  the check.
- `run.run_task_mode` defers such a task BEFORE claiming it. It uses the skip reason
  "paid-API budget exhausted ($x of $y today) and this task routes to the batch API —
  deferred, not claimed" and records status `deferred`. Subscription tasks still run.
- Budget 0 still means unlimited. `check_budget(~/Data)` on this mac returns
  `(True, 0.0, 0.0)`, and `test_budget_zero_stays_unlimited` pins that.
- Lean: `NightshiftLifecycle.Budget` has `no_paid_call_past_limit`, `unlimited_unchanged`
  and `overshoot_below_one_call`. `NightshiftGates.Budget.spend_past_limit` still describes
  the pre-N6 code. That file is not owned by this area, so the coordinator should re-label
  it "as found".

## Owner decisions applied (2026-09-23)

Decision N4 applied: threshold + reject blocks. `evaluate.load_quality_threshold`
reads `nightshift.quality_threshold` from `.datacore/settings.yaml`, overridden by
`settings.local.yaml` (default 0.80; a non-number or a value outside [0, 1] falls
back to 0.80 with a WARNING on stderr). `make_decision(consensus, variance, threshold,
rejected)`: approval needs ≥ 3 real votes, mean ≥ T, and no evaluator that ran
recommending `reject`. Review band, derived from T: low variance, mean ≥ T →
`approved`; variance > 0.1, mean ≥ min(1, T + 0.05) → `approved_with_notes`; all
else `needs_review`. The old fixed 0.70 `approved_with_notes` band is gone (it
approved below the configured minimum). Lean: `decision T rejected`,
`pass_implies_mean_ge_threshold` (replaces `pass_implies_mean_ge_070`),
`reject_blocks`, `pass_implies_no_reject`, `high_variance_needs_margin`,
`pass_implies_three_real_scores` re-proved over `pipeline T`; refutations
`old_approves_below_threshold`, `old_ignores_reject`. Mutations: the 0.70 floor
breaks `pass_implies_mean_ge_threshold`; dropping the reject clause breaks
`reject_blocks` / `pass_implies_no_reject`. Test changed:
`test_evaluator_nonvotes.py::test_timeouts_no_longer_manufacture_a_review` (its 0.73
real mean now needs review at the default; the non-vote point is shown at T = 0.70).

Decision N5 applied: a failed local commit of the canary input writes `failed`,
exit 1; `cmd_check` turns a `blocked` verdict older than `BLOCKED_MAX_AGE_HOURS`
(48) into `failed`, exit 1. Lean: `run` is `runWith (.failed, 1)`;
`commit_failure_fails`, `run_never_blocked`, `blocked_ages_out`; the pre-fix
`old_blocked_forever` is kept as a refutation of `runOld`. Mutations: putting
`(.blocked, 0)` back breaks `commit_failure_fails` / `run_never_blocked`; a check
that never ages breaks `blocked_ages_out`. Test changed:
`test_delegation_canary.py::test_a_canary_that_could_not_publish_is_blocked_not_failed`
→ `test_a_blocked_canary_ages_into_failed` (99 h blocked now fails; 2 h still passes).
Left for the owner: the `box-delegation-canary` contract regex still accepts
`blocked` (`lib/jobs/manifest.yaml`, not in this area); nothing writes it any more.

Decision N7 applied: `recurrence._save` returns whether the write landed.
`record` still never raises, but a lock failure or a failed save now prints
`WARNING: recurrence: …` to stderr and puts the same text in the returned
record's `warning`, naming the job and the lost reset (e.g. "the streak of 3
consecutive failure(s) is still on disk, so its next single failure will read 4
and escalate as recurring"). `describe` appends it to the alert line. Lean:
`passN7`, `lost_reset_is_warned`, `unlocked_always_warns`; refutation
`old_lost_reset_silent`. `swallowed_save_loses_reset` and
`unlocked_interleaving_loses_reset` still hold (the trade-off is kept, only no
longer silent). Mutation: warning only on the lock path breaks
`lost_reset_is_warned`. No existing test changed. Caveat: `job_verify._note_pass`
(not in this area) ignores the returned record, so on the pass path the warning
reaches stderr only.

Decision N8 applied: `delegation_requirements.execution_gaps(props,
roadmap_required=…)` now owns the ROADMAP clause, with `roadmap_spaces(root)` and
`space_of(path, root)`. `ai_task_gate._missing` and
`nightshift_parser._is_executable(task, roadmap_spaces, root)` both call it;
`find_ai_tasks(require_executable=True)` passes its data dir. Lean: the gate and
executor are modelled over one `executionGaps`; `gate_eq_executor` (every
property combination, both roadmap cases), `gate_never_weaker`,
`gate_never_stricter`; refutation `old_executor_weaker_than_gate` replaces
`roadmap_clause_is_stricter`. Mutation: an executor that ignores the roadmap flag
breaks `gate_eq_executor`. Impact: in 2-datacore and 5-plur (the spaces with a
roadmap.yaml today) nightshift no longer selects :AI: tasks without ROADMAP.

Tests: `lib/tests/test_decisions_nightshift_b.py` (N5, N7, N8 gate side),
`modules/nightshift/tests/test_decisions_nightshift_b.py` (N4, N8 executor ≡ gate,
G6).
