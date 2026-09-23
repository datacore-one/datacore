# datacore-lean — machine-checked models of Datacore core

`LedgerSpec/` models the event ledger; `DatacoreSpec/` models the rest of core
(`.datacore/lib` plus the DIP-0022 core modules gtd, nightshift, research).

Lean 4 (v4.34.0, core only, no Mathlib). Build: `lake build`. Axiom audit:
`lake env lean Audit.lean`. Everything checks with no `sorry`, `admit`,
custom axiom or `native_decide`; the proofs use only `propext`,
`Quot.sound` and `Classical.choice`.

The model mirrors the Python in `.datacore/lib/ledger/` handler by handler.
Where it abstracts (payload contents, how conditional edits are judged), the
theorems hold **for every oracle**, so the result does not depend on what
payloads contain.

| File | Python it models | Proved |
|---|---|---|
| `Hlc.lean` | `hlc.py`, stamp floor in `log.py` | `tick` is strictly monotone and never behind the clock; it fails only on counter overflow; an append sorts after every sibling tail it saw; **the zero-padded stamp string sorts exactly as `(pt, counter, actor)`**, as long as `pt < 10^13` and `counter ≤ 9999` |
| `Item.lean` | `fold.py` handlers, `edits.py` control flow | status moves only along created→claimed⇄created, claimed→completed→verified, any→dismissed; closed work never reopens; dismissal freezes status/owner/grant/closedAt/payload; only the owner completes; the edit-conflict gate blocks all forward progress; spend only accumulates |
| `Converge.lean` | `read_events` + `fold` | the whole-state fold restricted to one id **is** the per-item machine (frame property); hosts holding the same events in any order fold to the same state **iff** no two distinct events share a sort key (a counterexample shows ties are order-dependent) |
| `Chain.lean` | `verify.py`, `seal._chain_issue`, `fork.py`, `resolve_ledger_conflicts.py` | a valid chain commits to its whole history (under collision-freedom), so `fork.detect`'s per-seq hash comparison catches any divergence; `ours.startswith(theirs)` on newline-terminated lines is exactly event-prefix; the prefix resolver never drops an event |

## Findings, and fixes (2026-09-23)

All three were replayed against the real Python fold, fixed in
`lib/ledger/fold.py`, and pinned by `lib/tests/test_ledger_fold_formal_findings.py`.
The theorems below prove the FIXED behaviour for every event sequence.
Mutation-tested: putting each bug back into the model breaks the named theorem.

1. **History claimed "no-op" while state changed.** `fold.py` promised that
   nothing changes after dismissal. A conditional edit that races a dismissal
   records an edit conflict **by design**: the projector refuses the space
   until the conflict is reconciled, and `ledger_resolve_conflict.py` is the
   route out. So the behaviour is right and the promise was wrong. The real
   defect was that the reconciling dismiss, and a field-less conditional
   update, deleted conflicts and logged "no-op". This happened once in
   production, in 0-personal on 2026-09-16.
   *Fix:* those paths now log `applied (reconciled N conflict(s) …)`, and the
   docstrings state the exception exactly.
   *Proved:* `noop_means_unchanged`, `dismissed_frozen`, `dismissed_conflict_rule`.
2. **The completer could verify their own work.**
   *Fix:* `item.verify` by the item's owner is a no-op.
   *Proved:* `verify_needs_second_actor`, `completer_is_not_verifier`. The one
   residue is an `owner.set` override between complete and verify, which is a
   recorded admin act.
3. **A grant outlived its claim.**
   *Fix:* `item.release`, and an `owner.set` that changes the owner, clear
   `granted_by`/`granted_at`.
   *Proved:* `grant_belongs_to_claim` (an invariant preserved by every event)
   and `regrant_after_release`.

Real-data impact, checked by folding every space with the old and new code:
nine spaces are byte-identical. In 0-personal one history line changes (the
2026-09-16 reconcile above), which moves that space's state root. Its seal
already reported `n-a` (legacy frontier) under the old code, and its
checkpoint still restores.

Not a defect but a stated precondition: when HLC keys tie (possible only
for one actor writing two log files, e.g. `<actor>.jsonl` plus a run-branch
file), the result is still the same on every host, because every host reads
files in `sorted(glob)` order. The file whose name sorts first wins the tie,
not the event that happened first.

## The rest of core (DatacoreSpec/)

Scope: `.datacore/lib` plus the DIP-0022 core modules (gtd, nightshift and
research; `learning` is not installed). The process:

- A read-only survey found 71 candidate claims; see `survey/`.
- Sixteen agents modelled them following `AGENT_BRIEF.md`. They replayed every
  counterexample against the real Python, fixed what was clearly a bug, and
  mutation-checked each proof.
- Per-cluster evidence is in `findings/`. Policy calls that were left
  unchanged are in `DECISIONS.md`.

| Model | Covers |
|---|---|
| `GtdState` | DIP-0009 transition relation vs org_workspace; adapter org→ledger close; dedup/cleanup ids |
| `OrgTransaction` | journaled multi-file transaction and crash recovery |
| `OrgTools` | union merge, inbox cleanup, in-file dedup, id-conflict resolution, triage append, dedup |
| `Dates` | weekday calendar, the day-name fix hooks, relative dates, month windows |
| `NightshiftLifecycle` | claim / attempt fence / GC / queue admission, bounded executions |
| `NightshiftGates` | evaluator consensus, gstack gate, canary, cadence, recurrence, ai_task_gate, workflow executor |
| `LedgerPolicy` | delegation hops, approval retention, `merge_values` laws, dead-letter |
| `LedgerSeal` | seal finality monotonicity, ingest dismissal, restore-prefix lock, orphans, checkpoints |
| `Credentials` | broker serve verdicts, env precedence, config redaction, model-routing privacy floor |
| `Guards` | restricted hosts, tool policy, injection gate, log ownership, egress, pre-push, commit gate |
| `Publication` | workspace GC, repo lock, prepared write-back, retire CAS |
| `GitFleet` | "never push a fork" across all pushers, sweep guards, cron reconcile, visitor join |
| `Reconcile` | gh_reconcile "never a false DONE", three-way sync spec |
| `Detectors` | actor presence, seq gap, id churn, R5 SLO, today stage plan |
| `Knowledge` | DIP-0002 private-layer leak, learning prune, briefing dedupe, stream store, backup rotation |
| `Research` | research queue drain, item addressing, router bounds |

Whole-project checks:
- `lake build` builds both libraries.
- `lake env lean AuditAll.lean` checks mechanically that every theorem (1,979
  at the time of writing) depends only on `propext`, `Classical.choice` and
  `Quot.sound`.

## Not covered

The seq high-water-mark guard, signatures, `ledger_transport` converge/publish
as a whole (only its lock is modelled), and every non-core module. A model is
only as faithful as its reading of the Python. The replays and the
mutation checks guard against a vacuous proof, but not against modelling the
wrong code path.
