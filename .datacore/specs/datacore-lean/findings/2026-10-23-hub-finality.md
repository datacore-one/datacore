# Lean model of the hub (round 8, part C) -- notes

Model: r8/lean/.datacore/specs/datacore-lean/LedgerSpec/HubFinality.lean (at 54e3bdf9),
registered in LedgerSpec.lean and verify.yaml (covers hub.py, hub_admission.py,
hub_policy.py, finality.py, fold.py).

## Finding F-L1 (CONFIRMED, replayed): policy frontier below sealed coverage

LED10_confirmed_never_changes needs `FrontierCovers`: every policy version
admitted after a seal carries a frontier past every sealed event. The code
(`Hub._judge_policies`) takes the frontier from `_current_heads` -- what the hub
holds NOW. A hub that holds less than an earlier seal covered (a restore that
lost a writer's log; a promoted grantor started on a partial copy, the R2
setting) records frontier[log] below the sealed event; when the log returns,
the sealed event is judged by the NEW policy.

Lean counterexample: `policy_frontier_counterexample` (frontier 0, the sealed
event is judged by the new version).
Replay: r8/lean-findings/test_lean_finding_policy_frontier.py, from
r8/hub/.datacore/lib with PYTHONPATH=.:tests:.. -> 1 passed (= defect present):
a sealed, confirmed creation turns "failed" ("did not take effect") and the
task disappears after the log is restored and the hub reseals.

Suggested fix (parent, test first): frontier[log] = max(current held count,
highest watermark of any grantor seal held + its covered count, the cutover
manifest's legacy seq + 1), or refuse to admit a policy change while the hub
holds less than any seal covers (`behind_predecessor`-style), so FrontierCovers
holds. Re-prove: LED10 is stated with FrontierCovers as the hypothesis the fix
discharges.

## Mutation checks (each in r8/lean-findings/mutants/, `lake env lean`)

| Mutant | Breaks |
|---|---|
| M1 late guard `<` instead of `<=` (equal stamp not late, R1) | LED10 (unsolved goal at the tie) |
| M2 drop FrontierCovers | LED10 (and policy_frontier_counterexample shows it false) |
| M3 window ignores the seal | LED15 |
| M4 key.rotate added to the allow-list | LED13_allow_list_is_item_events |
| M5 fold skips the reversible-type check | LED13_authority_records_never_reversed, LED15 |
| M6 reader takes the local policy file (pre-8a, R10) | LED14 |
| M7 reader folds raw events (no admission) | LED11 |
| M8 drop the epoch floor W1 <= W2 (R2) | LED10 |

M2 and M8 break by the removed hypothesis being needed in the proof; M2 is
also refuted outright by the counterexample theorem.

## Left out of the model (stated, not proved)
- Global lease planning and approval lookups in the fold (`seen`): modelled
  as per-item history; both read only earlier events.
- Admission itself (signatures, domains, chains): an oracle `adm`.
- The hub's choice of admission time (`_admitted_ms`, rebaselining): an input
  `admittedAt`; LED-15 is the iff given the hub's recorded time.
- Frozen legacy reversals: a flag; their list is fixed by the activation record.

## After the model (round 8, Claude)

- The policy-frontier counterexample was replayed against the code and fixed test-first (60b531a1); the independent audit then found three more frontier/recovery variants (F8-1..F8-3), fixed in 10207233, and the re-check's attested-promotion variants, fixed in e0ab7c0e and a55d316a.
- `FrontierCovers` is now PROVED from the code's rule as of 10207233 (`policy_frontier_covers_sealed`, `policy_version_forward_exact`, `deferred_heads_cover_sealed`, `LED10_confirmed_never_changes_frontier_rule`); the replaced rules are refuted (`raw_heads_frontier_not_forward`, `held_heads_frontier_misses_sealed`). Mutants F1-F4 (raw heads, no deferral, shrinking seal, held-heads-only) each break a proof. Not modelled: the attested-promotion guard (e0ab7c0e, a55d316a) and the successor-frontier receipt fix.
- The model uses exclusive watermarks/frontiers; the code uses inclusive highest seqs (the round-8 audit's note). The mapping (add one) is not itself proved.
