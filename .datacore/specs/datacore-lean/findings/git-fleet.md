# GitFleet — findings (2026-09-23)

Model: `DatacoreSpec/GitFleet.lean` (namespace `DatacoreSpec.GitFleet`, imports
`LedgerSpec.Chain`). Checks clean with `lake env lean`; the audited theorems
use only `propext`, `Quot.sound` and (cron) `Classical.choice`.
Replays: throwaway repos plus a bare "origin" under tmp dirs, the relay's host
reached as a second clone (the `host:path` URL rewritten to a local path).
Tests: `lib/tests/test_git_fleet_formal.py` (13 tests). Scratch replay:
`scratchpad/gitfleet/replay_prefix.py`.

## Summary

| # | Candidate | Verdict |
|---|---|---|
| 1a | Relay pushes a fork. Its chain-only check passes a host that rewound and re-appended its log | CONFIRMED+FIXED |
| 1b | Relay refusal leaves the forked merge on the branch, and git_fleet_sync publishes it | CONFIRMED+FIXED |
| 1c | git_fleet_sync pushes HEAD with no fork check | CONFIRMED+FIXED |
| 1d | ledger_transport (clean-merge path) and knowledge_commit push with no ledger check | CONFIRMED (model) + NEEDS-OWNER (files are read-only) |
| 1e | "A merge is a union … nothing to conflict over" (git_fleet_sync.py:289) | REFUTED as stated; proved under its precondition |
| 1f | relay line 200 uses the local branch name, not the host's | DOWNGRADED |
| 2a | MERGE_HEAD guard assumes `.git` is a directory | CONFIRMED+FIXED (worse than the lead: it aborts a human's merge) |
| 2b | Continues after a failed pull | DOWNGRADED (pushes are FF-only; proved) |
| 2c | "must NEVER propagate a deletion" | REFUTED for the sweep's own commit (proved). The pushed range can still carry deletions → NEEDS-OWNER |
| 3 | state_loop_rollout uses the hostname as the ledger actor | CONFIRMED+FIXED (also gated its `--commit` push) |
| 4 | cron_install reconcile idempotence and verbatim preservation | Proved. One defect found and fixed (`splitlines`) |
| 5 | git_branch_hygiene revert reads as "built-on" | CONFIRMED+FIXED |
| 6 | visitor_join spacing lets joins overlap | CONFIRMED+FIXED (lock). "converged ignores in-grace ahead" is DOWNGRADED (by design) |

## 1. NEVER PUSH A FORK, over all pushers

**Property.** A push is safe when, for every writer log, origin's copy is an
event prefix of the pushed copy. `guard_sound` proves that fork.py's per-key
predicate, plus a missing-key (rewind) test, plus `verify_chain` on the
candidate, gives that prefix relation (under a collision-free hash, as in
`Chain.lean`). `never_push_a_fork` then proves: if every pusher's gate implies
`SafePub`, every trace of pushes, in any interleaving, only ever extends
origin's logs, and they stay valid chains. Mutation check M1: weakening the
guard to fork.py's collision test alone, without the rewind test, breaks
`kguard_iguard`/`guard_sound`, because a truncated log passes it.

**1a CONFIRMED+FIXED.** `merge_rewrite_is_not_union` shows the case. Local left
the log alone and the host rewrote it, so git's trivial merge takes the host's
copy whole. That copy is a valid chain, so `ledger_forks` (verify_chain +
check_not_rewound) passes it. `relay_old_publishes_fork` models this, and
`test_relay_does_not_publish_a_host_rewrite` replays it. Before the fix the
relay returned `RELAYED 1 commit(s) from HOSTX -> origin`, and origin's
`(w,2),(w,3)` were replaced.

**1b CONFIRMED+FIXED.** This is the survey's chain. The host edits an event in
place and local appends, so the merge is clean and the chain check fails.
The relay then returns "REFUSED … Local history retained", and HEAD is the
merge. The next `sync_repo(execute=True)` returns `PUSHED 1 file(s)`, and
origin's chain gets `line 2: hash mismatch`. Model: `survey_chain_old`; fixed
`relay_refusal_restores`. Mutation check M3 (keep the merge on refusal) breaks it.

**1c CONFIRMED+FIXED.** `test_fleet_sync_refuses_to_publish_a_rewritten_log`:
before the fix the sweep committed a rewound-and-re-appended `w.jsonl` and
returned `PUSHED`. Model: `ungated_publishes_fork`. Mutation check M2 (push
ignores the gate) breaks `never_push_a_fork`.

**Fixes (`git_relay.py`, `git_fleet_sync.py`, `state_loop_rollout.py`):**
- New `git_relay.publication_forks(repo, commit, ref)`. It reads the commit's
  tree (what the push publishes), not the working tree. It reuses fork.py's
  `_index` and `_git` and `verify_chain`, and nothing is reimplemented. It
  reports collisions (fork.py's predicate), missing keys (rewind), and a chain
  that fails where origin's copy passed. Identical blobs are skipped.
- `relay`: before a push it checks `ledger_forks` plus `publication_forks`
  against `origin/<branch>`, both after the host merge and after the origin
  pull. On refusal `_park` moves the merge to `refs/relay-refused/<host>/<branch>`
  and runs `reset --hard` back to the pre-merge HEAD. The tree was verified
  clean before the merge, so the reset loses nothing. A conflicted merge or
  pull still keeps its evidence in place: MERGE_HEAD blocks every sweep.
- `git_fleet_sync.sync_repo`: runs `publication_forks(HEAD, origin/<default>)`
  before the push. On a fork: `committed, PUSH REFUSED — ledger fork`, and
  `result['ledger_fork']` is set. The work stays committed locally, and
  `main()` exits 1.
- `state_loop_rollout --commit`: runs the same check before `git push`.

Real data: `git_relay.py --forks` and `fork.detect_all` over `~/Data` are
clean (0 chain failures; every space "ok … agree with origin"), so the new
gate blocks no current push.

**1d NEEDS-OWNER (read-only files).**
- `ledger_transport._converge_locked` → `_push_with_retry`. When a merge
  CONFLICTS, the result is prefix-proven (`Chain.resolution_lossless`). A
  CLEAN merge, `origin/<db>` or any `origin/ledger/*` ref, is pushed with no
  chain or fork check. A rewritten log on a ledger ref is exactly the 1a case.
- `knowledge_commit._push_commit` pushes `sha`, whose ancestry includes every
  unpushed local commit. Its lease proves the commit fast-forwards, not that
  the ledger does.
- Both are `ungated` in the model: `ungated_publishes_fork` is their
  counterexample.

**1e REFUTED as stated, proved under its precondition.**
`merge_union_of_appends`: when each side only appended, every clean one-sided
merge contains both sides. `merge_rewrite_is_not_union`: the claim fails for
a side that rewrote the log. The comment is kept; the gate now enforces the
precondition.

**1f DOWNGRADED.** `relay` fetches `relay-<host>/<local branch>`. If the host
sits on another branch, the relay reports "nothing on host", which is a false
negative, but `--check` keeps reporting the repo as trapped. No fork and no
loss follow. Not changed.

## 2. git_fleet_sync guards

**2a CONFIRMED+FIXED.** In a linked worktree or a submodule, `.git` is a
file, so `repo/.git/MERGE_HEAD` never exists. Replay: a worktree with a
hand-resolved and staged merge, then `sync_repo(execute=True, pull=True)`.
The pull fails ("Exiting because of unfinished merge"), the failure path runs
`git merge --abort`, and afterwards `f.txt` reads `'mainline\n'`: the
resolution is gone and MERGE_HEAD is gone. Fix: new `in_progress(repo)` asks
`git rev-parse --git-path` for MERGE_HEAD, rebase-merge, rebase-apply,
CHERRY_PICK_HEAD and REVERT_HEAD. An unanswerable query counts as busy.
Model: `guard_old_misses_worktree`, `guard_new_exact`,
`sync_new_keeps_resolution`. Mutation check M4 breaks it.

**2b DOWNGRADED.** After a failed pull the sweep still commits and pushes, but
`push_arguments` without a lease never forces. The push is either rejected or
a fast-forward (`ff_push_monotone`). Content-level forks are 1c's gate.

**2c.** `sweep_never_deletes`: a path gets into `to_add` only if its status
has no `D`, so the sweep's own commit never removes a path. But the push
publishes all of `origin..HEAD`, which can include earlier local commits that
deleted files. NEEDS-OWNER (below).

## 3. state_loop_rollout actor — CONFIRMED+FIXED

`_actor()` returned `socket.gethostname().split('.')[0]`. Replay: two "hosts"
with the same hostname `ubuntu.cloud.internal` and declared actors
alpha/beta. Both appended `('ubuntu', 0)`, with hashes `a0812c…` and `e3a9d1…`,
which is a fork on merge. Fix: `actor_identity.this_actor()`. Model:
`actor_old_forks`, `actor_new_distinct`; mutation check M8 breaks it. The
residue is `actor_new_residue`: two UNDECLARED hosts that share a short
hostname still collide, because this_actor falls back to the hostname. That
is actor_identity's policy.

## 4. cron_install.reconcile — proved, plus one fix

`reconcile_idempotent` holds given only that an emitted managed line is never
kept (its marker names a key in `entries`); dropping that hypothesis breaks
the proof. `reconcile_preserves_unmanaged`: the kept lines are an exact,
ordered prefix of the output.

The fix is to the premise that lines are split at `\n`. `str.splitlines()`
also splits at `\x0b \x0c \x1c-\x1e \x85    `. Replay:
`'0 1 * * * echo keep\x0c0 2 * * * /opt/x/.datacore/lib/foo.py # one cron line\n'`
reconciled to `'0 1 * * * echo keep\x0c\n…'`, cutting a managed-looking
fragment out of an unmanaged line (`split_fragments_drop`). Now it uses
`re.findall(r'[^\n]*\n|[^\n]+\Z', …)`.

Also noted, not changed: the intervening-edit check and the write in
`install` are not atomic (crontab has no compare-and-swap; the post-write
verify catches it). A line with unbalanced quotes makes `shlex` raise, so the
installer refuses, which is fail-safe.

## 5. git_branch_hygiene — CONFIRMED+FIXED (advisory tool)

`landed_earlier` searched `rev-list trunk -- path` over all of history. In the
replay, trunk goes v1→v2 (fork)→v3, the branch reverts to v1, and the branch
was classed `built-on`, meaning safe to delete. Fix: search `base..trunk`
only. `hygiene_revert` gives the counterexample and `hygiene_new_sound` the
fixed guarantee; mutation check M6 breaks it. The regression test that a real
land-then-build-on still reads `built-on` passes.

## 6. visitor_join — CONFIRMED+FIXED

The attempt marker is written after `converge` (≤1500 s) and duties run
after it (≤3900 s each), so `MIN_SPACING_S` spaces joins that have ENDED.
Replay: a tick during a join finds it due, and a nested `join()` recursed
without bound (RecursionError). Fix: `_exclusive()`, a non-blocking `flock` at
`join.lock` beside the attempt marker. `join()` returns `{"busy": True}` and
`due()` says "a join is already running". Model: `join_overlap_old`,
`join_mutual_exclusion`; mutation check M7 breaks it. launchd does not overlap
one job's runs, so the overlap needed `--now` plus a tick, or two agents.
"converged ignores in-grace ahead" is DOWNGRADED: the grace period is the
publisher's allowance by design.

## Files changed

- `.datacore/lib/git_relay.py`: `publication_forks`, `_park`, relay gates
- `.datacore/lib/git_fleet_sync.py`: `in_progress`, publication gate, exit 1 on a refused fork
- `.datacore/lib/state_loop_rollout.py`: `_actor` → `this_actor`; publication gate before push
- `.datacore/lib/cron_install.py`: splits lines at `\n` only
- `.datacore/lib/git_branch_hygiene.py`: `landed_earlier(..., base)` searches `base..trunk`
- `.datacore/lib/visitor_join.py`: `_exclusive` lock in `join`, `due`, `main`
- new: `.datacore/lib/tests/test_git_fleet_formal.py`, `DatacoreSpec/GitFleet.lean`, this file

## NEEDS-OWNER

1. Should `ledger_transport._push_with_retry` and `knowledge_commit._push_commit`
   call `git_relay.publication_forks(repo, sha, 'origin/<branch>')` before
   pushing? Today a clean merge of a rewritten log, or of a forked ledger ref,
   goes to origin unchecked.
2. Should git_fleet_sync refuse to push when earlier unpushed local commits
   (`origin/<default>..HEAD`) delete tracked files? Its own commit never
   deletes, but it publishes the whole range.
3. Should `actor_identity.this_actor()` be called with `strict=True` by ledger
   writers such as the rollout? Two undeclared hosts that share a short
   hostname still write one log.

## Decisions applied (2026-09-23)

**Decision P2 applied: refuse and name the commits (was 2c / NEEDS-OWNER 2).**
Before any push, `git_fleet_sync.sync_repo` runs the new
`range_deletions(repo, 'origin/<default>', HEAD)`:

    git log --no-renames --diff-filter=D --name-only --cc origin/<default>..HEAD

If any commit in the range deletes a tracked file, the push is refused.
- `result['deletions']` holds `[(sha, [paths])]`, and the status reads
  `committed, PUSH REFUSED — the range deletes tracked files: …`.
- `main()` prints every commit and path and exits 1.
- The sweep's commit stays local.

Details of the check:
- A range git cannot list (for example, no `origin/<default>`) is refused as
  unknown.
- `--cc` counts a merge's own deletions (an "evil merge"). It does not count a
  deletion merged in from origin, which origin already has.
- `--no-renames` counts a rename as the deletion it contains, as the sweep
  already does.
- The fork refusal and the deletion refusal are both reported before `main`
  exits 1.

Lean (`DatacoreSpec/GitFleet.lean` §4, after `sweep_never_deletes`):
- `range_gate_keeps_origin_paths`: with the gate, every path origin has is in
  the tree the push publishes.
- `old_range_publishes_deletion`: the sweep's own commit is clean, but the
  range deletes, and the gate rejects the range.
- Mutation M9 (a gate that checks nothing) proves `mutant_gate_passes_a_deletion`.

Tests in `lib/tests/test_decisions_sync.py`:
- `test_p2_an_earlier_local_deletion_is_not_pushed` (includes `main()` → 1, with
  the sha and path printed);
- `test_p2_a_merge_that_itself_deletes_is_refused`;
- `test_p2_a_deletion_pulled_from_origin_does_not_block` (the non-regression
  test).

The first two failed before the change.

Residual: without `--pull`, `origin/<default>` may be stale. Commits that
already reached origin and deleted files would then be in the range and be
refused. That is a false refusal, never a false push; the next `--pull` run
clears it.

**Decision L9 applied: gate both remaining pushers (1d closed).**
- `ledger_transport._push_with_retry`: on every attempt, before the push,
  `git_relay.publication_forks(space, HEAD, 'origin/<db>')`. A finding returns
  `push REFUSED — ledger fork …` with `context['ledger_fork']`, naming the
  recovery (`git_relay.py --forks`, `ledger_restore_prefix.py --find`). The
  commit stays local.
- `knowledge_commit._push_commit`: the same check on `sha` against
  `origin/<branch>` and against the destination just fetched; raises
  `LedgerForkRefused` (a `GitError`) naming the recovery. Its non-fast-forward
  path (`_push_converging` → `git_integration.integrate`) pushes a merge, so the
  `authorize_source` hook now gates `merge-tree base sha`, the exact tree
  `integrate` publishes, and the refusal is not swallowed.
- Replays (failed before the fix): the transport returned `pushed` for a
  rewound-and-re-appended log; `_push_commit` pushed it; with the gate removed
  the integration path published it too.
- Real data: `publication_forks(HEAD, origin/main)` on all ten spaces → no forks.
- Lean: `transportGate`, `knowledgeGate`, `Pusher`, `gateOf`, `gateOf_sound`,
  `never_push_a_fork_all_pushers`. Mutation: making `transport`/`knowledge`
  `ungated` breaks `gateOf_sound`.
- Tests changed (fixture only): `test_ledger_transport.py::_push_failing` stubs
  the gate (its fake repo has no git); `test_converge_folds_a_writers_own_ref_into_main`
  writes a real chain (its one-line stub fails verify_chain and is now refused).

**Decision L10 applied: an undeclared host may not append under its hostname.**
- This mac is declared (`identity.env` → `mac`); every server in
  `infrastructure.yaml` declares `access.actor`.
- `EventLog.append` (the one place every writer reaches) calls
  `actor_identity.this_actor(strict=True)` when the actor equals the short
  hostname; on an undeclared host that raises `UndeclaredActor`, naming
  identity.env and the registry. A name the caller chose is not a guess and is
  not affected, so tests and `--actor` keep working.
- Lean §5: `mayAppend`, `strict_no_guess`, `strict_actor_no_residue` (two hosts
  share a log only if they declared the same actor), `residue_refused`.
  Mutation: `mayAppend := True` breaks both.
- Test changed: `test_ledger_cli.py::test_default_actor_from_hostname_when_env_unset`
  → `test_undeclared_host_refuses_to_append_as_hostname` (it pinned the old
  fallback).
- Callers still resolve non-strictly and now fail at append instead of at
  resolution (not my files): ledger_accept_authored, ledger_resolve_conflict,
  ledger_ingest_org, ledger_phase1_prepare, ledger_claim, ledger_cli,
  ledger_seal, tool_policy, org_workspace_adapter, executors/base,
  chief-of-staff cos_generate; nightshift `claim.py`/`ledger_hooks.py` still
  read `socket.gethostname()` directly (not lowercased).

**Decision Q7 applied: fail the run (second board, 2026-09-23).** No code
change to the exit rule: a deletion refusal still makes `main()` return 1. It
is now documented in the module docstring and at the print site.
- A failed Q8 fetch is recorded as a deletion refusal. It appears in
  `result['deletions']` as `('?', ['cannot fetch origin/<default> …: <error>'])`,
  so it fails the run too.
- Lean: `exitCode`, `deletion_refusal_fails_run`, `clean_run_exits_zero`.
- Mutation M-Q7 (drop `deleting` from the first branch) breaks
  `deletion_refusal_fails_run`.
- Test: `test_q8_a_failed_fetch_refuses_and_names_it` (`main()` → 1), plus the
  existing P2 test.

**Decision Q8 applied: fetch first (second board, 2026-09-23).** Before
`range_deletions`, `_land` calls the new
`fetch_default(repo, default)`:

    git fetch -q origin +refs/heads/<default>:refs/remotes/origin/<default>

It does this with or without `--pull`, under the Q6 lock.
- A failure refuses the push: `committed, PUSH REFUSED — cannot fetch
  origin/<default> to check the range for deletions: <last git line>`. The
  work stays committed locally.
- This closes the P2 residual above: origin's own deletions, already merged
  here by some other path, no longer cause a false refusal.

Lean (§4, after `old_range_publishes_deletion`):
- `Q8Pass`.
- `q8_fetch_failure_refuses`.
- `q8_pass_sound`: the P2 guarantee against the freshly fetched tip.
- `stale_ref_false_refusal`: the same three trees are refused from a stale
  ref and pass from the fetched one.
- Mutation M-Q8 (`none ↦ True`) breaks `q8_fetch_failure_refuses`.

Tests (`lib/tests/test_followups_sync.py`):
- These failed before the change:
  - `test_q8_a_stale_tracking_ref_does_not_refuse_origins_own_deletion`;
  - `test_q8_a_failed_fetch_refuses_and_names_it`: the fetch URL is
    unreachable but the push URL works, so before the change it PUSHED.
- `test_q8_a_fresh_ref_still_refuses_a_local_deletion` passes.

Consequence to know: a host that cannot reach a remote, and has new work to
land there, now fails the run through the Q8 refusal. The pull's `NO ACCESS`
note never fails the run. Before this change the same case printed
`PUSH FAILED` and exited 0.

**Decision Q2 applied (follow-up board, 2026-09-23): every writer resolves its actor strictly at startup.**
- Entry points resolve once, before any work, with `this_actor(strict=True)`;
  an undeclared host exits 2 (or, in a library, raises `UndeclaredActor`)
  naming identity.env and `registry/infrastructure.yaml`. `--actor`,
  `DATACORE_ACTOR` and registry writer names still win; an explicit `--actor`
  is never resolved (ledger_claim and ledger_phase1_prepare used to evaluate
  `default=this_actor()` even when `--actor` was passed).
- Changed: `ledger_claim.main`, `ledger_phase1_prepare.main`,
  `ledger_accept_authored.main`, `ledger_resolve_conflict.main`/`_reconcile`,
  `ledger_cli` (append/approve only; verify/items/balances need no identity),
  `ledger_seal` (emit only), `ledger_ingest_org._this_actor` + `main` (resolved
  once, passed to `import_space`/`sync_state`), `executors/base._default_actor`
  (an undeclared host gets an error `ExecResult`, `_invoke` never runs),
  `org_workspace_adapter._ledger_emit` (Phase 1 refuses before
  `sync_generated`; Phase 0 mirror stays optional), nightshift
  `ledger_hooks.actor` (no raw `socket.gethostname()`; an old root lib accepts
  only an explicit DATACORE_ACTOR), `claim._ledger_actor` (no longer swallows
  into the hostname), `run.main` (exit 2 before the lock), chief-of-staff
  `cos_generate._briefing_log` (no record, briefing still runs).
- This mac, read-only: every changed resolver and CLI startup resolves `mac`
  (source `identity.env`).
- Lean §5: `startupActor`, `runEntry`, `explicit_wins`,
  `startup_resolves_or_refuses`, `startup_never_guesses`,
  `refused_writes_nothing`, `no_late_identity_failure`; refutation
  `late_failure_as_found`. Mutation (`| none => some (actorNew h)`) breaks all
  four non-trivial theorems.
- Tests: `lib/tests/test_followups_identity.py` (Q2 section),
  `modules/nightshift/tests/test_followups_nightshift.py` (Q2 section),
  `modules/chief-of-staff/tests/test_cos_generate_item.py` (2 added). Changed:
  `lib/tests/test_ledger_ingest_actor.py::test_sweep_does_not_fall_back_to_the_shared_genesis_actor`
  (it pinned the call-site text `import_space(space, actor=_this_actor())`).
- Not changed (not in scope): `tool_policy.py` (other agent),
  `ledger_dismiss_orphans._actor`, chief-of-staff `cos_merge_runs.py:931`
  (still falls back to `socket.gethostname()` on ImportError), nightshift
  `claim.get_executor_id` (`server:<hostname>` is an executor label, not the
  ledger actor).
