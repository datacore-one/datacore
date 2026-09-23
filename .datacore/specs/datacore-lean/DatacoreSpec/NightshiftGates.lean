/-!
# Nightshift gates — small verdict functions, "pass ⇒ evidence"

The house incident (CLAUDE.md, 2026-09-08): nightshift graded its own work
through a gate that "returned passed on every path including failure". Each
section below models one verdict function branch for branch, states the
safety property "a passing verdict implies the evidence it claims", and either
proves it or refutes it with a concrete counterexample (every counterexample
was replayed against the real Python; see `findings/nightshift-gates.md`).

Sections:
* `Evaluator` — `modules/nightshift/lib/evaluate.py` consensus (FIXED; N4 threshold + reject)
* `Gstack`    — `modules/nightshift/lib/postprocess.py` review+cso gate (proved)
* `Canary`    — `lib/delegation_canary.py` `--run`/`--check` verdict (N5 applied)
* `Cadence`   — `lib/cadence_liveness.py` contract line (FIXED)
* `Recurrence`— `lib/jobs/recurrence.py` reset persistence (N7: lost reset warns)
* `AiGate`    — `lib/ai_task_gate.py` ≡ `nightshift_parser._is_executable` (FIXED; N8 ROADMAP shared)
* `Workflow`  — `lib/workflow_executor.py` LIVE overall status (FIXED)
* `Awake`     — `lib/jobs/awake.py` `0 ≤ awake_age ≤ wall_age` (proved)
* `Budget`    — `run.check_budget` vs `execute._execute_task` (NEEDS-OWNER)

Scores are modelled in thousandths (`Nat`, 1000 = 1.0). Python rounds the
mean to 3 dp and the variance to 4 dp before comparing; the model compares
exact rationals (via cross-multiplication). The two can differ only on a
half-milli boundary, which none of the properties below depend on.
-/

namespace DatacoreSpec.NightshiftGates

/-! ## Evaluator consensus (`evaluate.py`) -/
namespace Evaluator

/-- What `run_evaluator` saw for one persona. `output s r` is an exit-0 run
whose first `score:` match (unclamped, in thousandths) is `s` (`none` = no match
or not a number) and whose `recommendation:` is `reject` iff `r`. -/
inductive Outcome
  | timeout | crashed | exitNonzero | unknownPersona
  | output (score : Option Nat) (reject : Bool)

inductive Decision | approved | approvedWithNotes | needsReview
  deriving DecidableEq, Repr

def sum : List Nat → Nat
  | [] => 0
  | x :: xs => x + sum xs

def sumSq : List Nat → Nat
  | [] => 0
  | x :: xs => x * x + sumSq xs

/-- variance > 0.1 ⇔ n·Σx² − (Σx)² > 0.1·10⁶·n² (exact arithmetic). -/
def highVar (votes : List Nat) : Prop :=
  votes.length * sumSq votes > sum votes * sum votes + 100000 * (votes.length * votes.length)

instance (vs : List Nat) : Decidable (highVar vs) := by unfold highVar; infer_instance

/-- POST-N4 `evaluate_output` + `make_decision` over the votes that count.
`T` is `nightshift.quality_threshold` in thousandths (default 800); `rejected`
is "some evaluator that ran recommended reject". The disagreement band is
`T + 50` (the old 0.85 − 0.80), capped at 1000. -/
def decision (T : Nat) (rejected : Bool) (votes : List Nat) : Decision :=
  let n := votes.length
  let s := sum votes
  if n < 3 then .needsReview                               -- MIN_REAL_EVALUATORS
  else if rejected then .needsReview                       -- a reject blocks
  else if s < T * n then .needsReview                      -- mean < threshold
  else if highVar votes then
    (if s ≥ min 1000 (T + 50) * n then .approvedWithNotes else .needsReview)
  else .approved

/-- PRE-N4 decision: fixed 0.80 / 0.70 / 0.85, recommendation ignored. -/
def decisionOld (votes : List Nat) : Decision :=
  let n := votes.length
  let s := sum votes
  if n < 3 then .needsReview
  else if highVar votes then
    (if s ≥ 850 * n then .approvedWithNotes else .needsReview)
  else if s ≥ 800 * n then .approved
  else if s ≥ 700 * n then .approvedWithNotes
  else .needsReview

/-- PRE-FIX vote extraction. Non-votes are only the outcomes whose feedback
starts with "Evaluator timed out" / "Error:". An unknown persona and an
unparseable exit-0 answer both vote 0.5; any number is clamped to 1.0. -/
def voteOld : Outcome → Option Nat
  | .timeout | .crashed | .exitNonzero => none
  | .unknownPersona => some 500
  | .output none _ => some 500
  | .output (some s) _ => some (min s 1000)

/-- POST-FIX: only an in-range score the evaluator actually wrote is a vote. -/
def voteNew : Outcome → Option Nat
  | .output (some s) _ => if s ≤ 1000 then some s else none
  | _ => none

/-- Evidence: an evaluator really returned a score on the requested scale. -/
def evidence : Outcome → Bool
  | .output (some s) _ => decide (s ≤ 1000)
  | _ => false

/-- A real vote that recommends reject. -/
def rejects : Outcome → Bool
  | .output (some s) true => decide (s ≤ 1000)
  | _ => false

/-- `rejected_by` in `evaluate_output`: only an evaluator that ran can reject. -/
def rejectedNew (outs : List Outcome) : Bool := outs.any rejects

def evidenceCount (outs : List Outcome) : Nat := (outs.filter evidence).length

/-- The whole post-N4 pipeline over raw outcomes. -/
def pipeline (T : Nat) (outs : List Outcome) : Decision :=
  decision T (rejectedNew outs) (outs.filterMap voteNew)

theorem voteNew_isSome (o : Outcome) : (voteNew o).isSome = evidence o := by
  cases o with
  | output s r => cases s with
    | none => rfl
    | some s => by_cases h : s ≤ 1000 <;> simp [voteNew, evidence, h]
  | _ => rfl

theorem length_filterMap_voteNew (outs : List Outcome) :
    (outs.filterMap voteNew).length = evidenceCount outs := by
  unfold evidenceCount
  induction outs with
  | nil => rfl
  | cons o os ih =>
    have h := voteNew_isSome o
    cases hv : voteNew o with
    | none =>
      rw [hv] at h
      simp [hv, ← h, ih]
    | some v =>
      rw [hv] at h
      simp [hv, ← h, ih]

theorem decision_pass_quorum (T : Nat) (r : Bool) (vs : List Nat)
    (h : decision T r vs ≠ .needsReview) : vs.length ≥ 3 := by
  unfold decision at h
  by_cases hn : vs.length < 3
  · simp [hn] at h
  · omega

/-- **Proved (fixed code).** An approving decision rests on at least
`MIN_REAL_EVALUATORS` evaluators that actually returned an in-range score. -/
theorem pass_implies_three_real_scores (T : Nat) (outs : List Outcome)
    (h : pipeline T outs ≠ .needsReview) : evidenceCount outs ≥ 3 := by
  have := decision_pass_quorum _ _ _ h
  rwa [length_filterMap_voteNew] at this

/-- **Proved (fixed code).** Every counted vote is a score an evaluator wrote. -/
theorem vote_is_written_score (outs : List Outcome) (v : Nat)
    (h : v ∈ outs.filterMap voteNew) :
    ∃ s r, Outcome.output (some s) r ∈ outs ∧ v = s ∧ s ≤ 1000 := by
  rw [List.mem_filterMap] at h
  obtain ⟨o, ho, hv⟩ := h
  cases o with
  | output s r => cases s with
    | none => simp [voteNew] at hv
    | some s =>
      by_cases hs : s ≤ 1000
      · simp [voteNew, hs] at hv; exact ⟨s, r, ho, hv.symm, hs⟩
      · simp [voteNew, hs] at hv
  | _ => simp [voteNew] at hv

/-- **Proved (N4).** An approval means the mean of the real votes is ≥ the
configured `quality_threshold`. -/
theorem pass_implies_mean_ge_threshold (T : Nat) (r : Bool) (vs : List Nat)
    (h : decision T r vs ≠ .needsReview) : sum vs ≥ T * vs.length := by
  unfold decision at h
  by_cases hn : vs.length < 3
  · simp [hn] at h
  by_cases hr : r = true
  · simp [hn, hr] at h
  by_cases hT : sum vs < T * vs.length
  · simp [hn, hr, hT] at h
  · omega

/-- **Proved (N4).** A `reject` from any evaluator blocks approval. -/
theorem reject_blocks (T : Nat) (vs : List Nat) : decision T true vs = .needsReview := by
  unfold decision
  by_cases hn : vs.length < 3 <;> simp [hn]

/-- **Proved (N4).** An approval means no evaluator that ran recommended reject. -/
theorem pass_implies_no_reject (T : Nat) (outs : List Outcome)
    (h : pipeline T outs ≠ .needsReview) : ∀ o ∈ outs, rejects o = false := by
  intro o ho
  cases hrej : rejects o
  · rfl
  · have : rejectedNew outs = true := List.any_eq_true.mpr ⟨o, ho, hrej⟩
    unfold pipeline at h
    rw [this, reject_blocks] at h
    exact absurd rfl h

/-- **Proved (N4).** Under disagreement the bar is `min 1000 (T + 50)`. -/
theorem high_variance_needs_margin (T : Nat) (vs : List Nat) (hv : highVar vs)
    (h : decision T false vs ≠ .needsReview) : sum vs ≥ min 1000 (T + 50) * vs.length := by
  unfold decision at h
  by_cases hn : vs.length < 3
  · simp [hn] at h
  by_cases hT : sum vs < T * vs.length
  · simp [hn, hT] at h
  by_cases hm : sum vs ≥ min 1000 (T + 50) * vs.length
  · exact hm
  · simp [hn, hT, hv, hm] at h

/-- **Refuted (pre-N4).** 0.75 ×3 was approved (with notes) below the
configured 0.80 minimum. -/
theorem old_approves_below_threshold :
    decisionOld [750, 750, 750] = .approvedWithNotes ∧ decision 800 false [750, 750, 750] = .needsReview := by
  decide

/-- **Refuted (pre-N4).** A unanimous 0.95 with one `reject` was approved. -/
theorem old_ignores_reject :
    let outs := [Outcome.output (some 950) false, .output (some 950) false, .output (some 950) true]
    decisionOld (outs.filterMap voteNew) = .approved ∧ pipeline 800 outs = .needsReview := by
  decide

/-- **Refuted (pre-fix), replayed.** Three evaluators answering "score: 8"
(eight out of ten, or a 0–10 scale) are clamped to 1.0 and approve. -/
theorem old_score_8_approves :
    decisionOld ([Outcome.output (some 8000) false, .output (some 8000) false,
      .output (some 8000) false].filterMap voteOld) = .approved := by decide

/-- "score: 6" — 60 % on a 0–10 scale — is also a unanimous 1.0. -/
theorem old_score_6_approves :
    decisionOld ([Outcome.output (some 6000) false, .output (some 6000) false,
      .output (some 6000) false].filterMap voteOld) = .approved := by decide

/-- **Refuted (pre-fix), replayed.** Two real votes plus one unparseable
answer reach the quorum of three and approve. -/
theorem old_two_real_plus_garbage_approves :
    let outs := [Outcome.output (some 1000) false, .output (some 1000) false, .output none false]
    decisionOld (outs.filterMap voteOld) = .approved ∧ evidenceCount outs = 2 := by decide

/-- Same with an unknown persona as the third "vote". -/
theorem old_two_real_plus_unknown_persona_approves :
    let outs := [Outcome.output (some 1000) false, .output (some 1000) false, .unknownPersona]
    decisionOld (outs.filterMap voteOld) = .approved ∧ evidenceCount outs = 2 := by decide

/-- The pre-fix code violates the property the fixed code satisfies. -/
theorem old_violates_pass_implies_evidence :
    ¬ ∀ outs, decisionOld (outs.filterMap voteOld) ≠ .needsReview → evidenceCount outs ≥ 3 := by
  intro hall
  have := hall [.output (some 1000) false, .output (some 1000) false, .output none false] (by decide)
  exact absurd this (by decide)

/-- The fixed code sends both cases to review instead. -/
theorem new_garbage_needs_review :
    pipeline 800 [Outcome.output (some 1000) false, .output (some 1000) false, .output none false]
      = .needsReview ∧
    pipeline 800 [Outcome.output (some 8000) false, .output (some 8000) false,
      .output (some 8000) false] = .needsReview := by decide

end Evaluator

/-! ## gstack gate (`postprocess.run_postprocess`, dev route) -/
namespace Gstack

/-- `_run_gstack_gate`'s result. `_parse_gate_verdict` only returns a verdict
whose `passed` is a real bool (`type(...) is bool`), so `passed : Bool`. -/
inductive Gate
  | unavailable
  | verdict (passed : Bool)

def Gate.available : Gate → Bool
  | .unavailable => false
  | .verdict _ => true

/-- `.get("passed") is True`. -/
def Gate.passedTrue : Gate → Bool
  | .verdict true => true
  | _ => false

inductive Outcome | notGated | failed | proceeds
  deriving DecidableEq

/-- The `if not gated / elif review_passed and cso_passed / else` block. -/
def gate (review cso : Gate) : Outcome :=
  let gated := review.available && cso.available
  if !gated then .notGated
  else if (gated && review.passedTrue) && (gated && cso.passedTrue) then .proceeds
  else .failed

/-- **Proved.** Output only proceeds past the gate when both gates ran and
both returned `passed: true`. -/
theorem proceeds_iff (r c : Gate) :
    gate r c = .proceeds ↔ r = .verdict true ∧ c = .verdict true := by
  cases r with
  | unavailable => cases c <;> simp [gate, Gate.available]
  | verdict pr => cases c with
    | unavailable => simp [gate, Gate.available]
    | verdict pc => cases pr <;> cases pc <;> simp [gate, Gate.available, Gate.passedTrue]

/-- The historical defect, for the mutation check: an unavailable gate read
as `.get("passed", True)`. -/
def gateLegacy (review cso : Gate) : Outcome :=
  let p : Gate → Bool := fun g => match g with | .unavailable => true | .verdict b => b
  if p review && p cso then .proceeds else .failed

theorem legacy_passes_without_evidence : gateLegacy .unavailable .unavailable = .proceeds := rfl

end Gstack

/-! ## Delegation canary (`delegation_canary.cmd_run` / `cmd_check`) -/
namespace Canary

inductive Verdict | dispatched | completed | failed | blocked | unknown
  deriving DecidableEq, Repr

/-- The job contract (`box-delegation-canary`): the artifact's verdict must
match `dispatched|completed|blocked` (and be < 26 h old). -/
def contractOk : Verdict → Bool
  | .dispatched | .completed | .blocked => true
  | _ => false

/-- The item's folded status, as `cmd_run` reads it. -/
inductive Status | closed | open_ | absent

structure Prev where
  verdict : Option Verdict      -- none: RESULT missing or unreadable
  hasItem : Bool

/-- `commitFail` is what a failed LOCAL `git commit` of the input writes:
`blocked`/exit 0 before decision N5, `failed`/exit 1 after it. -/
def runWith (commitFail : Verdict × Nat) (p : Prev) (st : Status) (ageH budget : Nat)
    (commitOk policyOk : Bool) : Option Verdict × Nat :=
  let judgePrev : Option (Option Verdict × Nat) :=
    if p.verdict = some .dispatched ∧ p.hasItem then
      match st with
      | .closed => none                                   -- writes completed, then seeds
      | _ => if ageH ≤ budget then some (none, 0)         -- in flight: leave alone
             else some (some .failed, 1)
    else none
  match judgePrev with
  | some r => r
  | none =>
    if !commitOk then (some commitFail.1, commitFail.2)
    else if !policyOk then (some .failed, 1)
    else (some .dispatched, 0)

/-- One `--run`, post-N5. -/
def run := runWith (.failed, 1)
/-- One `--run`, pre-N5. -/
def runOld := runWith (.blocked, 0)

/-- **Proved.** An abandoned canary (still open past its budget) is recorded as
failed, which the contract rejects. -/
theorem abandoned_canary_fails (st : Status) (a b : Nat) (c q : Bool)
    (hst : st ≠ .closed) (hage : a > b) :
    run ⟨some .dispatched, true⟩ st a b c q = (some .failed, 1) := by
  cases st with
  | closed => exact absurd rfl hst
  | open_ => simp [run, runWith, Nat.not_le.mpr hage]
  | absent => simp [run, runWith, Nat.not_le.mpr hage]

/-- **Proved (N5).** A failed local commit is `failed`, exit 1, which the
contract rejects. -/
theorem commit_failure_fails (v : Option Verdict) (st : Status) (a b : Nat) (q : Bool)
    (hv : v ≠ some .dispatched) :
    run ⟨v, true⟩ st a b false q = (some .failed, 1) ∧ contractOk .failed = false := by
  simp [run, runWith, hv, contractOk]

/-- **Proved (N5).** `--run` never writes `blocked`. -/
theorem run_never_blocked (p : Prev) (st : Status) (a b : Nat) (c q : Bool) :
    (run p st a b c q).1 ≠ some .blocked := by
  obtain ⟨v, hi⟩ := p
  by_cases hv : v = some .dispatched ∧ hi = true
  · obtain ⟨rfl, rfl⟩ := hv
    cases st <;> cases c <;> cases q <;> by_cases hab : a ≤ b <;> simp [run, runWith, hab]
  · cases c <;> cases q <;> simp [run, runWith, hv]

/-- `cmd_check` on a `blocked` verdict of age `ageH` hours (post-N5):
(what it writes, exit). -/
def checkBlocked (ageH : Nat) : Option Verdict × Nat :=
  if ageH > 48 then (some .failed, 1) else (none, 0)

/-- **Proved (N5).** A `blocked` verdict older than 48 h fails the check. -/
theorem blocked_ages_out (a : Nat) (h : a > 48) : checkBlocked a = (some .failed, 1) := by
  simp [checkBlocked, h]

/-- **Refuted (pre-N5), replayed.** When the local `git commit` of the input
failed, every run wrote `blocked`, exited 0, and the contract passed — for
ever, whatever the previous verdict (other than a judgeable dispatched one). -/
theorem old_blocked_forever (v : Option Verdict) (st : Status) (a b : Nat) (q : Bool)
    (hv : v ≠ some .dispatched) :
    runOld ⟨v, true⟩ st a b false q = (some .blocked, 0) ∧ contractOk .blocked = true := by
  simp [runOld, runWith, hv, contractOk]

end Canary

/-! ## Cadence liveness contract line (`cadence_liveness.collect`) -/
namespace Cadence

/-- One space as `collect` sees it. -/
inductive Space
  | notVenture                       -- no venture.yaml, archived, or no roles
  | unreadable                       -- venture.yaml or cadence log raised
  | engineError
  | readable (overdue : Nat)         -- rows past grace

def rowsOld : Space → Nat
  | .notVenture | .unreadable => 0
  | .engineError => 1
  | .readable k => k

def rowsNew : Space → Nat
  | .notVenture => 0
  | .unreadable | .engineError => 1
  | .readable k => k

def total (f : Space → Nat) : List Space → Nat
  | [] => 0
  | s :: ss => f s + total f ss

/-- The contract: last line `^0 cadence(s) overdue; …`. -/
def contractOk (f : Space → Nat) (spaces : List Space) : Bool := total f spaces == 0

/-- A space whose cadences were actually checked and found on time. -/
def checkedClean : Space → Prop
  | .notVenture => True
  | .readable k => k = 0
  | _ => False

/-- **Proved (fixed code).** "0 cadence(s) overdue" means every venture space
was read and checked, and none is overdue. -/
theorem pass_implies_all_checked (spaces : List Space)
    (h : contractOk rowsNew spaces = true) : ∀ s ∈ spaces, checkedClean s := by
  induction spaces with
  | nil => intro s hs; cases hs
  | cons x xs ih =>
    simp [contractOk, total] at h ih
    intro s hs
    cases hs with
    | head => cases x <;> simp_all [rowsNew, checkedClean]
    | tail _ hs => exact ih h.2 s hs

/-- **Refuted (pre-fix), replayed.** An unreadable venture passes. -/
theorem old_unreadable_passes : contractOk rowsOld [.unreadable] = true ∧ ¬ checkedClean .unreadable := by
  simp [contractOk, total, rowsOld, checkedClean]

end Cadence

/-! ## Recurrence counter (`jobs/recurrence.record`) -/
namespace Recurrence

/-- One job's `consecutive` on disk. A `record` is load → compute → save; a
save may fail (`_save` returns False). -/
def recordFail (disk : Nat) (saved : Bool) : Nat × Nat :=     -- (returned, new disk)
  (disk + 1, if saved then disk + 1 else disk)

def recordPass (disk : Nat) (saved : Bool) : Nat × Nat :=
  (0, if saved then 0 else disk)

/-- **Proved.** With the lock held and saves succeeding, a pass followed by a
failure counts one — the reset is never lost. -/
theorem locked_reset_holds (n : Nat) :
    (recordFail (recordPass n true).2 true).1 = 1 := rfl

/-- **Still true (the trade-off N7 keeps).** A pass that REPORTS a reset whose
save failed leaves the old streak; the next single failure reads 4. -/
theorem swallowed_save_loses_reset :
    (recordPass 3 false).1 = 0 ∧ (recordFail (recordPass 3 false).2 true).1 = 4 := ⟨rfl, rfl⟩

/-- The unlocked fallback (`lock unavailable`) as a trace: the failing
verifier loads, the passing verifier loads and saves, the failing verifier
saves what it computed from its stale load. -/
def interleaved (n : Nat) : Nat :=
  let failLoaded := n                          -- fail: _load()
  let _afterPass := (recordPass n true).2      -- pass: _load(), _save(0)
  failLoaded + 1                               -- fail: _save(n + 1)

/-- **Still true.** The unlocked interleaving ends at `n + 1`; the two serial
orders end at 1 (pass, fail) and 0 (fail, pass). The reset can be lost. -/
theorem unlocked_interleaving_loses_reset (n : Nat) :
    interleaved n = n + 1 ∧ (recordFail (recordPass n true).2 true).2 = 1 ∧
      (recordPass (recordFail n true).2 true).2 = 0 := ⟨rfl, rfl, rfl⟩

/-- What one `record(job, failed=False)` leaves behind (decision N7): the disk
value and whether a WARNING went to stderr and into the returned record.
`lockOk` is `_locked()` entering; `saved` is `_save` returning True. -/
structure PassOut where
  disk   : Nat
  warned : Bool

def passN7 (disk : Nat) (lockOk saved : Bool) : PassOut :=
  ⟨(recordPass disk saved).2, !lockOk || !saved⟩

/-- Pre-N7: the lock fallback printed a note that was not a warning and did
not reach the record, and a failed save was swallowed. -/
def passOld (disk : Nat) (_lockOk saved : Bool) : PassOut :=
  ⟨(recordPass disk saved).2, false⟩

/-- **Proved (N7).** Never silent: if the reset did not reach disk, the pass
warned. -/
theorem lost_reset_is_warned (n : Nat) (l s : Bool) (h : (passN7 n l s).disk ≠ 0) :
    (passN7 n l s).warned = true := by
  cases s <;> cases l <;> simp_all [passN7, recordPass]

/-- **Proved (N7).** Recording without the lock — the path on which a
concurrent verifier can overwrite the reset — always warns. -/
theorem unlocked_always_warns (n : Nat) (s : Bool) : (passN7 n false s).warned = true := by
  simp [passN7]

/-- **Refuted (pre-N7).** A lost reset was silent. -/
theorem old_lost_reset_silent : (passOld 3 true false).disk = 3 ∧ (passOld 3 true false).warned = false :=
  ⟨rfl, rfl⟩

end Recurrence

/-! ## `ai_task_gate` vs the executor (`find_ai_tasks(require_executable=True)`) -/
namespace AiGate

/-- A task's properties as both predicates see them. `surfaceBlank`: SURFACE
strips to ''. `exactU`: strips to exactly "unassigned". `ciU`: strips and
lower-cases to "unassigned" (`exactU → ciU`). -/
structure Props where
  sourceId     : Bool
  surfaceBlank : Bool
  exactU       : Bool
  ciU          : Bool
  doneStated   : Bool
  roadmap      : Bool

inductive Gap | surface | doneWhen | roadmap
  deriving DecidableEq

/-- `delegation_requirements.execution_gaps(props, roadmap_required=rr)`,
the ONE shared function (post-N8). `rr` is "the file's space (`space_of`) is in
`roadmap_spaces(root)`": both callers compute it with the same two functions. -/
def executionGaps (rr : Bool) (p : Props) : List Gap :=
  (if p.surfaceBlank || p.ciU then [.surface] else []) ++
  (if !p.doneStated then [.doneWhen] else []) ++
  (if rr && !p.roadmap then [.roadmap] else [])

/-- `ai_task_gate._missing`: one message per gap kind it knows. -/
def gateMissing (rr : Bool) (p : Props) : List String :=
  let g := executionGaps rr p
  (if g.contains .surface then ["no SURFACE"] else []) ++
  (if g.contains .doneWhen then ["no DONE_WHEN"] else []) ++
  (if g.contains .roadmap then ["no ROADMAP"] else [])

/-- The gate's verdict: a SOURCE_ID reference is skipped (passes). -/
def gateOk (rr : Bool) (p : Props) : Bool := p.sourceId || (gateMissing rr p).isEmpty

/-- `nightshift_parser._is_executable(task, roadmap_spaces, root)`, post-N8. -/
def executorOk (rr : Bool) (p : Props) : Bool := p.sourceId || (executionGaps rr p).isEmpty

/-- Pre-N8 executor: `execution_gaps(props)`, no ROADMAP clause. -/
def executorOkOld (p : Props) : Bool := executorOk false p

/-- Pre-fix (2026-09-23 morning) gate: its own case-sensitive copy. -/
def gateOkOld (hasRoadmap : Bool) (p : Props) : Bool :=
  p.sourceId || (!p.surfaceBlank && !p.exactU && p.doneStated && (!hasRoadmap || p.roadmap))

/-- Selection: the executor queues a heading when ANY tag starts with "AI";
the gate checked only for the exact tag "AI". -/
def execSelects (tags : List String) : Bool := tags.any (·.startsWith "AI")
def gateSelectsOld (tags : List String) : Bool := tags.contains "AI"
def gateSelectsNew := execSelects

/-- **Proved (N8).** Gate ≡ executor, for every property combination and
both roadmap cases. -/
theorem gate_eq_executor (rr : Bool) (p : Props) : gateOk rr p = executorOk rr p := by
  cases p with
  | mk a b c d e f =>
    cases a <;> cases b <;> cases c <;> cases d <;> cases e <;> cases f <;> cases rr <;> rfl

/-- **Proved.** Anything the gate passes, the executor runs, and vice versa. -/
theorem gate_never_weaker (rr : Bool) (p : Props) (h : gateOk rr p = true) :
    executorOk rr p = true := by rw [← gate_eq_executor]; exact h

theorem gate_never_stricter (rr : Bool) (p : Props) (h : executorOk rr p = true) :
    gateOk rr p = true := by rw [gate_eq_executor]; exact h

/-- **Refuted (pre-N8).** In a roadmap space the old executor ran a task with
no ROADMAP that the gate refuses. -/
theorem old_executor_weaker_than_gate :
    let p : Props := ⟨false, false, false, false, true, false⟩
    executorOkOld p = true ∧ gateOk true p = false ∧ executorOk true p = false := by decide

/-- **Refuted (pre-fix), replayed.** SURFACE "Unassigned": the old gate passed
it, the executor never runs it. -/
theorem old_gate_passes_unrunnable :
    let p : Props := ⟨false, false, false, true, true, true⟩
    gateOkOld false p = true ∧ executorOk false p = false := by decide

/-- **Refuted (pre-fix), replayed.** Tag `AIresearch`: queued, never gated. -/
theorem old_gate_misses_queued_tag :
    execSelects ["AIresearch"] = true ∧ gateSelectsOld ["AIresearch"] = false := by decide +kernel

end AiGate

/-! ## Workflow executor overall status (`workflow_executor.execute_workflow`) -/
namespace Workflow

/-- A phase's return status. `notExecuted` is the post-fix status of a LIVE
tool/agent/output phase, whose handler only logs. -/
inductive St | completed | skipped | notExecuted | paused | stopped | error
  deriving DecidableEq

structure Phase where
  st       : St
  optional : Bool
  /-- ghost: the phase was due to run (no condition skipped it) but nothing ran. -/
  owed     : Bool

/-- The loop: break on stopped/paused/non-optional error; post-fix, a
`notExecuted` phase downgrades an otherwise-completed run. -/
def overall : List Phase → St
  | [] => .completed
  | p :: ps =>
    match p.st with
    | .stopped => .stopped
    | .paused => .paused
    | .error => if p.optional then overall ps else .error
    | .notExecuted =>
      match overall ps with
      | .completed => .notExecuted
      | o => o
    | _ => overall ps

/-- LIVE handlers: post-fix a handler that did not run returns `notExecuted`. -/
def wellFormedNew (p : Phase) : Prop := p.owed = true → p.st = .notExecuted

theorem overall_cons {x : Phase} {xs : List Phase} (h : overall (x :: xs) = .completed) :
    overall xs = .completed ∧ x.st ≠ .notExecuted := by
  revert h
  cases hst : x.st <;> simp [overall, hst] <;> split <;> simp_all

/-- **Proved (fixed code).** `completed` never covers a phase that was owed
and did not run. -/
theorem completed_means_nothing_owed (ps : List Phase) (hw : ∀ p ∈ ps, wellFormedNew p)
    (h : overall ps = .completed) : ∀ p ∈ ps, p.owed = false := by
  induction ps with
  | nil => intro p hp; cases hp
  | cons x xs ih =>
    obtain ⟨hrest, hne⟩ := overall_cons h
    intro p hp
    cases hp with
    | head =>
      cases ho : x.owed
      · rfl
      · exact absurd (hw x (List.mem_cons_self ..) ho) hne
    | tail _ hp => exact ih (fun q hq => hw q (List.mem_cons_of_mem _ hq)) hrest p hp

/-- **Refuted (pre-fix), replayed.** Pre-fix a LIVE tool phase that ran
nothing returns `skipped`, and the run reports `completed`. -/
theorem old_live_reports_completed :
    overall [⟨.skipped, false, true⟩, ⟨.skipped, false, true⟩] = .completed := rfl

end Workflow

/-! ## Awake age (`jobs/awake.awake_age`) -/
namespace Awake

/-- `asleep_seconds_since`'s pairing loop over sorted (stamp, isSleep) events,
clipping each interval to `[since, now]`. -/
def asleepLoop (since now : Int) : Option Int → List (Int × Bool) → Int
  | _, [] => 0
  | none, (t, true) :: es => asleepLoop since now (some t) es
  | none, (_, false) :: es => asleepLoop since now none es
  | some a, (_, true) :: es => asleepLoop since now (some a) es
  | some a, (t, false) :: es =>
    let lo := max a since
    let hi := min t now
    (if hi > lo then hi - lo else 0) + asleepLoop since now none es

theorem asleepLoop_nonneg (since now : Int) (st : Option Int) (es : List (Int × Bool)) :
    0 ≤ asleepLoop since now st es := by
  induction es generalizing st with
  | nil => simp [asleepLoop]
  | cons e es ih =>
    obtain ⟨t, k⟩ := e
    cases st <;> cases k <;> simp only [asleepLoop]
    all_goals first | exact ih _ | (have := ih none; split <;> omega)

def asleep (since now : Int) (es : List (Int × Bool)) : Int :=
  if now ≤ since then 0 else min (asleepLoop since now none es) (now - since)

/-- `awake_age`. `failed` models the `except Exception` branch (wall age). -/
def awakeAge (mtime now : Int) (alwaysOn failed : Bool) (es : List (Int × Bool)) : Int :=
  if now - mtime ≤ 0 ∨ alwaysOn = true then now - mtime
  else if failed then now - mtime
  else max 0 ((now - mtime) - asleep mtime now es)

/-- **Proved.** `awake_age` never exceeds the wall age. -/
theorem asleep_bounds (m n : Int) (es : List (Int × Bool)) :
    0 ≤ asleep m n es ∧ (m < n → asleep m n es ≤ n - m) := by
  have h0 := asleepLoop_nonneg m n none es
  unfold asleep
  split <;> constructor <;> (try intro) <;> omega

theorem awake_le_wall (m n : Int) (ao f : Bool) (es) : awakeAge m n ao f es ≤ n - m := by
  have := asleep_bounds m n es
  unfold awakeAge
  split
  · omega
  · split
    · omega
    · omega

/-- **Proved.** For a non-negative wall age, `0 ≤ awake_age ≤ wall_age`. -/
theorem awake_between (m n : Int) (ao f : Bool) (es) (h : 0 ≤ n - m) :
    0 ≤ awakeAge m n ao f es ∧ awakeAge m n ao f es ≤ n - m := by
  refine ⟨?_, awake_le_wall m n ao f es⟩
  unfold awakeAge
  split
  · omega
  · split
    · omega
    · omega

end Awake

/-! ## Daily budget (item 7): reporting only -/
namespace Budget

/-- `check_budget`: limit 0 = unlimited. -/
def allowed (limit spent : Nat) : Bool := limit == 0 || spent < limit

/-- `_execute_task`: the batch API (auth='api') is used when the env flag is
set, whatever `api_fallback_allowed` says. Returns metered spend added. -/
def spend (batchEnabled : Bool) (_apiFallbackAllowed : Bool) (cost : Nat) : Nat :=
  if batchEnabled then cost else 0

/-- **As found, before decision N6 (2026-09-23).** With a positive limit
already exhausted, metered spend still grew. N6 fixed it; the fixed budget
gate is modelled and proved in `NightshiftLifecycle.Budget`
(`no_paid_call_past_limit`, `unlimited_unchanged`). -/
theorem spend_past_limit_as_found :
    allowed 10 12 = false ∧ spend true (allowed 10 12) 5 = 5 := by decide

end Budget

end DatacoreSpec.NightshiftGates
