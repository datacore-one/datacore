import LedgerSpec.Item

/-!
# Convergence — `read_events` + `fold`

Every host computes `fold(sorted(union of per-writer files, key=hlc))`. The
claim in DIP-0046 is "every host folds to the same winner". This file proves
the two facts that claim rests on, and pins down its precondition.

1. **Per-item projection.** The whole-state fold, restricted to one item id, is
   exactly the item state machine of `Item.lean` run over that item's events.
   So every lifecycle theorem there is a theorem about the real fold.
2. **Order-independence.** Two hosts holding the same events, in any file
   arrival order, produce the same sorted list — and so the same state — as
   long as no two DISTINCT events share a sort key. With a tie, the sort is
   stable and the order depends on input order (counterexample below). In
   Python that input order is `sorted(glob('*.jsonl'))` then file order, which
   is identical on every host, so ties do not break convergence — but they do
   mean the winner of a tie is decided by FILE NAME, not by time.
-/

namespace LedgerSpec.Converge
open LedgerSpec.Item

variable {P F : Type}

/-! ## 1. Whole-state fold and its per-item projection -/

/-- An event as the fold sees it: a target id (`none` = missing/invalid id,
which `_valid_item_id` routes to orphans) and either a create or a handler event. -/
structure GEv (P F : Type) where
  id   : Option String
  body : Sum (Option Actor × P) (Ev F)

/-- What one event does to the one item it addresses (`_handle_create`, or
`_get_item_or_orphan` followed by the handler). -/
def onItem (o : Oracle P F) : Option (Item P) → Sum (Option Actor × P) (Ev F) → Option (Item P)
  | none,   .inl (ow, p) => some { fresh P p with owner := ow }
  | some i, .inl _       => some i          -- "no-op (item already exists)"
  | none,   .inr _       => none            -- orphan: never invented into state
  | some i, .inr e       => some (step o i e)

def gstep (o : Oracle P F) (s : String → Option (Item P)) (g : GEv P F) :
    String → Option (Item P) :=
  match g.id with
  | none => s
  | some k => fun k' => if k' = k then onItem o (s k) g.body else s k'

def eventsFor (k : String) (gs : List (GEv P F)) : List (Sum (Option Actor × P) (Ev F)) :=
  gs.filterMap fun g => if g.id = some k then some g.body else none

/-- **Frame / projection.** Item `k` after the whole fold equals item `k`'s own
event stream run through the per-item machine. Events for other ids, and
events with invalid ids, never touch it. -/
theorem fold_project (o : Oracle P F) (k : String) :
    ∀ (gs : List (GEv P F)) (s : String → Option (Item P)),
      (gs.foldl (gstep o) s) k = (eventsFor k gs).foldl (onItem o) (s k) := by
  intro gs
  induction gs with
  | nil => intro s; rfl
  | cons g gs ih =>
    intro s
    rw [List.foldl_cons, ih]
    unfold eventsFor gstep
    cases hid : g.id with
    | none => simp [hid]
    | some k' =>
      by_cases hk : k = k'
      · subst hk; simp [List.filterMap_cons, hid]
      · simp [List.filterMap_cons, hid, hk, Ne.symm hk]

/-! ## 2. Sorting a permutation is unique when keys are unique -/

/-- A list sorted by a relation that is antisymmetric on its elements is
determined by its elements. -/
theorem eq_of_perm_of_pairwise {α : Type} {r : α → α → Prop} :
    ∀ {l1 l2 : List α}, l1.Perm l2 → l1.Pairwise r → l2.Pairwise r →
      (∀ a ∈ l1, ∀ b ∈ l1, r a b → r b a → a = b) → l1 = l2
  | [], [], _, _, _, _ => rfl
  | [], _ :: _, hp, _, _, _ => absurd hp.length_eq (by simp)
  | _ :: _, [], hp, _, _, _ => absurd hp.length_eq (by simp)
  | a :: l1, b :: l2, hp, h1, h2, hanti => by
    have hab : a = b := by
      apply Classical.byContradiction
      intro hne
      have ha : a ∈ l2 := by
        have := hp.mem_iff.1 (List.mem_cons_self (a := a) (l := l1))
        simp at this; rcases this with h | h
        · exact absurd h hne
        · exact h
      have hb : b ∈ l1 := by
        have := hp.mem_iff.2 (List.mem_cons_self (a := b) (l := l2))
        simp at this; rcases this with h | h
        · exact absurd h.symm hne
        · exact h
      have rab := (List.pairwise_cons.1 h1).1 b hb
      have rba := (List.pairwise_cons.1 h2).1 a ha
      exact hne (hanti a (by simp) b (by simp [hb]) rab rba)
    subst hab
    congr 1
    exact eq_of_perm_of_pairwise hp.cons_inv (List.pairwise_cons.1 h1).2
      (List.pairwise_cons.1 h2).2
      (fun x hx y hy => hanti x (by simp [hx]) y (by simp [hy]))

/-- **Convergence.** Hosts that hold the same events (as a multiset: any
permutation, i.e. any glob/arrival order) fold to the same state, provided the
sort key is a total preorder and no two distinct events tie. -/
theorem converge {α σ : Type} (le : α → α → Bool)
    (trans : ∀ a b c, le a b = true → le b c = true → le a c = true)
    (total : ∀ a b, (le a b || le b a) = true)
    (fold : List α → σ) {l1 l2 : List α} (hp : l1.Perm l2)
    (hunique : ∀ a ∈ l1, ∀ b ∈ l1, le a b = true → le b a = true → a = b) :
    fold (l1.mergeSort le) = fold (l2.mergeSort le) := by
  congr 1
  apply eq_of_perm_of_pairwise (r := fun a b => le a b = true)
  · exact (List.mergeSort_perm l1 le).trans (hp.trans (List.mergeSort_perm l2 le).symm)
  · exact List.pairwise_mergeSort trans total l1
  · exact List.pairwise_mergeSort trans total l2
  · intro a ha b hb; exact hunique a (by simpa using ha) b (by simpa using hb)

/-- The precondition is necessary: with a tie, a stable sort keeps input order,
so two arrival orders give two different lists. (Python ties are broken by
`sorted(glob)` file order, which every host shares — deterministic, but the
tie is won by the file name that sorts first, not the event that happened
first.) -/
theorem ties_break_order_independence :
    [(1, 0), (1, 1)].mergeSort (fun (x y : Nat × Nat) => decide (x.1 ≤ y.1)) ≠
    [(1, 1), (1, 0)].mergeSort (fun (x y : Nat × Nat) => decide (x.1 ≤ y.1)) := by
  -- both inputs are already sorted (the keys tie), so the stable sort returns each unchanged
  rw [List.mergeSort_of_pairwise (by decide), List.mergeSort_of_pairwise (by decide)]
  decide

end LedgerSpec.Converge
