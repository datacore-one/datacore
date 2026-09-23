# LedgerSeal cluster: findings (2026-09-23)

Model: `DatacoreSpec/LedgerSeal.lean` (449 lines). It checks cleanly with `lake env lean`, with no sorry, admit, axiom or native_decide. The axiom audit, run on a scratch copy, shows only propext, Quot.sound and Classical.choice.
Tests: `lib/tests/test_ledger_seal_formal.py` has 10 tests. Before the fix, 8 fail and 2 pass (the 2 are positive controls). This was run against the HEAD versions of the owned files in a scratch copy. After the fix, all 10 pass.
Targeted run: every test file that imports an owned module (15 files) plus `test_ledger_exceptions.py` gives **226 passed**.

| # | Candidate | Verdict | Lean theorems |
|---|---|---|---|
| 3a | Seal monotonicity: settled(t) ⊆ settled(t') | CONFIRMED+FIXED | `seal_settled_monotone`, `seal_regression_cex`, `seal_regression_rejected` |
| 3b | "Latest seal is by the sequencer" | CONFIRMED (refuted claim), NEEDS-OWNER | `latest_seal_not_by_sequencer` |
| 3c | A far-future HLC seal stays latest | DOWNGRADED | none |
| 6 | ingest_org: an unreadable live file lets its ids be dismissed | CONFIRMED+FIXED | `ingest_never_dismisses_live`, `ingest_needs_evidence`, `ingest_unreadable_cex` |
| 6b | ingest_org scans only `org/*.org` for liveness | CONFIRMED+FIXED (liveness only) | `ingest_nested_cex`, same safety theorem |
| 7 | dismiss_orphans two-sweep rule | REFUTED (holds, proved), plus a hardening fix | `orphans_two_sweeps`, `sweep_sound` |
| 8 | restore_prefix: no lock between read and write | CONFIRMED+FIXED | `restore_locked_no_loss`, `restore_race_loses_append` |
| 9 | Checkpoint monotonicity | REFUTED (holds, proved) | `checkpoint_monotone`, `checkpoint_history_grows` |
| 12a | Invariants baseline: an empty `detail_startswith` accepts everything | CONFIRMED+FIXED | `baseline_empty_mutes_all`, `baseline_accept_sound` |
| 12b | Invariants baseline: "tris seq 5" also accepts "tris seq 50" | CONFIRMED+FIXED | `baseline_seq5_mutes_seq50_old`, `baseline_seq5_spares_seq50_new` |
| 12c | Invariants: could-not-tell findings still give verdict SOUND, exit 0 | CONFIRMED, NEEDS-OWNER | `unknown_only_exits_sound` |

## 3a. Seal regression
`latest_seal` picks the maximum HLC from any writer. `verify_seal` never compared the new seal with earlier ones.

Replay (`scratchpad/LedgerSeal/replay.py`, before the fix):
- Seal 1 by winston verifies over 3 events.
- A later seal by `mallory` over `mac` seq 0 prints `True seal by mallory verifies over ~1 event(s)`.
- `settled_events` went from 3 to 1.

Fix:
- `seal.py`: new `regressions()` and `seal_regressions()`. `verify_seal` returns `False, "SEAL REGRESSION..."` when the latest v2 seal lowers or drops any watermark of an earlier v2 seal.
- `ledger_seal.py emit`: refuses to emit a regressing seal ("behind an earlier seal; converge").

Proof: `seal_settled_monotone`. It assumes the reader's event set only grows and that seal HLCs are unique.

Live impact: `ledger_seal.py status --all` gives the same output before and after. 8 spaces are `n-a` (legacy v1 seal), and 5-plur and 9-practice are `n-a` (no seal yet). All existing seals are by winston. No verdict flipped.

## 3b. Sequencer authority (NEEDS-OWNER)
Even after the fix, a seal by any writer that dominates the earlier seals becomes latest and verifies. The seal only reports "seal by X". Making non-sequencer seals inert would also make `emit --force` inert, so that is a policy choice. I did not change it.

## 3c. Far-future HLC (DOWNGRADED)
`EventLog.append` stamps above every sibling log's tail. So the sequencer's next seal after a sync sorts after the far-future seal. The condition heals itself at the next seal.

## 6. ingest_org archived dismissal
Replay, before the fix: with the live `next_actions.org` set to mode 000 and the id also in `next_actions_archive.org`, `_dismiss_archived` returned 1 (a terminal dismiss). After the fix it returns 0.

Fix:
- Archive evidence is still only the top-level `org/*archive*.org` files. An unreadable archive only withholds evidence.
- Liveness now comes from every non-archive `.org` under `org/`, found with `os.walk`. Nested archive directories are excluded.
- Any unreadable live file or directory skips the whole archived-dismiss pass.

Rationale: the comment says "An id still present in ANY live org file is left alone". Widening liveness can only reduce dismissals.

Live impact: dry-run dismiss counts are 0 old and 0 new in all 10 spaces.

## 7. dismiss_orphans two-sweep rule
The claim holds and is proved. `orphans_two_sweeps` shows that every dismissed id meets four conditions:
- it is absent in this readable scan;
- it was absent in an earlier recorded readable scan;
- that scan was at least `min_gap` earlier;
- the dismissed count is at or under the ceiling.

The proof assumes found ⊆ live, which is true because found is computed from the live items.

Hardening (replayed): `json` accepts `"at": -Infinity`, which let a single scan confirm a dismissal. Non-finite `at` is now treated as 0. That makes the Int abstraction faithful.

Residue: the earlier scan may be arbitrarily old. The proof allows that on purpose.

## 8. restore_prefix race
Replay, before the fix: an append was injected between the read and the write. The restore returned rc 0, the LIVE event was gone, and parked seq 2 took its place. This is a silent fork, and the HWM witness still vouches for the lost event.

Fix: `restore` opens the log `r+b` and takes the same `fcntl.flock(LOCK_EX)` that `EventLog.append` uses. It holds the lock across the read, the prefix check, the chain check, the backup, the truncate-and-write (with fsync), the post-check and any rollback.

Tests:
- The racing append now makes the restore refuse, and the event survives.
- A probe confirms the lock is held (LOCK_NB gets BlockingIOError) during the chain check and the write.

## 9. Checkpoint monotonicity
The claim holds and is proved. Every saved chain is a prefix of its successor, transitively across any sequence of checkpoints. Through `LedgerSpec.Chain.ser_prefix_iff` this is also an event prefix.

Note: `is_recorded` is keyed on the local space directory name. That fails closed (the exception is not applied), so it is DOWNGRADED.

## 12. ledger_invariants
Fix: `_prefix_matches` needs a non-empty prefix and a token boundary after it.

Live `--quick` run before and after: ok, 0 broken, 1 accepted, 0 unknown. Both shipped baseline entries still match.

12c (NEEDS-OWNER): when the only findings are unknown, the run prints `SOUND` and exits 0, and the nightly job's `last_line_regex: 'ledger-invariants: SOUND'` passes it. That contradicts "never as sound". I left it unfixed because `test_an_accepted_finding_does_not_fail_the_sweep` runs on a non-git tmp space, which produces an unknown `unforked` finding, and expects exit 0. The exit-code policy also belongs to the owner.

## Mutation checks (scratch copies)
Each bug was put back into the model. Every named theorem stops proving:
- M1: drop the seal gate.
- M2: drop the unreadable guard.
- M2b: nested files no longer count as live.
- M3: allow the unlocked interleaving.
- M4: drop the gap check.
- M4b: drop the ceiling.
- M5: accept any checkpoint replacement.
- M6: allow an empty prefix.
- M6b: drop the boundary check.

## Files changed
`lib/ledger/seal.py`, `lib/ledger_seal.py`, `lib/ledger_ingest_org.py`, `lib/ledger_restore_prefix.py`, `lib/ledger_dismiss_orphans.py`, `lib/ledger_invariants.py`. New: `lib/tests/test_ledger_seal_formal.py`, `DatacoreSpec/LedgerSeal.lean`, this file. `ledger_checkpoint.py` and `ledger/exceptions.py` are unchanged.

## Owner decisions applied (2026-09-23)

**Decision L1 applied: sequencer only.** `ledger/seal.py` gained `sequencer()`
(`$DATACORE_SEQUENCER`, default `winston`, read at call time; this was the only
place the sequencer was configured, as `ledger_seal.SEQUENCER`, and the CLI now
imports it). `latest_seal`, `seal_regressions`, and through them `verify_seal`,
`settled_events` and `settled`, read only `_seal_events` (seals whose actor is
the sequencer). Other writers' seals are inert and are reported in the
`verify_seal` detail ("ignored N seal(s) by X (not the sequencer Y)"), never
fatal. `ledger_seal.py emit --force` from a non-sequencer still appends, and
now warns that readers ignore the seal. Candidate 3b is closed.
- Lean: `bySeq`, `IsLatestSeq`, `latest_is_sequencer`, `foreign_seal_inert`,
  `foreign_latest_unchanged`, `seq_settled_monotone`, `old_cex_ignored`.
  `latest_seal_not_by_sequencer` is kept, relabelled as the pre-L1 reader.
- Mutation: with `bySeq` as the identity, `latest_is_sequencer`,
  `foreign_seal_inert` and `old_cex_ignored` stop proving, and
  `IsLatestSeq "winston" [s1, s3] s3` (a foreign seal is latest) proves. On
  the real model that statement fails.
- Live: `ledger_seal.py status --all` output is byte-identical before and
  after (8 spaces n-a legacy v1, 2 n-a no seal). All 12 live seals are by
  winston.
- Tests: new `test_decisions_ledger_a.py` (6 L1 tests). Changed:
  `test_ledger_seal_formal.py::test_a_later_seal_cannot_unsettle_what_an_earlier_seal_settled`
  now regresses as `winston`, because a `mallory` seal is ignored. The same
  file's `test_emit_refuses_a_regressing_seal` uses `ledger_seal.sequencer`.
  An autouse fixture clears `DATACORE_SEQUENCER`. `test_seal_preservation.py`
  gained an autouse fixture that sets `DATACORE_SEQUENCER=sealer`, and its
  last test sets `worker`.

**Decision L2 applied: print UNKNOWN, exit 2.** `ledger_invariants.verdict()`
returns BROKEN/1 if there is a new finding, else UNKNOWN/2 if there is a
could-not-tell finding, else SOUND/0. `--json` gains `"verdict"`, and `ok`
now means SOUND. Candidate 12c is closed.
- Lean: `exitCode` (new), `exitCodeOld` + `unknown_only_exits_sound` (old),
  `unknown_only_exits_unknown`, and `exit_zero_iff_all_sound` (exit 0 iff
  every finding is known and accepted).
- Mutation: the old `exitCode` breaks both new theorems, and
  `exitCode [unknown] = 0` proves.
- Changed test: `test_ledger_invariants.py::test_an_accepted_finding_does_not_fail_the_sweep`
  now expects 2 and an UNKNOWN last line. Its tmp space is not a git repo, so
  `unforked` is could-not-tell.
- Live `--quick` on the mac: SOUND, 0 could-not-tell, exit 0 (unchanged).
- Nightly job `nightshift-ledger-invariants` (manifest.yaml, read-only): a
  night with only could-not-tell findings now exits 2. That is outside
  `exit_ok: [0, 1]`, so `on_fail: telegram` pages. The artifact check
  `last_line_regex: 'ledger-invariants: SOUND'` also fails on the UNKNOWN line.
  Such a night used to pass silently. `audit_trio_run.sh invariants` passes
  the rc through.

**Decision L3 recorded: top-level only.** No change. Archive evidence for
`ledger_ingest_org._dismiss_archived` stays the top-level
`org/*archive*.org` files. Nested archive files are not evidence. Liveness
still comes from every non-archive `.org` under `org/` (fix 6b).
