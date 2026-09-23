# Knowledge cluster — findings (2026-09-23)

Model: `DatacoreSpec/Knowledge.lean` (namespace `DatacoreSpec.Knowledge`, 45
theorems, checks cleanly with `lake env lean`, no sorry/axiom/native_decide;
`#print axioms` shows only `propext`, `Quot.sound`, `Classical.choice`).
Tests: `lib/tests/test_knowledge_formal.py` (17 tests; 10 failed before the fixes).
Survey leads: `survey/research-agents-rest.md` items 1, 5, 7, 10, 11.

| # | Candidate | Verdict |
|---|---|---|
| 7a | context_merge: PRIVATE layer written to a path git would track | CONFIRMED+FIXED |
| 7b | context_merge: "no override, pure concatenation" | DOWNGRADED (DIP-0002 says so; comment corrected) |
| 7c | context_merge: validation hit only warns, still writes | DOWNGRADED (DIP-0002 puts the block at commit/CI) |
| 7d | context_merge: `.space.md` never validated | DOWNGRADED (DIP-0002 lets SPACE hold team contacts) |
| 10a | prune_learning_buffer: retired engrams count as promotions | CONFIRMED+FIXED |
| 10b | prune_learning_buffer: kept entries rewritten | CONFIRMED+FIXED |
| 5a | briefing/actions: "no matter how the briefing rephrases it" | CONFIRMED+FIXED (docstring; unachievable by hashing) |
| 5b | briefing/actions: check-then-act without a lock | CONFIRMED+FIXED (duplicate create only; never a resurrection, proved) |
| 11a | agent_stream_store: append idempotence | CONFIRMED+FIXED (lead said "expected to hold"; it did not) |
| 11b | agent_stream_store: framing (`splitlines`) bricks the stream | CONFIRMED+FIXED (new, found while modelling) |
| 11c | agent_stream_store: batch atomicity | REFUTED (holds; proved) |
| 1 | rotate_learning_backup purge (coordinator's fix) | modelled: old glob refuted, new purge proved |

## 7. context_merge.py (DIP-0002)

**7a. CONFIRMED+FIXED.** `rebuild_context` wrote the composed `CLAUDE.md`, including
`.local.md`, wherever it was asked, and never checked that git ignores the output.
DIP-0002 says the composed file is "always gitignored", but that is a property of
the repository, and nothing enforced it.
*Fix:* when a PRIVATE layer exists, `output_untracked_refusal` runs
`git rev-parse --is-inside-work-tree` and `git check-ignore -q`. It writes only if
the path is ignored (exit 0; git never reports a tracked path as ignored, so a
tracked `CLAUDE.md` is refused too) or the directory is outside any work tree.
Anything else fails closed: the file is not written, and `rebuild_context` returns
`(False, [REFUSED…])`.
*Lean:* `old_leaks` refutes the old code. `new_never_leaks` proves the fix: a
written file contains PRIVATE text only when git ignores it or no repository
exists. `new_writes_when_safe` proves the fix changes nothing when there is no
private layer or the output is ignored. `layer_survives` proves every layer's
text reaches the output.
*Replay:* in a `git init` tmp dir with base + local and no `.gitignore`, the old
code wrote `CLAUDE.md`, and it was untracked and not ignored
(`test_private_layer_is_not_written_where_git_would_track_it`).
*Live impact:* none today. All four composed files with a local layer
(`CLAUDE.md`, `2-datacore/`, `4-forge/`, `6-meridian/`) report
`check-ignore` 0, so the fix changes no live rebuild.
*Mutation:* with the guard removed (`newWrites := anyLayer`),
`new_never_leaks` no longer proves.

**7b. DOWNGRADED.** DIP-0002 answers this itself ("Resolved Questions 1: …
layers are concatenated, not merged … both appear"). The "Merge Behavior"
bullet "Override values" in the same DIP conflicts with that answer, but the DIP's
own resolution and the tests (`test_all_layers`) pin concatenation. Only the
code comment "later layers extend/override" was wrong; it now says "extend".
`layer_survives` proves the additive behaviour.

**7c / 7d. DOWNGRADED.** DIP-0002 puts the blocking gate at pre-commit and CI
(`git_privacy.py --index`, which imports `PRIVATE_PATTERNS`). The composed output
is gitignored, so a warning at rebuild time publishes nothing. The DIP's content
rules allow team contacts in SPACE, so holding `.space.md` to the PUBLIC patterns
would contradict it. Both behaviours are now stated in a comment at
`VALIDATED_LAYERS`.

## 10. prune_learning_buffer.py

**10a. CONFIRMED+FIXED.** `load_engram_index` indexed every `statement:` line,
retired engrams included, so a learning entry was pruned as "promoted" when its
only matching engram had been retired.
*Fix:* statements are grouped per engram record (`  - ` at indent 2) and kept only
if the record's own `    status:` is `active` or absent. Status may come before or
after the statement.
*Lean:* `old_prunes_on_retired` refutes the old index; `new_prune_needs_active`
proves the fix for every similarity oracle.
*Real data (read-only):* the index went from 6261 statements to 5978, which equals
the active count. At `--days 90`, the old index would prune 1178 entries and the new
one 1162: 16 entries matched only a retired engram.
*Mutation:* removing the filter breaks `new_prune_needs_active`.

**10b. CONFIRMED+FIXED.** The rewrite ran
`raw.rstrip("\n").rstrip("-").rstrip("\n")` on every kept entry. It then re-added
`\n\n---` after each entry and `rstrip`ped the header. So a trailing `--` was lost,
a `---` separator was added where there had been none, and header blank lines were
dropped. Read-only check: 625 of 2976 real entries are not already in the forced
`…\n\n---` shape, so their bytes would change on any live prune.
*Fix:* the output is `"\n".join([header] + [raw of kept entries])`, with the header
omitted when the file starts with an entry. That is the original with the pruned
blocks' lines removed.
*Lean:* `parse_roundtrip` (parse loses nothing), `rebuild_identity` (nothing pruned
gives an identical file), `rebuild_sublist` (the output only deletes lines),
`kept_entry_verbatim` (every kept entry is a contiguous infix), and
`old_mutates_kept_entry` (the refutation).
*Replay:* `test_kept_entries_and_header_are_byte_preserved` failed on the old code.
*Mutation:* dropping the header breaks all three preservation theorems.

## 5. briefing/actions.py

**5a. CONFIRMED+FIXED (docstring).** `item_id` hashes lowercase and
whitespace-collapsed text. `"Buy milk"` and `"Buy the milk"` get different ids, so
the claim "no matter how the briefing rephrases it" was false.
`rephrase_changes_id` proves that for any collision-free hash, texts that
normalise differently get different ids. The module and `item_id` docstrings now
say exactly which texts are equivalent. `tests/test_briefing_actions.py`'s module
docstring repeats "phrased slightly differently". That file is not owned by this
cluster, so it was left unchanged.

**5b. CONFIRMED+FIXED.** `materialize` folded a snapshot and then appended, with no
lock. Two concurrent calls both saw the id absent and both appended `item.create`
(replayed with two threads: 2 creates).
*Lean:* the race was never a resurrection. `_handle_create` on an existing id
(dismissed or not) is a no-op, so `dismissed_absorbing`/`no_resurrection` prove a
dismissed id stays dismissed under any later events. `race_state_benign` proves
the racing log folds to the same state as the serialized one, even with a
dismissal after it. The defect is a duplicate event, and a `created` result that two
callers both report (`racing_two_creates` vs `serialized_one_create`).
*Fix:* `materialize` holds `file_lock(<space>/.datacore/state/briefing-materialize,
timeout=60)` across its fold and its appends. This is the house pattern from
`ledger/policy.py`. It is a separate file because `file_lock` is not reentrant and
`guarded_append` takes the policy lock inside. The lock order is always materialize,
then policy.
*Replay:* `test_concurrent_materialize_appends_one_create` gives 1 create and
results `[0, 1]`. Existing briefing tests still pass.
*Mutation:* letting create revive a dismissed item breaks `dismissed_absorbing`.

## 11. agent_stream_store.py

**11b. CONFIRMED+FIXED (new).** Rows are written with
`json.dumps(ensure_ascii=False)`, which leaves U+2028, U+2029 and U+0085 raw inside
strings, and `remember` re-read them with `str.splitlines()`, which splits on those
characters. One summary containing U+2028 made every later `append_events` to the
whole stream raise `JSONDecodeError`. Every retained day is scanned, so this covers
all days. `agent_emit` swallows the error, so all agent events would be lost
silently. This is the same outage class as 2026-09-11.
*Fix:* split on `"\n"` only, and drop one trailing empty piece. A blank line inside
the log still raises, as before (`test_blank_line_in_log_is_still_refused`).
*Lean:* `newFrame_render` proves the reader recovers exactly the written records.
`oldFrame_splits_record` refutes the old reader.
*Replay:* the old code gave `first 1 / retry raised JSONDecodeError / unrelated
append raised JSONDecodeError`, for U+2028 and U+0085 alike.
*Live impact:* none so far. All 129 local stream files frame identically under
both readers.
*Mutation:* using the `splitlines` separator set breaks `newFrame_render`.

**11a. CONFIRMED+FIXED.** A row skipped for a seen `dedup_key` did not reserve its
id. The batch `[{id 0, key k}, {id 1, key k, "first"}, {id 1, "second"}]` was
accepted (2 written), and its retry raised `EventConflict`. The existing rule
"conflicting ids within one batch do not commit" was bypassed.
*Fix:* a key-skipped row calls `known.setdefault(id, content)`. That batch is now
refused whole.
*Lean:* `old_retry_conflicts` and `new_rejects_batch` are the refutation.
`append_idempotent` proves the fix for every history and batch: if
`append d rows = ok (d', n)` then `append d' rows = ok (d', 0)`. The proof is by
the run invariant `Inv` (`run_inv`) and the no-add lemma `run_noadd`.
*Replay:* the old code gave `first 2 / retry raised EventConflict`.
*Live impact:* latent. `agent_emit` ids are content hashes, so the same id with
different content does not occur today.
*Mutation:* without the reservation, `inv_step`/`run_noadd` (and so
`append_idempotent`) no longer prove.

**11c. REFUTED (holds).** `append_extends`: on success the new history is the old
one plus the additions. There is one `atomic_write_text`, and it runs after all
checks, so an error writes nothing (`test_store_failure_preserves_previous_batch`,
`test_conflicting_ids_within_one_batch_do_not_partially_commit`).

## 1. rotate_learning_backup.py (read-only; coordinator's fix)

*Lean:* `old_glob_deletes_the_new_backup` computes, with real code points, one
`engrams` backup beside seven `engrams-candidates` backups. The old glob with
`sorted()` deletes exactly the backup just written.
`new_shape_selects_own` shows the exact-shape matcher selects only the `engrams`
backup, and `new_purge_keeps_it` shows nothing is deleted. For every directory and
any sort order: `purge_only_own` (every deleted name is a stamp-shaped backup of
this stem and suffix, present in the directory), `kept_count` (exactly
`min KEEP n` kept), and `kept_newest` (every deleted backup sorts before every kept
one, for any total preorder).
*Mutation:* using the glob as the matcher breaks `new_shape_selects_own`.
*Not modelled:* the 1-second to microsecond stamp change. Python's `\d` also
matches non-ASCII digits, and the model uses ASCII.

## Files changed

- `.datacore/lib/context_merge.py`: PRIVATE-output guard, comments
- `.datacore/lib/prune_learning_buffer.py`: active-only index, byte-preserving rewrite
- `.datacore/lib/briefing/actions.py`: honest docstrings, per-space materialize lock
- `.datacore/lib/agent_stream_store.py`: LF-only framing, dedup-skip reserves id
- new: `.datacore/lib/tests/test_knowledge_formal.py`,
  `specs/datacore-lean/DatacoreSpec/Knowledge.lean`, this file

## Tests

`cd .datacore/lib && python3 -m pytest -q tests/test_knowledge_formal.py tests/test_agent_stream_store.py tests/test_agent_event_identity.py tests/test_agent_outbox.py tests/test_stream_tail_preservation.py tests/test_context_merge.py tests/test_integration.py tests/test_git_privacy.py tests/test_briefing_actions.py tests/test_briefing_materialize.py tests/test_live_damage_fixes.py`
gives **115 passed**.

## NEEDS-OWNER

1. DIP-0002's "Merge Behavior" still lists "Override values", and its Resolved
   Question 1 says layers are only concatenated. Should the DIP bullet be struck?
   The DIPs repo is read-only here.
2. Should the no-tracked-output guard also cover SPACE/TEAM layers when the
   composed file sits in the upstream (public) repo? DIP-0002 says the composed
   file is always gitignored, but only PRIVATE is enforced now.
3. Should `candidate` engrams count as promoted for pruning? They do not now;
   only `active` does, or a record with no status.

## Owner decisions applied (2026-09-23)

**Decision D5 (strike "Override values" in DIP-0002): out of bounds here.** The
`.datacore/dips` repo is read-only for this work. The exact change, in
`.datacore/dips/DIP-0002-layered-context-pattern.md`, section "### Merge Behavior"
(line 83), is to delete this line:

    - **Override values** - Specific key-value patterns can be overwritten

After the change the list reads "Add sections" and "Extend sections" only. That
matches Resolved Question 1 ("layers are concatenated, not merged … both appear") and
`layer_survives`. Optional, same section: line 82 says "Content under same header is
concatenated". Strictly, same-named sections appear twice, one per layer, and are not
joined under one header.

**Decision D6 applied (extend the guard to public repos):** `shared_layer_refusal` refuses
to write a composed file that holds a SPACE or TEAM layer when git would track the output
(it is in a work tree and `check-ignore` says it is not ignored) and the repository has a
public remote, or when that cannot be told. "Public" reuses the pre-push hook's test: a
remote URL reduced to `org/name` by the hook's own sed rule (`remote_slug`) and listed
under `protected_repos` in `.datacore/config/public-repo-denylist.yaml` (the same
file and default as `pre_push_scan.DEFAULT_DENYLIST`). Every remote counts, including
`upstream`. No remote at all is not public. Unknown (policy missing or unreadable, no
`protected_repos` list, git failing) refuses. This is stricter than the hook, which
falls back to a built-in list when the policy file is missing.
Lean: `Vis`, `allowedShared`, `newWritesD6`, `prev_fix_publishes_space` (the previous
fix wrote a tracked base + space file in a public repo), `d6_never_publishes` (neither
SPACE/TEAM publication nor a PRIVATE leak), `d6_unknown_refuses`,
`d6_space_and_team_count`, `d6_writes_when_safe`. Mutation check: treating unknown as
private kills `d6_unknown_refuses`, dropping the guard kills `d6_never_publishes`, and
not counting TEAM kills `d6_space_and_team_count`.
Read-only run on this mac (scratch `rest/d6_live.py` over all 78 `*.base.md`, nothing
written): 17 composed files have SPACE layers. **One would now be refused:
`2-datacore/SCAFFOLDING.md`.** It is tracked, not ignored, and 2-datacore has
`upstream = datacore-one/datacore-org`, which is in `protected_repos`. The other 16 are
ignored or in repos with no public remote. `2-datacore/CLAUDE.md` is ignored and still
writes. Note: `2-datacore/SCAFFOLDING.space.md` is itself tracked in the same repo, so
the composed file adds no exposure beyond what the layer file already has.
Tests: `tests/test_decisions_rest.py::test_d6_*` (7 tests; the 3 refusal cases failed
before the change). No existing test changed.

**Decision D7 recorded (candidate engrams do not count as promoted):** no code. That is
the current behaviour: only `active`, or a record with no status, counts.
