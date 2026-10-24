# Ledger role pool: build report (2026-10-18)

Role pools on the ledger hub. Spec: the DIP-0034 October 2026 amendment (a
draft), section F. Task 7727bbff.

- **Branch:** `feat/ledger-role-pool`, branched from `feat/ledger-hub` at 89bc63a
- **Commits:**
  - 064056e, 461af42, d470b09: the first build (membership model), its tests and its report
  - 01aea80: the tests for the owner's model, committed while they still failed
  - one more commit: the code change and this report, rewritten
- **Not pushed, not merged, not deployed.**

## The model: the role is named in the task

The first build gave roles to agents through a membership record
(`member.role`) that the hub wrote into its own log. The owner replaced that
the same day (Gregor, 2026-10-18):

> "The role is defined in the task. If the task isn't delegated to any agent
> concretely, an available agent picks it up, loads the role description and
> executes the task. This is not the main scenario; main is that most tasks
> will be directly delegated."

The membership model is gone. The `member.role` event type, `Hub.record_role`,
`fold.role_live` and the fold's membership state were deleted, not left unused:
nothing else needed them. `pool.budget` stays.

### Rules as implemented (in a space with pools on)

- **A task addressed to a named agent** (`assignee`): only that agent may lease
  it. This is the assignee rule, unchanged, and it is the main scenario.
  - It may also carry a `role`. Then the grant names that role's description,
    and a role with no description is refused (`unknown_role`).
  - It needs no DONE_WHEN, is not capped by any pool's budget, and its own
    completion counts as before.
- **An unaddressed task with a role** (`role`, no `assignee`) is a pool task.
  Any *available* agent may lease it. No membership is needed. Available means:
  - the lease request is signed by the writer (the hub already refuses an
    unsigned or wrongly signed request);
  - the writer belongs to a principal in `principals.yaml` with `kind: agent`.
    A human principal, or a writer with a key but no principal, is refused
    (`not_eligible`);
  - the role's pool is under its daily budget (`budget_reached` otherwise).
  - The task needs a DONE_WHEN: the `done_when` field in the payload, or the
    `org.properties.DONE_WHEN` property.
  - One live lease per task, as for every hub item: a second agent gets `taken`.
- **The role description.** When the task has a role, the signed lease event
  carries `role: {name, template}`. `template` is the installation-relative
  path the holder loads: `.datacore/templates/roles/<role>.md`. Before the
  composed file is built from its layers (DIP-0002), the hub names
  `<role>.base.md` instead, the file that exists. The hub looks the file up at
  lease time, so a description added later is found on the next request. A role
  with neither file, or a role that is not a plain role name (no paths), is
  refused with `unknown_role`. `set_budget` uses the same lookup.
- **An unaddressed task with no role** is still not dispatched (owner rule since
  2026-09-23).
- **The daily budget.** Spending is counted from `spend.record` events that carry
  a hub stamp and name a pool task (`payload.item`), grouped by role and by the
  UTC day of the timestamp. Once the day's total reaches `cents_per_day`, the
  hub refuses new leases on that role's tasks. Existing leases carry on (renew,
  spend, complete); a new UTC day starts from zero; a role with no budget has no
  cap; spending on addressed tasks does not count.
- **Completion and check.** A pool task's completion needs a DONE_WHEN, and
  counts as accepted (`pool_accepted`) only once an actor other than the lease
  holder appends an `item.verify`. The holder's own check is refused by
  `HubWriter` before anything is written; if one reaches the hub anyway, the hub
  accepts the line, marks the receipt `not_counted: own_check…`, and the fold
  ignores it. A completion after the DONE_WHEN was removed is handled the same
  way (`no_done_when`). The reason the hub accepts instead of rejecting: a
  rejected line the writer already appended locally would leave a gap that
  blocks every later append from that writer.
- **In the fold**, a lease of a pool task with no DONE_WHEN is a recorded
  conflict in every copy, never a lease.
- **Off unless the space opts in** (`python3 -m ledger.hub pools <space>`). With
  pools off, every task with a role is refused exactly as before, addressed or
  not, and `set_budget` is refused (`pools_off`).

### Where it lives

| Piece | Where |
|---|---|
| `pool.budget` {role, space, cents_per_day}, written only by a hub (`hub_only_type` for a writer) | `ledger/events.py` (`POOL_EVENT_TYPES`), `ledger/hub.py` (`HUB_ONLY_TYPES`) |
| Eligibility with pools on: `pool_rule`, `registered_agent`, `role_template` | `ledger/hub.py` |
| The role description in the grant, `unknown_role`, `budget_reached` | `Hub.request_lease` in `ledger/hub.py` |
| `Hub.set_budget`, `enable_pools`, CLI `pools` | `ledger/hub.py` |
| Writer pre-check (`own_check`, `no_done_when`) | `HubWriter._pool_precheck` in `ledger/hub.py` |
| `pool_role`, `is_pool_item`, `pool_refusal`, `pool_accepted`, `done_when_of`, budget and spend state | `ledger/fold.py` |

## Results (run in this worktree, 2026-10-18)

The evals are in `.datacore/lib/tests/test_ledger_role_pool.py`: 16 tests.

- **Red first:** at 01aea80, 14 of 16 failed against the membership code. The 2
  that passed were the pools-off test and the test that an unaddressed task
  without a role is refused, behaviour that did not change.
- **Green:** after the code change, 16 passed.
- **Ledger suites** (`python3 -m pytest -q tests/test_ledger_*.py`): **620
  passed** (837 s). Before this rework: 617 passed. The difference is the 13 old
  role-pool tests replaced by 16 new ones.

| Required behaviour | Tests |
|---|---|
| An addressed task: only its agent; the grant names its role; budget does not apply | `test_an_addressed_task_is_leased_only_by_its_agent_and_names_its_role` |
| An unaddressed role task: any registered agent, no grant needed, one lease at a time | `test_any_registered_agent_may_lease_an_unaddressed_role_task_one_at_a_time`, `test_only_a_registered_agent_principal_is_available` |
| The role description is named and must exist, checked at lease time | `test_a_role_without_a_template_is_refused_and_checked_at_lease_time`, `test_a_role_with_only_the_base_layer_names_the_base_file` |
| No assignee and no role: not dispatched | `test_an_unaddressed_task_without_a_role_is_not_dispatched` |
| The daily budget | `test_daily_budget_stops_new_leases_keeps_existing_ones_and_resets_next_day`, `test_a_role_without_a_budget_is_not_capped_and_a_budget_is_per_role` |
| DONE_WHEN and another actor's check | `test_an_unaddressed_role_task_without_done_when_is_not_leased`, `test_the_fold_refuses_a_lease_of_a_pool_task_without_done_when`, `test_completion_is_accepted_only_after_another_actors_check`, `test_the_fold_refuses_a_pool_completion_when_done_when_was_removed` |
| Off by default; membership gone; old state roots unchanged | `test_pools_are_off_unless_the_space_opts_in`, `test_membership_is_gone_and_the_budget_is_written_only_by_the_hub`, `test_folding_a_log_without_pool_events_hashes_as_before` |
| Worked example | `test_worked_example_research_space` |

The tests run in a tmp installation: a tmp `principals.yaml` (agents `wa`, `wb`,
`wc`; the owner as a human writing as `own`) and fake role descriptions. The
real role templates, keys and principals are never read.

## Worked example: one research space with the researcher role

```bash
python3 -m ledger.hub enable <space> --hub-id research-hub --grantor hubd
python3 -m ledger.hub pools <space>
```

```python
hub.set_budget("researcher", 2_000)                               # 20.00 a day
owner.append("item.create", {"id": "competitor-scan", "title": "Scan three competitors' pricing pages",
                             "role": "researcher",
                             "done_when": "a sourced summary of three competitors is attached and a reviewer has read it"})
grant = wa.request_lease("competitor-scan")   # granted: wa is a registered agent
grant["lease"]["payload"]["role"]             # {"name": "researcher", "template": ".datacore/templates/roles/researcher.md"}
wb.request_lease("competitor-scan")           # taken: one lease at a time
wa.append("spend.record", {"item": "competitor-scan", "cents": 120, "model": "..."})
wa.append("item.complete", {"id": "competitor-scan"})
wa.append("item.verify", ...)                 # own_check: refused before writing
wb.append("item.verify", {"id": "competitor-scan", "check": "..."})   # accepted -> pool_accepted() is True
```

This runs as `test_worked_example_research_space`. No production space config
was changed.

## Limits, and what is not verified

- **The fold cannot check who is an agent.** `principals.yaml` is a private file
  on each host, and the fold must reach the same state from the same log on
  every host. So "a registered agent principal" and "the role description
  exists" are checked by the hub only. A hub with an override, an older hub, or
  a hand-written lease can give a pool task to a human or for a role with no
  description, and the fold will accept it. The fold still refuses a pool lease
  with no DONE_WHEN. The first build's fold did check membership; that check is
  what was given up with the membership record.
- **The role description lives on the hub's host.** `ROLES_DIR` is the code
  checkout the hub runs from. The holder is told a path, not the content; if
  the holder's host has a different version of that file, or none, it loads
  that. Not verified: whether the role templates are tracked and synced to every
  agent host. In the main checkout today the composed `.md` files are untracked.
- **"Available" means eligible, not idle.** The hub does not track how many tasks
  an agent already holds; an agent can lease several pool tasks at once. The
  owner's words "an available agent" may mean more than this. Not decided.
- **No dispatcher picks pool tasks yet.** An agent must ask for the lease
  itself. Not built: the loop that offers unaddressed role tasks to idle agents,
  and the step where the agent loads the named description before it starts.
- **Addressed tasks with a role need pools on.** With pools off, the default
  assignee rule still refuses every task that carries a role, addressed or not,
  because that rule predates roles. If the main scenario (addressed, with a
  role) should work in every hub space, the default rule needs to change; that
  is a decision for the owner.
- **The budget is enforced by the hub, not re-checked by the fold.** A late
  spending record from a writer that was offline would otherwise cancel, after
  the fact, a lease granted in good faith. Two hubs during a partition could
  together grant past one day's cap. The check runs before the work, so one
  lease granted just under the cap can push the day over it.
- **Setting a budget from the command line.** Not built. `set_budget` is a method
  on the running hub, because the event must be signed with the grantor's key.
  After a `promote`, the new grantor has to set budgets again; until then the
  role has no cap. No test of that.
- **HttpTransport and datacored routes for pools.** Not added, not verified over
  a network. Every test uses `LocalTransport`.
- **Who may check.** The checker must be someone other than the holder; it need
  not be an agent or hold any role. The spec does not say more.

## Update: merged with the hub's audit fixes, and audit finding 14 (2026-10-18)

Commits on `feat/ledger-role-pool` (nothing pushed):

| Commit | What |
|---|---|
| 7307465 | Merge of `feat/ledger-hub` at 3943110 (audit fixes, parts 1 and 2) |
| cd1564c | Tests first for audit finding 14 (red: 7 failed, 15 passed) |
| 964b039 | Code for finding 14 |

### The merge

The pool now runs on the hub's admission and lease model:

- Leases come from the hub's `_grant`. A grant names an attempt, the task-content hash and the epoch. When the task has a role, it also names `role` {name, template, sha256}. Pool eligibility (`pool_rule`), the role-description check and the budget check sit inside `_grant`.
- In the fold, the hub's lease plan decides first (earliest lease wins). After that, a pool item with no DONE_WHEN is still a recorded lease conflict.
- `pool.budget` is a hub-only type in `hub_admission`. Only the grantor writes it, in its own log.
- The first build's "accept, but mark `not_counted`" is gone. A holder's own check, or a pool completion without a DONE_WHEN, is now refused at the front door by the policy rule (`not_authorized`, `hub_policy.authorize`). The fold run with policy turns the same event into a recorded no-op, and the writer still refuses it before writing. If the event is already in the writer's signed log, the hub keeps it as a rejected proposal (audit finding 11), so that writer's chain is not stranded.
- A hub-side bug was found here: after a renewal, `HubWriter._attempt_for` lost the task hash, so a completion after a renewal folded to a lease conflict. *(Corrected 2026-10-18: the fix is on `feat/ledger-hub`, 73a26f0 test and 31eb47b fix, and merged into this branch and `feat/agent-checkin`. The earlier sentence saying it was missing there was stale.)*

### Finding 14: what is implemented

| Requirement (amendment G) | Implementation | Tests (`test_ledger_role_pool_review.py`) |
|---|---|---|
| The grant refuses a pool task without a DONE_WHEN | `pool_rule`; whitespace counts as none | `test_f14_the_grant_refuses_a_pool_task_without_done_when_on_the_production_path` (also passed before the code) |
| The role template version is pinned to the attempt | The grant's `role.sha256` is the hash of the file's contents. The folded `lease_ref.role` keeps it. `HubWriter.load_role` refuses a file that has changed (`role_changed`). A new attempt pins the file as it is then | `test_f14_the_lease_pins_the_role_templates_content_hash_to_the_attempt` |
| A completion means "submitted for review", with a digest of result and evidence | Status `submitted` (org REVIEW). `ItemState.submission` = {attempt, task_hash, result_digest, role_sha256, holder}. A digest that does not match is refused. A submitted item is never leased again | `test_f14_a_pool_completion_is_submitted_for_review_with_a_digest_of_result_and_evidence`, `test_f14_a_submitted_pool_item_stays_in_the_org_projection_as_review` |
| The item is done only after a check by an independent execution identity | `item.verify` must be bound to the submission (attempt, task hash, result digest). Its writer must belong to a registered principal, and not the holder's (`hub_policy.pool_review_problem`). The rule is applied by the writer, by the hub, and by the fold with policy | `test_f14_only_another_execution_identity_accepts_the_submission`, `test_f14_a_same_identity_check_imported_by_git_is_a_no_op_on_replay` |
| Budget | This already existed on the branch. It stays as a **stop-on-recorded-spend threshold, not a hard cap**. Work in flight, parallel leases and late spend can cross it | `test_f14_the_budget_stops_new_leases_on_recorded_spend_and_is_not_a_hard_cap` |
| Production eligibility path | Every pool and finding-14 test uses `eligibility=None` (`pool_rule`), with policy inputs read from the tmp registry. One older test uses `allow_any` on purpose: it shows the fold refusing a bad hub's grant. It does not claim the feature | all |

**What "independent execution identity" means here.** The registry (`principals.yaml`) identifies an execution identity as a principal: one entry per agent or human, with every writer name it uses. The rule therefore compares principals, not writer names, so `wa2` (a writer name of principal `wa`) cannot check `wa`'s work. **The registry cannot tell apart two separate runs of one principal, or two principals sharing a process or host.** That is not checked. A human principal counts as independent. The amendment says "a different agent"; whether a human review is acceptable is for the owner to decide. A plain `fold()` without policy can only see writer names.

**The budget's semantics as implemented** (the amendment says these must be settled):

- The day is the UTC day of the spend's HLC.
- The unit is cents of an unspecified currency.
- Spend counts toward a role only when it is a hub-stamped `spend.record` naming a pool item.
- Only the grantor can set the threshold (`set_budget`).
- There are no reservations and no refunds.
- Late spend counts on its own HLC day.

### Tests changed, and why

- Merge: the fixture enables the hub with an owner and uses the fixed policy inputs. `submit_raw` names the lease attempt, as `HubWriter.append` does. Two tests now expect `not_authorized` at the front door instead of an accepted line marked `not_counted`; one of them also checks that a git copy folds the completion to nothing.
- Finding 14: the fixture's policy now carries the writer-to-principal map from its tmp registry. Two tests expect `submitted` where they expected `completed`, because a pool completion now means something different. Three tests compare the grant's role with `granted_role()`, which includes the new `sha256`.

### Counts (seen in this session)

`python3 -m pytest .datacore/lib/tests -k ledger -q`:

| After | Result |
|---|---|
| The merge | 844 passed (828 hub baseline + 16 pool tests) |
| Finding 14 | 851 passed, 0 failed, 6277 deselected |

### Still open

- DIP-0034 section G still says "Not implemented". The DIP was not edited; the owner amends it.
- Acceptance case 12 (independent clocks, real git) is **not verified**.
- No route sends rejected work back. A submitted item cannot be re-leased, and no event returns it to the pool or to its holder.
- The `pools` switch is a config field. Unlike `hub.enable` and `hub.promote`, it is not an owner-signed ledger record.
- `hub_policy` refuses the old `item.claim` path for any item with a role, addressed or not. Such items are taken only through a lease.
- Role templates are read from each host's code checkout. Whether they are tracked and synced to every agent host is **not verified**.
- There are no HTTP routes for pools, and no dispatcher that offers pool tasks to idle agents.

## Audit fixes, part 3

Source: the Codex re-audit of 2026-10-18 (`2-datacore/1-tracks/engineering/reports/2026-10-18-ledger-hub-codex-reaudit-report.md`), new defects 5 and 6, on top of the hub's part 3 (see the hub review note, items A–J).

Commits (root `feat/ledger-role-pool`): 395ff5f (merge of the hub fixes), f877aa9 (tests first, K and L), c6bf462 (fixes), 77df4ed and d4d62e3 (merges of the hub's two follow-ups). Merges are merge commits; nothing is pushed.

| Item | What was wrong | Fix | Tests (`test_ledger_role_pool_review.py`) |
|---|---|---|---|
| K (new 5) | A pool holder set itself as the assignee and completed without independent review | A grant in a pool space pins the attempt's class (`pool` or `addressed`) and its review requirement beside the role it already pinned; `fold.is_pool_item` follows the pin, so a re-addressed task's completion is still a submission for review. While an attempt is live, only the policy's approver changes the task's assignee, role or DONE_WHEN (`hub_policy._attempt_obligations`, at the front door and in the fold) | `test_reaudit_new5_a_live_attempt_keeps_its_class_role_and_review_whatever_the_task_says_later` |
| L (new 6) | `HubWriter._pool_prepare` folded without admission, so a lease nobody granted decided what a check was bound to | It reads `ledger.hub.authoritative_state` | `test_reaudit_new6_pool_preparation_reads_the_authoritative_state`; also the hub's source guard |

Merge conflicts (395ff5f), resolved by keeping both sides: `LedgerState` carries the pool's budgets and spend and the hub's per-event outcomes; the grant checks the execution policy before the role pin and the budget; `hub_policy` governs both `item.verify` (pool review) and `item.lease`.

**Existing test changed:** `test_ledger_role_pool.py::test_the_fold_refuses_a_pool_completion_when_done_when_was_removed` removed DONE_WHEN during a live attempt as the task's owner writer. K refuses that now; the test asserts the refusal and removes it as the approver instead. Its assertions about the completion are unchanged.

**Decision for the owner:** "owner re-approval" for changing a live attempt's assignee, role or DONE_WHEN is implemented as the approvals policy's approver. Should it instead be the hub space's recorded owner?

**Still open:** the budget currency and day boundary (finding 14), and everything listed as open in the hub note.

**Test counts (seen in this session):** pool + review + hub suites: 92 passed after the fixes. `-k ledger` on this branch: 852 passed before (7c16bdd), 867 passed, 0 failed after (at d4d62e3).
