/-!
# GtdState — DIP-0009 v2.0 task states vs org-workspace and the adapter

Models, branch for branch:

* `org_workspace/_types.py` `StateConfig.default()` / `can_transition`, and
  `workspace.py` `transition` (including the repeater branch that reopens a
  task as TODO instead of closing it);
* `lib/org_workspace_adapter.py` `cmd_complete` / `cmd_update`: which ledger
  events each emits, on the direct-append path and on the Phase 1
  generated-file path (`sync_generated`, a diff of what the file now says);
* `lib/dedup_tasks.py` `retire_duplicate`;
* `lib/task_cleanup.py` dup-id reassignment (id minting across reruns).
* section 5: the owner decisions of 2026-09-23 — G1 (the adapter warns iff a
  move is outside its relation, and performs it anyway), G2 (that relation is
  the v2.0 table plus DEFERRED→CANCELLED), G5 (the v2.0 `StateConfig.default()`)
  and G4 (dup-id planning skips generated next_actions.org).

What is abstracted: payloads (only the ledger status is tracked), dates (a
repeater is a Bool), and UUID generation (an oracle that never returns a
taken id — `task_cleanup._fresh_id` loops until that holds).
-/

namespace DatacoreSpec.GtdState

/-! ## 1. The transition relation -/

inductive St
  | todo | next | waiting | review | done | deferred | cancelled
  | queued | working | failed          -- retired by v2.0, still in the code's config
  deriving DecidableEq, Repr

def St.retired : St → Bool
  | .queued | .working | .failed => true
  | _ => false

/-- DIP-0009 v2.0 "Valid transitions" table (lines 373-381). -/
def spec : St → St → Bool
  | .todo, b    => b ∈ [.next, .waiting, .review, .done, .deferred, .cancelled]
  | .next, b    => b ∈ [.todo, .waiting, .review, .done, .deferred, .cancelled]
  | .waiting, b => b ∈ [.todo, .next, .review, .done, .deferred, .cancelled]
  | .review, b  => b ∈ [.done, .next, .deferred, .cancelled]
  | .deferred, b => b == .todo
  | _, _ => false

/-- `StateConfig.default()`: one sequence holding all ten states; terminal = {DONE, CANCELLED}. -/
def codeTerminal : St → Bool
  | .done | .cancelled => true
  | _ => false

/-- `valid_transitions`: any non-terminal state to any other state of its sequence. -/
def code (a b : St) : Bool := !codeTerminal a && a != b

/-- Every spec transition is allowed by the code. -/
theorem spec_sub_code : ∀ a b, spec a b = true → code a b = true := by
  intro a b; cases a <;> cases b <;> decide

/-- Code ⊆ spec is FALSE. Replayed: `can_transition` is True for each pair. -/
theorem code_not_sub_spec :
    code .deferred .done = true ∧ spec .deferred .done = false ∧
    code .deferred .next = true ∧ spec .deferred .next = false ∧
    code .review .todo = true ∧ spec .review .todo = false ∧
    code .review .waiting = true ∧ spec .review .waiting = false ∧
    code .todo .queued = true ∧ spec .todo .queued = false ∧
    code .next .failed = true ∧ spec .next .failed = false := by decide

/-- Exact characterisation of the excess: a retired state on either side,
DEFERRED leaving to anything but TODO, or REVIEW leaving to TODO/WAITING. -/
theorem excess_exact : ∀ a b,
    (code a b = true ∧ spec a b = false) ↔
    (a ≠ b ∧ codeTerminal a = false ∧
      (a.retired = true ∨ b.retired = true ∨
       (a = .deferred ∧ b ≠ .todo) ∨
       (a = .review ∧ (b = .todo ∨ b = .waiting)))) := by
  intro a b; cases a <;> cases b <;> decide

def canonical : List St := [.todo, .next, .waiting, .review, .done, .deferred, .cancelled]

/-- Among the seven v2.0 states the code admits exactly seven spec-illegal moves. -/
theorem canonical_excess :
    (canonical.flatMap fun a => (canonical.filter fun b => code a b && !spec a b).map (a, ·)) =
    [(.review, .todo), (.review, .waiting),
     (.deferred, .next), (.deferred, .waiting), (.deferred, .review),
     (.deferred, .done), (.deferred, .cancelled)] := by decide

/-- "REVIEW → … — owner only": the spec relation carries an actor; the code has
none (`transition(node, new_state, agent)` uses `agent` only for COMPLETED_BY). -/
def specBy (actor owner : Nat) (a b : St) : Bool := spec a b && (a != .review || actor == owner)
def codeBy (_actor _owner : Nat) (a b : St) : Bool := code a b

theorem owner_only_not_enforced : codeBy 1 0 .review .next = true ∧ specBy 1 0 .review .next = false := by
  decide

/-! ## 2. Adapter: org state change → ledger events -/

structure Task where
  st       : St
  repeater : Bool      -- SCHEDULED carries +Nd/++Nd/.+Nd
  hasId    : Bool

/-- `OrgWorkspace.transition`: `none` = raised InvalidTransitionError. A terminal
target on a repeater advances SCHEDULED and reopens as TODO. -/
def wsTransition (t : Task) (b : St) : Option St :=
  if t.st = b then some t.st
  else if code t.st b = false then none
  else if codeTerminal b && t.repeater then some .todo
  else some b

inductive Kind | done | dropped deriving DecidableEq, Repr
inductive Ev | update | dismiss (k : Kind) deriving DecidableEq, Repr

def dismissKind : St → Option Kind
  | .done => some .done
  | .cancelled => some .dropped
  | _ => none

inductive LStatus | live | dismissed deriving DecidableEq, Repr

def applyEv : LStatus → Ev → LStatus
  | s, .update => s
  | _, .dismiss _ => .dismissed

def applyAll (s : LStatus) (es : List Ev) : LStatus := es.foldl applyEv s

/-- Phase 1 generated next_actions.org: `sync_generated` diffs what the file
says. A changed state that is DONE/CANCELLED becomes update(rest)+dismiss. -/
def syncEvents (pre post : St) : List Ev :=
  if pre = post then [.update]
  else match dismissKind post with
    | some k => [.update, .dismiss k]
    | none => [.update]

/-- Pre-fix `cmd_complete` (direct path): dismiss(done) regardless of outcome. -/
def oldComplete (gen : Bool) (t : Task) : Option (St × List Ev) :=
  if codeTerminal t.st then none else
  (wsTransition t .done).map fun post =>
    (post, if gen then syncEvents t.st post else [.dismiss .done])

/-- Pre-fix `cmd_update --state b` (direct path): item.update only. -/
def oldUpdate (gen : Bool) (t : Task) (b : St) : Option (St × List Ev) :=
  (wsTransition t b).map fun post =>
    (post, if gen then syncEvents t.st post else [.update])

/-- `_ledger_emit_close`: dismiss iff the RESULTING org state closes, the task
has an id, and the path is not the generated one. -/
def closeEv (gen : Bool) (t : Task) (post : St) : List Ev :=
  if gen || !t.hasId then [] else
  match dismissKind post with
  | some k => [.dismiss k]
  | none => []

/-- Fixed `cmd_complete`. -/
def newComplete (gen : Bool) (t : Task) : Option (St × List Ev) :=
  if codeTerminal t.st then none else
  (wsTransition t .done).map fun post =>
    (post, if !t.hasId then []
           else if gen then syncEvents t.st post
           else if post ≠ .done then [.update]
           else closeEv gen t post)

/-- Fixed `cmd_update --state b`. -/
def newUpdate (gen : Bool) (t : Task) (b : St) : Option (St × List Ev) :=
  (wsTransition t b).map fun post =>
    (post, (if gen then syncEvents t.st post else [.update]) ++ closeEv gen t post)

/-- Counterexample 1 (replayed): completing a repeater leaves org TODO, ledger dismissed. -/
theorem old_complete_dismisses_repeater :
    oldComplete false ⟨.todo, true, true⟩ = some (.todo, [.dismiss .done]) := by decide

/-- Counterexample 2 (replayed): update to DONE/CANCELLED leaves the ledger live. -/
theorem old_update_never_dismisses :
    (oldUpdate false ⟨.todo, false, true⟩ .done).map (fun r => applyAll .live r.2) = some .live ∧
    (oldUpdate false ⟨.todo, false, true⟩ .cancelled).map (fun r => applyAll .live r.2) = some .live := by
  decide

theorem applyAll_append (s : LStatus) (a b : List Ev) :
    applyAll s (a ++ b) = applyAll (applyAll s a) b := by
  simp [applyAll, List.foldl_append]

theorem applyAll_update_only (s : LStatus) : applyAll s [.update] = s := rfl

theorem applyAll_live_of_no_dismiss (es : List Ev) (h : ∀ e ∈ es, e = .update) :
    applyAll .live es = .live := by
  induction es with
  | nil => rfl
  | cons e es ih =>
    have he := h e (by simp)
    subst he
    exact ih (fun x hx => h x (by simp [hx]))

/-- Every event list the fixed adapter emits, in every branch. -/
def newEvents (gen : Bool) (t : Task) (post : St) (isComplete : Bool) : List Ev :=
  if isComplete then
    (if !t.hasId then [] else if gen then syncEvents t.st post
     else if post ≠ .done then [.update] else closeEv gen t post)
  else (if gen then syncEvents t.st post else [.update]) ++ closeEv gen t post

/-- SOUNDNESS: after the fixed adapter, "ledger dismissed ⇒ org DONE or
CANCELLED", for every start state, target, repeater flag, id flag and path. -/
theorem dismissed_implies_terminal (gen c : Bool) (t : Task) (post : St) :
    applyAll .live (newEvents gen t post c) = .dismissed → codeTerminal post = true := by
  cases gen <;> cases c <;> cases hId : t.hasId <;> cases post <;>
    simp [newEvents, closeEv, syncEvents, dismissKind, applyAll, applyEv, codeTerminal, hId] <;>
    split <;> simp_all [applyEv]

/-- COMPLETENESS: a task with an id that ends DONE/CANCELLED (from a
non-closed state) is dismissed, with kind done/dropped respectively. -/
theorem terminal_implies_dismissed (gen c : Bool) (t : Task) (post : St)
    (hId : t.hasId = true) (hpre : t.st ≠ post) (ht : codeTerminal post = true)
    (hc : c = true → post = .done) :
    applyAll .live (newEvents gen t post c) = .dismissed := by
  cases gen <;> cases c <;> cases post <;>
    simp_all [newEvents, closeEv, syncEvents, dismissKind, applyAll, applyEv, codeTerminal]

/-- DEFERRED never dismisses. -/
theorem deferred_never_dismisses (gen c : Bool) (t : Task) :
    applyAll .live (newEvents gen t .deferred c) = .live := by
  cases gen <;> cases c <;> cases hId : t.hasId <;>
    simp [newEvents, closeEv, syncEvents, dismissKind, applyAll, applyEv, hId] <;>
    split <;> simp [applyEv]

/-- The repeater case closes: a repeater completed through the fixed adapter
stays live in the ledger (org reopened it as TODO). -/
theorem repeater_stays_live (gen : Bool) :
    (newComplete gen ⟨.todo, true, true⟩).map (fun r => (r.1, applyAll .live r.2)) =
      some (.todo, .live) := by
  cases gen <;> decide

/-- `newEvents` is exactly what `newComplete`/`newUpdate` emit. -/
theorem newComplete_events (gen : Bool) (t : Task) :
    newComplete gen t = if codeTerminal t.st then none else
      (wsTransition t .done).map fun post => (post, newEvents gen t post true) := rfl

theorem newUpdate_events (gen : Bool) (t : Task) (b : St) :
    newUpdate gen t b = (wsTransition t b).map fun post => (post, newEvents gen t post false) := rfl

/-! ## 3. dedup_tasks.retire_duplicate -/

/-- Pre-fix: true whenever fingerprints match (duplicate is OPEN: TODO/NEXT/WAITING). -/
def oldRetire (t : Task) : Option (Bool × St) :=
  (wsTransition t .cancelled).map fun post => (true, post)

/-- Fixed: refuses a repeater; otherwise transitions and checks it closed. -/
def newRetire (t : Task) : Option (Bool × St) :=
  if t.repeater then some (false, t.st)
  else (wsTransition t .cancelled).map fun post => (post == .cancelled, post)

theorem old_retire_lies_on_repeater :
    oldRetire ⟨.todo, true, true⟩ = some (true, .todo) := by decide

def openSt : St → Bool
  | .todo | .next | .waiting => true
  | _ => false

/-- "retire_duplicate returned True ⇒ the duplicate is CANCELLED" (fixed code). -/
theorem retire_true_implies_cancelled (t : Task) (hopen : openSt t.st = true) (post : St) :
    newRetire t = some (true, post) → post = .cancelled := by
  cases t with
  | mk st rep hid =>
    cases rep <;> cases st <;> simp_all [newRetire, wsTransition, code, codeTerminal, openSt]

/-! ## 4. task_cleanup dup-id minting -/

/-- Ids on disk as a count per id. -/
abbrev Disk (I : Type) := I → Nat

def reassign {I : Type} [DecidableEq I] (d : Disk I) (old new : I) : Disk I :=
  fun i => if i = new then d i + 1 else if i = old then d i - 1 else d i

/-- Pre-fix minting is a function of (tid, position): the same pair on a rerun
gives the same id. Run 1 minted `m tid 1` once; a new copy of `tid` arrives;
run 2 mints `m tid 1` again → count 2 (the file is refused on load). -/
theorem old_mint_collides {I : Type} [DecidableEq I] (m : I → Nat → I) (tid : I)
    (d : Disk I) (hne : m tid 1 ≠ tid) (h0 : d (m tid 1) = 0) :
    let d1 := reassign d tid (m tid 1)
    let d2 := reassign (fun i => if i = tid then d1 i + 1 else d1 i) tid (m tid 1)
    d2 (m tid 1) = 2 := by
  simp [reassign, h0, hne]

/-- Fixed minting: `fresh` never returns an id already on disk. Then the minted
id occurs exactly once, however many runs came before. -/
theorem fresh_mint_unique {I : Type} [DecidableEq I] (fresh : Disk I → I)
    (hfresh : ∀ d : Disk I, d (fresh d) = 0) (d : Disk I) (tid : I) :
    reassign d tid (fresh d) (fresh d) = 1 := by
  simp [reassign, hfresh]

/-- …and every other id's count is unchanged except the reassigned one, which
drops by one: minting never creates a duplicate. -/
theorem fresh_mint_no_new_dup {I : Type} [DecidableEq I] (fresh : Disk I → I)
    (_hfresh : ∀ d : Disk I, d (fresh d) = 0) (d : Disk I) (tid i : I) (hi : i ≠ fresh d) :
    reassign d tid (fresh d) i ≤ d i := by
  simp only [reassign, hi, ite_false]
  split <;> omega


/-! ## 5. Owner decisions G1, G2, G5, G4 (2026-09-23)

G1 (warn only): the adapter (`_transition_warning`) warns iff the requested
move is a real move outside its DIP-0009 relation, and then performs exactly
what it performed before. G2: that relation is the v2.0 table plus
DEFERRED→CANCELLED (`DIP0009_V2_TRANSITIONS`). -/

/-- The adapter's relation: the v2.0 table plus DEFERRED→CANCELLED (G2). -/
def specG2 (a b : St) : Bool := spec a b || (a == .deferred && b == .cancelled)

/-- `_transition_warning`: a same-state request is a no-op, not a move. -/
def warn (a b : St) : Bool := a != b && !specG2 a b

/-- G1, the warning predicate: warn ⇔ the move is not in the spec relation. -/
theorem warn_iff_not_spec (a b : St) : warn a b = true ↔ (a ≠ b ∧ specG2 a b = false) := by
  cases a <;> cases b <;> decide

/-- G2: DEFERRED→CANCELLED is in the relation, so it never warns. -/
theorem deferred_cancelled_no_warn : specG2 .deferred .cancelled = true ∧ warn .deferred .cancelled = false := by
  decide

/-- G2 adds exactly one move to the v2.0 table. -/
theorem specG2_exact (a b : St) :
    specG2 a b = true ↔ (spec a b = true ∨ (a = .deferred ∧ b = .cancelled)) := by
  cases a <;> cases b <;> decide

/-- The adapter call: the org-workspace outcome (unchanged) plus the warning flag. -/
def adapterTransition (t : Task) (b : St) : Option St × Bool := (wsTransition t b, warn t.st b)

/-- G1 "then performs it as before": the warning never changes the outcome. -/
theorem warn_performs_as_before (t : Task) (b : St) :
    (adapterTransition t b).1 = wsTransition t b := rfl

/-- No spec move ever warns. -/
theorem spec_never_warns (a b : St) (h : specG2 a b = true) : warn a b = false := by
  simp [warn, h]

/-- Among the seven v2.0 states, the moves that are performed AND warned are
exactly the excess of `canonical_excess` minus DEFERRED→CANCELLED (G2). -/
theorem canonical_warned :
    (canonical.flatMap fun a => (canonical.filter fun b => code a b && warn a b).map (a, ·)) =
    [(.review, .todo), (.review, .waiting),
     (.deferred, .next), (.deferred, .waiting), (.deferred, .review),
     (.deferred, .done)] := by decide

/-! G5: `StateConfig.default()` in the org-workspace repo now holds the seven
v2.0 states only. The code relation keeps its shape (any non-terminal state to
any other state of the sequence), restricted to non-retired states. DEFERRED is
done-class for parsing (`env_keys`) but is not terminal, so it can still wake. -/

def codeV2 (a b : St) : Bool := !a.retired && !b.retired && code a b

def doneClassV2 : St → Bool
  | .done | .deferred | .cancelled => true
  | _ => false

/-- No retired state is reachable, or left, under the v2.0 default. -/
theorem codeV2_no_retired (a b : St) (h : codeV2 a b = true) :
    a.retired = false ∧ b.retired = false := by
  cases a <;> cases b <;> simp_all [codeV2, St.retired]

/-- Under the v2.0 default every warned move is one of six canonical pairs. -/
theorem codeV2_warn_exact (a b : St) :
    (codeV2 a b = true ∧ warn a b = true) ↔
    ((a = .review ∧ (b = .todo ∨ b = .waiting)) ∨
     (a = .deferred ∧ (b = .next ∨ b = .waiting ∨ b = .review ∨ b = .done))) := by
  cases a <;> cases b <;> decide

/-- DEFERRED is done-class yet non-terminal: it wakes to TODO without warning. -/
theorem deferred_done_class_wakes :
    doneClassV2 .deferred = true ∧ codeTerminal .deferred = false ∧
    codeV2 .deferred .todo = true ∧ warn .deferred .todo = false := by decide

/-! G4: `task_cleanup.plan_dup_ids` skips every id group that has a copy in a
generated next_actions.org. A copy is (file, generated flag); the plan lists the
copies it re-ids (the canonical copy, first, is kept). -/

structure Copy (F : Type) where
  file : F
  generated : Bool

def planGroup {F : Type} (copies : List (Copy F)) : List (Copy F) :=
  if copies.any (·.generated) then [] else copies.drop 1

/-- G4: nothing is re-id'd in a group that touches a generated file… -/
theorem plan_skips_generated {F : Type} (cs : List (Copy F)) (h : ∃ c ∈ cs, c.generated = true) :
    planGroup cs = [] := by
  obtain ⟨c, hc, hg⟩ := h
  have : cs.any (·.generated) = true := List.any_eq_true.mpr ⟨c, hc, hg⟩
  simp [planGroup, this]

/-- …so no re-id'd copy is ever a generated one, and every re-id'd copy's group
is free of generated files. -/
theorem plan_never_touches_generated {F : Type} (cs : List (Copy F)) (c : Copy F)
    (h : c ∈ planGroup cs) : ∀ d ∈ cs, d.generated = false := by
  intro d hd
  unfold planGroup at h
  split at h
  · simp at h
  · rename_i hany
    cases hdg : d.generated
    · rfl
    · exact absurd (List.any_eq_true.mpr ⟨d, hd, hdg⟩) hany

end DatacoreSpec.GtdState
