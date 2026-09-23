# Open decisions from the core verification (2026-09-23)

Each item is a place where the code, its docs and its tests disagree, and
choosing between them is a policy call. Nothing here was changed. Evidence
for each item is in `findings/<cluster>.md`. **Bold** marks items with live
safety or data impact.

## Ledger
1. **Seal authority** (ledger-seal): should readers accept only seals written by the designated sequencer (`DATACORE_SEQUENCER`, default winston), so that `emit --force` seals are inert? Today any writer's seal that verifies without regressing is accepted.
2. ledger_invariants: when every finding is "could not tell", should the run stop printing SOUND and exit non-zero? If so, with 2 or 1? Either way, one existing test changes.
3. ledger_ingest_org: should nested archive files count as evidence for dismissal?
4. **Cross-host claim race** (ledger-policy): should dispatch be pinned to one host per principal and space, or require the exact writer name in `assignee`? Right now two hosts of one principal can both run an item.
5. Delegation depth through the adapter: should `executors/base.py` export `DATACORE_HOPS` (the parent's hops + 1)? Until it does, adapter-created follow-ups start at depth 0.
6. `max_creates_per_day`: per principal per space (today), or per principal across all spaces?
7. `merge_values` compares with `True == 1 == 1.0`. Switch to type-strict equality? That would change fold results.
8. `canonical_bytes`: refuse NaN (`allow_nan=False`)?
9. **Pre-push fork gate** (git-fleet): should `ledger_transport._push_with_retry` and `knowledge_commit._push_commit` call `git_relay.publication_forks()`? The relay and the fleet sweep are gated now; these two pushers are not.
10. Should ledger writers call `this_actor(strict=True)`, so that two undeclared hosts sharing a hostname cannot write one log?

## GTD / org
11. **DIP-0009 transitions** (gtd-state): should the adapter refuse moves the v2.0 table forbids? The code allows 49 of them, including REVIEW→TODO/WAITING and DEFERRED→anything other than TODO. `gtd_decision_board` (someday path) and `delegation_gate` rely on some of these. Also: should DEFERRED→CANCELLED be allowed?
12. "REVIEW exits are owner only": enforce it? If so, what identity counts as the owner?
13. task_cleanup dup-ids: skip the inbox.org / generated next_actions.org pair that shares ids by design? Is the 6-meridian hold still needed?
14. org_workspace package (a separate repo): should `StateConfig.default()` drop QUEUED/WORKING/FAILED and move DEFERRED to the done class?
15. nightshift_parser `_persist_task`: DONE/CANCELLED sends only `item.update`, the same defect already fixed in the adapter. Reuse `_ledger_emit_close`?
16. org_transaction: should `write_org_text` refuse to overwrite a file that was not watched first? That enforces read-before-write and changes the API.
17. **Writers that bypass the transaction lock**: route the live `org_date_hook` (PostToolUse) and `triage_utils` through `@serialized` with `file_lock`? A lost update was replayed. Also affected: org_union_merge --apply, org_dedup_within_file, org_resolve_id_conflicts, inbox_dedup, stamp_seq_todo, validate_org_dates --fix.
18. org_dedup_within_file: dropping a copy whose `:ID:` differs from the kept copy is deliberate (the 2026-08-15 incident), but the ledger may track that id. Refuse, or reconcile the ledger?
19. org_resolve_id_conflicts: when the discarded id already has an `item.create`, what reconciles the ledger item?
20. triage_utils: stop appending to DEFERRED tasks?
21. org_union_merge: if theirs skips a heading level, is placing the item under the deepest earlier sibling acceptable?

## Nightshift
22. **Fenced-NEXT zombies** (nightshift-lifecycle): a failure that leaves the attempt fence closed (`unknown:`, or `completed` after a hand-off or write failure) sits NEXT forever, and `reconcile_attempt` refuses to reopen it. Proposal: `complete_task` escalates when the fence is blocked, and `reconcile_attempt --reopen` resets it.
23. Should a success or a human reopen reset NIGHTSHIFT_REQUEUES? Today it is a lifetime cap of 2.
24. What should `max_retries` mean? The code allows at most 1 retry per run, and escalation is hard-coded at 2.
25. Evaluator: read `quality_threshold` (0.80 in settings, ignored today)? Should a `recommendation: reject` block approval?
26. Delegation canary: count a failed local commit as `failed`, and let a streak of `blocked` days age into `failed`? A test pins "blocked 99h still passes".
27. Budget: when `budget_daily_usd > 0`, refuse batch-API spend once the budget is exhausted?
28. jobs/recurrence: when the lock or the save fails, warn loudly or refuse instead of silently losing a reset?
29. ai_task_gate: keep the ROADMAP requirement, which makes it stricter than the executor it claims to mirror, or drop it?

## Credentials and routing
30. **`creds get` on n-a** (credentials): make `--strict` (exit 3 on n-a) the default, or use a distinct exit code? Either change breaks `X=$(creds get …)` under `set -e` for NO_PROBE credentials.
31. Duplicate keys in one env store: the broker reads the first, every other parser the last or raises. Which is right?
32. dmcc `client-report` / `contract-review` now route to local models, because the privacy floor wins over escalation. Was frontier intended? If so, lower the floor or add a private premium class.
33. **Roll the new `creds.py` out to all hosts together.** Its lock file name changed, so old and new code don't exclude each other.
34. `load_env_files()` now lets the host's `local.env` beat the fleet `.env`, as CLAUDE.md promises. On the mac no key is in both files; the other hosts are unchecked.

## Guards
35. tool_policy: always add the input's JSON to the matched text (the MCP rule in tool_effects.yaml)?
36. **injection_integrity_guard**: while the gate is armed, Bash, including `curl` and `git push`, is still allowed. Restrict it to read-only commands on the spill file?
37. log_ownership_guard: treat commits no remote ref contains as this machine's, whatever the author email says? Should a range `rev-list` cannot list refuse the push?
38. egress_runtime_check: give an all-n-a run its own exit code?
39. hooks.py: fail validation on an unknown hook type? Stop `python hooks.py <agent>` writing a mock error into the real `hook_state.yaml`?
40. restricted_hosts_guard now refuses network git after `cd $VAR` or a loop `cd`, because the directory is unknown until run time. Confirm this stricter behaviour is wanted.

## Sync, publication, reconcile
41. **git_fleet_sync**: take `ledger_transport._repo_lock` around its commit? The race behind the 2026-09-16 stranded record is still open from that side.
42. git_fleet_sync: refuse to push a range whose earlier commits delete tracked files?
43. writeback ABA: fail closed on retry after a crash and revert (needs a durable "applying" mark), or keep re-applying?
44. publication GC: treat trees anchored under `refs/datacore/publication-captures/*` as recoverable?
45. gh_reconcile: should an archived nightshift output close a task whose PR is still open? Should an issue closed as duplicate cancel the task, or follow the canonical issue?
46. sync/conflict.py: add a per-task base snapshot so the three-way rule `resolve3` can be wired in, or remove ConflictDetector until the engine exists?

## Detectors, knowledge, research
47. id_churn: schedule `--acknowledge` on each host so the new set-based baseline takes effect? Keep the 25% noise floor?
48. R5 SLO: a single missed probe (95/96) now fails, as the ≥99.5% target says. Intended, or should the SLO page state a one-miss allowance?
49. actor_presence: MISSING/STALLED now clears only when the log recovers. Add an explicit acknowledge flag?
50. DIP-0002 "Merge Behavior" still says "Override values". Strike it, since layers only concatenate? Should the gitignore guard also cover SPACE/TEAM layers?
51. prune_learning_buffer: should `candidate` engrams count as promoted?
52. research: should an analysis failure count toward parking even when every analysis in the run failed (a Claude outage)? Move `:FETCH_ATTEMPTS:`, `:ANALYSIS_ATTEMPTS:` and `:RESULT:` into the properties drawer?
53. Dates: widen the "suspect year" beyond last year only?
