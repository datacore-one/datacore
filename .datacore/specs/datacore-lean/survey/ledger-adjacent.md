# Survey: ledger-adjacent code (2026-09-23)

Read-only subagent survey; suspicions cite lines and are UNVERIFIED until modelled or replayed.

1. **claim_gate.check_create delegation limits** (lib/claim_gate.py:108-166) — `hops = payload.get("hops", 0)` (133) is caller-supplied and never incremented: `chain_follow_up` (ledger_claim.py:437-438) omits hops; `run_task(hops=0)` (110) never passed; org_workspace_adapter.py:380 reads unset `DATACORE_HOPS`. A→B→A loops always carry hops 0. Daily cap counted per space, not per principal. Model S.
2. **policy.guarded_append approval gate** (lib/ledger/policy.py:282-517) — `previously_approved` (340-341) only inspects `item.create` events with `approval_ref`; create(no effects) → guarded update adds `payment` with grant → unguarded update drops `effects` → claim succeeds with no grant, contradicting 316-317. Model M.
3. **seal finality** (lib/ledger/seal.py:68-235, ledger_seal.py:52-78) — `latest_seal` takes max HLC from any actor, `verify_seal` never checks sequencer; no watermark monotonicity between seals (settled set can shrink); far-future HLC seal stays latest. Model M.
4. **edits.merge_values / apply_condition / guard_projection** (lib/ledger/edits.py:28-98, projection_state.py:119-140) — pure; prove identity laws, symmetry, guard soundness. Minor: Python `==` makes True == 1 == 1.0, so 1→True reads as no change. Model S.
5. **ledger_claim dispatcher loop** (lib/ledger_claim.py:446-717) — `addressed_to` accepts any writer of a principal (actor_identity.py:289-294); per-host policy lock (policy.py:291-293) ⇒ "race impossible" holds per principal-host only. Dead-letter dismiss and releases bypass guarded_append. Model M.
6. **ledger_ingest_org archived dismissal** (lib/ledger_ingest_org.py:341-367) — "Unreadable file: treat its ids as LIVE" (358-359) but line 360 `continue`s, so ids go in neither set → an id also in an archive is dismissed (terminal). Only `org/*.org` scanned, not nested. Model S.
7. **ledger_dismiss_orphans two-sweep** (lib/ledger_dismiss_orphans.py:155-220) — no bug found; `prev` may be arbitrarily old. Model S.
8. **ledger_restore_prefix** (lib/ledger_restore_prefix.py:76-126) — no log lock between read (81) and `write_text` (109): concurrent append lost, post-check still passes. No tests. Model S.
9. **ledger_checkpoint monotonicity / exceptions** (ledger_checkpoint.py:208-300, ledger/exceptions.py:57-89) — no bug; `is_recorded` keyed on local directory name (288) while hosts clone under different names. Model S–M.
10. **ledger_transport converge/publish** (lib/ledger_transport.py:377-633) — "cannot conflict" (23-24) contradicted by `_merge` (106-113); append retry → `_converge_locked` → `add -A` (422) can publish unrelated WIP; `foreign_writer_logs` stands down when principal unknown (327-329). Model L.
11. **projector overwrite guards / phase1** — no bug; reduces to #4.
12. **ledger_invariants.py (not Lean)** — "never as sound" (31) but unknown findings skipped (184-185) and verdict SOUND exit 0 (198-200); empty `detail_startswith` baseline mutes everything (169-172).

Skipped: ledger/index.py, keys.py, genesis.py, shadow.py, attestation_coverage.py, ledger_attest.py, ledger_publish_safe.py (M, low suspicion).
