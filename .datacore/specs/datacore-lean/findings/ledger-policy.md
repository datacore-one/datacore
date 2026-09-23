# LedgerPolicy findings (2026-09-23)

Model: `DatacoreSpec/LedgerPolicy.lean` (namespace `DatacoreSpec.LedgerPolicy`, 640 lines,
imports `LedgerSpec.Item`). Checks cleanly. No `sorry`/`admit`/`axiom`/`native_decide`.
`#print axioms` shows only `propext`, `Quot.sound`, `Classical.choice`.
Tests: `lib/tests/test_ledger_policy_formal.py` (18 tests).
Replays: `scratchpad/LedgerPolicy/replay_{hops,approval,dispatch}.py`.

| # | Candidate | Verdict |
|---|---|---|
| 1a | check_create max_hops ("a delegation chain deeper than max_hops is refused") | CONFIRMED+FIXED |
| 1b | adapter/executor side of hops (`DATACORE_HOPS` is never set) | CONFIRMED+NEEDS-OWNER (the file is not mine) |
| 1c | daily cap counted per space, not per principal | DOWNGRADED: it is per principal, but within one space. Scope is NEEDS-OWNER |
| 2 | approval retention after an unguarded update drops the effects | CONFIRMED+FIXED |
| 3 | merge_values laws and guard_projection soundness | REFUTED (the laws hold, and are proved), with two Python-equality caveats (NEEDS-OWNER) |
| 4a | dead-letter counted no-op releases, so a stranger can dismiss real work | CONFIRMED+FIXED |
| 4b | dead-letter dismiss bypassed guarded_append (stage-5 arbitration) | CONFIRMED+FIXED (routed through the gate) |
| 4c | cross-host claim race inside one principal | CONFIRMED+NEEDS-OWNER |

## 1a. Delegation depth

The survey was right. `chain_follow_up` spread `then` without `hops`, and `check_create`
trusted the declared count. So every link of an A→B→A chain read as 0.

- Refuted (old): `Hops.old_chain_unbounded`. With `max_hops = 0`, chains of every length pass.
- Replay (before the fix): a nested 5-link miles/winston chain with `max_hops: 1` chained 4
  times, and every item had `hops=None`.
- Fix:
  - `claim_gate.check_create` now derives a floor. For an agent, a create with `after` must
    declare `hops ≥ parent_hops + 1`. If `after` names no item in the space, the create is
    refused.
  - New helpers `recorded_hops` and `parent_hops`.
  - `ledger_claim.chain_follow_up` writes `hops = parent + 1` and overrides whatever `then`
    says.
  - Humans are exempt from the floor, as they already are from `max_hops`. A human reissuing
    work restarts the count.
- Proved: `Hops.chain_depth_bounded`. A chain whose every link passed the gate has at most
  `max_hops` links after its root. Also `depth_le_hops` and `follow_up_passes_floor`.
- Replay (after the fix): step1 has `hops=1`. step2 is refused with "delegation chain is 2
  hops deep; winston may go 1".
- Mutation: removing the floor conjunct from `gateNew` makes `depth_le_hops` fail.

## 1b. Hops on the adapter and executor side (NEEDS-OWNER, other files)

- `org_workspace_adapter.py:380` reads `DATACORE_HOPS`.
- Nothing sets it. `ledger_claim.run_task(hops=0)` is never passed through.
- Needed change: `executors/base.py::_execution_env`, which already folds the claimed task,
  should set `env["DATACORE_HOPS"] = str(recorded_hops(task.payload) + 1)`.
- Until then, an agent that creates items through the adapter during a run starts at depth 0.
- The `after` floor does not help here, because the adapter writes no `after`.

## 1c. Daily cap

`creates_today` already resolves every writer to its principal. What it counts is one
space's log, so the effective cap is `max_creates_per_day × spaces`. The docstring ("a writer
past its daily creation allowance") does not say which. This is an owner decision.

## 2. Approval retention

`previously_approved` only looked at `item.create` events carrying `approval_ref`.

- Replay (a real EventLog in a tmp space): create with no effects → guarded update adds
  `payment` with a valid grant → an unguarded `log.append` update sets `effects: []`. The
  claim then SUCCEEDED with no valid grant. A guarded update could drop the effects the same
  way.
- Refuted (old): `Approval.old_update_approval_lost`.
- Fix (`ledger/policy.py`): an item counts as previously approved when its current payload
  carries `approval_ref`, or when any `item.create` or `item.update` for its id did.
- Proved: `Approval.claim_keeps_approval`. It holds for every cosign set, every grant-validity
  oracle and every history.
- Replay (after the fix): "claim refused: approval does not bind this payload".
- Mutation: reverting `prevNew` to count creates only breaks `claim_keeps_approval`.
- Existing tests are unaffected. That includes `test_claim_revalidates_imported_updates` and
  `test_approved_content_update_needs_new_grant`.

## 3. merge_values and guard_projection

The JSON model is a reflexive inductive type with atoms, objects of type `String → J`, and the
`absent` sentinel. The model docstring shows that Python's loop over `base ∪ local` equals
merging at every key.

Proved, for any Python `==` that is an equivalence:
- `merge_base_local`: `merge(b,b,r) = r`, exactly.
- `merge_base_remote` / `merge_remote_base`: `merge(b,l,b) == l`, up to `==`.
- `merge_symm`: swapping local and remote gives the same conflict/success outcome, and on
  success the results are `==`.
- `guard_sound`: holds for *any* relation, even a non-reflexive one. If the guard passes, then
  at every key path the authored value is unchanged from the base or already equals the
  proposal (`Subsumed`).
- `guard_complete`: the converse.

Caveats (NEEDS-OWNER, since a change would move fold results and state roots):

- **True == 1 == 1.0**
  - `bool_one_edit_lost`: `merge_values(1, True, 1)` returns `1`. An edit from 1 to True reads
    as no change. This was replayed: the result is not `True`.
  - `bool_one_asymmetric`: symmetry holds only up to `==`. `merge(0,1,True)=True`, but
    `merge(0,True,1)=1`.
  - The fix would be type-strict equality in `merge_values`.
- **NaN**
  - `events.canonical_bytes` allows NaN (`allow_nan` defaults to True).
  - Replayed: `merge_values(NaN, NaN, 5)` raises "concurrent edit". Identity law 1 fails.
  - A terminal dismiss of an item with a NaN field will always conflict.
  - The fix would be to refuse NaN at append (`events.py`, not my file).

## 4. Dispatcher

**4a. Stranger releases.**
- Replay (`ledger_claim.main`): three `item.release` events by `bridge` fold to
  "no-op (not owner)", yet the dispatcher counted them as attempts and dismissed the item.
- Refuted (old): `Dispatch.old_deadletter_griefable`.
- Fix: the dispatcher counts only releases the fold *applied*, taken from item history.
- Proved: `Dispatch.deadletter_counts_applied`. By `LedgerSpec.Item.note`, an applied release
  was written by the current owner.
- Mutation: weakening "applied" to "not conflict" breaks it.

**4b. Dead-letter dismiss bypassed the gate.**
- It is now `guarded_append(..., "item.dismiss")`, so stage-5 arbitration applies.
- A refusal prints `DEADLETTER REFUSED` and the run continues.
- In practice the item is usually unowned, so this rarely changes the outcome.
- Releases, verifies and completions still use a plain `EventLog.append`. For releases this is
  harmless: they are always the owner's own item.

**4c. Cross-host race (NEEDS-OWNER).**
- `addressed_to` accepts any writer of the principal, and the policy lock is per host.
- Replay: host A (`miles`) and host B (`nightshift`) each claimed successfully on their own
  copy of the log. After convergence the owner is `miles`, and the `nightshift` claim is
  "no-op (already claimed)". Both would have executed.
- Model: `cross_host_race`, `second_claim_noop`, and one candidate fix `exact_no_race`.

## Files changed

- `.datacore/lib/claim_gate.py`
- `.datacore/lib/ledger/policy.py`
- `.datacore/lib/ledger_claim.py`
- New: `.datacore/lib/tests/test_ledger_policy_formal.py`
- New: `specs/datacore-lean/DatacoreSpec/LedgerPolicy.lean`
- New: this file
- `ledger/edits.py` is unchanged.

## Owner decisions applied (2026-09-23)

**Decision L4 applied: require the exact writer.** New
`actor_identity.dispatchable_by(actor, assignee)` and `dispatch_ambiguity(assignee)`.
`ledger_claim.main` uses them in place of `addressed_to`. The rules:
- A declared writer (or an unregistered name) must be the exact actor
  (`base_writer` equality).
- A name that is only a principal (for example `gregor`, who writes as `mac`)
  keeps today's behaviour only when that principal has exactly one writer.
- Otherwise nobody dispatches the item, and it is reported as AMBIGUOUS.
- A principal with no `writes_as` writes as itself.

The gate (`ledger/policy.py`) still uses `addressed_to`. Only who the
dispatcher offers work to got narrower. Skips are never silent. The summary
names:
- "addressed to X, a sibling writer of P -- only X's dispatcher takes it: <title>";
- "N AMBIGUOUS -- not dispatched, …".

Candidate 4c is closed.
- Lean (`Dispatch`): `dispatchOk`, `claimGuardExact` (redefined over the new
  rule), `exact_no_race` (for every registry, two writers that pass for one
  assignee are equal), `sibling_refused`, `single_writer_principal_kept`.
  `cross_host_race` and `second_claim_noop` remain as the old-rule record.
- Mutation:
  - `some ws => actor ∈ ws` (the old principal rule) admits a race:
    `left` and `right` both take `team`.
  - Old same-principal matching in the writer branch admits
    `nightshift` / `miles`.
  - Both counterexamples fail on the real model.
- Changed test: `test_dispatch_addressing.py::test_an_executors_own_log_name_reaches_its_own_dispatcher`
  now dispatches as `nightshift`. New test `test_a_sibling_writer_skips_it_and_says_so`
  covers `miles` skipping and naming it. The module docstring notes L4.
- Live impact (read-only fold of all spaces): 5 open delegated items, all
  addressed to `miles` (1 in 2-datacore, 4 in 8-firm). Under L4 only a
  dispatcher running as `miles` takes them. A dispatcher running as
  `nightshift` no longer does.
- NOT changed (not owned): `delegation_drill.py` scenario
  "miles completes work addressed to its own executor log" (line ~345) now
  FAILS. It pins the old rule. The fix is to dispatch as `nightshift` there and
  assert that `miles` skips it.

**Decision L5 applied: export parent hops + 1.** `executors/base.py
_execution_env` sets `DATACORE_HOPS = claim_gate.recorded_hops(task.payload) + 1`
for every executed (claimed) task. This overrides any ambient value. A run
with no claimed item keeps what it inherited. `org_workspace_adapter.py`
already records `hops = $DATACORE_HOPS`, so adapter-created follow-ups now
carry depth. Candidate 1b is closed. No Lean change: the `Hops` model already
assumes a child declares `parent + 1`, which is `follow_up_passes_floor`.

**Decision L6 applied: per principal per space (docs only).** Documented in
the `claim_gate.py` module docstring and in `creates_today`, and in the
`config/approvals_policy.yaml` `principals:` comment. No behaviour change.
Candidate 1c is closed.

**Decision L7 applied: type-strict merge for new conditional edits, versioned.**
- `ledger/edits.py`: `merge_values(..., strict=True)` compares by canonical JSON
  bytes (`strict_equal`), so `1`, `True` and `1.0` differ. `apply_condition`
  dispatches on `_merge.version`: `2` (the integer only) is strict, and
  anything the historical `version == 1` test accepted stays Python `==`.
  `conditional_payload(..., version=1)` gains a `version` argument.
- Enabling value: `.datacore/ledger-edit-protocol` = `2`. `require_edit_protocol`
  now returns the enabled version (`1` or `2`); anything else still refuses.
  `EventLog.append` stamps version 2 on every conditional edit in a space set
  to `2` (callers need no change), and refuses an explicit version-2 edit in a
  space set to `1`.
- Real data (read-only): all ten spaces folded with the pre-L7 code and the new
  code give identical `state_root`s (356 existing `_merge` events, all version
  1). Folding all history as if it were strict also gives identical roots, so
  no existing event depends on `True == 1`.
- Lean (`Merge`): `eqv_eq`, `merge_symm_strict` (symmetry is now exact
  equality), `merge_base_remote_strict`, `strict_edit_kept`, `mergeV_one`
  (version 1 = the old merge), `mergeV_two_symm`. Mutation: restating
  `merge_symm_strict`/`strict_edit_kept` with `pyEq` fails to check
  (`bool_one_asymmetric` is the counterexample).
- Not enabled on any real space (all ten say `1`). Out of bounds: follow-up.
- Residue (not my files): `projection_state` and `ledger_phase1_prepare` pick
  WHICH fields changed with Python `!=`, so an Org edit from 1 to True is not
  proposed at all; strict v2 only helps once a change reaches
  `conditional_payload`.

**Decision L8 applied: refuse NaN/Infinity at append.** `EventLog.append`
runs `json.dumps(payload, allow_nan=False)` before stamping and raises
`ValueError` ("NaN or Infinity"); nothing is written. `canonical_bytes` and
hashing are unchanged, so existing events verify as before. Real data: no event
in any space holds a non-finite number.

Tests: `lib/tests/test_decisions_ledger_b.py` (L7, L8 sections).

**Decision Q3 applied (follow-up board, 2026-09-23): changed-field detection is type-strict under protocol 2.**
- `ledger/projection_state.py`: `edit_strict(space)` (protocol file == 2),
  `changed_fields(fields, existing, strict=)`. Under 2, `reconcile` merges with
  `merge_values(strict=True)`, the no-base agreement check, `guard_projection`'s
  comparison and `sync_generated`'s changed-field pick all use
  `edits.strict_equal`, and planned edits carry `_merge.version` 2. Under 1 (or
  absent/unknown) every comparison is Python `==`, as before.
- `ledger_phase1_prepare.py`: `plan` picks changes with `changed_fields(strict=)`
  and plans version-2 edits under 2; `apply`'s stale-plan check compares
  strictly under 2.
- Real data (read-only): all ten spaces are protocol 1. Folding each one and
  running `sync_generated(dry_run=True)` with the pre-Q3 and the new module gave
  identical results (`{'dismissed': 0, 'updated': 0}` everywhere), identical
  `state_root`s before/after and identical projections; no ledger/org file
  changed (0-personal's `knowledge.db` was written concurrently by another
  process). `ledger_phase1_prepare` refuses every real space (all Phase 1), so
  there is nothing to compare there.
- Lean (`Merge`): `eqV`, `proposeV`, `proposeOld`, `proposeV_one` (protocol 1
  plans exactly what it planned before), `proposeV_two_kept` (under 2 every
  authored edit to a field the ledger has not moved is proposed, with the
  authored value), `changed_strict_sees_edit`, `changed_v1_as_before`.
  Mutation (detect with `pyEq` under 2) breaks `proposeV_two_kept` and so its
  corollary `changed_strict_sees_edit`.
- Tests: `lib/tests/test_followups_identity.py` (Q3 section). No existing test
  changed.
