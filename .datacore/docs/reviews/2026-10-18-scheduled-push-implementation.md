# Scheduled pushes, step 1: implementation (2026-10-18)

The design is in `2-datacore/1-tracks/engineering/reports/2026-10-18-scheduled-push-report.md`. This is step 1 of the approved plan, on branch `feat/scheduled-push`. Nothing has been pushed, merged or installed on any host.

## What changed

| File | Change |
|---|---|
| `.datacore/lib/tests/test_scheduled_push.py` | New. 26 tests, committed first. 25 failed before the code existed. |
| `.datacore/lib/ledger_transport.py` | `push_mode()`, `push_policy()`, `urgent=` on `append()`, `load_slots()`, `in_slot()`, `publish_slot()`, CLI `publish-slot`. |
| `.datacore/config/push_slots.yaml` | New. The slot table. |

### The switch, and why merging it changes nothing

- `push_mode()` reads `DATACORE_PUSH_MODE` from the environment, then from `~/.datacore/identity.env`. It reuses the identity-file reader that `DATACORE_LEDGER_SIGN` already uses (`ledger.log._identity_file_value`).
- If the value is unset or not recognised, the mode is `immediate`, which is today's behaviour. A typo never holds events back.
- A host opts in with one line in its identity file: `DATACORE_PUSH_MODE=scheduled`.

### Who waits and who does not

`push_policy(actor, urgent=False)` returns `deferred` only when all three of these are true:

- the host is in scheduled mode;
- the event is not urgent;
- the writer belongs to a principal declared `kind: agent` in `principals.yaml`.

Everything else returns `now`: the owner's sessions (`kind: human`), `urgent=True`, a writer nobody declared, and a registry that cannot be read.

`append(..., urgent=True)` is the flag for the briefing and anything the owner is waiting on. A deferred append is committed but not pushed. Its result says `appended (push deferred to this host's slot)` and carries `deferred: slot`. An explicit `push=False` behaves as before.

### The slot job

```
python3 .datacore/lib/ledger_transport.py publish-slot --host <name> [--force] [--at HH:MM]
```

- It reads `config/push_slots.yaml` and runs the existing knowledge-repo `sync` (`sync_all(include_code=False)`). That means commit, merge (never rebase), and a push through the fork gate. No new publication path was added.
- Outside the host's window it does nothing, says so, and exits 0. A timer that fires early is therefore not an alarm. `--force` publishes anyway.
- A host with no minutes (`mac`) prints that it pushes at once and exits 0.
- An unknown host, or a table that cannot be used, exits 2.

| Host | Minutes (5-minute window) |
|---|---|
| box | :00, :30 |
| nightshift | :10, :40 |
| hermes | :20, :50 |
| plur-claw | :25, :55 |
| mac | none: owner sessions push at once |

`load_slots()` refuses a table where two windows share a minute (mod 60). The 5-minute window is the widest that fits: hermes's :20 window and plur-claw's :25 window touch but do not overlap. The 90-minute grace of the gap detector for unpublished events (`seq_gap`) is three times the 30-minute cycle.

### Example timers (documentation only; nothing installed)

cron (box):
```
0,30 * * * * cd $HOME/Data && python3 .datacore/lib/ledger_transport.py publish-slot --host box --quiet >> $HOME/.datacore/state/publish-slot.log 2>&1
```

systemd timer (nightshift), `OnCalendar=*-*-* *:10,40:00` running:
```
ExecStart=/usr/bin/python3 %h/Data/.datacore/lib/ledger_transport.py publish-slot --host nightshift
```

launchd is not needed: the Mac has no slot.

## Tests (run in the worktree, 2026-10-18)

| Command | Result |
|---|---|
| `pytest .datacore/lib/tests/test_scheduled_push.py` before the code | 25 failed, 1 passed (the one pass is the existing `push=False` contract) |
| same, after the code | 26 passed |
| `pytest test_scheduled_push.py test_ledger_transport.py test_ledger_transport_profile_a.py test_transport_lock_safety.py test_transport_foreign_writer.py test_publication_transport_lock.py` | 103 passed, 1 failed |
| the same five existing files on unchanged `main`, before any edit | 77 passed, 1 failed (the same test) |

The one failure has nothing to do with this change. `test_transport_foreign_writer.py::test_registry_falls_back_to_the_shipped_copy` expects the private `.datacore/registry/repositories.yaml`. That file is gitignored, so a fresh worktree does not have it. The test fails the same way on `main` in this worktree.

The other 19 test files that import the transport were also run: 168 passed, 10 failed. The same 10 fail on unchanged `main` (6ec05c2) in a clean worktree: 10 failed, 23 passed across those five files. They are in `test_disk_full_is_named.py`, `test_local_mcp_server_handoff.py`, `test_sim_harsh_sync_fixes.py`, `test_sync_conflict_org_and_alerts.py` and `test_sync_conflict_alert_shared_marker.py`. The likely cause is modules that a fresh worktree lacks, such as chief-of-staff and nightshift, which are separate repositories. That cause is not verified.

## The direct `git push` call sites

Production code only. Tests, the fleet simulator (`fleet_week_sim.py`, `sim/stand_in.py`) and the chaos drill are left out.

### Ledger and coordination: the high-frequency traffic

| # | Site | What pushes | Recommendation |
|---|---|---|---|
| 1 | `lib/ledger_transport.py` `append()` | one event per call | **Routed through the policy (this change).** No production caller uses it yet; see below. |
| 2 | `lib/ledger_transport.py` `_converge_locked` → `_push_with_retry` | every `converge` and `sync` | Keep. It is the publication the slot job uses. Callers outside the slot are rows 3 to 6. |
| 3 | `lib/ledger_claim_run.sh` (converge before and after each claim tick) | claim and run events, per tick | **Next step:** in scheduled mode, make the trailing converge receive-only (`_converge_locked(publish=False)` exists) and let the slot publish. |
| 4 | `lib/ledger_phase1_cycle.sh` (converge per space) | hourly cycle | Put its cron on the host's slot minutes; it then becomes the slot job. |
| 5 | `lib/ledger-claim-pull.sh:81` `git push origin HEAD:ledger/$ACTOR` | satellite claim and completion | Defer through the policy (addressed tasks no longer race; see the design report). |
| 6 | `modules/nightshift/lib/claim.py:455` (`commit_to_branch` push) | nightshift claim per task (5-plur: 18 a day) | Defer through the policy. |
| 7 | `modules/nightshift/lib/run.py:767` `git push … db` | after a run | Defer. |
| 8 | `modules/ventures/lib/cadence_run.py:431` | cadence records (6-meridian: 27 a day) | Defer. |
| 9 | `modules/nightshift/lib/scoreboard.py:456` | scoreboard | Defer. |
| 10 | `lib/ledger_publish_safe.py:157` | the Mac's machine-written ledger files | Keep immediate (owner workstation, no slot). |
| 11 | `lib/git_fleet_sync.py:858` | the box's sync of trapped agent work | It is already a scheduled sync; align its cron to the box's slot. |

### Content: journals, reports, knowledge

| # | Site | Recommendation |
|---|---|---|
| 12 | `lib/knowledge_commit.py:92,109` (`commit_to_branch`, `commit_knowledge(push=)`), called by `agent_wrap_up.py:216` (`push=True`) and `publication_manifest.py:37` | Pass `push=(push_policy(actor) == "now")` from `agent_wrap_up`. Owner-facing deliverables pass `urgent`. |
| 13 | `lib/gh_reconcile.py:333` | Defer. |
| 14 | `lib/cross_model_audit.py:1301` | Defer. |
| 15 | `skills/github-triage/run_nightly.sh:59` (via converge) | Nightly; leave, or run inside the slot. |
| 16 | `lib/wrap_up_mechanics.py:751` | Usually an owner session; keep immediate. |

### Code and operator tools: leave as they are

| Site | Why |
|---|---|
| `modules/nightshift/lib/run_publication.py:214,219`, `run_rescue.py:198,268` | Run branches for pull requests: code, pushed when ready |
| `modules/chief-of-staff/server/lib/cos_merge_runs.py:783` | Merge gate's atomic push |
| `lib/git_integration.py:87` | Integration merge (code) |
| `lib/git_relay.py:386,400` | Operator relay for a host that cannot reach its remote |
| `lib/state_loop_rollout.py:225`, `lib/nightshift_recover_stranded.py:162,207` | Manual operator tools |
| `lib/plur_store_sync.py:581` | The memory store's own repository, not a space |
| `modules/outbox/lib/archive_sync.py:339` (`git push origin HEAD \|\| true`) | Archive repository. It swallows a failed push, which is a separate defect worth fixing. |

## What step 1 does not yet do

- **On its own, opting in a host changes little today.** No production code calls `ledger_transport.append()`. The "ledger:" commits measured on 2026-10-18 come from converge autosaves (rows 2 to 4), the claim scripts (rows 5 and 6) and the cadence records (row 8). The policy is in place and tested; routing those callers through it is the next step. Row 3 is the largest single source.
- `converge` called outside the slot by any loop still pushes. That is correct for the owner's sessions, but in scheduled mode it lets agent loops publish off-slot until row 3 is routed.
- Not verified: behaviour on the real hosts, whether their timers fire on time, and the effect on merge-commit counts. The design report's 24-hour measurement settles those once a host opts in.
