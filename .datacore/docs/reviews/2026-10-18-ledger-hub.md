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
