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
| 1 | `lib/ledger_transport.py` `append()` | one event per call | **Routed (step 1).** No production caller uses it yet. |
| 2 | `lib/ledger_transport.py` `_converge_locked` → `_push_with_retry` | every `converge` and `sync` | Keep. It is the publication the slot job uses. Callers outside the slot are rows 3 to 6. |
| 3 | `lib/ledger_claim_run.sh` (converge before and after each claim tick) | claim and run events, per tick | **Routed (step 2).** Both converges pass `--actor "$ACTOR"`. |
| 4 | `lib/ledger_phase1_cycle.sh` (converge per space) | hourly cycle | Slot-aligned schedule documented below; no code change. |
| 5 | `lib/ledger-claim-pull.sh:81` `git push origin HEAD:ledger/$ACTOR` | satellite claim and completion | Defer through the policy (addressed tasks no longer race; see the design report). |
| 6 | `modules/nightshift/lib/claim.py:455` (`commit_to_branch` push) | nightshift claim per task (5-plur: 18 a day) | Defer through the policy. |
| 7 | `modules/nightshift/lib/run.py:767` `git push … db` | after a run | Defer. |
| 8 | `modules/ventures/lib/cadence_run.py:431` | cadence records (6-meridian: 27 a day) | Defer. |
| 9 | `modules/nightshift/lib/scoreboard.py:456` | scoreboard | Defer. |
| 10 | `lib/ledger_publish_safe.py:157` | the Mac's machine-written ledger files | Keep immediate (owner workstation, no slot). |
| 11 | `lib/git_fleet_sync.py:858` | the box's sync of trapped agent work | Slot-aligned schedule documented below; no code change. |

### Content: journals, reports, knowledge

| # | Site | Recommendation |
|---|---|---|
| 12 | `lib/knowledge_commit.py` `commit_knowledge`, `publication_manifest.py` `publish`, `agent_wrap_up.py` | **Routed (step 2)** through `resolve_push(push, actor, urgent=)`. |
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


## Step 2: the callers that generate the traffic

### What changed

| File | Change |
|---|---|
| `.datacore/lib/tests/test_scheduled_push_callers.py` | New. 17 tests. 11 of the first 13 failed before the code (the 2 that passed hold today's behaviour). The 4 tests for the `policy` command failed before it existed. |
| `.datacore/lib/ledger_transport.py` | `converge(actor=)` and `converge --actor`. New command `policy --actor A [--urgent]`. |
| `.datacore/lib/ledger_claim_run.sh` | Both converges of every tick pass `--actor "$ACTOR"`. |
| `.datacore/lib/knowledge_commit.py` | `resolve_push(push, actor, urgent=)`. `commit_knowledge(..., actor=, urgent=)` reports `pushed`. CLI flags `--actor=NAME` and `--urgent`. |
| `.datacore/lib/publication_manifest.py` | `publish(..., actor=None, urgent=False)`. With no actor, this host's declared writer decides. This covers the research module's call, which passes no actor, without editing that module. |
| `.datacore/lib/agent_wrap_up.py` | Passes `actor=agent` and the new `--urgent` flag. |

### How it behaves

**Claim tick, scheduled mode, agent writer.** `converge` *receives*:
- autosave, fetch, and merge, through `_converge_locked(publish=False)`, the same code `_push_with_retry` already uses;
- never a thinner `git pull`;
- the log line reads `received; publish deferred to this host's slot`.

**Claim tick, any other case.** The owner, an undeclared writer, the default mode, or a converge that names no actor (the slot job, the hourly cycle, `sync`) publishes exactly as before.

**Knowledge commit.**
- An explicit `push=False` always wins.
- In the default mode, `push` is returned unchanged, without even looking up the actor.
- In scheduled mode, an agent's commit lands on the default branch locally and waits for the slot, unless it is `urgent`.

**`policy` command.** `ledger_transport.py policy --actor A [--urgent]` prints `now` or `deferred`, so a shell script can guard its push in one line.

### Tests after step 2 (run in the worktree, 2026-10-18)

| Command | Result |
|---|---|
| `test_scheduled_push_callers.py`, before the code | 11 failed, 2 passed. The 4 `policy` tests, added later, failed 4 of 4 before the command existed. |
| `test_scheduled_push.py` + `test_scheduled_push_callers.py`, after | 43 passed (26 + 17) |
| New tests plus the 5 existing transport files plus every test file that touches `knowledge_commit`, `publication_manifest`, `agent_wrap_up` or `ledger_claim_run` (16 files) | 328 passed, 1 failed |
| All of the above plus every test file that imports the transport (32 files) | 460 passed, 11 failed |

The 11 failures are the same 11 that fail on unchanged `main` in a fresh worktree:
- the shipped-registry test in `test_transport_foreign_writer.py`;
- the 10 tests in `test_disk_full_is_named.py`, `test_local_mcp_server_handoff.py`, `test_sim_harsh_sync_fixes.py`, `test_sync_conflict_org_and_alerts.py` and `test_sync_conflict_alert_shared_marker.py`.

No new failure appeared. One limit: the 16-file run happened before the `policy` command was added. The 32-file run came after it and includes all 16 files.

### Hourly cycle and fleet sync: the slot-aligned schedule (documented, not deployed)

The smaller honest change is documentation. A `--respect-slot` guard would let these jobs skip their own runs, but they also ingest and project, which must keep running. Moving their crons is deployment, so it is left for the owner.

**One finding:** every host that runs `ledger_phase1_cycle.sh` is set to the **same minute, :25** (`agent_host_setup.sh:112,122,134`; `jobs/manifest.yaml:363,381`). That is a race of its own, every hour. One exception: the manifest notes that hermes's cron is disabled for now; what each host's live crontab holds was not checked. The box's fleet sync runs at 06:10 and 18:10 UTC, which falls in nightshift's slot.

| Job | Now | Slot-aligned | Where it is set |
|---|---|---|---|
| phase-1 cycle, box | `25 * * * *` | `0 * * * *` | the box's crontab; `jobs/manifest.yaml:363` (schedule text) |
| phase-1 cycle, nightshift | `25 * * * *` | `10 * * * *` | `agent_host_setup.sh:112`; `jobs/manifest.yaml:381` |
| phase-1 cycle, hermes | `25 * * * *` | `20 * * * *` | `agent_host_setup.sh:134` |
| phase-1 cycle, plur-claw | `25 * * * *` | `25 * * * *` (already its slot) | `agent_host_setup.sh:122` |
| fleet sync, box | `06:10, 18:10 UTC` | `06:30, 18:30 UTC` | `install_fleet_sync_timer.sh:59,60` (`OnCalendar`); `jobs/manifest.yaml:529` |
| publish-slot, each host | none | its two minutes from `push_slots.yaml` | new timer (examples above) |

Each job then publishes inside its own host's window. Converge runs under the repo lock, so a cycle and the slot job on the same host serialise rather than race.

### Follow-ups in the module repositories (not edited: separate repositories)

Each item names the line that pushes and the change it needs. Python modules get the policy with `from ledger_transport import push_policy` and `from actor_identity import this_actor`. Both are importable wherever `.datacore/lib` is on the path, as it is for these modules.

1. **`modules/ventures/lib/cadence_run.py:431`** (`rc, out = git("push", "-q")`; cadence records, 27 a day in 6-meridian). Insert before it:
   `if push_policy(this_actor()) == "deferred": return "committed; the host's slot job publishes it"`
2. **`modules/nightshift/lib/scoreboard.py:456`** (`_sh(['git', 'push'], cwd=firm)`). Change it to:
   `if push_policy(this_actor()) == "now": _sh(['git', 'push'], cwd=firm)`
3. **`modules/nightshift/lib/run.py:767`** (push of the default branch after merging an unfinished run branch). Wrap it the same way:
   `if push_policy(this_actor()) == "now": subprocess.run(['git', 'push', '-q', _push_remote(repo), db], ...)`
4. **`lib/ledger-claim-pull.sh:81`** (`git push -q origin "HEAD:refs/heads/ledger/$ACTOR"`). This file is in the root repository, not a module. It was left alone as instructed, and can be done on this branch. Change it to:
   `[ "$(python3 "$ROOT/.datacore/lib/ledger_transport.py" policy --actor "$ACTOR")" = now ] && git push -q origin "HEAD:refs/heads/ledger/$ACTOR" 2>&1 | tail -1`
   Low value: it pushes to the writer's own ref, which nobody else writes, so it never makes a merge on main.
5. **`modules/nightshift/lib/claim.py:455`.** **Recommend leaving this one immediate.** The push *is* the claim: it publishes a claim candidate against the remote tip it was fetched from (`push_arguments(commit, ref)` with a snapshot check). Holding it back would let a second host claim the same task in the meantime, which is the 2026-09-18 double run. If it must defer anyway, the one-line form is the same guard as item 2, but the owner should decide that explicitly.

### A separate bug, not touched

`modules/outbox/lib/archive_sync.py:339` runs `git remote get-url origin >/dev/null 2>&1 && git push origin HEAD || true`. The `|| true` throws away a failed push, so the archive can report success while its commits exist only on this disk. The transport was written to end this pattern (`ledger_transport.py` docstring). It needs its own fix, which should report the push's result. It has nothing to do with scheduling.

## What scheduled mode now covers / still does not

**Covered: on a host with `DATACORE_PUSH_MODE=scheduled`, these wait for the slot:**
- `ledger_transport.append()` by an agent;
- both converges of every claim tick (`ledger_claim_run.sh`) for an agent actor;
- agent wrap-up commits (`agent_wrap_up.py`), unless `--urgent`;
- recorded publications (`PublicationManifest.publish`), including the research module's, when the host's writer is an agent;
- `knowledge_commit.py` on the command line, with `--actor=`.

**Still pushes at once in scheduled mode:**
- The hourly cycle (`ledger_phase1_cycle.sh`) and the box's fleet sync. They converge with no actor, by design. Aligning their schedules (table above) makes them slot pushes.
- The module call sites: cadence records, scoreboard, nightshift run merge, satellite claim ref (follow-ups 1 to 4), and the nightshift claim (5, recommended to stay immediate).
- `gh_reconcile.py:333`, `cross_model_audit.py:1301`, `skills/github-triage/run_nightly.sh`. Each is one guard with `policy`/`push_policy`; they are not done here.
- A converge into an **empty** remote. The "first publish" branch of `_converge_locked` pushes before the `publish` flag is read. It happens once per new space, so it was left as is.
- Owner sessions, `urgent` work, writers not declared as agents, and every host in the default mode. All of these push at once, as intended.

**Not verified:**
- behaviour on the real hosts;
- whether their timers fire inside the 5-minute windows;
- the effect on merge-commit counts.

The design report's 24-hour measurement after a host opts in settles all three. Nothing was installed and no host was contacted.

## Follow-ups (2026-10-18)

The owner approved two follow-ups on 2026-10-18:
- (a) stagger the hourly cycle;
- (b) route the module pushes listed above through the push policy.

Nothing has been pushed, merged or installed, and no host was contacted.

### (a) The hourly phase-1 cycle: one minute per host, outside every push window

This replaces the "slot-aligned" table above. The owner's rule: each host's cycle runs at its own minute, and that minute falls outside its own push slot and outside every other host's. The 5-minute windows start at :00, :10, :20, :25, :30, :40, :50 and :55. That leaves four free stretches: :05-:09, :15-:19, :35-:39 and :45-:49. Each host gets one of them.

| Host | Was | Now | Defined in |
|---|---|---|---|
| plur-claw | `25 * * * *` | `05 * * * *` | `agent_host_setup.sh`, openclaw profile |
| nightshift | `25 * * * *` | `15 * * * *` | `agent_host_setup.sh`, nightshift profile; contract text in `jobs/manifest.yaml` |
| hermes | `25 * * * *` | `35 * * * *` | `agent_host_setup.sh`, hermes profile |
| box | `25 * * * *` | `46 * * * *` | contract text in `jobs/manifest.yaml` only; the crontab exists only on the box |

Why :46 for the box, rather than :05 or :45:
- its hourly ingest at :00 takes the cycle lock;
- its claim tick runs at :05 and :35;
- its CoS sync runs at :45.

Why :05 for plur-claw: its dispatcher runs every 15 minutes, and :05 sits between two runs.

**The test** is `.datacore/lib/tests/test_phase1_cycle_stagger.py`. It reads the minutes from the installer and the manifest, and checks three things:
- all four minutes are different;
- none falls inside a window in `push_slots.yaml`;
- the installer and the contract give nightshift the same minute.

Before the change: 2 failed. After: 2 passed.

**What Gregor changes on the hosts.** Nothing in this repository writes the box's crontab. A satellite gets its new line only when its installer runs again.
- **box:** in `crontab -e`, change the minute of the `ledger_phase1_cycle.sh` line from `25` to `46`. Nothing else on the line changes.
- **nightshift, plur-claw, hermes:** after the merge and a runner refresh, run `agent_host_setup.sh --host <name>`. It replaces the `phase1-cycle` line by its marker. The hand edit is the same one field: nightshift `15`, plur-claw `05`, hermes `35`.
- **hermes:** the manifest says its cycle cron is disabled until its clones are renamed. Running the installer there would put the line back, so leave hermes off until the rename is done.
- **Not changed:** the box's fleet sync at 06:10 and 18:10 UTC still lands in nightshift's slot. It was not part of this approval.

### (b) The module pushes

| # | Push site | Repository and branch | Change |
|---|---|---|---|
| 1 | `ventures/lib/cadence_run.py` `_commit_push` | datacore-ventures, `feat/scheduled-push-ventures` | Added `_push_deferred()`, which asks `ledger_transport.push_policy(this_actor())`. When the answer is "deferred", the record is committed and not pushed. The call returns `committed; this host's push slot publishes it`. |
| 2 | `nightshift/lib/scoreboard.py` | datacore-nightshift, `feat/scheduled-push-nightshift` | Added `lib/push_gate.py` with `push_now()`. The scoreboard commits, then pushes only when `push_now()` is true. |
| 3 | `nightshift/lib/run.py` `_merge_unfinished_run_branches` | none | **Not changed, because this code never runs.** Its last caller was removed in release c556862 (2026-09-15). It also calls `_push_remote`, which that release deleted, so any call would raise NameError. It should be removed separately. |
| 4 | `lib/ledger-claim-pull.sh` | this repository, `feat/scheduled-push` | Its converge now passes `--actor "$ACTOR"`; before, it published in scheduled mode as well. The push to `ledger/<actor>` runs unless `ledger_transport.py policy` prints `deferred`. |
| 5 | `nightshift/lib/claim.py:455` | none | Left immediate, as recommended above: that push is the claim. |

**All three guards fail open.** A core without `push_policy`, a policy that raises, or an empty answer each mean "push as before". The live hosts run a core without the policy until this root branch merges. So if the module branches merge first, nothing changes.

`venture_heartbeat.push_cadence_record` needs no module change. It calls `commit_knowledge(push=True)` with no actor, and step 2's `resolve_push` then applies the host's own writer.

### Tests (run 2026-10-18)

| Test | Before | After |
|---|---|---|
| root `test_phase1_cycle_stagger.py` | 2 failed | 2 passed |
| root `test_claim_pull_scheduled_push.py` | 2 failed, 2 passed (the 2 that passed hold today's behaviour) | 4 passed |
| root `test_claim_pull_publishes_only_its_own.py`, `test_scheduled_push.py`, `test_scheduled_push_callers.py` | — | 3 + 43 passed |
| root, every test file that reads `jobs/manifest.yaml` or `agent_host_setup.sh` (28 files, promise evals excluded) | — | 490 passed, 1 failed, 14 skipped |
| nightshift `tests/test_scheduled_push_gate.py` | collection error (no `push_gate` yet) | 7 passed |
| nightshift `tests/`, whole suite, on `origin/main` and on the branch | `origin/main`: 16 failed, 832 passed | branch: 14 failed, 841 passed |
| ventures `lib/tests/test_cadence_run_scheduled_push.py` | 1 failed, 3 passed (the 3 that passed hold today's behaviour) | 4 passed |
| ventures `lib/tests`, whole suite, on `origin/main` and on the branch | `origin/main`: 73 failed, 316 passed, 9 errors | branch: 73 failed, 320 passed, 9 errors |

Notes on the failures:
- **The one root failure** is `test_jobs_grounded.py::test_runner_manifest_matches_canonical`. It compares the manifest with the runner copy installed on this Mac, which was byte-identical to the manifest before the edit. It passes again once the branch merges and the runner refreshes, so it is expected and not a regression.
- **Nightshift:** every branch failure also fails on `origin/main`. The two `origin/main`-only failures (`test_model_output_admission`, `test_one_space_does_not_cost_the_night`) passed on the branch run. That looks flaky, but it is not verified.
- **Ventures:** the same tests fail before and after; the 4 extra passes are the new file. The shared failures include the two existing `report` tests. On this Mac a global git hook (ledger-write-gate) refuses their fixture commit, with or without this change. The new test gives its fixture repository its own hooks directory.

**End-to-end check.** I ran each guard against this branch's core, with the real principal registry and `DATACORE_PUSH_MODE=scheduled`:
- `push_gate.push_now()` for `miles`: `False`;
- `cadence_run._push_deferred()` for `tris`: `True`.

With the mode set to `immediate`, both said push.

**Not verified:**
- that the slot job's `sync` covers plur-claw's dispatch space (`2-plur-space`), and so publishes the claims that `ledger-claim-pull.sh` now holds back;
- how any of this behaves on a real host.
