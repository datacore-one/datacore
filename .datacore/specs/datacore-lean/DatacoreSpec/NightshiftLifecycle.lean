/-!
# Nightshift task lifecycle — claim, attempt fence, escalation, stalled-GC, admission

Models, branch for branch, the per-task lifecycle spread across
`modules/nightshift/lib/`:

* `claim.claim_task` / `claim.release_claim` / `claim.complete_task`
* `task_attempt.blocked` / `begin` / `finish`
* `requeue_stalled._is_stalled` / `gc_stalled`
* `task_queue.build_queue` admission, including `resolve_queued_task`
  (a `nightshift.org` NEXT entry with `SOURCE_ID` hands back the SOURCE task
  under the ENTRY's state)
* `run.run_task_mode`'s per-task exit paths and in-run retry

A task is `(org state, NIGHTSHIFT_ATTEMPTS, fence, status, output?, live claim?,
NIGHTSHIFT_REQUEUES, has a queue entry?, generated space?)` plus a ghost
counter `execs` of executions that did work (quota refusals excluded: they are
deferred by design and do no work). The claim stamps are abstracted to one
bit `live` = `NIGHTSHIFT_STARTED > NIGHTSHIFT_COMPLETED`, which is the only
way either the queue or the GC reads them. How long a live claim has been
held is an oracle `stale` (the GC's 6-hour rule).

Every function takes `fixed : Bool`: `false` is the code as found on
2026-09-23, `true` the code after the fixes in `findings/nightshift-lifecycle.md`.
Theorems about `fixed := true` hold for EVERY sequence of nights, every
execution outcome, every evaluator decision and every staleness oracle.
Counterexamples about `fixed := false` are concrete traces, each replayed
against the real Python (`tests/test_lifecycle_formal.py`).

Owner decisions applied 2026-09-23 (`fixed := true` includes them):

* N1 — `complete_task` escalates a failure whose attempt fence is closed to
  REVIEW at once (`no_fenced_zombie`, `dead_is_visible`), and
  `reconcile_attempt --reopen` is the way back (`reopenTool`,
  `reopen_restores_inv`, `reopened_is_admitted`).
* N2 — a successful run and `--reopen` reset NIGHTSHIFT_REQUEUES
  (`requeues_reset_on_success`, `reopen_resets_counters`).
* N3 — the escalation threshold is `max_retries` (`m`), read as
  `thr m = max 1 m`; the bound is `executions_bounded : execs ≤ thr m`.
* N6 — the paid-API budget (`Budget` namespace at the end).

Owner follow-ups applied 2026-09-23 (second board):

* Q10 — `--reopen` in a ledger-phase-1 space reopens the item IN the ledger
  (`reopenLedger`, `reopen_phase1_renders_next`); only statuses no event can
  move back are refused (`completed_update_does_not_reopen`).
* Q11 — `budget_exhausted` from execute is a deferral, not an attempt
  (`Outcome.budget`, `Budget.budget_defer_not_attempt`); every lifecycle
  theorem above now also covers that outcome.

Not modelled: git publication itself (a claim is ok/refused), plan-only
mode's proposal phase (fence `proposal-completed` is treated as the full-mode
gate reads it; the Python passes `plan_only` so a plan-only failure behind that
fence escalates too), legacy QUEUED/WORKING/FAILED states, human edits other
than the `reopen` step used to state "consecutive" and the `--reopen` tool.
-/

namespace DatacoreSpec.NightshiftLifecycle

inductive OState | todo | next | waiting | review | done | cancelled
  deriving DecidableEq, Repr

/-- `NIGHTSHIFT_ATTEMPT`. `unknown` stands for every `unknown:<uuid>`. -/
inductive Fence | none | pending | completed | proposalCompleted | notStarted | unknown
  deriving DecidableEq, Repr

/-- `NIGHTSHIFT_STATUS` values the lifecycle writes. -/
inductive Status | none | failed | done | review | requeued | deferred
  deriving DecidableEq, Repr

structure Task where
  st        : OState
  attempts  : Nat
  fence     : Fence
  status    : Status
  hasOutput : Bool
  live      : Bool
  requeues  : Nat
  queueEntry : Bool
  generated : Bool
  execs     : Nat
  deriving DecidableEq, Repr

/-- `task_attempt.blocked(task)` in full mode. -/
def blocked : Fence → Bool
  | .none | .notStarted | .proposalCompleted => false
  | _ => true

/-! ## Stalled-task GC (`requeue_stalled.py`) -/

def MAX_REQUEUES : Nat := 2

/-- `_is_stalled`. `stale` is the oracle "STARTED is more than 6h old". -/
def isStalled (fixed : Bool) (stale : Bool) (t : Task) : Bool :=
  if blocked t.fence then false
  else if fixed && t.st == .review && t.status == .failed then false
  else if t.st == .review then !t.hasOutput
  else if t.st == .next then t.live && stale
  else false

/-- One task's pass through `gc_stalled`. -/
def gc (fixed stale : Bool) (t : Task) : Task :=
  if blocked t.fence then t
  else if !isStalled fixed stale t then t
  else if t.st == .review && t.generated then t
  else if t.requeues ≥ MAX_REQUEUES then t
  else { t with st := .next, status := .requeued, live := false, requeues := t.requeues + 1 }

/-! ## Queue admission (`task_queue.build_queue`) -/

def closed (s : OState) : Bool := s == .done || s == .cancelled

/-- Is the task a candidate, and by which route? The direct route
(`find_unqueued_ai_tasks`) reads TODO/NEXT; the queue-entry route resolves any
non-closed source and hands it back under the entry's NEXT. -/
def admitted (fixed : Bool) (t : Task) : Bool :=
  let direct := t.st == .todo || t.st == .next
  let viaEntry := t.queueEntry && !closed t.st &&
    !(fixed && (t.st == .review || t.st == .waiting))
  (direct || viaEntry) && !blocked t.fence && !t.live

/-! ## `complete_task` -/

inductive Decision | approved | needsReview | proposal
  deriving DecidableEq, Repr

/-- `claim.escalation_threshold`: `max_retries`, at least 1 (decision N3). -/
def thr (m : Nat) : Nat := max 1 m

theorem one_le_thr (m : Nat) : 1 ≤ thr m := Nat.le_max_left 1 m

/-- `complete_task(status='failed')`. As found: hard-coded 2, fence ignored.
Fixed: a closed fence escalates at once (N1), else `max_retries` (N3). -/
def completeFailed (fixed : Bool) (m : Nat) (t : Task) : Task :=
  let a := t.attempts + 1
  let esc := if fixed then blocked t.fence || decide (a ≥ thr m) else decide (a ≥ 2)
  { t with attempts := a, st := if esc then .review else .next,
           status := .failed, hasOutput := false, live := false }

def completeOk (fixed : Bool) (d : Decision) (t : Task) : Task :=
  { t with st := (match d with | .approved => .done | _ => .review),
           status := (match d with | .approved => .done | _ => .review),
           hasOutput := true, live := false,
           attempts := if fixed then 0 else t.attempts,
           requeues := if fixed then 0 else t.requeues }

/-! ## One task in one run (`run.run_task_mode` loop body) -/

/-- What `execute_task` returned, as the run reads it. -/
inductive Outcome | success | knownFail | unknown | quota | budget
  deriving DecidableEq, Repr

/-- The post-execution checks: `is_background_handoff`, `write_output`. -/
inductive Post | ok | handoff | writeFail
  deriving DecidableEq, Repr

/-- The exit path the run takes for an admitted task. -/
inductive Path
  | claimRefused
  | preHookAbort
  | executed (o : Outcome) (p : Post) (d : Decision)

/-- `task_attempt.finish`. -/
def finishFence : Outcome → Fence
  | .success => .completed
  | .unknown => .unknown
  | _ => .notStarted

def runOne (fixed : Bool) (m : Nat) (path : Path) (t : Task) : Task :=
  match path with
  | .claimRefused => { t with live := !fixed }     -- STARTED written, then refused
  | .preHookAbort => { t with live := !fixed }     -- claim held, no execution
  | .executed o p d =>
    let t := { t with live := true, fence := finishFence o }
    match o with
    | .quota => { t with live := false, status := .deferred }   -- run.py quota deferral
    -- `budget_exhausted=True` from execute (Q11): nothing ran. Fixed, run.py
    -- defers it like the pre-claim budget gate; as found it was a failed attempt.
    | .budget => if fixed then { t with live := false, status := .deferred }
                 else completeFailed fixed m t
    | .knownFail | .unknown => completeFailed fixed m { t with execs := t.execs + 1 }
    | .success =>
      let t := { t with execs := t.execs + 1 }
      match p with
      | .ok => completeOk fixed d t
      | _ => completeFailed fixed m t

/-- One night for one task: GC, then admission, then (if admitted) one exit path. -/
def night (fixed : Bool) (m : Nat) (stale : Bool) (path : Path) (t : Task) : Task :=
  let t := gc fixed stale t
  if admitted fixed t then runOne fixed m path t else t

def nights (fixed : Bool) (m : Nat) : List (Bool × Path) → Task → Task
  | [] , t => t
  | (s, p) :: rest, t => nights fixed m rest (night fixed m s p t)

def fresh (entry : Bool) : Task :=
  { st := .next, attempts := 0, fence := .none, status := .none, hasOutput := false,
    live := false, requeues := 0, queueEntry := entry, generated := false, execs := 0 }

/-! ## (c) Claims are released on every exit path -/

theorem claim_released_on_every_exit (m : Nat) (path : Path) (t : Task) :
    (runOne true m path t).live = false := by
  cases path with
  | claimRefused => rfl
  | preHookAbort => rfl
  | executed o p d =>
    cases o <;> cases p <;> cases d <;> rfl

/-- Refutation of the code as found: a refused claim stays live. -/
theorem claim_refused_stays_live (m : Nat) (t : Task) :
    (runOne false m .claimRefused t).live = true := rfl

/-- …and two refused claims plus the GC's staleness rule cap a task that never
ran: it is live forever, so it is never admitted again and never GC'd again. -/
def refusedTwice : List (Bool × Path) :=
  [(false, .claimRefused), (true, .claimRefused), (true, .claimRefused), (true, .claimRefused)]

theorem refused_claims_cap_a_task_that_never_ran :
    let t := nights false 2 refusedTwice (fresh false)
    t.requeues = 2 ∧ t.live = true ∧ t.execs = 0 ∧ admitted false t = false ∧
      gc false true t = t := by decide

theorem refused_claims_do_not_cost_requeues (m : Nat) (ps : List (Bool × Path)) :
    (nights true m (ps.map fun (s, _) => (s, Path.claimRefused)) (fresh false)).requeues = 0 := by
  suffices h : ∀ (qs : List (Bool × Path)) (t : Task), t.st = .next → t.live = false →
      t.fence = .none → (nights true m (qs.map fun (s, _) => (s, Path.claimRefused)) t).requeues
        = t.requeues from h ps _ rfl rfl rfl
  intro qs
  induction qs with
  | nil => intro t _ _ _; rfl
  | cons q qs ih =>
    intro t hs hl hf
    obtain ⟨s, _⟩ := q
    have hgc : gc true s t = t := by
      simp [gc, isStalled, blocked, hs, hl, hf]
    simp only [List.map, nights, night, hgc]
    split
    · exact ih _ hs rfl hf
    · exact ih _ hs hl hf

/-! ## (a)+(b) Liveness and bounded executions (N1, N3) -/

/-- A task no automated step can make admissible again. -/
def dead (t : Task) : Bool := blocked t.fence || (t.st == .review && t.status == .failed)

/-- The invariant: executions happen only while `execs = attempts < max(1, max_retries)`. -/
def Inv (m : Nat) (t : Task) : Prop :=
  t.execs ≤ thr m ∧ (dead t = false → t.execs = t.attempts ∧ t.attempts < thr m)

theorem fresh_inv (m : Nat) (e : Bool) : Inv m (fresh e) := by
  have := one_le_thr m
  refine ⟨by simp [fresh], fun _ => ⟨rfl, by simp [fresh]; omega⟩⟩

theorem gc_preserves (m : Nat) (s : Bool) (t : Task) (h : Inv m t) : Inv m (gc true s t) := by
  unfold gc
  split
  · exact h
  split
  · exact h
  rename_i hb hs
  split
  · exact h
  split
  · exact h
  -- a requeue: the task was stalled, so it is not an escalation; `dead` stays false
  have hd : dead t = false := by
    simp only [isStalled] at hs
    simp only [dead]
    revert hs hb
    cases t.st <;> cases t.status <;> simp_all
  obtain ⟨h1, h2⟩ := h
  exact ⟨h1, fun _ => h2 hd⟩

theorem admitted_not_dead (t : Task) (h : admitted true t = true) : dead t = false := by
  simp only [admitted, Bool.and_eq_true, Bool.or_eq_true, Bool.not_eq_true'] at h
  obtain ⟨⟨hr, hb⟩, _⟩ := h
  simp only [dead, hb, Bool.false_or]
  rcases hr with hr | hr <;> revert hr <;> cases t.st <;> simp

/-- A failure recorded on the `k+1`-th run keeps the invariant: it either
escalates (dead, and `k+1 ≤ thr m`) or stays NEXT with `k+1 < thr m`. -/
theorem completeFailed_inv (m : Nat) (t : Task) (he : t.execs = t.attempts + 1)
    (ha : t.attempts < thr m) : Inv m (completeFailed true m t) := by
  obtain ⟨st, att, f, stt, ho, lv, rq, qe, gen, ex⟩ := t
  simp only at he ha
  subst he
  refine ⟨by simp only [completeFailed]; omega, fun hd => ⟨rfl, ?_⟩⟩
  simp only [completeFailed, dead] at hd ⊢
  by_cases hb : blocked f = true <;> by_cases hc : att + 1 ≥ thr m <;> simp_all <;> omega

theorem runOne_preserves (m : Nat) (path : Path) (t : Task) (hn : dead t = false)
    (h : Inv m t) : Inv m (runOne true m path t) := by
  obtain ⟨h1, h2⟩ := h
  obtain ⟨he, ha⟩ := h2 hn
  cases path with
  | claimRefused => exact ⟨h1, fun _ => ⟨he, ha⟩⟩
  | preHookAbort => exact ⟨h1, fun _ => ⟨he, ha⟩⟩
  | executed o p d =>
    cases o with
    | quota => exact ⟨h1, fun _ => ⟨he, ha⟩⟩
    | budget => exact ⟨h1, fun _ => ⟨he, ha⟩⟩
    | knownFail => exact completeFailed_inv m _ (by simp [he]) ha
    | unknown => exact completeFailed_inv m _ (by simp [he]) ha
    | success =>
      cases p with
      | ok =>
        -- the fence is `completed`, so the task is dead; only the bound matters
        refine ⟨?_, fun hd => ?_⟩
        · cases d <;> (simp only [runOne, completeOk]; omega)
        · cases d <;> simp [runOne, completeOk, dead, blocked, finishFence] at hd
      | handoff => exact completeFailed_inv m _ (by simp [he]) ha
      | writeFail => exact completeFailed_inv m _ (by simp [he]) ha

theorem night_preserves (m : Nat) (s : Bool) (p : Path) (t : Task) (h : Inv m t) :
    Inv m (night true m s p t) := by
  have hg := gc_preserves m s t h
  show Inv m (if admitted true (gc true s t) = true then runOne true m p (gc true s t)
    else gc true s t)
  split
  · rename_i ha
    exact runOne_preserves m p _ (admitted_not_dead _ ha) hg
  · exact hg

theorem nights_preserves (m : Nat) (ns : List (Bool × Path)) :
    ∀ t, Inv m t → Inv m (nights true m ns t) := by
  induction ns with
  | nil => intro t h; exact h
  | cons n ns ih => intro t h; exact ih _ (night_preserves m n.1 n.2 t h)

/-- **(b), decision N3.** Whatever the outcomes, evaluator decisions,
post-checks and GC staleness, a task runs (does work) at most
`max(1, max_retries)` times between human actions. -/
theorem executions_bounded (m : Nat) (e : Bool) (ns : List (Bool × Path)) :
    (nights true m ns (fresh e)).execs ≤ thr m :=
  (nights_preserves m ns _ (fresh_inv m e)).1

/-- **(a)** Liveness: once a task has run `max(1, max_retries)` times it is
`dead` — escalated to REVIEW or behind a fence — and a dead task is never
admitted again. -/
theorem exhausted_is_dead (m : Nat) (e : Bool) (ns : List (Bool × Path)) :
    (nights true m ns (fresh e)).execs = thr m → dead (nights true m ns (fresh e)) = true := by
  intro h2
  have hi := nights_preserves m ns _ (fresh_inv m e)
  cases hd : dead (nights true m ns (fresh e))
  · have := (hi.2 hd); omega
  · rfl

theorem dead_never_admitted (t : Task) (h : dead t = true) : admitted true t = false := by
  cases ha : admitted true t
  · rfl
  · have := admitted_not_dead t ha; simp_all

theorem escalated_review_not_stalled (s : Bool) (t : Task)
    (h : t.st = .review) (hs : t.status = .failed) : gc true s t = t := by
  simp [gc, isStalled, h, hs]

theorem review_source_not_admitted (t : Task) (h : t.st = .review ∨ t.st = .waiting) :
    admitted true t = false := by
  rcases h with h | h <;> simp [admitted, closed, h]

/-! ### N1 — every dead task is in front of a person (no fenced-NEXT zombies) -/

/-- A dead task is visible: in REVIEW (a person's queue) or DONE (closed). -/
def Visible (t : Task) : Prop := dead t = true → t.st = .review ∨ t.st = .done

/-- The class N1 removes: behind a closed fence yet sitting in TODO/NEXT. -/
def zombie (t : Task) : Bool := blocked t.fence && (t.st == .next || t.st == .todo)

theorem fresh_visible (e : Bool) : Visible (fresh e) := by
  intro h; simp [dead, fresh, blocked] at h

theorem gc_visible (s : Bool) (t : Task) (h : Visible t) : Visible (gc true s t) := by
  unfold gc
  split
  · exact h
  split
  · exact h
  split
  · exact h
  split
  · exact h
  rename_i hb _ _ _
  intro hd
  simp [dead, hb] at hd

/-- Every executed exit path leaves a visible task, whatever it started from. -/
theorem runOne_executed_visible (m : Nat) (o : Outcome) (p : Post) (d : Decision) (t : Task) :
    Visible (runOne true m (.executed o p d) t) := by
  intro hd
  cases o <;> cases p <;> cases d <;>
    simp_all [runOne, completeFailed, completeOk, finishFence, dead, blocked]

theorem night_visible (m : Nat) (s : Bool) (p : Path) (t : Task) (h : Visible t) :
    Visible (night true m s p t) := by
  have hg := gc_visible s t h
  show Visible (if admitted true (gc true s t) = true then runOne true m p (gc true s t)
    else gc true s t)
  split
  · rename_i ha
    have hn := admitted_not_dead _ ha
    cases p with
    | claimRefused => intro hd; simp_all [runOne, dead]
    | preHookAbort => intro hd; simp_all [runOne, dead]
    | executed o q d => exact runOne_executed_visible m o q d _
  · exact hg

theorem dead_is_visible (m : Nat) (e : Bool) (ns : List (Bool × Path)) :
    Visible (nights true m ns (fresh e)) := by
  suffices h : ∀ t, Visible t → Visible (nights true m ns t) from h _ (fresh_visible e)
  induction ns with
  | nil => intro t h; exact h
  | cons n ns ih => intro t h; exact ih _ (night_visible m n.1 n.2 t h)

/-- **N1.** No sequence of nights produces a fenced-NEXT (or TODO) zombie. -/
theorem no_fenced_zombie (m : Nat) (e : Bool) (ns : List (Bool × Path)) :
    zombie (nights true m ns (fresh e)) = false := by
  have hv := dead_is_visible m e ns
  cases hz : zombie (nights true m ns (fresh e))
  · rfl
  · simp only [zombie, Bool.and_eq_true, Bool.or_eq_true, beq_iff_eq] at hz
    obtain ⟨hb, hs⟩ := hz
    have := hv (by simp [dead, hb])
    rcases hs with hs | hs <;> rw [hs] at this <;> simp at this

/-- Refutation of the code as found: one unknown outcome is a zombie. -/
theorem unknown_outcome_zombie_as_found (m : Nat) :
    zombie (runOne false m (.executed .unknown .ok .approved) (fresh false)) = true := by
  simp [zombie, runOne, completeFailed, finishFence, blocked, fresh]

/-- …and so is a success whose deliverable was a hand-off. -/
theorem handoff_zombie_as_found (m : Nat) :
    zombie (runOne false m (.executed .success .handoff .approved) (fresh false)) = true := by
  simp [zombie, runOne, completeFailed, finishFence, blocked, fresh]

/-! ### N1 + N2 — `reconcile_attempt --reopen` -/

/-- The `--reopen` tool: fence → not-started, ATTEMPTS and REQUEUES → 0, state
→ NEXT. `execs` is the ghost count of executions since the last human action,
so a person's reopen starts it again. -/
def reopenTool (t : Task) : Task :=
  { t with st := .next, fence := .notStarted, attempts := 0, requeues := 0, execs := 0 }

theorem reopen_resets_counters (t : Task) :
    (reopenTool t).attempts = 0 ∧ (reopenTool t).requeues = 0 ∧
      (reopenTool t).fence = .notStarted ∧ (reopenTool t).st = .next := ⟨rfl, rfl, rfl, rfl⟩

/-- After a reopen the bound holds again, for every later sequence of nights. -/
theorem reopen_restores_inv (m : Nat) (t : Task) : Inv m (reopenTool t) := by
  have := one_le_thr m
  exact ⟨by simp [reopenTool], fun _ => ⟨rfl, by simp [reopenTool]; omega⟩⟩

theorem reopened_bounded (m : Nat) (t : Task) (ns : List (Bool × Path)) :
    (nights true m ns (reopenTool t)).execs ≤ thr m :=
  (nights_preserves m ns _ (reopen_restores_inv m t)).1

/-- A reopened task (no live claim) is work the queue will pick up. -/
theorem reopened_is_admitted (t : Task) (hl : t.live = false) :
    admitted true (reopenTool t) = true := by
  simp [admitted, reopenTool, blocked, hl]

/-! ### Q10 — `--reopen` in a ledger-phase-1 space (owner follow-up, 2026-09-23)

In a generated space the org state is RENDERED from the ledger
(`fold.org_task_state`): `completed` renders REVIEW and `verified`/`dismissed`
render DONE/CANCELLED whatever the payload says; any other status renders the
payload's `state`. As found, `--reopen` refused every REVIEW task in such a
space. Fixed, it writes through `update_task`, whose Phase 1 path is the
adapter's `_ledger_emit` → `sync_generated`: a conditional `item.update` of the
payload state to NEXT (plus the reset drawer). It still refuses — before
writing anything — the statuses whose rendering no event can move: no event
un-completes an item (`item.release` is un-claim only). -/

inductive LStatus | created | claimed | completed | verified | dismissed
  deriving DecidableEq, Repr

/-- `fold.org_task_state` (DONE vs CANCELLED collapsed: both closed). -/
def rendered (s : LStatus) (payloadSt : OState) : OState :=
  match s with
  | .completed => .review
  | .verified | .dismissed => .done
  | _ => payloadSt

/-- What `--reopen` does to the ledger in Phase 1: `none` = refused, nothing
written; `some p` = an `item.update` setting the payload state to `p`. -/
def reopenLedger (fixed : Bool) (s : LStatus) : Option OState :=
  if !fixed then none
  else match s with
    | .completed | .verified | .dismissed => none
    | _ => some .next

/-- **The ledger reopens it.** Whenever the fixed tool writes, the space's
projection renders the item NEXT — the file and the ledger agree, so the next
projection is not wedged (`project_space` → generated). -/
theorem reopen_phase1_renders_next (s : LStatus) (p : OState)
    (h : reopenLedger true s = some p) : rendered s p = .next := by
  cases s <;> simp [reopenLedger] at h <;> subst h <;> rfl

/-- An escalated failure (its claim was released: status `created`) — or one
whose claim is still held — is reopened, not refused. -/
theorem reopen_phase1_escalated_reopens (s : LStatus)
    (hs : s = .created ∨ s = .claimed) : reopenLedger true s = some .next := by
  rcases hs with rfl | rfl <;> rfl

/-- As found: refused. -/
theorem reopen_phase1_refused_as_found : reopenLedger false .created = none := rfl

/-- Why `completed` is still refused: an update alone would change the file and
nothing else — the ledger keeps rendering REVIEW (the stalled-GC's wedge). -/
theorem completed_update_does_not_reopen : rendered .completed .next = .review := rfl

/-- **N2.** A successful run resets the requeue cap. -/
theorem requeues_reset_on_success (d : Decision) (t : Task) :
    (completeOk true d t).requeues = 0 := rfl

/-! ### Refutations of the code as found (replayed: 4 and 8 executions) -/

def alwaysFails (n : Nat) : List (Bool × Path) :=
  List.replicate n (false, .executed .knownFail .ok .approved)

/-- Without a queue entry the GC undoes the escalation twice: 4 executions. -/
theorem gc_resurrects_escalation :
    (nights false 2 (alwaysFails 8) (fresh false)).execs = 4 := by decide

/-- With a queue entry the escalated source is re-admitted every night. -/
theorem queue_entry_unbounded :
    (nights false 2 (alwaysFails 8) (fresh true)).execs = 8 := by decide

/-- **N3**, the bound is tight: with max_retries m, a task that always fails
runs exactly max(1, m) times (samples; `executions_bounded` is the general bound). -/
theorem threshold_is_max_retries :
    (nights true 0 (alwaysFails 8) (fresh true)).execs = 1 ∧
    (nights true 1 (alwaysFails 8) (fresh true)).execs = 1 ∧
    (nights true 2 (alwaysFails 8) (fresh true)).execs = 2 ∧
    (nights true 3 (alwaysFails 8) (fresh true)).execs = 3 ∧
    (nights true 5 (alwaysFails 8) (fresh true)).execs = 5 := by decide

/-- As found, `max_retries` did not reach the escalation: 5 still meant 2. -/
theorem max_retries_ignored_as_found :
    (runOne false 5 (.executed .knownFail .ok .approved)
      (runOne false 5 (.executed .knownFail .ok .approved) (fresh false))).st = .review := by
  decide

def KF : Bool × Path := (false, .executed .knownFail .ok .approved)

/-- The steady state after four nights: escalated, at the requeue cap, entry kept. -/
def Wedged (t : Task) : Prop :=
  t.queueEntry = true ∧ t.st = .review ∧ t.status = .failed ∧ t.fence = .notStarted ∧
    t.live = false ∧ t.requeues = 2 ∧ 1 ≤ t.attempts

theorem wedged_night (t : Task) (h : Wedged t) :
    Wedged (night false 2 false KF.2 t) ∧ (night false 2 false KF.2 t).execs = t.execs + 1 := by
  obtain ⟨hq, hs, hst, hf, hl, hr, ha⟩ := h
  obtain ⟨st, att, f, stt, ho, lv, rq, qe, gen, ex⟩ := t
  simp only at hq hs hst hf hl hr ha
  subst hq hs hst hf hl hr
  have h2 : 2 ≤ att + 1 := by omega
  cases ho <;> cases gen <;>
    simp [KF, night, gc, isStalled, blocked, admitted, closed, MAX_REQUEUES, runOne,
          completeFailed, finishFence, Wedged, h2] <;> omega

theorem wedged_nights (m : Nat) : ∀ t, Wedged t →
    (nights false 2 (List.replicate m KF) t).execs = t.execs + m := by
  induction m with
  | zero => intro t _; rfl
  | succ m ih =>
    intro t h
    obtain ⟨hw, he⟩ := wedged_night t h
    show (nights false 2 (List.replicate m KF) (night false 2 false KF.2 t)).execs = _
    rw [ih _ hw, he]; omega

/-- For every n: n + 4 nights of failure, n + 4 executions — no bound. -/
theorem queue_entry_unbounded_general (n : Nat) :
    (nights false 2 (List.replicate (4 + n) KF) (fresh true)).execs = n + 4 := by
  have happ : ∀ (a b : List (Bool × Path)) t,
      nights false 2 (a ++ b) t = nights false 2 b (nights false 2 a t) := by
    intro a; induction a with
    | nil => intro b t; rfl
    | cons x a ih => intro b t; simp [nights, ih]
  rw [← List.replicate_append_replicate, happ, wedged_nights n _
    (by refine ⟨?_, ?_, ?_, ?_, ?_, ?_, ?_⟩ <;> decide)]
  have : (nights false 2 (List.replicate 4 KF) (fresh true)).execs = 4 := by decide
  omega

/-! ## "SECOND consecutive failure" -/

/-- A person sends a task back for another go (by hand, not the tool). -/
def reopen (t : Task) : Task := { t with st := .next, fence := .notStarted }

theorem attempts_reset_on_non_failure (d : Decision) (t : Task) :
    (completeOk true d t).attempts = 0 := rfl

/-- fail, reviewed success, reopen, fail: one failure after a success. -/
def failSucceedFail (fixed : Bool) : Task :=
  let t := runOne fixed 2 (.executed .knownFail .ok .approved) (fresh false)
  let t := runOne fixed 2 (.executed .success .ok .needsReview) t
  runOne fixed 2 (.executed .knownFail .ok .approved) (reopen t)

theorem not_consecutive_escalated_as_found : (failSucceedFail false).st = .review := by decide
theorem consecutive_after_fix : (failSucceedFail true).st = .next := by decide

/-! ## (d) In-run retry (`run.py`, one `if`) -/

/-- The first result's classification as the run reads it. -/
structure First where
  failed    : Bool
  quota     : Bool   -- quota_exhausted ∧ ¬outcome_unknown
  unknown   : Bool   -- outcome_unknown
  retryable : Bool   -- analyze_failure(...)['retryable']

/-- How many times `execute_task` is called for one task in one run
(`NIGHTSHIFT_RETRIES` starts at 0: it is never persisted). -/
def calls (fixed : Bool) (maxRetries : Nat) (f : First) : Nat :=
  if !fixed && f.failed && f.quota then 1          -- quota check came first
  else if f.failed && !f.quota && !f.unknown && f.retryable && 0 < maxRetries then 2
  else 1

theorem retries_bounded (fixed : Bool) (m : Nat) (f : First) :
    calls fixed m f - 1 ≤ min 1 m := by
  unfold calls
  repeat' split
  all_goals (simp_all; try omega)

/-- The verdict on the result the run ends with. -/
inductive Verdict | deferred | failed | proceed
  deriving DecidableEq, Repr

/-- `retryQuota`: the retry's own result hit the quota wall. -/
def verdict (fixed : Bool) (m : Nat) (f : First) (retryQuota retrySucceeded : Bool) : Verdict :=
  if f.failed && f.quota then .deferred
  else if calls fixed m f = 2 then
    (if retrySucceeded then .proceed else if fixed && retryQuota then .deferred else .failed)
  else if f.failed then .failed else .proceed

theorem quota_on_retry_defers (m : Nat) (f : First) (hq : f.quota = false)
    (hr : calls true m f = 2) : verdict true m f true false = .deferred := by
  simp [verdict, hq, hr]

theorem quota_on_retry_failed_as_found :
    verdict false 2 ⟨true, false, false, true⟩ true false = .failed := by decide

/-- Total `execute_task` calls between human actions: at most two per run
(`retries_bounded`) and at most `max(1, max_retries)` runs (`executions_bounded`). -/
theorem calls_bounded (fixed : Bool) (m : Nat) (f : First) : calls fixed m f ≤ 2 := by
  have := retries_bounded fixed m f; omega

/-! ## N6 — the paid-API budget (`execute._execute_task`, `run.run_task_mode`) -/
namespace Budget

/-- `check_budget`: limit 0 = unlimited. Amounts in any fixed unit. -/
def allowed (limit spent : Nat) : Bool := limit == 0 || decide (spent < limit)

/-- Does a paid (batch-API) call start? `paidRoute` = `uses_paid_api(task)`.
As found the budget was computed and ignored; fixed, `_execute_task` refuses
(and `run.py` defers before claiming) when it is not allowed. -/
def paidCallStarts (fixed : Bool) (paidRoute : Bool) (limit spent : Nat) : Bool :=
  paidRoute && (!fixed || allowed limit spent)

theorem no_paid_call_past_limit (r : Bool) (limit spent : Nat)
    (hl : 0 < limit) (hs : limit ≤ spent) : paidCallStarts true r limit spent = false := by
  have h1 : (limit == 0) = false := by simp; omega
  have h2 : decide (spent < limit) = false := by simp; omega
  simp [paidCallStarts, allowed, h1, h2]

/-- Budget 0 (this machine today) changes nothing. -/
theorem unlimited_unchanged (r : Bool) (spent : Nat) :
    paidCallStarts true r 0 spent = paidCallStarts false r 0 spent := by
  cases r <;> rfl

theorem paid_call_past_limit_as_found : paidCallStarts false true 10 12 = true := by decide

/-- One day's paid spend over a list of task costs. -/
def day (fixed : Bool) (limit : Nat) : List (Bool × Nat) → Nat → Nat
  | [], s => s
  | (r, c) :: rest, s => day fixed limit rest (if paidCallStarts fixed r limit s then s + c else s)

/-- With a positive limit the day overshoots it by less than one call. -/
theorem overshoot_below_one_call (limit c : Nat) (hl : 0 < limit) :
    ∀ (ts : List (Bool × Nat)) (s : Nat), (∀ x ∈ ts, x.2 ≤ c) → s < limit + c →
      day true limit ts s < limit + c := by
  intro ts
  induction ts with
  | nil => intro s _ hs; exact hs
  | cons x ts ih =>
    intro s hc hs
    obtain ⟨r, cx⟩ := x
    have hcx : cx ≤ c := hc (r, cx) (List.mem_cons_self ..)
    have hrest : ∀ y ∈ ts, y.2 ≤ c := fun y hy => hc y (List.mem_cons_of_mem _ hy)
    simp only [day]
    apply ih _ hrest
    split
    · rename_i hp
      simp [paidCallStarts, allowed] at hp
      omega
    · exact hs

/-! ### Q11 — `budget_exhausted` from execute is a deferral (owner follow-up)

If execute refuses the paid call anyway (the day's spend crossed the limit
mid-run, or on the in-run retry), run.py now treats the result like the
pre-claim gate: the claim is released, the state and `NIGHTSHIFT_ATTEMPTS` are
untouched, nothing counts as an execution, and the run goes on (`continue`,
not the quota wall's `break`). As found it was a failed attempt. -/

theorem budget_defer_not_attempt (m : Nat) (p : Post) (d : Decision) (t : Task) :
    let t' := runOne true m (.executed .budget p d) t
    t'.attempts = t.attempts ∧ t'.st = t.st ∧ t'.execs = t.execs ∧
      t'.live = false ∧ t'.status = .deferred ∧ t'.fence = .notStarted :=
  ⟨rfl, rfl, rfl, rfl, rfl, rfl⟩

/-- As found: a paid call that never started cost the task an attempt. -/
theorem budget_counted_as_attempt_as_found :
    (runOne false 2 (.executed .budget .ok .approved) (fresh true)).attempts = 1 := by decide

end Budget

end DatacoreSpec.NightshiftLifecycle
