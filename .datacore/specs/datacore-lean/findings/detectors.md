# Detectors cluster: findings (2026-09-23)

Model: `DatacoreSpec/Detectors.lean`, namespace `DatacoreSpec.Detectors`. It checks
cleanly with `lake env lean` and has no `sorry`, `admit`, `native_decide` or custom
axiom. The proofs use only `propext`, `Quot.sound` and `Classical.choice`.
Tests: `.datacore/lib/tests/test_detectors_formal.py` (20 tests). All of them were
written first and 18 failed on the pre-fix code.

These are alarms, so the property proved for each one is **"reports ok ⇒ the
healthy condition holds"**. Each counterexample below was replayed against the
real Python in tmp dirs (scratch `replay_before.py`), before and after the fix.

| # | Candidate | Verdict | Theorems |
|---|---|---|---|
| 2a | actor_presence: a log with no readable events reported ok | CONFIRMED+FIXED | `ap_old_unreadable_is_ok`, `ap_ok_sound`, `ap_ok_has_readable` |
| 2b | actor_presence: deleted in one space, still present in another: never detected | CONFIRMED+FIXED | `ap_old_deletion_elsewhere_is_ok`, `ap_ok_sound` |
| 2c | actor_presence: STALLED rewrites the baseline and self-heals | CONFIRMED+FIXED | `ap_old_stalled_self_heals`, `ap_failing_is_sticky` |
| 6a | seq_gap: local None, remote present, prints "ok … published", exit 0 | CONFIRMED+FIXED (narrowed) | `sg_old_unreadable_local_ok`, `sg_ok_sound` |
| 6b | seq_gap: head_seq is the last parseable line, not the max | CONFIRMED+FIXED | `sg_old_last_line_hides_gap`, `sg_head_is_max`, `sg_ok_sound` |
| 6c | seq_gap: local < remote clamped to 0 hides truncation | DOWNGRADED | — |
| 9 | id_churn: count-based growth masks churn | CONFIRMED+FIXED (+ owner action) | `ic_count_masks_churn`, `ic_set_sound` |
| 8a | scoreboard R5: `max(1, e//200)` passes 3/4 and 95/96 | CONFIRMED+FIXED (matches its own docstring and the SLO page) | `r5_old_passes_below_slo`, `r5_ok_sound` (an iff) |
| 8b | scoreboard R5: vacuous pass below 4 expected probes | CONFIRMED+FIXED | `r5_old_passes_below_slo`, `r5_ok_sound` |
| 8c | scoreboard streak: file order; `--date` backfill appended out of order | CONFIRMED+FIXED | `streak_old_file_order`, `streak_old_stale_after_backfill`, `rechain_ok`, `rechain_step` |
| 4a | today_registry: dependency on a LATER stage passes validate | CONFIRMED+FIXED | `tr_old_later_stage_dep`, `tr_valid_plan_respects_deps` |
| 4b | today_registry: unknown stage silently coerced to gather | CONFIRMED+FIXED | `tr_old_unknown_stage_coerced`, `tr_valid_stage_honoured` |

## Details

### actor_presence (2a, 2b, 2c)

Replay before the fix: (a) garbage log after a baseline of seq 4 gave `rc 0 ['ok'] {'0-a': None}`.
(b) Log deleted from `1-b` with `0-a` intact gave `rc 0 ['ok']`. (c) Log truncated from
9 to 3: run 1 gave `rc 1 stalled`, run 2 gave `rc 0 ok`. After the fix: `missing`,
`missing` (still missing on re-run), and `stalled` then `stalled`.

Fix: the new pure `classify(here, prev)` is the Lean `classify`. It compares every
baseline space, so a space that is absent or unreadable now counts as lost, which
means MISSING. The new `next_baseline` moves the baseline only on ok/silent. MISSING
already kept the baseline for this reason, and STALLED now does too. A stalled
actor clears only when its log is back at or past the baseline, or when the state
file is reset. That is the same recovery MISSING already had.
Proved: `ap_ok_sound` (ok ⇒ every baseline space is readable, at or past its seq),
`ap_ok_has_readable`, `ap_failing_is_sticky`.
Residue: a space directory that is removed from a box on purpose now reads MISSING
until the state is reset. That is consistent with the module docstring's premise
(deletion is the failure).

### seq_gap (6a, 6b, 6c)

Replay before the fix: `head_seq([0,1,2,9,3]) = 3`. Local `garbage`, remote at 4, gave
`gap None, error None`, which prints "ok … published" and exits 0. Local 0..6 then a
stray 3, remote at 4, gave `local_seq 3, gap 0` with seqs 5 and 6 unpublished. After
the fix: `9`; `error: local log has no readable events`; `local_seq 6, gap 2`.

Narrowed (6a): an EMPTY local log still gives gap 0, because nothing was written
there, so zero unpublished is the true answer. Only a log with lines but no
readable event is an error, since what it holds cannot be counted.
DOWNGRADED (6c): `max(0, local - remote)` is the right answer to this detector's
question ("facts here that are not on the remote"). The scan covers every actor's
log in a space. For another machine's actor, local < remote just means "not pulled
yet". The module docstring says it deliberately does not conflate unpushed with
corrupt. Truncation is actor_presence's STALLED/MISSING, which now holds (2c).
Proved: `sg_head_is_max`, and `sg_ok_sound`: ok ⇒ every readable local seq ≤ the
remote head, or the only unpublished events are inside the documented grace
("pending").
Live check (read-only, no fetch): `67 log(s), 0 with unpublished events, 0 error(s)`,
the same as before the fix.

### id_churn (9)

`apply_baseline` compared counts, so two acknowledged ids repaired plus two new ids
churned gave "growth 0". Fix: `scan_space` returns `orphaned_ids`, `--acknowledge`
writes id lists, and growth is `orphaned_ids − acknowledged`. A legacy numeric
baseline still reads count-based and says so in its `ack` line. The two existing
tests in `test_id_churn_baseline.py` pass unchanged. The baseline file is
`~/.datacore/state/id-churn.baseline.json`, which only this file reads.
Proved: `ic_set_sound` (no growth ⇒ every orphaned id was acknowledged).
**Owner action:** the set semantics only take effect after `id_churn.py --acknowledge`
is re-run on each host. Until then the old numeric baseline keeps the count
behaviour, and its `ack` line says "by COUNT (legacy baseline; re-run --acknowledge)".
Not changed: the 25 % noise floor is applied before the baseline, so new churn below
25 % of live ledger ids is still unreported. That is a pre-existing policy threshold.

### reliability_scoreboard (8a, 8b, 8c)

Replay before the fix: `r5_reachable` returned True for 3/4 hits at 01:00 and 95/96
at 24:00. After the fix both are False, and 96/96 passes.
The SLO page (`2-datacore/1-tracks/ops/reliability-slo.md`, R5) and the module header
both say ≥ 99.5 %. `expected // 200` is exactly the 0.5 % allowance rounded down.
The `max(1, …)` made the check always forgive one miss. `r5_ok_sound` proves the
fixed rule is **exactly** `expected > 0 ∧ hits/expected ≥ 99.5 %`. A probe on a
fixed 15-minute cadence always lands at least `floor(elapsed/15)` hits whatever its
phase, so no slack is needed for phase. "Nothing due yet" (expected 0) now fails
with "cannot judge" instead of passing. The job runs at 07:20, so that case does
not arise in the schedule.
Streak: `compute` now finds yesterday's line by date. The new
`write_day_lines` keeps the log date-sorted and re-chains every line after a
backfilled day. Replay before the fix: after a backfill was appended, the next day
restarted at streak 1 although yesterday was `PASS streak=29`. A backfilled FAIL left
`09-04 streak=29`, so 09-05 reached level 5. After the fix the next day is 30 in the
first case. In the second case the rewrite makes 09-04 streak 1, so 09-05 is 2
(pytest `test_backfilling_a_fail_rechains_later_days`). A normal daily write leaves
history byte-identical (`test_a_normal_daily_write_changes_no_history`).
Proved: `rechain_ok` (every re-chained log is consistent) and `rechain_step` (a
streak n ≥ 2 is backed by a PASS on the previous calendar day carrying n − 1).
Not changed: `--date` re-runs a past day against the current state files. That is
inherent to the flag, and only the log bookkeeping is fixed. The prober-log branch
does not check "MCP connected" from the SLO row. I noted this and did not model it.

### today_registry (4a, 4b)

Replay before the fix: a gather section `a` that depends on a compose section `b`
gave `validate → []` and `plan → [['a'], ['b'], []]`, so `a` runs first. A section
with stage `narate` parsed as gather and gave `validate → []`. After the fix both are
reported, and the live registry still validates with 0 problems (14 sections).
Fix: new rule 4b reports an unknown stage. `stage_declared` keeps what was written,
and `stage` still falls back to gather so `plan` is unchanged. New rule 5b reports a
dependency on a later stage. Earlier-stage dependencies are intended and unchanged:
chief-of-staff's `observation` in narrate depends on compose and gather sections.
Proved: `tr_valid_plan_respects_deps`. For any registry that the fixed gate accepts,
and for ANY sort order of the rounds, `plan` puts each registration after a producer
of every one of its dependencies, and the cycle fallback never fires
(`sched_correct`). `tr_valid_stage_honoured` proves every section runs in the stage
it declared.
Assumed, not modelled: rule 5's DFS cycle check is taken as its standard equivalent,
a rank that every dependency strictly decreases. Only "≥ 1 producer" is used from
rule 2.

## Mutation check

Script: scratch `mutate.py`. It puts each bug back into a copy of the model and
reports which theorem breaks.

| Mutant | Result |
|---|---|
| M1 classify without the lost-space check | killed: `ap_ok_sound` |
| M2 "no readable log" test weakened to `here = []` | killed: `ap_ok_has_readable` (`ap_ok_sound` alone survives it: the lost check subsumes it for baselined actors) |
| M3 STALLED rewrites the baseline | killed: `ap_failing_is_sticky` |
| M4 unreadable local reads ok | killed: `sg_ok_sound` |
| M5 head = last line | killed: `sg_ok_sound` |
| M6 growth drops a new id | killed: `ic_set_sound` |
| M7 R5 forgives one miss | killed: `r5_ok_sound` |
| M8 R5 vacuous at 0 due | killed: `r5_ok_sound` |
| M9 chain ignores the calendar | killed: `rechain_step` |
| M10 rule 5b off by one stage | killed: `tr_valid_plan_respects_deps` |
| M11 scheduler treats every dependency as ready | killed: `sched_correct` (used by the plan theorem) |
| M12 unknown stage accepted | killed: `tr_valid_stage_honoured` |

## Files changed

- `.datacore/lib/detectors/actor_presence.py`: `classify`, `next_baseline`; the main loop uses them
- `.datacore/lib/detectors/seq_gap.py`: `head_seq` returns the max; an unreadable local log is an error
- `.datacore/lib/detectors/id_churn.py`: `orphaned_ids`, set baseline, legacy count read
- `.datacore/lib/reliability_scoreboard.py`: R5 tolerance, no vacuous pass, `write_day_lines`, yesterday found by date
- `.datacore/lib/today_registry.py`: `stage_declared`, rules 4b and 5b
- new: `.datacore/lib/tests/test_detectors_formal.py`, `DatacoreSpec/Detectors.lean`, this file

Targeted tests: `cd .datacore/lib && python3 -m pytest -q tests/test_detectors_formal.py
tests/test_reliability_scoreboard.py tests/test_id_churn_baseline.py tests/test_seq_gap.py
tests/test_today_hooks_grounded.py` gives 39 passed. Also
`cd .datacore/modules/chief-of-staff && python3 -m pytest -q tests/test_briefing_integration.py
tests/test_module_yaml.py` gives 16 passed.

## Owner decisions applied (2026-09-23)

**Decision D1 recorded (run `--acknowledge` per host):** no code. It is a per-host
action and outside this change. Read-only evidence from this mac: the live baseline
`~/.datacore/state/id-churn.baseline.json` is still the legacy COUNT form
(`0-personal: 360, 2-datacore: 271`, acknowledged 2026-09-03). Today every space has
**0** orphaned open ledger ids, with or without the floor (0-personal folds 1021 open
items, 2-datacore 434, and org holds all of them). The count baseline therefore
forgives up to 360 and 271 NEW orphans in those two spaces until it is re-acknowledged.
Re-running `id_churn.py --acknowledge` here would record empty sets.

**Decision D2 applied (drop the noise floor):** `scan_space(space, noise_floor=False)`
no longer zeroes orphans below 25 % of the open ledger ids. `is_legacy_baseline()`
detects a COUNT baseline, and only then does `main` pass `noise_floor=True` and print a
`note … legacy COUNT baseline` line on stderr, so those hosts keep their old behaviour
until they are re-acknowledged. `--acknowledge` records every orphan, with no floor.
Lean: `ic_floor_hides_new_churn` (1 of 10 lost, none acknowledged: the floor reported
nothing, the fixed rule reports `[0]`), `ic_report_complete` (every open ledger id missing
from org and not acknowledged is reported, however few), `ic_report_sound`. Mutation
check: with the floor put back, `ic_report_complete` stops proving.
Read-only run on this mac: `id_churn.py` gives exit 0, `10 space(s), 0 with findings`,
plus the legacy note. It would say the same under a set baseline, since there are 0
orphans.
Tests: `tests/test_decisions_rest.py::test_d2_*` (5 tests, 3 of which failed before
the change). No existing test changed.

**Decision D3 recorded (R5 strict):** no code. A single missed probe (95/96) fails, as
the ≥ 99.5 % target says. That is already the behaviour (`r5_ok_sound` is an iff).

**Decision D4 applied (`--acknowledge <actor>`):** `actor_presence.py --acknowledge ACTOR`
accepts the current state of a MISSING or STALLED actor. The entry's `spaces` become its
readable spaces now, and `acknowledged: {by, at, status, previous}` records who
(`user@host`), when (local ISO time), from which verdict, and the replaced baseline.
The command refuses (exit 2, state untouched) an actor that is not rostered or not
MISSING/STALLED. Only that actor's entry changes. `classify(here, prev, acknowledged)`
differs from before in exactly one case: an acknowledged EMPTY baseline (a retired actor
with no log left) with nothing readable is `ok`. `next_baseline` carries the audit
record forward on ok/silent and still never moves on a failure.
Lean: `classifyA`, `ackBase`, `lookup_of_mem` (a dict's keys are unique),
`ap_ack_then_unchanged_ok` (acknowledged, then unchanged, reads ok, for any state),
`ap_ack_then_held` (afterwards the actor is held to the acknowledged state, so there is
no blanket pass), `ap_ack_nonempty_is_classify`, `ap_unacked_is_classify` and
`ap_unacked_failing_is_sticky` (unacknowledged failures stay sticky). Mutation check:
dropping the empty-baseline branch kills `ap_ack_then_unchanged_ok`. Recording nothing
(`ackBase := []`) kills `ap_ack_then_held`. Letting the flag pass any baseline kills
`ap_ack_nonempty_is_classify` and `ap_ack_then_held`.
Tests: `tests/test_decisions_rest.py::test_d4_*` (5 tests, all of which failed before the
change). No existing test changed.
