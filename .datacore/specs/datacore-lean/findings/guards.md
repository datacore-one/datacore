# Guards cluster: findings (2026-09-23)

Model: `DatacoreSpec/Guards.lean`, namespace `DatacoreSpec.Guards`. It checks
cleanly with `lake env lean DatacoreSpec/Guards.lean`. There is no `sorry`,
`admit`, `axiom` or `native_decide`, and the only axioms used are `propext`,
`Quot.sound` and `Classical.choice`. The file is 690 lines, over the
400-line guide. It covers eight candidates at medium depth each, and every
decision function is small.

Replays: `lib/tests/test_guards_formal.py`, 53 tests. They pipe synthetic hook
JSON into the real scripts, or build throwaway git repos in `tmp_path`. Every
host name is fictional, and none of them reaches a network. The same tests
run against the pre-fix copies via `GUARDS_UNDER_TEST=… GUARDS_LIB_UNDER_TEST=…`,
which point at copies taken with `git show HEAD:…`. On those copies 27 + 12
tests fail, and on the fixed code all 53 pass.

Mutation check: each fix below was put back into a scratch copy of the model,
and the named theorem stopped proving in 12 of 12 cases. The scratch copies are in
`scratchpad/guards/lean/Mut.lean`.

| # | Guard | Verdict |
|---|---|---|
| 4 | restricted_hosts_guard | CONFIRMED+FIXED |
| 5 | tool_policy | REFUTED (never ⇒ deny holds) · field gap CONFIRMED+NEEDS-OWNER · MultiEdit/NotebookEdit DOWNGRADED · docstring FIXED |
| 6 | injection_integrity_guard | CONFIRMED+FIXED (clear) · Bash-while-armed NEEDS-OWNER |
| 7 | log_ownership_guard | evil merge CONFIRMED+FIXED · forgeable author NEEDS-OWNER · failed rev-list NEEDS-OWNER (now loud) |
| 8 | egress_scan / egress_runtime_check | `--enforce` skips CONFIRMED+FIXED · all-n-a exit 0 NEEDS-OWNER |
| 9 | hooks.py retry/validate | retry key CONFIRMED+FIXED · unknown validator NEEDS-OWNER |
| 11 | pre_push_scan | exit contract CONFIRMED+FIXED · empty stdin DOWNGRADED · A-only gate DOWNGRADED |
| 12 | commit_gate | REFUTED (partition proved) |

---

## 4. restricted_hosts_guard: CONFIRMED+FIXED

**Claim:** a network git subcommand whose remote is restricted is blocked.

**Refuted** by `Hosts.old_misses_cd` and `Hosts.old_misses_prefixes`, both
decided on the Python's classification of the tokens. The old `_git_targets`
recognised git only as the first token of the whole command (operators
included), and skipped only `-C`'s value. Replayed with a fake HOME that
restricts `acme` and a repo whose origin is `git.acme.example`. Each of these
exited 0 while plain `git push` exited 2:

- `cd <bad> && git push`, both from the bad repo and from a safe cwd
- `FOO=1 git push`, `env git push`, `env -i PATH=… git push`
- `git -c k=v push`, where `k=v` was read as the subcommand
- `git --git-dir …`, `sudo -u me git push`, `timeout 30 git push`
- `(cd bad; git push)`, `true; git fetch`, `sleep 1 & git push`
- `bash -c 'git push'`, `eval git push`, `echo $(git ls-remote)`

The same head detection skipped remote tools: `sudo ssh acme`,
`timeout 5 ssh acme`, `nohup ssh acme`, `(ssh acme)` and `env ssh acme` all
passed. Both paths now share one `_invocation`.

**Also confirmed: exceptions exited 0.** A private overlay written as a YAML
list (`- acme`) raised AttributeError in `load()`. `__main__`'s catch-all
turned that into exit 0, so `ssh acme` was allowed. Its docstring promises the
opposite: "Fails closed when it cannot finish evaluating a command that can
reach a network". `hosts: acme` as a bare string was iterated character by
character.

**Fix** (`lib/hooks/restricted_hosts_guard.py`):

- The command is split into simple commands with quote-aware `shlex`
  (`punctuation_chars`), and falls back to the old regex split on unbalanced
  quotes.
- Before git or a remote tool is looked for, the guard strips keywords,
  `VAR=value` and wrappers, unpacks `sh -c`, `eval` and `$( )`/backticks, and
  skips git's option values (`-c`, `-C`, `--git-dir`, …).
- Every directory the shell could be in is tracked: the start directory plus
  each `cd`, applied or not. `-C`, `--git-dir` and `GIT_DIR` are added.
- A `cd $VAR` before a network git is Unevaluable, and so fails closed.
- `load()` validates the shape of both configs.
- Any exception in evaluation now takes the fail-closed path when the command
  can reach a network.
- A `git remote -v` failure other than "not a git repository" is Unevaluable.

**Proved on the fixed model:**

- `git_candidates_sound`: for every command and every choice of which `cd`s
  take effect, if the shell runs a network git in a directory with a
  restricted remote, the guard blocks it.
- `invocation_strips_assignments`, `invocation_unwraps` and
  `gitSub_skips_option_value` (all for every lexer).
- `fails_closed`, with `old_fails_open` as the counterexample.

**Mutations:** dropping `cd` tracking breaks the soundness theorem, and so
does assuming every `cd` applied. Reading `-c`'s value as the subcommand
breaks the `-c` lemma.

**Not modelled in Lean** (covered by the pytest replays instead): quoting,
`sh -c`/`eval`/substitution unpacking, URL/scp extraction, and runtime
directories.

**Behaviour change to know about.** The guard is live in `~/.claude/settings.json`.
A network git after `cd "$X"` (for example `for d in */; do (cd $d && git pull); done`)
is now refused as unevaluable. Before, it was checked against the wrong
directory. A realistic command corpus (pytest runs, heredocs, pipes, `git log`,
`git fetch` in ~/Data, `curl` to GitHub) still passes.

## 5. tool_policy

- **Never-effect ⇒ deny for all grants: REFUTED (holds).** Proved by
  `Policy.never_denies_for_all_grants`. The converse is
  `allowed_means_permitted`: an allowed call hits no never-effect, and every
  cosign effect it hits was granted. `policy_error_denies` covers the case
  where any exception in `evaluate_hook` denies. Dropping the never branch
  breaks the theorem.
- **MultiEdit `edits[].new_string` / NotebookEdit `new_source`: DOWNGRADED.**
  Every effect in `config/tool_effects.yaml` has a `tools:` list of acting
  tools, and MultiEdit and NotebookEdit are not on it. The file says why:
  "Edit and Write cannot send, pay or deploy by themselves". `classify`
  therefore returns ∅ for them before any text is looked at
  (`unlisted_tool_never_classified`).
- **Field coverage: CONFIRMED+NEEDS-OWNER.** When any `_TEXT_KEYS` field is
  present, the other fields are not matched (`call_text_field_gap`). Example:
  an MCP input `{"url": "https://example.org", "body": "api.stripe.com/v1/charges"}`.
  `tool_effects.yaml` says an MCP input is matched "as its JSON". The fix
  would always append the JSON. It is not made, because
  `.datacore/tests/test_tool_policy.py::test_call_text_prefers_command_fields_then_json`
  pins `call_text({"command": "ls", "description": "list"}) == "ls"` on
  purpose, and because `hermes_plugin.foreign_actor_write` consumes the same
  text.
- **Docstring vs code on an unlisted principal: FIXED (docstring only).** The
  `limits_for` docstring said an unlisted principal gets the global cosign
  set with no never-effects. The code raises, and `evaluate_hook` denies. The
  code is the safer of the two and
  `test_unlisted_principal_cannot_bypass_declared_limits` pins it, so the
  docstring now says what the code does.

## 6. injection_integrity_guard

- **Clear on any mention: CONFIRMED+FIXED.** The claim is that the gate
  clears only after an actual full read. Replay: a 10-line Read of a
  5000-line spill disarmed the gate, and so did `ls tool-results` whose
  output named the file. The guard's own deny text says "reading the first
  and last few KB is how a redaction rule gets skipped".
  - *Fix:* only a Read of the spill file (compared by realpath) counts.
    Coverage accumulates as offset/limit ranges across Read calls, and the
    gate lifts when they cover lines 1..N. A Read refused as too large adds
    nothing. A vanished file unlinks the state, to stay fail-safe as the
    docstring requires.
  - *Proved:* `sweep_sound` (in any order) and
    `gate_clears_only_on_full_read`. The end-to-end result is
    `cleared_means_every_line_read`: every line lies in the range of an
    actual Read event of that file. `mention_is_not_a_read` shows a mention
    adds no coverage. `old_clears_on_peek` is the counterexample.
  - *Mutations:* making a mention add coverage breaks `absorb_from_reads`,
    and removing the gap check breaks `sweep_sound`.
- **Bash is allowed while armed: NEEDS-OWNER.** Bash is in READERS on
  purpose (line 42: "Tools that must stay available so the spilled file can
  actually be read"). But `curl` or `git push` then run while memory is
  truncated. Left unchanged.
- **Failures exit 0: design.** This is stated in the FAIL-SAFE docstring,
  lines 26-30. Not a bug.

The guard is not wired in `~/.claude/settings.json` today, although
`install_redaction_guards.py` would wire it.

## 7. log_ownership_guard

- **Evil merge: CONFIRMED+FIXED.** A merge authored by this machine that
  edits `winston.jsonl` while merging exited 0, because `--no-merges`
  dropped it.
  - *Fix:* merges are now included, and each commit is inspected with
    `git show --cc --name-only`. This is a combined diff: it lists only paths
    whose result differs from every parent. A clean union merge lists
    nothing, so the 2026-08 false refusal does not come back. The replay test
    `test_clean_union_merge_is_allowed` and the existing
    `TestLogOwnershipGuardAuthorship` confirm it. `--cc` also pins the output
    against a host's `log.diffMerges` setting.
  - *Proved:* `ownership_sees_merge_writes`. Re-adding `!isMerge` breaks it.
- **Author email is forgeable: NEEDS-OWNER.** `author_filter_is_forgeable`
  shows the verdict depends only on the `author` field. The docstring chose
  this ("What this machine is accountable for is what it wrote"). An
  alternative is to treat any commit not reachable from a remote-tracking ref
  as this machine's.
- **A failed rev-list allows the push: NEEDS-OWNER.**
  `unlistable_range_allows` shows this. It now prints "this range was NOT
  checked" to stderr but still exits 0. Refusing instead would block pushes
  on every repo, protected or not.

## 8. egress_scan / egress_runtime_check

- **`--enforce` skipped manifest errors and unparseable files:
  CONFIRMED+FIXED.** Replay: a module with `egress: [unclosed` and a
  `requests.post` exited 0 under `--enforce`. So did an opted-in module with
  a `.py` file that does not parse.
  - *Fix:* both are counted as UNSCANNABLE and fail `--enforce`. Unparseable
    files count only in opted-in modules, which is consistent with the
    ratchet. On the real modules, old and new give the same verdict (exit 1,
    from one pre-existing undeclared site), with 0 unscannable.
  - *Proved:* `enforce_counts_unknowns`. `old_passes_broken_manifest` is the
    counterexample.
- **egress_runtime_check exits 0 when every row is n-a: NEEDS-OWNER.**
  `runtime_all_na_exits_zero` shows this. The one caller, `v2_verify.py:971-990`,
  parses "N unverifiable" from stdout and maps it to n-a correctly. So only a
  human reading `$?` is misled. Changing the exit-code contract (for example
  3 for unknown) is the owner's call.

## 9. hooks.py

- **Retry counter keyed "default": CONFIRMED+FIXED.** Replay: after three
  transient errors on task A, task B got `retry=False`.
  `_hook_retry_schedule` reads `instructions["task_id"]`, which nothing ever
  set. There are no production callers today; only the CLI self-test and the
  tests call it.
  - *Fix:* `execute_error_hooks(..., task_id=None)` and
    `handle_error(..., task_id=None)` put the task id into the instructions.
    `reset_retries(agent_id, task_id)` forgets a budget after success. With
    no id, callers get the old shared counter.
  - *Proved:* `retry_budget_is_per_task`. `old_budget_is_global` is the
    counter-example. A global counter breaks the theorem.
- **An unknown validate hook is skipped, so validation passes:
  NEEDS-OWNER.** `unknown_validators_pass` shows that a misspelt validator
  list passes. Whether an unknown validator should fail is the owner's call.
  Separately, the CLI self-test (`python hooks.py <agent>`) writes a mock
  error into the real `hook_state.yaml`.

## 11. pre_push_scan

- **Exit contract: CONFIRMED+FIXED.** Replay:
  - A denylist with a YAML syntax error exited 1.
  - A list-shaped denylist and `forbidden_paths: 5` also exited 1, with no
    ✗ lines printed.
  - The hook reported all three as "policy violations".

  *Fix:* YAMLError and non-mapping policies return 2, and `__main__` maps
  every exception to 2. *Proved:* `every_failure_is_exit_2`;
  `failures_always_block` shows the push was blocked before and after, so
  safety was never lost, only the diagnosis.
- **Empty stdin is "clean": DOWNGRADED.** The hook exits "no outgoing commits"
  before calling the scanner (`.git/hooks/pre-push`, the `ALL_RANGE` check).
  `scanner_only_sees_nonempty` shows this.
- **New-file gate only on status A: DOWNGRADED.** `git_privacy.changed_paths`
  runs with `--no-renames`, so a rename is D+A and the gate sees the new path.

## 12. commit_gate: REFUTED (proved)

The following hold for every dirty list and every `produced`, including `None`:

- `partition`: dirty = allowed ∪ withheld.
- `disjoint`.
- `allowed_produced`: allowed ⊆ produced.
- `sizes`: |allowed| + |withheld| = |dirty|.

Mutating `allowed` to `dirty` (the old `git add -A`) breaks `disjoint`.

## Files changed

- `lib/hooks/restricted_hosts_guard.py`
- `lib/hooks/injection_integrity_guard.py`
- `lib/hooks/log_ownership_guard.py`
- `lib/hooks.py`
- `lib/pre_push_scan.py`
- `lib/egress_scan.py`
- `lib/tool_policy.py` (docstring only)
- new: `lib/tests/test_guards_formal.py`, `specs/datacore-lean/DatacoreSpec/Guards.lean`, this file

## Tests

- `cd .datacore/lib && python3 -m pytest -q tests/test_guards_formal.py`: 53 passed.
- Regression run over `tests/test_hooks.py`, `tests/test_pre_push_scan.py`,
  `tests/test_commit_gate.py`, `tests/test_policy_adversarial.py`,
  `../tests/test_tool_policy.py`, `tests/test_git_privacy.py`,
  `tests/test_ledger_attacks2.py` and `../tests/test_log_ownership_run_scoped.py`:
  147 passed.

---

## Owner decisions applied (2026-09-23)

Tests: `lib/tests/test_decisions_guards.py` (52 tests, 32 of them failed on
the code before these changes). Lean: `DatacoreSpec/Guards.lean` re-checked
with `lake env lean`. Each changed theorem was mutation-checked in
`scratchpad/guards-apply/Mut_*.lean`: putting the old behaviour back breaks
it (9 of 9).

- **Decision S1 applied: add the JSON always.** `tool_policy.call_text` returns
  the text-key fields first and then the JSON of the whole input, whatever
  else is present. `{"url": …, "body": "api.stripe.com/v1/charges"}` now
  classifies as `payment`. Lean: `call_text_covers_every_field` and
  `call_text_text_keys_first`. `old_call_text_field_gap` is kept as the
  counterexample. The pinned test
  `tests/test_tool_policy.py::test_call_text_prefers_command_fields_then_json`
  now expects `"ls\n{json}"`. `hermes_plugin.foreign_actor_write` still
  works: the raw fields still come first, JSON escaping creates no new actor
  match, and `test_hermes_plugin.py` passes. Side effect to know about: a
  Bash `description` is now matched as well, so a description that quotes
  an effect pattern (for example a Stripe charges URL) classifies the call.
- **Decision S2 applied: read-only Bash on the spill file.** Bash is no longer
  in READERS. While the gate is armed, `bash_reads_only` allows only these
  commands: `cat`, `head`, `tail`, `sed -n 'N[,M]p'`, `grep`, `wc` and
  `less`. Each has a per-program option whitelist, and the spill file
  (compared by realpath) must be the only file operand. The command must
  not contain `| ; & < > $ \` ( ) {}` or a newline. Everything else is
  denied, and the denial says the gate lifts once the Read tool has read
  every line. A Bash read never clears the gate. Lean:
  `armed_bash_reads_only_the_spill`, `armed_denies_other_tools`,
  `unarmed_allows`, and `old_allows_curl_while_armed` as the
  counterexample. Not modelled: the per-program option whitelist (`_opts_ok`,
  an oracle in the model), which pytest covers with 14 allowed and 24 denied
  forms.
- **Decision S3 applied: judge by "not on any remote".** `changed()` now runs
  `git rev-list <range> --not --remotes` and inspects every commit it
  returns, whatever the author email. Commits a fetch brought in are on a
  remote-tracking ref and are excluded. Lean: `unpushed_writes_are_judged`,
  `carried_commits_pass`, and `ownership_sees_merge_writes` (kept).
  `author_filter_is_forgeable` is now the counterexample, and S3 refuses it.
  Pinned tests changed:
  `test_hooks.py::TestLogOwnershipGuardAuthorship::test_foreign_authored_commit_in_range_is_allowed`
  and the `merge_repo` fixture in `test_guards_formal.py`. In both, the
  foreign commit now sits on `refs/remotes/origin/*`, as a fetch leaves it.
  Consequence: a repo with no remote-tracking refs (never fetched, or pushed
  to a bare URL) has every commit in the range judged. A range of that kind
  that holds historical foreign-log edits is refused.
- **Decision S4 applied: refuse.** A range that `git rev-list` cannot list
  (and a `git show` failure on a listed commit) raises `UnlistableRange`.
  `main` then exits 1 with "REFUSED … NOT checked". Lean:
  `unlistable_range_refuses`, with `old_unlistable_range_allowed` as the
  counterexample. The pinned test
  `test_guards_formal.py::test_unlistable_range_is_reported_not_silent` now
  expects exit 1.
- **Decision S5 applied: exit 3 when nothing could be checked.**
  `egress_runtime_check` exits 1 on any broken row (or a failed
  `--functional`), 3 when no row is verified (all rows n-a, or no rows), and
  0 otherwise. Lean: `runtime_exit_zero_means_something_verified` and
  `runtime_all_na_exits_three`, with `old_all_na_exits_zero` as the
  counterexample. The caller, `v2_verify.py:971-990` (read-only here),
  ignores the exit code and parses the "runtime wiring:" line. So it maps an
  all-n-a run to n-a correctly. With zero rows it prints "0 unverifiable"
  and reports **ok**, although the exit is now 3.
- **Decision S6 applied: both.** `execute_validate_hooks` returns
  `(False, "Unknown validate hook type: …")` on an unknown type. Lean:
  `validate_passes_only_known`, with `old_unknown_validators_pass` as the
  counterexample. `HookExecutor(write_root=…)` sends every write (hook
  state, learning candidates, embed queue, journal) under that root and
  skips the execution log. The registry and context are still read from
  DATACORE_ROOT. `python hooks.py <agent>` runs in a
  `TemporaryDirectory`, so it never writes the real `hook_state.yaml` or
  `execution_log.yaml`.
- **Decision S7 applied: keep refusing.** `restricted_hosts_guard` still
  refuses a network git after `cd $VAR` or a loop `cd` as unevaluable. There
  is no code change, and `tests/test_restricted_hosts_guard.py` stays green.

## Follow-up decisions applied (2026-09-23, second board)

Tests: `lib/tests/test_followups_sync.py` (Q2, Q4 sections). Lean:
`DatacoreSpec/Guards.lean` §2 re-checked with `lake env lean`. Mutation files
are in `scratchpad/followups-sync/`.

- **Decision Q4 applied: exclude description.** `tool_policy.call_text(tool_input,
  tool_name=None)` now drops the tool's prose fields (`_PROSE_FIELDS`: for `Bash`,
  only `description`) before building both the text-key part and the JSON part.
  Every other field stays. Every field of every other tool stays, and so does
  any call that does not name its tool (fail closed). `classify` and
  `evaluate_hook`'s record detail pass the tool name. Hermes calls it without
  a tool name, so its behaviour is unchanged.
  - Lean: `callText` now takes the tool.
    - `call_text_covers_every_field` is re-proved with the exclusion as a
      hypothesis (`prose tool kv.1 = false`).
    - `call_text_non_bash_covers_every_field` covers every other tool.
    - `call_text_bash_description_unmatched`: a value that only the description
      carries is not matched.
    - `call_text_text_keys_first` is kept.
    - `callTextS1` with `s1_matches_bash_description` is the counterexample.
      `q4_drops_bash_description` is the replay.
  - Mutation M-Q4 (`prose := false`) breaks `call_text_bash_description_unmatched`.
  - Tests:
    - `test_q4_a_bash_description_quoting_an_effect_does_not_classify` and
      `test_q4_the_hook_allows_the_innocent_command` failed before the change.
    - `test_q4_every_other_bash_field_is_still_matched` and
      `test_q4_a_description_outside_bash_is_still_matched` pin what stays.
  - No pinned test changed: `call_text(inp)` without a tool name still carries
    the whole JSON.
- **Decision Q2 applied to tool_policy (L10 follow-up).** `principal_for()` and
  `record_refusal` now resolve this host's actor with
  `actor_identity.this_actor(strict=True)`.
  - An undeclared host raises `UndeclaredActor`, which `evaluate_hook` turns
    into a deny.
  - A refusal is not recorded under a guessed hostname: it returns False, and
    the refusal itself stands.
  - An explicit actor (`executors/base.py` passes its own) is unaffected.
  - This mac resolves `mac` from identity.env, principal `gregor`.
  - Tests: `test_q2_principal_for_refuses_an_undeclared_host` and
    `test_q2_a_refusal_is_not_recorded_under_the_hostname` failed before the
    change. `test_q2_the_hook_denies_on_an_undeclared_host` passes.
- **Decision Q5: accept.** No change. A repo with no remote-tracking refs has
  its whole push range judged by log_ownership_guard (the S3 consequence
  above), and that is the intended behaviour.
