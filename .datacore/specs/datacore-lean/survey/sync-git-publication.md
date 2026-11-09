# Survey: sync, git, publication, state (2026-09-23)

Read-only survey by a subagent. Claims are quoted from source; suspicions cite
lines and are UNVERIFIED until a model or a replay confirms them.
Ranked by (risk × likelihood claim is subtly wrong) / model size.

1. **publication_workspace_gc reclaim** (publication_workspace_gc.py:55-91) — claims HEAD-ancestor-of-origin proves the checkout is "a copy, not the only copy" (15-17). Suspicion (strong): `git worktree remove --force` (81) deletes staged/untracked work; a failed publication commit leaves HEAD at base with content staged, passes the proof, is destroyed. Contradicts worktree_lifecycle.py:3-4. Tests don't cover a dirty worktree. Model S.
2. **git_relay "NEVER PUSH A FORK" vs other pushers** (git_relay.py:110-256, git_fleet_sync.py:278-455) — on fork detection relay returns but leaves the forked merge commit on the local branch; git_fleet_sync pushes HEAD with no fork check; git_fleet_sync.py:289-290 "merge is a union … nothing to conflict over" contradicted by git_relay.py:115-117. Line 200 uses local branch name vs host branch. Model M.
3. **publication_state reservation / lock / reconcile** (publication_state.py:48-140, publication_reconcile.py:52-110, ledger_transport.py:145-152) — lock keyed by `space.name` while record lives in git-common-dir (worktrees/same-basename repos break mutual exclusion); git_fleet_sync commits without `_repo_lock`; reconcile accepts a local-only origin ref as "landed". Model M.
4. **writeback_store prepared writes** (writeback_store.py:122-195) — "retry recognizes the already-applied bytes … A third version is a conflict, never an overwrite target" (3-7). Suspicions: two writes queued against the same file before processing share the same `before` → the second always conflicts (no test); ABA re-apply if `_finish` never ran and file returned to `before`. Model S.
5. **gh_reconcile "Never a false DONE"** (gh_reconcile.py:482-898) — failed ref lookups (None) skipped at 842-843, so merged PR + unreachable ref closes the task; archive match by basename only (514,518); "not planned" → DONE (677); read-modify-write with no lock/atomic replace (819/893). Model S.
6. **git_fleet_sync guards** (git_fleet_sync.py:98-456) — MERGE_HEAD check at 268-270 assumes `.git` is a dir (worktrees/submodules have a file); after a failed pull (293-353) continues to commit and push; review_gate proceeds on inconclusive. Model M.
7. **visitor_join spacing** (visitor_join.py:188-252) — "converged" ignores ahead/behind in grace (195); attempt recorded after converge (214, up to 1500s) so spacing guard doesn't stop overlaps (depends on launchd overlap). Model S.
8. **cron_install reconcile** (cron_install.py:77-149) — idempotence/preservation is provable; intervening-edit check (144) and write (146) not atomic. Model S.
9. **git_branch_hygiene "safe to delete"** (git_branch_hygiene.py:61-108) — `rev-list trunk -- path` over whole history, not base..trunk: a revert-to-old-content branch is "built-on". Advisory only. Model S.
10. **sync/conflict.py resolver** (sync/conflict.py:125-422) — two-way detection ignores `last_sync` (129); ORG_WINS on state would reopen human-closed issues. Dormant: SyncEngine.sync is a stub (engine.py:318-332). Model S.
11. **worktree_lifecycle retire CAS** (worktree_lifecycle.py:83-108) — no suspicion; the guarantee #1 breaks. Model S.

Extra hazard: state_loop_rollout.py:106-108 appends ledger events with `actor = socket.gethostname()` instead of `actor_identity.this_actor()` — same short hostname on two hosts ⇒ same (actor, seq) ⇒ ledger fork.

Excluded as glue: datacore_sync.py, state_store.py, session_state.py, update_check.py, relay_client.py, reconcile_sync_merge.py, git_conflict.py, git_inventory.py, publication_history/moves/manifest.py. Not read in depth: github_rulesets.py, git_integration.py, spaces.py, space_catalog.py, migration_*.py, artifact_sync.py, assets_sync.py, sync/ adapters.
