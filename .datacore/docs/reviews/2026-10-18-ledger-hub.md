# Ledger hub: the ledger's front door (review note, 2026-10-18)

Step 4 of the hub plan. Spec: the DIP-0034 October 2026 amendment, sections A–E and G (branch `dip/0034-hub-front-door` in datacore-dips, not yet ratified). Task: a6cf76bf-cc50-4a5b-8d99-1a03ea25bb9b.

## What changed

| Repo | Branch | Commits | Files |
|---|---|---|---|
| datacore (root) | `feat/ledger-hub` | a88afb4 (tests first), 89bc63a (code) | `.datacore/lib/ledger/hub.py`, `.datacore/lib/tests/test_ledger_hub.py`, `ledger/fold.py` (+153, lease rules), `ledger/events.py`, `ledger/log.py`, `ledger/seal.py`, and one-line updates in eight callers so the hash covers the new `hub` field (14 files, +1662/−22) |
| datacore-app | `feat/ledger-hub` | 0bd4ca3 | `daemon/datacored/api/ledger_hub.py`, `daemon/datacored/app.py` (+5), `daemon/tests/test_ledger_hub_api.py` |

Nothing is pushed. No PR is open.

## What it does

- **The hub answers first; the ledger stays the arbiter.** A hub accepts signed appends and lease requests for the spaces it serves, orders them and answers at once. Every answer is an event in its own per-writer log, so a hub rebuilt from a git copy gives the same answers.
- **Leases.** Only the active hub grants, and only when no live lease exists. If two leases still end up in the log, the earliest one by hub sequence wins, and the loser's completion is recorded as a conflict.
- **Signing is required in hub spaces.** Unsigned, wrongly signed, edited, forked, gapped and foreign-log appends are refused, and so are events only a hub may write.
- **Two confirmations.** The hub's answer is the first. The second is each writer checking its own events and leases in the sealed log (`receipt_check`). The hub never checks its own seal.
- **Outage.** Writers append locally first, buffer while the hub is unreachable and replay when it returns. After a restart, the hub waits one lease period before re-granting an expired item.
- **No automatic failover.** A second hub refuses to grant until the owner runs `promote`. A second process on the same host is locked out.
- **Off by default.** A space opts in with `.datacore/ledger-hub.json`. datacored mounts the routes only with `DATACORE_LEDGER_HUB=1` and a named hub writer. The bearer token gates the network; identity comes from the signature.

## Verified in this session

- `.datacore/lib/tests/test_ledger_hub.py`: **12 passed**.
- `daemon/tests/test_ledger_hub_api.py`: **3 passed** with `DATACORE_ROOT` pointing at the hub worktree. Without it the test **skips** ("needs an installation with ledger/hub.py"). The skip is correct, but it means CI on datacore-app proves nothing until the root branch is merged or CI points `DATACORE_ROOT` at it.
- Wider suites: a baseline-versus-branch run over related suites hit the same two promise-eval collection errors on both trees (CAD3 and CAD7, existing, not touched). A rerun without them did not complete, so the wider comparison is **not verified** for this branch alone. The ledger suites on the role-pool branch, which contains this branch, gave 617 passed.

## Not done, for the owner

- Enabling on any host, and which host is the first hub.
- Pushing the branches and opening PRs (two repos; the app PR depends on the root one).
- The stray modified `daemon/.datacore/state/cos-sidecar-breaker.json` in the app worktree is runtime state from the test environment. It was left uncommitted on purpose.

## Audit fixes, part 1

Source: the Codex audit of 2026-10-18 (`2-datacore/1-tracks/engineering/reports/2026-10-18-dip-0034-amendment-codex-audit-report.md`), findings 1, 2, 3, 5, 6, 7, 16 and 18. Findings 4, 8, 9, 10, 11, 12, 15 and 17 are part 2 (a second pass), and 13 and 14 are spec changes.

Commits (root `feat/ledger-hub`): fca80a5 (tests first, all 19 seen failing), 9cf1107 (code). datacore-app `feat/ledger-hub`: 51f7306 (API test first, seen failing), 7788497 (code). One code commit per repo: the fixes share one admission module, so they could not be split by finding without committing half-applied states.

The new piece is `.datacore/lib/ledger/hub_admission.py`: the one admission rule. HTTP append (`check_event`), a git copy read from disk, a rebuild and the hub's seal (`admit`, via `Hub._state`) all use it. It is pure: who the grantors are, the cutover manifest and "now" are inputs (`Authority`, `now_ms`), never read during replay.

| # | What was wrong | Fix | Tests (`test_ledger_hub.py` unless noted) | Not fully closed |
|---|---|---|---|---|
| 1 | Legacy, and so "valid unsigned", was decided by the event's own timestamp | Legacy is membership in the cutover manifest `enable` records (`HubConfig.cutover_logs`: per log, highest seq and head hash; pinned by `cutover_sha256`). Network append refuses every unsigned event. A legacy prefix from disk counts only when it is complete and ends exactly at the manifest's head | `test_audit1_backdated_unsigned_event_is_refused_over_the_network`, `test_audit1_legacy_is_membership_in_the_cutover_manifest_not_a_timestamp`, `test_audit1_an_edited_cutover_manifest_is_refused` | The manifest lives in the config file. Whoever can rewrite that file can rewrite both the manifest and its sha. Real immutability needs the manifest signed by the owner or recorded in the ledger (ties to finding 4's promotion record). `cutover_hlc` is kept as information only |
| 2 | Events arriving by git, restore or rebuild went into the fold unchecked; any writer's lease counted because it had a `hub` field | `Hub._state` folds only admitted events. Hub-only types (leases, renewals, lease conflicts, seals) count only from the grantor or a grantor it succeeded (`previous_grantors`, set by `promote`), in that grantor's own log. Post-cutover signatures are verified strictly: no key on this machine means not admitted. The first refused event ends what is admitted of its log. `fold()` takes `lease_grantors` as an explicit input | `test_audit2_a_lease_an_ordinary_writer_wrote_and_git_imported_confers_nothing`, `test_audit2_an_event_signed_with_an_unknown_key_after_cutover_is_not_admitted`, `test_audit2_the_fold_honours_leases_only_from_the_grantors_it_is_given` | Other `fold()` callers outside the hub (projector, dashboards) still fold unfiltered. Their view of lease state in a hub space is not authoritative until they call `admit` or pass `lease_grantors`. A seal by a non-hub sequencer in a hub space now ends that writer's admitted chain |
| 3 | `receipt_check` returned `ok: true` while something was pending or the seal could not be verified | Returns `status` = confirmed / pending / failed. `ok` is true only for confirmed: `verify_seal` is True and every required event (default: all of mine plus my leases; or `required=[hashes]` for one operation) is covered and byte-identical. A different event at my position is a failure even before a seal covers it | `test_audit3_a_result_newer_than_the_seal_is_pending_not_ok`, `test_audit3_a_seal_that_cannot_be_verified_is_not_a_confirmation`, `test_audit3_no_seal_yet_is_pending_and_not_ok` | Not done: binding the receipt to a verified git commit (the caller still passes the checkout it trusts), and checking that the sealed decision actually authorises the operation (co-sign, finding 12) |
| 5 | Retired keys signed fresh requests; any registered writer's signature passed as a hub answer | Requests are verified with the key current at the hub's clock (`at_ms=now`). Network appends must also verify under the current key. A lease answer must be the expected type, signed with the current key of the grantor the writer's copy of the config names, carry the space/hub/log domain, and record this request's nonce and kind | `test_audit5_a_retired_key_cannot_sign_a_fresh_lease_request`, `test_audit5_a_lease_answer_signed_by_another_writer_is_not_a_first_confirmation`, `test_audit5_a_lease_answer_is_bound_to_its_request_nonce_and_type` | There is no key identifier in requests, and no separate revocation, only rotation. Events buffered before a rotation and replayed after it are refused over HTTP; they have to come by git |
| 6 | Signatures did not bind space or log; renewals did not check the hub | New optional envelope field `domain` = `ledger/2 space=<id> hub=<id> log=<chain>`, inside the hashed and signed body, stamped by every writer in a hub space. Older events have none and keep their bytes. Admission checks it. Requests carry `space`; renewals check hub and space like grants. Lease receipts are stored per space, hub, log and actor. Each config gets an immutable `space_id` | `test_audit6_a_signed_event_cannot_be_replayed_into_another_space_or_log`, `test_audit6_a_renewal_addressed_to_another_hub_is_refused`, `test_audit6_lease_receipts_are_kept_per_space_hub_and_log` | Readers that predate the `domain` field cannot read hub-space events. That is finding 17 (versioned compatibility), part 2. Six callers outside the ledger package that rebuild an event body now pass `domain` through |
| 7 | Clock problems were dropped at admission; a lease could sort before its own item | Admission refuses a malformed HLC, one more than the writer's tolerance (10 min) ahead of the hub's clock, and one not after the previous event in its own log (`bad_clock`). Lease, renewal and conflict answers take the item's latest HLC as their causal floor | `test_audit7_malformed_future_and_backwards_clocks_are_refused_at_admission`, `test_audit7_a_granted_lease_sorts_after_every_event_of_its_item` | A far-future event read from disk is held back until it is in range. That is deterministic for a given clock but means admitted state can grow later. The TTL clock-uncertainty policy is not specified |
| 16 | The chain was validated before the file lock was taken | Read, validate, deduplicate, append and fsync all happen under the same flock `EventLog` takes. A refusal onto a log the hub does not hold yet creates no file | `test_audit16_a_local_append_racing_the_hub_cannot_fork_the_chain` | Not done: the hub's append still does not update the rewind witnesses that `EventLog` writes |
| 18 | `POST /seal` let a bearer-token caller make the hub sign a seal | `Hub.seal_request` needs a signed `seal.request` (current key, window, nonce, hub and space) from the grantor or a configured `seal_operators` entry. The API route calls it, and a token-only POST gets 409 `unsigned`. `Hub.seal` is idempotent: if nothing changed, it answers `unchanged` and writes nothing | `test_audit18_sealing_over_the_network_needs_a_signed_operator_request`; app: `test_a_token_alone_cannot_make_the_hub_sign_a_seal` | No rate limit beyond idempotency. The nonce cache is still process-local (finding 10) |

**Existing tests changed, and why (no assertion was weakened):**
- `test_tampered_hub_entry_fails_the_writers_receipt_check`: the `_reseal` helper simulates a *dishonest* hub. It used to do this by running the honest `Hub.seal`, which now refuses to seal a tampered writer event. It now signs the seal directly with the hub key. Every assertion is unchanged.
- `test_second_hub_refuses_to_grant_until_explicitly_promoted`: after `promote`, the writer now copies the promoted config into its checkout, as a `git pull` would, before its lease request. Without that it correctly refuses the new grantor's answer (finding 5). Every assertion is unchanged.
- `test_append_lease_seal_and_receipt_over_http` (app): `POST /seal` now sends a signed operator request.
- One of this pass's own new tests (`test_audit1_an_edited_cutover_manifest_is_refused`) was adjusted once the manifest moved from a separate file into the config file, so that `git pull` carries it.

**Test counts:** see the final section below.

**Test counts (seen in this session):**

| Suite | Before | After |
|---|---|---|
| `test_ledger_hub.py` | 12 passed | 31 passed (19 new) |
| `python3 -m pytest .datacore/lib/tests -k ledger -q` (root worktree) | 787 passed, 6277 deselected | 806 passed, 6277 deselected |
| `daemon/tests/test_ledger_hub_api.py` with `DATACORE_ROOT=<root worktree>` | 3 passed | 4 passed (1 new) |

`test_cadence_liveness.py` (it touches `cadence_liveness.py`, which this pass changed by one line) was run after the change only: 25 passed, 1 failed. The failure is `ModuleNotFoundError: cadence_engine`, a module that does not exist in this worktree or in `~/Data`, so it is not caused by this change. The baseline for that file was **not verified**.
