# Ledger role pool: build report (2026-10-18)

The first role pool on the ledger hub. Spec: the DIP-0034 October 2026 amendment
(a draft), section F ("Roles belong to membership, not to the actor"). Task 7727bbff.

- **Branch:** `feat/ledger-role-pool`, branched from `feat/ledger-hub` at 89bc63a
- **Commits:**
  - 064056e: the evals, committed while they still failed
  - 461af42: the implementation
  - one more commit adds this report
- **Not pushed, not merged, not deployed.**

## What was built

| Piece | Where |
|---|---|
| `member.role` {actor, role, from, until} and `pool.budget` {role, space, cents_per_day}. Only a hub writes them; a writer that submits one is refused (`hub_only_type`). | `ledger/events.py` (`POOL_EVENT_TYPES`), `ledger/hub.py` (`HUB_ONLY_TYPES`) |
| The opt-in: `HubConfig.pools`, off by default; `enable_pools(space, on=True)`; CLI `python3 -m ledger.hub pools <space> [--off]` | `ledger/hub.py` |
| `Hub.record_role(actor, role, from_ms=, until_ms=)`, `Hub.set_budget(role, cents_per_day)`. The active grantor only; refused with `pools_off` when the space has not opted in, and with `unknown_role` when the role is not defined in `.datacore/templates/roles/` | `ledger/hub.py` |
| `pool_rule`: the eligibility hook the hub left for this step. It is used only when pools are on and no explicit override is passed. | `ledger/hub.py` |
| Budget gate in `request_lease`, refusal code `budget_reached` | `ledger/hub.py` |
| Writer pre-check (`HubWriter._pool_precheck`): refuses a holder's own check (`own_check`) and a completion without a DONE_WHEN (`no_done_when`) before anything is written | `ledger/hub.py` |
| Fold: the `members`, `budgets` and `pool_spend` state; the membership and DONE_WHEN rules on pool leases; the completion and check rules; and the helpers `role_live`, `pool_budget`, `pool_spent`, `pool_refusal`, `pool_accepted` and `done_when_of` | `ledger/fold.py` |

### Rules as implemented

- **Who may lease a pool item.** An item with a `role` and no `assignee` is a pool item. It is leased only if:
  - it has a DONE_WHEN: the `done_when` field in the payload, or the `org.properties.DONE_WHEN` property;
  - the requesting actor has a `member.role` for that role;
  - that membership was recorded by **the same hub log that grants the lease** (same hub id, same grantor);
  - the membership is live at the hub-clock moment of the grant, meaning `from <= t < until` (an empty `until` means it never ends).
  - A later `member.role` for the same actor and role replaces the earlier one. Ending a membership early is a record whose `until` is now.
- **The same rule holds in the fold.** A lease that breaks it folds to a recorded `lease_conflicts` entry and never becomes a lease. That covers a hub with an override, an older hub, or a hand-written lease. Every copy of the log reaches the same state root.
- **The daily budget.** Spending is counted from `spend.record` events that carry a hub stamp and name a pool item (`payload.item`). Each is grouped by role and by the UTC day of its HLC timestamp. Once the day's total reaches `cents_per_day`, the hub refuses new leases with `budget_reached`. A cap of 0 closes the pool.
  - Existing leases are untouched: they can still be renewed, recorded as spending and completed.
  - A new UTC day starts again from zero.
  - A role without a `pool.budget` has no cap.
  - Spending on items that are not pool items never counts.
- **Completion and check.** When the holder completes a leased pool item, its status becomes `completed`. It counts as accepted (`pool_accepted`) only once an actor other than the holder appends an `item.verify` and the status reaches `verified`.
  - The holder's own `item.verify` is refused by `HubWriter` before anything is written.
  - If such a check reaches the hub some other way, the hub accepts it and marks the receipt `not_counted: own_check…`. The fold ignores it.
  - A completion of a pool item whose DONE_WHEN was removed is handled the same way, under `no_done_when`.
- **The assignee rule stays the default.**
  - An item with neither an assignee nor a role is still `not_eligible`.
  - An item with both an assignee and a role is refused as ambiguous.
  - Addressed items behave exactly as before: they need no role and no DONE_WHEN, and they ignore the pool budget.
  - With pools off, a role item is refused exactly as before.

### Design choice to review: the hub accepts a refused check instead of rejecting it

The hub *accepts* the holder's own check and a completion without a DONE_WHEN, marking them `not_counted`, rather than rejecting the append. A writer appends to its own log first. If the hub rejected an event the writer had already appended locally, every later append from that writer would hit a gap at that point, and the writer could not recover without someone editing its log. So the refusal is enforced in three places, and none of them can strand a writer:

1. at the writer, before the write;
2. in the fold, which is the arbiter;
3. in the hub's receipt.

## Results (run in this worktree, 2026-10-18)

The evals are in `.datacore/lib/tests/test_ledger_role_pool.py`: 13 tests.

- **Red first:** at 064056e the file failed to collect. It imports `pool_accepted` from `ledger.fold`, which did not exist yet.
- **Test fixes after the red commit:** before the first green run I changed three things in the fixture, and no assertion was changed:
  - added a `sys.path` entry so the file can import the hub test's fixtures;
  - made the log's clock-skew guard follow the test clock, the same way the timestamp clock already did;
  - made the checker in two tests pull the log before checking. Without that its check got a timestamp earlier than the completion it was checking.

| DONE_WHEN line | Tests | Result |
|---|---|---|
| 1. Leases go only to live role members, in the hub and in the fold | `test_pool_item_is_leased_only_to_a_live_member_with_the_role`, `test_a_membership_that_has_expired_or_not_started_is_refused`, `test_the_fold_refuses_a_lease_to_an_actor_without_the_role`, `test_membership_is_recorded_only_by_the_hub_and_only_for_known_roles` | pass |
| 2. The budget stops new leases, existing ones carry on, the next day resets | `test_daily_budget_stops_new_leases_keeps_existing_ones_and_resets_next_day`, `test_a_role_without_a_budget_is_not_capped_and_a_budget_is_per_role` | pass |
| 3. DONE_WHEN is required; only another actor's check accepts the work | `test_a_pool_item_without_done_when_is_not_leased`, `test_completion_is_accepted_only_after_another_actors_check`, `test_the_fold_refuses_a_pool_completion_when_done_when_was_removed` | pass |
| 4. The assignee rule is still the default | `test_with_pools_on_unaddressed_items_wait_and_addressed_items_are_unchanged` | pass |
| 5. Off by default; old state roots unchanged | `test_pools_are_off_unless_the_space_opts_in`, `test_folding_a_log_without_pool_events_hashes_as_before` | pass |
| Worked example | `test_worked_example_research_space` | pass |

Other suites:

- **Ledger suites** (`pytest tests/test_ledger*.py`, which includes the hub tests): **617 passed**. Before this change, at 89bc63a, the count was 604 passed; the difference is the 13 new tests.
- **Wider run** (`-k "claim or projector or seal or briefing or hermes or done_report or cadence or write_gate"`): 479 passed, 1 failed. The failure is `test_cadence_liveness.py::test_a_continuous_cadence_is_a_bot_loop_listed_grey_never_a_missing_registration`. It fails the same way on a clean copy of 89bc63a, so it was already failing before this work.

## Worked example: one research space with the researcher role

```bash
python3 -m ledger.hub enable <space> --hub-id research-hub --grantor hubd
python3 -m ledger.hub pools <space>
```

```python
hub.record_role("wa", "researcher", until_ms=now + 30 * DAY_MS)   # the hub's own log
hub.set_budget("researcher", 2_000)                               # 20.00 a day
owner.append("item.create", {"id": "competitor-scan", "title": "Scan three competitors' pricing pages",
                             "role": "researcher",
                             "done_when": "a sourced summary of three competitors is attached and a reviewer has read it"})
wa.request_lease("competitor-scan")          # granted: wa is a live researcher
wb.request_lease("competitor-scan")          # not_eligible: wb holds no researcher membership
wa.append("spend.record", {"item": "competitor-scan", "cents": 120, "model": "..."})
wa.append("item.complete", {"id": "competitor-scan"})
wa.append("item.verify", ...)                # own_check: refused before writing
wb.append("item.verify", {"id": "competitor-scan", "check": "..."})   # accepted -> pool_accepted() is True
```

This example runs as `test_worked_example_research_space`. No production space config was changed.

## Not verified, and limits

- **Recording roles and budgets from the command line.** Not built. `record_role` and `set_budget` are methods on the running hub, because they must be signed with the grantor's key. The command line covers only `pools`. Not verified: how the owner triggers them in production, whether through a datacored route or an owner command on the hub host.
- **Promotion.** Memberships and budgets count only from the hub log that grants. After a `promote`, the new grantor has to record them again. Until it does, pool leasing refuses everyone. That fails safe, but it is a step to remember, and there is no test of it.
- **The budget is enforced by the hub, not re-checked by the fold.** A spending record from a writer that was offline can arrive later with an earlier timestamp. Re-checking the budget in the fold would then cancel, after the fact, a lease the hub granted in good faith. So the fold records the spending, and only the hub applies the cap. Two hubs during a partition could therefore together grant past one day's cap. The double run would still be visible through the earliest-lease rule.
- **The budget check runs before the work, not during it.** It counts what has already been recorded, so one lease granted just under the cap can still push the day's total over it.
- **Signatures on pool events through git.** The fold accepts a `member.role` from whatever log names the granting hub as its writer. A forged one is caught only by strict signature verification (`verify_chain(strict=True)` after the cutover point). The fold itself does not check keys. That is the same trust model as `item.lease` today.
- **HttpTransport and datacored routes for pools.** Not added. Not verified over a network; every test uses `LocalTransport`.
- **Who may check.** The checker is not required to hold any role, only to be someone other than the holder. The spec does not say more.
