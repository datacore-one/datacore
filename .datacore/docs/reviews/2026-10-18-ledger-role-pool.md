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
