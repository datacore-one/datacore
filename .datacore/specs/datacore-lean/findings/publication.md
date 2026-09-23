# Findings: Publication cluster (2026-09-23)

Model: `DatacoreSpec/Publication.lean` (namespace `DatacoreSpec.Publication`),
checked with `lake env lean DatacoreSpec/Publication.lean`: clean, with no
`sorry`, `axiom` or `native_decide`. Replays run in throwaway repositories under
pytest `tmp_path`. The "origin" is a local bare repository.
Tests: `lib/tests/test_publication_formal.py`.

| # | Candidate | Verdict |
|---|-----------|---------|
| 1 | publication_workspace_gc reclaim | CONFIRMED+FIXED |
| 3a | publication_state / `_repo_lock` keyed by basename | CONFIRMED+FIXED |
| 3b | reconcile accepts local refs as "landed" | DOWNGRADED (intended) |
| 3c | git_fleet_sync commits without `_repo_lock` | CONFIRMED (by reading), NEEDS-OWNER (not my file) |
| 4a | writeback: second write queued before processing always conflicts | CONFIRMED+FIXED |
| 4b | writeback: ABA re-apply | DOWNGRADED (outside the claim; the only fix is a policy choice) |
| 4c | writeback docstring claim (retry idempotent, third version never overwritten) | REFUTED lead, claim PROVED |
| 11 | worktree_lifecycle retire CAS | Claim holds; PROVED and replayed |

## 1. publication_workspace_gc: reclaim deleted work that existed only in the checkout

**Claim** (docstring): "HEAD is an ancestor of origin/<default> ⇒ every file is
reproducible."

**Model.** A worktree is `(retired, head, index, files)`. `files` stands for
every byte on disk: tracked, untracked and ignored. `Lossless` means HEAD is
published and `files` equals the tree of some published commit.

- `old_reclaim_loses_work`: a counterexample. HEAD is published, the index holds
  a staged change and `files` has an extra file, so `oldReclaim = true` while
  `¬ Lossless`.
- `new_reclaim_sound`: the fixed rule implies `Lossless` for every oracle whose
  tree lookup is sound. The fixed rule requires all of:
  - the worktree is retired;
  - HEAD is published;
  - files = index (no unstaged change, untracked file or ignored file);
  - the index is the tree of a published commit.
- `reclaim_cas_keeps_anchor`: the HEAD is compare-and-swapped to the published
  commit that is the index. Both commits are published, so the swap removes no
  anchor from any history.
- `live_workspace_kept`: a workspace that is not retired is never reclaimed.

**Replay (before the fix).** The test file above, run against the old code:

- `test_failed_publication_commit_leaves_staged_and_untracked_work_that_gc_must_keep`
  failed with `FileNotFoundError: .../retired-worktree-qr64rkme/worktree/report.md`.
  The staged content and the untracked file were deleted.
- `test_ignored_files_are_work_too` failed: `run.log` was gone.
- `test_a_live_unretired_workspace_is_not_reclaimed_mid_publication` failed: a
  live candidate at the published base was deleted mid-publication.

In production, knowledge_commit anchors the captured tree under
`refs/datacore/publication-captures/<token>` before it commits. For that caller,
the staged captured bytes were therefore recoverable. Untracked and ignored
files, hook output, a human's conflict resolution in a failed `integrate()`
checkout (which is never retired), and any live workspace were not recoverable.

**Fix** (`publication_workspace_gc.py`). A worktree is reclaimed only when all
four hold:

1. The worktree is retired (its path is under `retired-worktree-*`).
2. HEAD is published.
3. `git diff --quiet` passes and `git ls-files --others --directory` is empty,
   which covers ignored files too.
4. `git write-tree` of the index is a tree on origin/<default>.

Removal first compare-and-swaps HEAD to that published commit (`update-ref
--no-deref HEAD <c> <head>`), following worktree_lifecycle. It then runs
`git worktree remove` **without `--force`**, so git re-checks the worktree at
removal time and refuses rather than deletes.

Condition 4 is used instead of "index == HEAD" for a reason. knowledge_commit's
`target` checkout has its branch advanced under it by `update-ref`, so it reads
as dirty in reverse while holding exactly the published base tree.
`test_a_reservation_checkout_whose_branch_advanced_is_still_reclaimed` keeps
that disk reclamation working.

**Residual.** A status check is a snapshot (worktree_lifecycle.py:3). A writer
could add an ignored file between the check and `worktree remove`. The
retirement precondition makes quiescence a declared fact, but git cannot prove
it.

**Mutation checks** (scratch copies):
- M1: dropping `files = index` breaks `new_reclaim_sound`. The mutant also
  proves the counterexample `mutant_unsound`, so the break is semantic, not
  structural.
- M5: dropping the retired gate breaks `live_workspace_kept`.

## 3a. The repo lock was keyed by the path's basename

**Model.** `basename_key_not_exclusive`: `0-personal` and a linked worktree
`worktree-7` share one git-common-dir but take different locks.
`common_dir_key_exclusive` proves that the fixed key (the resolved
git-common-dir) is one lock per repository.
`main_checkout_key_unchanged` proves that a main checkout keeps its old lock
name, so a cooperating process still on the old code still excludes the new
code during rollout.

**Replay (before the fix).**
`test_a_publication_from_a_linked_worktree_excludes_the_main_checkouts_autosave`
got `'ACQUIRED'`. The transport took the main checkout's lock while a
publication held the lock from the linked worktree.

**Fix** (in `ledger_transport.py`, the `_repo_lock` region only). The new
`_lock_name(space)` works as follows:

- It resolves `git rev-parse --path-format=absolute --git-common-dir`.
- If the common dir is `<dir>/.git`, it returns `<dir>`'s basename.
- Otherwise it returns the common dir's own name.
- If git cannot resolve the path, it falls back to `space.name`.

This keeps `test_transport_preserves_existing_lock_inode_and_contents` passing
for a non-repo path. It also adds `_REPO_LOCK_TIMEOUT = 120`, so a test can
shorten the timeout.

`_repo_lock` is now **re-entrant for the thread that holds it** (a
thread-local set of held lock paths). Without this,
`test_git_preservation.py::test_pending_publication_blocks_other_linked_worktree_publishers`
failed. That test nests `reserve(other_linked_worktree)` inside `reserve(repo)`
in one thread. The two paths used to get different locks and reached the
O_EXCL refusal (`FileExistsError`). With one key per repository, the nested call
instead waited 120s for its own lock and raised `TimeoutError`. Other threads
and processes still block, because flock is per open file description.

**Not fixed, and safe.** Two different repositories with the same basename in
different roots still share one lock. That over-excludes; it never
under-excludes. Nothing nests two repositories' locks, so it cannot deadlock.

**Mutation check.** M2: keying on the basename breaks `common_dir_key_exclusive`.

## 3b. Reconcile accepts `refs/heads/<b>` and a local `refs/remotes/origin/<b>` tip

DOWNGRADED. The pending record means "the local publication commit was not
verified", not "the push was not acknowledged". `Reservation.verify_commit`
clears on a verified **local** commit, and a failed push is reported separately
by `_push_commit` / `_push_converging` and the transport's `gaps`. Reconcile
proves the same fact the record guards: the intended bytes reached the target
branch after the base. Remote-tracking refs only ever hold fetched or pushed
tips. Forging one needs `update-ref` by a same-user process, which is outside
the cooperative boundary the module declares.

## 3c. git_fleet_sync commits without `_repo_lock`

`git_fleet_sync.py:432` runs `git commit` in the space with no `_repo_lock`.
That is the same race the 2026-09-16 incident closed for the transport. It is
not my file: NEEDS-OWNER.

## 4a. Writeback: a second write queued before processing always conflicted

**Model.** `unchained_second_write_conflicts` (`decide`): two renders queued
from one file give `[completed, conflict]`.

- `queueNew_is_chain`: the fixed `queue`, which renders from `_predicted` (the
  file as the pending writes will leave it), builds a chain.
- `chained_writes_all_apply`: processing that chain in order completes every
  write, and the file ends as the composition of all the changes.
- `third_version_never_overwritten` and `retry_idempotent` prove that the
  docstring's claim still holds.

**Replay (before the fix).**
`test_two_completions_queued_against_one_file_both_apply[queued]` failed the
same way `writeback_engine --queue` run twice, then `--process`, would: the
second write ended with status `conflict`.

**Is user data lost?** Yes, after an explicit operator step.
`writeback_engine.clear_failed_writes` deletes `status='conflict'` rows, so the
second task's completion disappears. The plan row survives, orphaned.

**Fix** (`writeback_store.py`):

- `queue` renders from `_predicted(conn, target, current)`. That function runs
  `_apply`'s rule over the pending plans in id order, so a stale predecessor is
  skipped, as it will conflict.
- `process(identity)` first processes that write's pending predecessors on the
  same target, in id order. `process_all_pending` orders by `created_at`, which
  can tie, so processing order no longer matters.
- Tests cover in order, reversed order, a third version (every chained write
  still conflicts, and the file is untouched), a write queued after an external
  edit, and a chain past a stale write.

**Mutation check.** M3: an unchained `queueNew` breaks `queueNew_extends_chain`.
`unchained_second_write_conflicts` exhibits the failure.

## 4b. ABA re-apply

The file is applied (B→A), the process crashes before `_finish`, and someone
reverts the file to B. The retry re-applies A. This is outside the docstring's
claim, because B is the write's own `before`, not a third version. No byte of
any version is overwritten that was not the intended precondition.

The protocol cannot tell this case apart without a durable "applying" mark.
With such a mark, a crash after the mark and before the file write looks
identical to the revert. Failing closed there would turn today's retry-after-
failure (`test_file_failure_preserves_original_and_pending_intent`) into a
conflict. That is a policy choice: NEEDS-OWNER.

## 11. worktree_lifecycle retire CAS

**Replay.** `git update-ref --no-deref HEAD A A` with a symbolic HEAD at B
refuses ("is at B but expected A"). The old value is checked against the
symref's resolved value.

- `test_retirement_refuses_when_a_late_commit_moved_head` injects a commit
  between the capture and the CAS. `retire_worktree` raises, and the moved
  checkout's HEAD is still the late commit.
- `retire_cas_keeps_anchors` proves that a successful CAS keeps every commit
  that was anchored before it (by the resolved HEAD or by any branch).
- `forced_detach_loses_late_commit` shows that an unconditional detach would
  orphan a late detached commit.

**Mutation check.** M4: removing the check breaks `retire_cas_keeps_anchors`.

## Files changed

- `.datacore/lib/publication_workspace_gc.py`
- `.datacore/lib/ledger_transport.py` (the `_repo_lock` region only:
  `_REPO_LOCK_TIMEOUT`, `_lock_name`, thread re-entrancy; plus `import threading`)
- `.datacore/lib/writeback_store.py`
- New: `.datacore/lib/tests/test_publication_formal.py`,
  `specs/datacore-lean/DatacoreSpec/Publication.lean`, this file.

## Decisions applied (2026-09-23)

**Decision P1 applied: git_fleet_sync takes `_repo_lock` (was 3c, NEEDS-OWNER).**
`git_fleet_sync.sync_repo`, when executing, now holds
`ledger_transport._repo_lock(repo)` (same key rule: the resolved
git-common-dir, §2) from the inventory through staging, commit, the fork and
deletion gates, and the push. The inventory sits inside the lock so it cannot
go stale under another writer's commit. The pull stays outside it. If the lock
is still busy after `_REPO_LOCK_TIMEOUT`, the repo is reported `BUSY`, and its
work is left untouched for the next run. The dry run takes no lock.
- Lean: new §5 in `DatacoreSpec/Publication.lean`. It is a transition system
  of lock holders: idle, wait, hold, and the pre-P1 `bare` sweep.
  - `lock_mutual_exclusion`: in every reachable state, at most one process
    mutates a repository.
  - `fleet_sync_excludes_publication`: this still holds across checkouts that
    share a common dir.
  - `lock_no_deadlock`: every waiter either can acquire, or waits on a holder
    that can release. Holders never wait (no nesting), so there is no cycle.
  - `lock_reachable_hold` shows the model is not vacuous.
  - `old_sweep_races` is the refutation of the pre-P1 sweep.
- Deadlock check against the code. `converge`/`publish` and `reserve` each
  take this one lock and take no other `_repo_lock` inside it. The sweep takes
  it once per repo, in a separate process (a timer). A same-thread re-entry is
  a no-op (`_HELD`).
- Mutation checks, in scratch:
  - M1: the sweep may stage without the lock. A proved mutant theorem,
    `mutant_races`, puts two mutators in one repository in the unguarded
    system.
  - M2: a holder may request a second lock. `mutant_deadlock` reaches a state
    where two processes each hold one lock and wait for the other's. This
    violates `lock_no_deadlock`.
- Tests: `lib/tests/test_decisions_sync.py`
  - `test_p1_sweep_waits_for_the_repo_lock_and_does_not_commit_under_it`: the
    old code committed while another process held the lock.
  - `test_p1_commit_and_push_happen_while_the_lock_is_held`: pre-commit and
    pre-push hooks probe the lock from another process and find it HELD.
    Before the fix they found it FREE.

**Decision P3 applied: Keep.** The writeback ABA case (4b) is unchanged. A
retry after a crash and a revert re-applies the write. There was no code
change.

**Decision P4 applied: Keep retaining.** Publication GC does not treat trees
anchored under `refs/datacore/publication-captures/*` as recoverable. A
retired worktree with staged or untracked work is kept, as §1 now does. There
was no code change.

**Decision Q6 applied: lock the pull (second board, 2026-09-23).**
`git_fleet_sync.sync_repo` with `--execute` now takes `_repo_lock` once per
repository. One critical section covers the in-progress check, the pull
(`_pull`: fetch + merge, and `merge --abort` on failure), the inventory, the
commit, the Q8 deletion-check fetch, and the push.
- If the lock stays busy, the repo is `BUSY`: nothing is fetched, merged or
  committed.
- The dry run takes no lock and never pulls (unchanged).
- There are no git hooks in these repos that take `_repo_lock` from a child
  process, and no holder nests a second lock. So the argument of §5 still
  holds.

Lean (§5 re-modelled):
- A holder now performs an operation `Op` (`pull` or `land`). The sweep
  acquires for `pull` and then `advance`s to `land` without releasing.
  `bareOk : Op → Bool` says which operations may run without the lock:
  `preP1` (all of them), `p1` (the pull only), and `locked` (none, Q6).
- Proved for `locked`:
  - `lock_mutual_exclusion`: no two processes fetch/merge/stage/commit/push
    in one repository at once.
  - `fleet_sync_excludes_publication`.
  - `lock_no_deadlock`: every waiter can acquire, or waits on a holder that
    can release.
  - `holder_stable` + `holder_of_hold`: no step hands a held lock to another
    process, so between the sweep's pull and its land nobody else holds it.
  - `lock_reachable_pull_then_land` shows the model is not vacuous.
- Refuted:
  - `old_sweep_races` (pre-P1, kept).
  - `p1_pull_races`: the P1 sweep merges while the transport holds the lock.
- Mutation checks, in `scratchpad/followups-sync/`:
  - M-Q6 (`locked` lets the pull run bare) breaks `inv_step`, and with it
    `lock_mutual_exclusion`.
  - M-Q6b (acquire without a free lock) breaks `inv_step` and `holder_stable`.

Tests in `lib/tests/test_followups_sync.py`:
- These failed before the change:
  - `test_q6_the_pull_waits_for_the_repo_lock`: HEAD and `origin/main` stay
    put while another process holds the lock.
  - `test_q6_pull_and_push_share_one_critical_section`: the lock is entered
    once, and fetch, pull and push all run inside it.
  - `test_q6_the_merge_hook_sees_the_lock_held`: a post-merge hook probes
    from another process and finds the lock HELD.
- `test_q6_the_dry_run_neither_pulls_nor_locks` passes.
- The P1 tests in `test_decisions_sync.py` pass unchanged.
