# Findings: Credentials cluster (2026-09-23)

Model: `DatacoreSpec/Credentials.lean` (namespace `DatacoreSpec.Credentials`,
542 lines). It checks cleanly with
`lake env lean DatacoreSpec/Credentials.lean`, with no `sorry`, `admit`,
`axiom` or `native_decide`.
Survey items: `survey/credentials-guards.md` #1, #2, #3, #10.
Every replay used a tmp HOME, a tmp index and env with FAKE values, a fake
`_secret_urlopen` (2xx, body `{}`) and a no-op `attest`. No real credential was
read and no network call was made.

Replay script: `scratchpad/credentials/replay.py`. Its outputs before and after
the fix are quoted below. Pinned tests: `lib/tests/test_credentials_formal.py`.
Against the HEAD versions of the five files, 15 of those tests fail (the tests
were copied into a scratch tree next to `git show HEAD:` copies). Against the
fixed files, all of them pass.

## Verdict table

| # | Candidate | Verdict | Lean theorems |
|---|---|---|---|
| 1a | `get` skips the index-row verifiers that `doctor` applies (`disabled`, `api_base`, provider) | CONFIRMED+FIXED | `get_old_serves_dead`, `get_old_ignores_disabled`, `get_agrees_with_doctor` |
| 1b | `doctor` passes `c.extra`, which has no `provider`. The gitea/gitlab verifiers never run, and any 2xx from the api_base root counts as ok | CONFIRMED+FIXED | `doctor_old_passes_any_2xx`, `get_agrees_with_doctor` |
| 1c | (new, not in the survey) `get` verifies the entry's PRIMARY variable while serving the variable asked for. `creds get GH_TOKEN` sent the GitHub token to OpenAI's verifier | CONFIRMED+FIXED | `get_old_verifies_wrong_var`, `get_verifies_served` |
| 1d | `get` serves n-a with exit 0 | CONFIRMED+NEEDS-OWNER (opt-in `--strict` added) | `serve_never_dead`, `serve_ok_or_warned`, `strict_serves_only_ok`, `exit_zero_iff_served`, `default_exit_hides_na` |
| 1e | Broker lock keyed on the raw argument | CONFIRMED+FIXED | `lock_old_splits`, `lock_key_canonical` |
| 1f | `doctor` crashes when there is no index | CONFIRMED+FIXED | (pytest only) |
| 2a | `load_env_files` default gives fleet `.env` precedence over host `local.env` | CONFIRMED+FIXED (the pinned test pins something else; see below) | `load_old_fleet_beats_host`, `load_agrees_with_resolve` |
| 2b | Four parsers disagree on a duplicate key | CONFIRMED+NEEDS-OWNER | `parsers_disagree` |
| 2c | A plain-path `storage:` is not `~`-expanded | CONFIRMED+FIXED | (pytest only) |
| 2d | `ConfigError` echoes the raw line (a secret-leak path) | CONFIRMED+FIXED | `config_error_old_leaks`, `config_error_redacted` |
| 3a | A venture escalation overwrites the privacy floor (`contract-review`/dmcc → opus) | CONFIRMED+FIXED | `pick_old_breaks_floor`, `pick_respects_all`, `pick_respects_floor`, `pick_never_escalates_privacy`, `pick_respects_privacy_class` |
| 3b | An unknown privacy constraint ranks 0, so it fails open | CONFIRMED+FIXED | `pick_old_unknown_constraint_open`, `unknown_constraint_rejected` |
| 10 | file_utils CAS/lock in the credential domain | DOWNGRADED | The only defect is the lock key (1e). Otherwise `adopt_oauth_token` uses `file_lock` plus `atomic_write_text`, so a concurrent `get` reads the old file or the new one, never a torn file. As a consistency step, `adopt-token` now also takes the broker lock under the same id key. |

## Details

### 1. Serve decision (`creds.py cmd_get` / `cmd_doctor`, `credential_access.verify_value`)

The model covers `verify` (the branch order of `verify_value`),
`entryVerifier` (`_entry_verifier`), `probe` (the HTTP leg), `varFor`
(`_var_for`) and `serve` (what `cmd_get` does with a verdict). The tables and
the network are oracles, so every positive theorem holds for every table and
every provider response.

Replay before the fix:

```
1 get GEMINI (NO_PROBE n-a): (0, 'gem: served WITHOUT verification (...)')
2a doctor forge-gitea: ok    forge-gitea   HTTP 200            <- generic probe of the base root
2b get forge-gitea: (0, 'forge-gitea: served WITHOUT verification (no probe by design ...)')
2c verify with FULL entry: ('FAIL', 'HTTP 200, unexpected body')  <- /api/v1/user, "login" absent
3 get GH_TOKEN: served==fake-gh: True verified var: ['OPENAI_API_KEY']
4 lock files: ['cred-GEMINI_API_KEY.lock', 'cred-GH_TOKEN.lock', 'cred-forge-gitea.lock', 'cred-gem.lock']
5 doctor no index raised: AttributeError
```

After the fix, `doctor` and `get` both report `FAIL HTTP 200, unexpected body`
for forge-gitea, and `get` refuses to serve it (exit 1). `get GH_TOKEN` now
verifies `GH_TOKEN`. The locks are `cred-gem.lock` and `cred-multi.lock`
(keyed on the id). `doctor` without an index returns exit 2 with a message.

Fix (`creds.py`):
- `cmd_get` resolves the entry first. It verifies
  `ca._var_for(entry, cred_id)` with `entry=entry` (the full row).
- `cmd_doctor` passes `_full_entry(c)`, which is `extra` plus id, provider,
  name, type and status.
- A new `_cred_lock(id)` keys the lock on the index id. `cmd_get` and
  `cmd_adopt_token` share it.
- `--strict` refuses n-a with exit 3. Without it, n-a is still served with
  exit 0 plus the stderr notice, as before.
- A missing index gives exit 2.

Behaviour change to note: an entry with `api_base` (Gitea/GitLab) is now
actually probed on every `creds get`. Before, its NO_PROBE variable made the
answer n-a without any network call. That is the contract ("verifies against
the provider before returning"). An unreachable host is still n-a and is
still served.

Mutation checks (scratch copy, `scratchpad/credentials/mutate.py`):
- get passes no entry → `get_agrees_with_doctor` fails.
- doctor passes `extraOnly` → `get_agrees_with_doctor` fails.
- strict serves n-a → `strict_serves_only_ok` fails.
- lock keyed on the raw name → `lock_key_canonical` fails.

NEEDS-OWNER (1d): the theorem `default_exit_hides_na` records the remaining
gap. Without `--strict`, the exit status does not distinguish n-a from ok;
only stderr does. Refusing every n-a by default would break every consumer of
a NO_PROBE credential (GEMINI, PERPLEXITY, EXA, wallet keys, PyPI tokens, and
others) at the same moment.

### 2. Precedence and parsing

`resolve` + `get_value` rank `local.env` above `.env`.
`load_env_files()`'s default list was `[.env, local.env]`, and it used
first-wins when `override=False`. So the fleet value won for all 14 comms
callers (`modules/comms/lib/{run_follow_daily, draft_pipeline x2,
content_engine, engagement_health, content_scheduler, today_thread,
follow_list_builder, engagement_watchdog, engagement_learner,
engagement_callback, engagement_engine, morning_digest, engagement_analyzer}`),
all of which use the defaults with `override=False`. With `override=True` the
host already won (last-wins). So the effective precedence depended on the
`override` flag.

Replay before: `load_env_files -> fleet | resolve -> host`. After: `host | host`.

The pinned test `tests/test_env_configuration.py:48-54` pins first-wins versus
last-wins for an EXPLICIT path list. It does not pin the default order, and
it still passes. The fix orders the default list by the flag:
`[host, fleet]` when not overriding, `[fleet, host]` when overriding. The
returned dict now follows the same precedence as the exported environment;
before, it was always last-wins, so it could disagree with what was exported.

The model proves `load_agrees_with_resolve`: whenever `get_value` returns
`v`, `load_env_files()` exports `v`, for any override flag, any scope, and any
host or fleet contents (given the process has not already set the variable, or
override is on). Mutation: restoring `[fleet, host]` breaks it.

The model does not cover `today_orchestrator._load_env_files` in nightshift,
which is a separate loader outside this cluster.

2b duplicate keys (NEEDS-OWNER). `K=a` / `K=b` in one file gives:
`_read_var` → a (first), `_vars_in` → b, `config_plane.load` → b,
`env_utils.parse_env_file` → error. Shell `source` and systemd use last-wins.
So the broker can serve a different value from the one a sourced shell sees,
and `duplicates()`/`test-divergent` can probe a value `get` never serves.
This is not fixed, because making `_read_var` raise (matching env_utils)
could take down every credential in a real `.env` that holds a duplicate. I
did not inspect the real `.env` to check.

2c: before, `get_value("tilde")` with `storage: ~/fake-store.env` raised
`CredentialUnresolvable`. After, it returns the value. `resolve()` now calls
`expanduser()` on plain-path stores. keychain: and json: stores are untouched.

2d: before, `ConfigError echoes line: True`. After, `False`. The messages are
now `line N: no '=' found` and `line N: invalid key`. The existing tests,
which check line numbers and that the two reasons are distinguishable, still
pass. Before the fix, `test_config_plane.py:572-575` already warned that the
message embedded the raw line.

### 3. `model_routing.pick_model`

Replay before the fix:
- `pick_model("contract-review", venture="dmcc")` → `('anthropic', 'claude-opus-4-7')`.
- `pick_model("high-stakes", privacy_class="confidential")` → opus, with no
  error.

After the fix:
- The first call returns `('ollama', 'qwen2.5-7b')`.
- The second raises `ValueError: Unknown privacy class: 'confidential'`.

Fix: `pick_model` now escalates first, then applies every constraint. The
constraints are the venture floor, the task's own class when it is a privacy
level ("Sensitive tasks NEVER escalate"), and `privacy_class`. They go through
a monotone bump. An unknown constraint raises (`_constraint_rank`). An unknown
task or result class still ranks 0, which is the fail-closed side.

The model proves `pick_respects_all`: every constraint's rank is ≤ the rank
of the picked class. This holds for every level list, class set and venture
policy. Its corollaries are floor, privacy_class and own-level.
`unknown_constraint_rejected` proves that an unknown constraint yields
`none`.

The pytest checks all 192 (venture, task, privacy_class) combinations of the
shipped `routing.yaml`: any constraint at internal or above routes to ollama.

Mutation checks:
- Dropping the floor from the constraints breaks `pick_respects_floor`.
- A fail-open `bump` for unknown classes breaks `fold_unknown_none`, the lemma
  behind `unknown_constraint_rejected`.

Consequence for the owner: dmcc's escalations `client-report: reasoning` and
`contract-review: high-stakes` are now no-ops. Both resolve to internal
(local), because the floor wins. `lib/model_routing.pick_model` has no
production callers in `.datacore`. The chief-of-staff and mail modules use the
daemon's separate `model_routing`.

## Files changed

- `.datacore/lib/creds.py`: get/doctor verdict agreement, served-variable
  verification, lock keyed on the id (shared with adopt-token), `--strict`,
  doctor with no index.
- `.datacore/lib/credential_access.py`: `~` expansion for plain-path stores.
- `.datacore/lib/env_utils.py`: default file order follows `override`, so host
  beats fleet; the returned map follows the same precedence.
- `.datacore/lib/config_plane.py`: `ConfigError` never echoes line text.
- `.datacore/lib/model_routing.py`: constraints applied after escalation; an
  unknown constraint raises.
- New: `.datacore/lib/tests/test_credentials_formal.py` (206 tests, including
  the 192-case routing sweep).
- New: `DatacoreSpec/Credentials.lean` and this file.

## Targeted tests

`cd .datacore/lib && python3 -m pytest -q tests/test_credentials_formal.py tests/test_model_routing.py tests/test_config_plane.py tests/test_env_configuration.py tests/test_creds.py tests/test_credential_access.py tests/test_credential_preservation.py`
→ **366 passed**.

## NEEDS-OWNER questions

1. Should `creds get` refuse n-a by default, making `--strict` the default?
   Or should it at least return a distinct nonzero exit code for "served, but
   unverified"? Either choice breaks `X=$(creds get …)` under `set -e` for
   every NO_PROBE credential.
2. Duplicate keys in an env store: should `_read_var` raise (matching
   `env_utils.parse_env_file`), or use last-wins (matching shell `source`,
   systemd, `_vars_in` and `config_plane`)? Today the broker is the only
   reader that uses first-wins.
3. dmcc routing: with the floor now enforced, `client-report` and
   `contract-review` route to local models. If frontier was intended for them,
   the owner has two options: drop or lower dmcc's `privacy_floor`, or add a
   private premium class to escalate to.
4. Lock-key rollout: a process still running the old code locks on the raw
   argument, while new code locks on the id. The two do not exclude each other
   until every host runs the new `creds.py`. Should this be rolled out to all
   hosts together?

## Owner decisions applied (2026-09-23)

Tests: `lib/tests/test_decisions_creds.py` (26 tests, all failing on the
pre-decision code except the regression guards that the floor still holds).
Mutation script: `scratchpad/creds/mutate_decisions.py` (7 mutants, all break).

Decision C1 applied: serve n-a, warn on stderr; `--strict` stays opt-in. No
behaviour change. The n-a stderr line now has one fixed prefix,
`creds.NA_NOTICE_PREFIX = "creds get: n-a:"`, and reads
`creds get: n-a: <id>: served UNVERIFIED on stdout, exit 0 — <why>. Use --strict to refuse n-a (exit 3).`
No other outcome prints that prefix: FAIL says `value is DEAD`, `--strict`
says `NOT served, exit 3`, and `--no-verify` prints nothing. `creds get --help`
now has an epilog that lists every outcome with its exit code and quotes the
line. Lean: `Outcome.warned` became `Outcome.notice : Notice`. New theorems:
`na_notice_iff` (the n-a notice appears iff the check ran, the verdict was
n-a and `--strict` was off) and `notice_determines_outcome`. The theorem
`default_exit_hides_na` now records the decided residue. Mutants: `--no-verify`
printing the notice, or `--strict` reusing it, each break `na_notice_iff`.
Changed test: `test_credentials_formal.py::test_na_is_served_with_notice_by_default_and_refused_under_strict`
now asserts the prefix instead of the old wording.

Decision C2 applied: last wins everywhere, and doctor warns.
`credential_access._read_var` now keeps the last match; before, it kept the
first. `env_utils.parse_env_file` no longer raises on a duplicate key; the
error is now `invalid environment assignment at line N`. `_vars_in` and
`config_plane.load` were already last-wins; they now carry a comment saying so.
New `credential_access.duplicate_keys(path)` and `within_store_duplicates()`
return key names only. `creds doctor` prints `WARNING: keys duplicated within
one store` with the store path and key names. It never prints values and
never changes the exit code. With `--id`, it lists only that credential's
variables. Lean: `parsers_disagree` became `parsers_disagreed`, a historical
theorem. New definitions: `assign`/`parseFold` (the one fold), `readVarNew`,
`varsIn`, `configLoad`, `parseEnvFile`. `all_parsers_agree` proves that all
four equal `lastWins`. `unlisted_key_unchanged` proves that the change is
confined to what doctor lists: on a key assigned at most once, first-wins
equals last-wins. Mutants: first-wins `_read_var`, or a raising
`parse_env_file`, each break `all_parsers_agree`. Changed tests:
- `test_env_configuration.py::test_ambiguous_environment_is_refused_without_echoing_values`
  lost its duplicate-key (the same key assigned twice) case.
- The new `test_duplicate_key_takes_the_last_value` covers that case instead.
- The `broker` fixture in `test_credentials_formal.py` now also points
  `ca.DATA` at tmp, so doctor's store scan never reads a real store.

Needs an edit in a file this cluster does not own:
`modules/chief-of-staff/tests/test_cos_environment.py::test_invalid_last_layer_refuses_without_partial_environment[DUP=a\nDUP=b\n]`
still expects a duplicate to be refused. `cos_env.py`/`cos_env.sh` parse
through `env_utils.parse_env_file`, so the Python and shell loaders stay in
agreement: both are now last-wins. The fix is to drop that parameter.

Decision C3 applied: the floor is lowered NARROWLY. `routing.yaml` dmcc gains
`floor_exceptions: [client-report, contract-review]`, with a comment giving
the date and saying that this was the owner's explicit choice.
`pick_model` skips ONLY the venture floor for a listed task, and adds
`dmcc floor exception for <task> (owner decision C3)` to the rationale. The
task's own privacy level and the caller's `privacy_class` still apply.
Results:
- dmcc contract-review goes to anthropic/claude-opus-4-7.
- dmcc client-report goes to anthropic/claude-sonnet-4-6.
- Every other dmcc task still goes to ollama.
- Either exception with `privacy_class=internal` or `sensitive` goes to
  ollama.
- No other venture has exceptions (pinned by a test).

Lean: `Venture.exc`, `ventureFloor`; `constraints` omits the venture floor
for an excepted task. `pick_respects_all` is unchanged in form. The
restated `pick_respects_floor` now takes `v.exc task = false`: the floor
holds for all tasks except the listed exceptions. New theorems:
`exception_only_drops_floor`, `dmcc_exceptions_escalate`,
`dmcc_floor_elsewhere`, `dmcc_exceptions_keep_privacy_class`.
`pick_old_breaks_floor` now uses `dmccOld`, dmcc before C3. Mutants: waiving
the floor for every task breaks `pick_respects_floor`; widening dmcc's list
breaks `dmcc_floor_elsewhere`; letting an exception drop `privacy_class`
breaks `dmcc_exceptions_keep_privacy_class`. Changed tests:
- `test_credentials_formal.py::test_venture_floor_survives_escalation` now
  runs on a copy of dmcc without exceptions.
- `test_any_private_floor_yields_a_local_model` now honours
  `floor_exceptions` and also sweeps `client-report`.

C4/C5 (prepared, not run): `lib/env_overlap_report.py` is read-only and
stdlib-only. It prints the keys that are in both `.env` and `local.env`,
with the 12-hex sha256 of each value (the same format as
`credential_access.fingerprint`) and a same/DIFFER verdict, plus the keys
duplicated inside each file. It never prints a value, and the fingerprints
can be compared across hosts. It was tested only on FAKE files in tmp. To run
it on a host that lacks the new code, pipe it over ssh:
`ssh <host> 'python3 - --root ~/Data' < .datacore/lib/env_overlap_report.py`.
On the mac:
`python3 .datacore/lib/env_overlap_report.py`.
