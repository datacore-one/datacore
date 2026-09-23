/-!
# Reconcile — `gh_reconcile.py` closure decisions and `sync/conflict.py` merge

## Part 1: gh_reconcile "Never a false DONE"

`reconcile_file` Path A, branch for branch (the fixed code), plus Path B (the
NIGHTSHIFT_OUTPUT archive route), and the old code for the refutations.
Decisions applied 2026-09-23: P5 (Path B only when no ref is open or unknown)
and P6 (an issue closed as a duplicate is dropped → CANCELLED), and Q9
(its reason names the canonical issue when GitHub recorded one).

Abstractions:
* A ref's true GitHub state is `truth r : RS`. `open` covers every non-terminal
  state (open, draft, closed-unmerged PR: none of them proves done or
  will-not-do). `done` = merged PR or issue closed `completed` (or with no
  recorded reason). `dropped` = issue closed `not_planned`, or (decision P6)
  closed as a `duplicate`: both are "will not do as written" and CANCELLED.
* The lookup `check_github_ref` is an oracle `look r : Option RS`: `none` is a
  failed call (auth, timeout, 5xx, 404). The only assumption is that a lookup
  that answers answers truthfully (`Sound`). Theorems hold for every such oracle.
* The archive check is a Bool: its basename identity was DOWNGRADED (names carry
  a uuid4 since output.py `generate_exec_id`), so it is taken as given.

## Part 2: the read-modify-write

The file is a value. The run decides on snapshot `s0`; by write time the file
holds `cur`. The old code wrote `f s0` whatever `cur` was; the fixed code is a
compare-and-swap (org_transaction's serialized writer + digest check).

## Part 3: three-way merge spec for a future sync engine

`sync/conflict.py`'s ConflictDetector was two-way (compared org vs external,
ignored `last_sync`). Decision P7 (2026-09-23) removed it and ConflictResolver,
since only the stub `SyncEngine` referenced them. `resolve3` below is kept as
THE SPECIFICATION for when the engine is built: it needs the base = the value
both sides held at the last successful sync, stored per task and field.
-/

namespace DatacoreSpec.Reconcile

/-! ## Part 1 -/

inductive RS | open | done | dropped
  deriving DecidableEq, Repr

inductive Verdict | skip | done | cancelled
  deriving DecidableEq, Repr

/-- A lookup that answers, answers truthfully. -/
def Sound {R : Type} (truth : R → RS) (look : R → Option RS) : Prop :=
  ∀ r s, look r = some s → truth r = s

/-- Path B: the archived-output check itself. -/
def pathB (archived : Bool) : Verdict := if archived then .done else .skip

/-- The lookup answered terminal (merged / completed / not planned). -/
def isTerm : Option RS → Bool
  | some .done => true | some .dropped => true | _ => false
/-- The lookup blocks a close: answered open, or failed (`result is None`). -/
def isBlk : Option RS → Bool
  | some .open => true | none => true | _ => false
def isOpen : Option RS → Bool
  | some .open => true | _ => false
def isDone : Option RS → Bool
  | some .done => true | _ => false
def isDropped : Option RS → Bool
  | some .dropped => true | _ => false

/-- Old Path A (pre-fix): failed lookups dropped (`continue`), not-planned counted
as a plain terminal ⇒ DONE; only "terminal and open" blocked. -/
def oldDecide {R : Type} (look : R → Option RS) (refs : List R) (archived : Bool) : Verdict :=
  if refs.any (fun r => isTerm (look r)) && !refs.any (fun r => isOpen (look r)) then .done
  else if refs.any (fun r => isTerm (look r)) then .skip
  else pathB archived

/-- Fixed Path A. `unknown_refs` block like open ones; one close kind required.
(Python takes the close kind over `terminal_refs`; on this branch no ref is
blocked, so every ref is terminal and `refs.all` is the same set.)
Decision P5: Path B runs only when no ref is open or unknown; after the first
two branches no ref is terminal, so that means the task has no refs. -/
def newDecide {R : Type} (look : R → Option RS) (refs : List R) (archived : Bool) : Verdict :=
  if refs.any (fun r => isTerm (look r)) && refs.any (fun r => isBlk (look r)) then .skip
  else if refs.any (fun r => isTerm (look r)) then
    (if refs.all (fun r => isDone (look r)) then .done
     else if refs.all (fun r => isDropped (look r)) then .cancelled
     else .skip)
  else if refs.any (fun r => isBlk (look r)) then .skip
  else pathB archived

/-- The decision code before P5: Path B ran over open and unknown refs. -/
def preP5Decide {R : Type} (look : R → Option RS) (refs : List R) (archived : Bool) : Verdict :=
  if refs.any (fun r => isTerm (look r)) && refs.any (fun r => isBlk (look r)) then .skip
  else if refs.any (fun r => isTerm (look r)) then
    (if refs.all (fun r => isDone (look r)) then .done
     else if refs.all (fun r => isDropped (look r)) then .cancelled
     else .skip)
  else pathB archived

theorem isDone_sound {R : Type} (truth : R → RS) (look : R → Option RS)
    (hs : Sound truth look) (r : R) (h : isDone (look r) = true) : truth r = .done := by
  cases hl : look r with
  | none => simp [hl, isDone] at h
  | some s => cases s <;> simp [hl, isDone] at h; exact hs r _ hl

theorem isDropped_sound {R : Type} (truth : R → RS) (look : R → Option RS)
    (hs : Sound truth look) (r : R) (h : isDropped (look r) = true) : truth r = .dropped := by
  cases hl : look r with
  | none => simp [hl, isDropped] at h
  | some s => cases s <;> simp [hl, isDropped] at h; exact hs r _ hl

/-- **Never a false DONE (Path A).** If the fixed code closes a task DONE and
some ref answered terminal (so the GitHub route decided), every ref is truly
done. -/
theorem new_done_sound {R : Type} (truth : R → RS) (look : R → Option RS)
    (hs : Sound truth look) (refs : List R) (archived : Bool)
    (hterm : refs.any (fun r => isTerm (look r)) = true)
    (h : newDecide look refs archived = .done) :
    ∀ r ∈ refs, truth r = .done := by
  unfold newDecide at h
  rw [hterm] at h
  cases hb : refs.any (fun r => isBlk (look r)) <;> simp only [hb, Bool.true_and] at h
  · simp only [Bool.false_eq_true, ↓reduceIte] at h
    cases ha : refs.all (fun r => isDone (look r))
    · simp only [ha] at h
      split at h
      · rename_i hf; exact absurd hf (by decide)
      · split at h <;> simp at h
    · intro r hr
      exact isDone_sound truth look hs r (List.all_eq_true.mp ha r hr)
  · simp at h

/-- **Never a false CANCELLED.** A CANCELLED verdict means there is a ref and
every ref is truly closed not-planned. -/
theorem new_cancelled_sound {R : Type} (truth : R → RS) (look : R → Option RS)
    (hs : Sound truth look) (refs : List R) (archived : Bool)
    (h : newDecide look refs archived = .cancelled) :
    refs ≠ [] ∧ ∀ r ∈ refs, truth r = .dropped := by
  unfold newDecide at h
  cases ht : refs.any (fun r => isTerm (look r))
  · cases hb : refs.any (fun r => isBlk (look r)) <;>
      simp only [ht, hb, Bool.false_and, Bool.false_eq_true, ↓reduceIte, pathB] at h <;>
      (try split at h) <;> simp at h
  · cases hb : refs.any (fun r => isBlk (look r))
    · simp only [ht, hb, Bool.and_false, Bool.false_eq_true, ↓reduceIte] at h
      cases ha : refs.all (fun r => isDone (look r))
      · cases hd : refs.all (fun r => isDropped (look r))
        · simp [ha, hd] at h
        · refine ⟨?_, ?_⟩
          · intro hnil; simp [hnil] at ht
          · intro r hr
            exact isDropped_sound truth look hs r (List.all_eq_true.mp hd r hr)
      · simp [ha] at h
    · simp [ht, hb] at h

/-- Non-vacuity: all refs answered done ⇒ the fixed code does close. -/
theorem new_closes_when_proven {R : Type} (look : R → Option RS) (r : R) (refs : List R)
    (archived : Bool) (hall : ∀ x ∈ r :: refs, look x = some .done) :
    newDecide look (r :: refs) archived = .done := by
  have ht : (r :: refs).any (fun x => isTerm (look x)) = true :=
    List.any_eq_true.mpr ⟨r, List.mem_cons_self, by simp [hall r List.mem_cons_self, isTerm]⟩
  have hb : (r :: refs).any (fun x => isBlk (look x)) = false := by
    apply Bool.eq_false_iff.mpr
    intro h
    obtain ⟨x, hx, hbx⟩ := List.any_eq_true.mp h
    simp [hall x hx, isBlk] at hbx
  have ha : (r :: refs).all (fun x => isDone (look x)) = true :=
    List.all_eq_true.mpr (fun x hx => by simp [hall x hx, isDone])
  unfold newDecide
  simp [ht, hb, ha]

/-! ### Q9: a duplicate's :CANCEL_REASON: names the canonical issue

Decision Q9 (2026-09-23). For each ref closed as a duplicate,
`canonical_of_duplicate` asks GitHub for the MarkedAsDuplicate timeline event:
an oracle `canon : R → Option R` (`none` = no event, an undone event, or a
failed call). The reason names `canon r` when there is one, else `r` itself
(the P6 wording). The lookup words the reason only; it is not an input to the
verdict. -/

/-- The issue a duplicate ref's reason names. -/
def cancelNames {R : Type} (canon : R → Option R) (r : R) : R := (canon r).getD r

theorem q9_names_canonical {R : Type} (canon : R → Option R) (r c : R)
    (h : canon r = some c) : cancelNames canon r = c := by
  simp [cancelNames, h]

theorem q9_fallback_names_duplicate {R : Type} (canon : R → Option R) (r : R)
    (h : canon r = none) : cancelNames canon r = r := by
  simp [cancelNames, h]

/-- `reconcile_file` with the reason: the verdict and the issue each ref's
reason names. -/
def decideWithReason {R : Type} (look : R → Option RS) (canon : R → Option R) (refs : List R)
    (archived : Bool) : Verdict × List R :=
  (newDecide look refs archived, refs.map (cancelNames canon))

/-- **The lookup never changes a verdict**: a failed or missing canonical
lookup leaves the same DONE / CANCELLED / skip as a successful one, so
`new_cancelled_sound` and `new_done_sound_all` are untouched by Q9. -/
theorem q9_verdict_independent {R : Type} (look : R → Option RS) (c1 c2 : R → Option R)
    (refs : List R) (archived : Bool) :
    (decideWithReason look c1 refs archived).1 = (decideWithReason look c2 refs archived).1 := rfl

/-! ### Counterexamples against the old code (replayed in Python, see findings) -/

/-- Refs 0 and 1: 0 is a merged PR, 1 is an open issue whose lookup failed. -/
def cexTruth : Nat → RS | 0 => .done | _ => .open
def cexLookUnknown : Nat → Option RS | 0 => some .done | _ => none

theorem cexLookUnknown_sound : Sound cexTruth cexLookUnknown := by
  intro r s h
  match r with
  | 0 => simp [cexLookUnknown] at h; simp [cexTruth, h]
  | _ + 1 => simp [cexLookUnknown] at h

/-- R1: old code closes DONE though ref 1 is open. -/
theorem old_false_done_unknown :
    oldDecide cexLookUnknown [0, 1] false = .done ∧ cexTruth 1 ≠ .done := by
  decide

theorem new_blocks_unknown : newDecide cexLookUnknown [0, 1] false = .skip := by decide

/-- R3: an issue closed not-planned: old says DONE, the truth is dropped. -/
def cexDropped : Nat → RS := fun _ => .dropped
theorem old_false_done_not_planned :
    oldDecide (fun n => some (cexDropped n)) [0] false = .done ∧ cexDropped 0 ≠ .done := by
  decide
theorem new_not_planned_cancelled :
    newDecide (fun n => some (cexDropped n)) [0] false = .cancelled := by decide

/-- R2, decision P5: an open PR plus an archived nightshift output. Before P5
Path B closed it DONE; now the open ref keeps it open. The same holds for a
ref whose lookup failed. -/
theorem pathB_open_ref_kept_open :
    newDecide (fun _ => some RS.open) [0] true = .skip ∧
    newDecide (fun _ => (none : Option RS)) [0] true = .skip ∧
    preP5Decide (fun _ => some RS.open) [0] true = .done ∧
    oldDecide (fun _ => some RS.open) [0] true = .done := by decide

/-- Non-vacuity of Path B after P5: a task with no refs still closes on its
archived output. -/
theorem pathB_closes_without_refs {R : Type} (look : R → Option RS) :
    newDecide look [] true = .done := by simp [newDecide, pathB]

theorem not_term_not_blk (o : Option RS) (ht : isTerm o = false) (hb : isBlk o = false) :
    False := by
  cases o with
  | none => simp [isBlk] at hb
  | some s => cases s <;> simp_all [isTerm, isBlk]

/-- **Never a false DONE, whichever path decides (decision P5).** Every DONE
the fixed code produces, through the GitHub refs or through the archived
output, leaves no tracked ref that is not truly done. Before P5 this failed:
`pathB_open_ref_kept_open` (third conjunct) is the counterexample. -/
theorem new_done_sound_all {R : Type} (truth : R → RS) (look : R → Option RS)
    (hs : Sound truth look) (refs : List R) (archived : Bool)
    (h : newDecide look refs archived = .done) :
    ∀ r ∈ refs, truth r = .done := by
  cases ht : refs.any (fun r => isTerm (look r))
  · have hb : refs.any (fun r => isBlk (look r)) = false := by
      cases hb : refs.any (fun r => isBlk (look r))
      · rfl
      · unfold newDecide at h; simp [ht, hb] at h
    intro r hr
    have h1 : isTerm (look r) = false := by
      cases e : isTerm (look r)
      · rfl
      · have : refs.any (fun r => isTerm (look r)) = true := List.any_eq_true.mpr ⟨r, hr, e⟩
        rw [ht] at this; cases this
    have h2 : isBlk (look r) = false := by
      cases e : isBlk (look r)
      · rfl
      · have : refs.any (fun r => isBlk (look r)) = true := List.any_eq_true.mpr ⟨r, hr, e⟩
        rw [hb] at this; cases this
    exact (not_term_not_blk _ h1 h2).elim
  · exact new_done_sound truth look hs refs archived ht h

/-! ## Part 2: compare-and-swap write -/

def oldWrite {S : Type} (f : S → S) (s0 _cur : S) : S := f s0
def casWrite {S : Type} [DecidableEq S] (f : S → S) (s0 cur : S) : S :=
  if cur = s0 then f s0 else cur

/-- The fixed write never erases a concurrent edit: if the file changed after the
snapshot, it is left exactly as the other writer left it. -/
theorem cas_preserves_concurrent_edit {S : Type} [DecidableEq S] (f : S → S) (s0 cur : S)
    (h : cur ≠ s0) : casWrite f s0 cur = cur := by
  simp [casWrite, h]

theorem cas_applies_when_unchanged {S : Type} [DecidableEq S] (f : S → S) (s0 : S) :
    casWrite f s0 s0 = f s0 := by simp [casWrite]

/-- R4: the old write drops a concurrent edit (files as Nat; the edit is 7→8). -/
theorem old_write_loses_edit : oldWrite (fun _ => 100) 7 8 ≠ 8 := by decide

/-! ## Part 3: three-way merge for sync/conflict.py -/

inductive Strategy | orgWins | extWins | ask
  deriving DecidableEq, Repr

/-- Per-field three-way resolution; `none` = queued for a human. -/
def resolve3 {α : Type} [DecidableEq α] (st : Strategy) (base org ext : α) : Option α :=
  if org = ext then some org
  else if org = base then some ext        -- only external changed: take it
  else if ext = base then some org        -- only org changed: push it
  else match st with                      -- both changed: a real conflict
    | .orgWins => some org
    | .extWins => some ext
    | .ask => none

/-- What the removed ConflictDetector + ConflictResolver did (decision P7):
two-way; any difference is a conflict. Kept as the refuted baseline. -/
def resolve2 {α : Type} [DecidableEq α] (st : Strategy) (org ext : α) : Option α :=
  if org = ext then some org
  else match st with
    | .orgWins => some org
    | .extWins => some ext
    | .ask => none

/-- **A change made on only one side is never overridden**, whatever strategy is
configured. -/
theorem resolve3_respects_one_sided {α : Type} [DecidableEq α] (st : Strategy)
    (base org ext : α) :
    (org = base → resolve3 st base org ext = some ext) ∧
    (ext = base → resolve3 st base org ext = some org) := by
  constructor
  · intro ho; subst ho
    unfold resolve3; by_cases h : org = ext <;> simp [h]
  · intro he; subst he
    unfold resolve3; by_cases h : org = ext
    · simp [h]
    · by_cases h2 : org = ext <;> simp [h, h2]

/-- The strategy is consulted only on a true both-sides conflict. -/
theorem resolve3_strategy_only_on_conflict {α : Type} [DecidableEq α] (base org ext : α)
    (hne : resolve3 .orgWins base org ext ≠ resolve3 .extWins base org ext) :
    org ≠ base ∧ ext ≠ base ∧ org ≠ ext := by
  unfold resolve3 at hne
  refine ⟨?_, ?_, ?_⟩ <;> intro h <;> subst h <;> simp_all

/-- Refutation of the two-way detector: a human closes the issue on GitHub (org
untouched since the last sync); ORG_WINS pushes "open" back — the reopen. -/
theorem resolve2_reopens_human_close :
    let base := RS.open; let org := RS.open; let ext := RS.done
    org = base ∧ resolve2 .orgWins org ext = some RS.open ∧
      resolve3 .orgWins base org ext = some RS.done := by
  decide

end DatacoreSpec.Reconcile
