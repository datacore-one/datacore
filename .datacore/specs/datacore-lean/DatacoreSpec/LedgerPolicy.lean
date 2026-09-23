import LedgerSpec.Item

/-!
# LedgerPolicy — delegation depth, approval retention, three-way merge, dispatch

Models, branch for branch:

* `claim_gate.check_create`'s hops rule and `ledger_claim.chain_follow_up`
  (section `Hops`);
* `ledger/policy._guarded_append_locked`'s `previously_approved` /
  `needs_cosign` decision for `item.update` and `item.claim` (section `Approval`);
* `ledger/edits.merge_values` and `projection_state.guard_projection`
  (section `Merge`);
* `ledger_claim.main`'s dead-letter counter and the per-host claim guard,
  old (`claimGuard`) and after decision L4 (`dispatchOk`, the exact-writer rule
  of `actor_identity.dispatchable_by`) (section `Dispatch`).

Abstractions: payload contents that the properties do not depend on are
opaque; Python `==` on non-dict JSON values is an arbitrary relation `peq`
(assumed an equivalence only where a theorem says so — Python `NaN` breaks
reflexivity, see findings).
-/

namespace DatacoreSpec.LedgerPolicy

/-! ## Merge — `ledger/edits.merge_values` -/
namespace Merge

/-- A JSON value as `merge_values` sees it. `absent` is the Python sentinel
`object()` the dict branch uses for a missing key; an object maps every key
(missing keys to `absent`). Every non-dict value (null, bool, number, string,
list) is an `atom`: `merge_values` only ever compares those with `==`. -/
inductive J (A : Type) where
  | absent
  | atom (a : A)
  | obj (f : String → J A)

variable {A : Type} (peq : A → A → Prop)

/-- Python `==` lifted to `J`: dicts are equal iff equal at every key (absent
keys included), atoms by `peq`, the sentinel only to itself. -/
def Eqv : J A → J A → Prop
  | .absent, .absent => True
  | .atom a, .atom b => peq a b
  | .obj f, .obj g => ∀ k, Eqv (f k) (g k)
  | _, _ => False

open Classical in
/-- `merge_values(base, local, remote)`; `none` is `raise EditConflict`.

Branch for branch: (1) `local == base or local == remote` → `remote`;
(2) all three dicts → merge every key (see `Merge` docstring below for why
"every key" equals Python's loop over `base ∪ local`); (3) otherwise
`remote != base` → conflict, else `local`.

Why the per-key loop is `merge` at every key: Python keeps `remote[k]` when
`before == now or now == live` — so does branch (1); a key in neither base
nor local is `absent == absent` — branch (1) again; `now is absent` →
conflict iff `live` present and `!= before`, else pop — branch (3) with
`l = absent`; `before is absent` → conflict iff `live` present and `!= now`
(given `now != live`), else set — branch (3) with `b = absent`; `live is
absent` → conflict — branch (3) with `r = absent`, `b` present; otherwise the
recursive call. -/
noncomputable def merge : J A → J A → J A → Option (J A)
  | b, l, r =>
    if Eqv peq l b ∨ Eqv peq l r then some r
    else match b, l, r with
      | .obj fb, .obj fl, .obj fr =>
        if ∀ k, (merge (fb k) (fl k) (fr k)).isSome
        then some (.obj fun k => (merge (fb k) (fl k) (fr k)).getD .absent)
        else none
      | _, _, _ => if Eqv peq r b then some l else none


section Laws
variable {peq}
variable (hr : ∀ a, peq a a) (hs : ∀ a b, peq a b → peq b a)
  (ht : ∀ a b c, peq a b → peq b c → peq a c)
include hr in
theorem eqv_refl (x : J A) : Eqv peq x x := by
  induction x with
  | absent => trivial
  | atom a => exact hr a
  | obj f ih => exact fun k => ih k

include hs in
theorem eqv_symm (x y : J A) (h : Eqv peq x y) : Eqv peq y x := by
  induction x generalizing y with
  | absent => cases y <;> simp_all [Eqv]
  | atom a => cases y <;> simp_all [Eqv]
  | obj f ih => cases y <;> simp_all [Eqv]

include ht in
theorem eqv_trans (x y z : J A) (h1 : Eqv peq x y) (h2 : Eqv peq y z) : Eqv peq x z := by
  induction x generalizing y z with
  | absent => cases y <;> cases z <;> simp_all [Eqv]
  | atom a => cases y <;> cases z <;> simp_all [Eqv]; exact ht _ _ _ h1 h2
  | obj f ih =>
    cases y <;> cases z <;> simp_all [Eqv]
    intro k; exact ih k _ _ (h1 k) (h2 k)

end Laws

open Classical

/-! Three equations, one per Python branch. -/

theorem merge_first {b l r : J A} (c : Eqv peq l b ∨ Eqv peq l r) :
    merge peq b l r = some r := by
  rw [merge.eq_def]; simp [c]

theorem merge_obj {fb fl fr : String → J A}
    (c : ¬(Eqv peq (.obj fl) (.obj fb) ∨ Eqv peq (.obj fl) (.obj fr))) :
    merge peq (.obj fb) (.obj fl) (.obj fr) =
      if ∀ k, (merge peq (fb k) (fl k) (fr k)).isSome
      then some (.obj fun k => (merge peq (fb k) (fl k) (fr k)).getD .absent)
      else none := by
  rw [merge.eq_def]; simp only [c, ite_false]

def AllObj : J A → J A → J A → Prop
  | .obj _, .obj _, .obj _ => True
  | _, _, _ => False

theorem merge_tail {b l r : J A} (c : ¬(Eqv peq l b ∨ Eqv peq l r)) (hno : ¬AllObj b l r) :
    merge peq b l r = if Eqv peq r b then some l else none := by
  rw [merge.eq_def]; simp only [c, ite_false]
  split
  · exact absurd trivial hno
  · rfl

theorem allObj_iff {b l r : J A} :
    AllObj b l r ↔ ∃ fb fl fr, b = .obj fb ∧ l = .obj fl ∧ r = .obj fr := by
  cases b <;> cases l <;> cases r <;> simp [AllObj]

section Laws2
variable {peq}
variable (hr : ∀ a, peq a a) (hs : ∀ a b, peq a b → peq b a)
  (ht : ∀ a b c, peq a b → peq b c → peq a c)

include hr in
/-- **Identity law 1, exact.** `merge(b, b, r) = r`. -/
theorem merge_base_local (b r : J A) : merge peq b b r = some r :=
  merge_first peq (Or.inl (eqv_refl hr b))

include hr hs ht in
theorem remote_base_step {b l r : J A} (hrb : Eqv peq r b)
    (hobj : ∀ fb fl fr, b = .obj fb → l = .obj fl → r = .obj fr →
      ∃ m, merge peq b l r = some m ∧ Eqv peq m l) :
    ∃ m, merge peq b l r = some m ∧ Eqv peq m l := by
  by_cases c : Eqv peq l b ∨ Eqv peq l r
  · refine ⟨r, merge_first peq c, ?_⟩
    rcases c with c | c
    · exact eqv_trans ht _ _ _ hrb (eqv_symm hs _ _ c)
    · exact eqv_symm hs _ _ c
  · by_cases ho : AllObj b l r
    · obtain ⟨fb, fl, fr, rfl, rfl, rfl⟩ := allObj_iff.mp ho
      exact hobj _ _ _ rfl rfl rfl
    · rw [merge_tail peq c ho]; simp only [hrb, ite_true]; exact ⟨l, rfl, eqv_refl hr l⟩

include hr hs ht in
/-- **Identity law 2, up to Python equality.** `merge(b, l, r)` with `r == b`
succeeds and is `== l`; in particular `merge(b, l, b) == l`. -/
theorem merge_remote_base (b : J A) :
    ∀ l r, Eqv peq r b → ∃ m, merge peq b l r = some m ∧ Eqv peq m l := by
  induction b with
  | absent => intro l r h; exact remote_base_step hr hs ht h (by intros; contradiction)
  | atom a => intro l r h; exact remote_base_step hr hs ht h (by intros; contradiction)
  | obj fb ih =>
    intro l r h
    refine remote_base_step hr hs ht h ?_
    rintro _ fl fr ⟨⟩ rfl rfl
    by_cases c : Eqv peq (.obj fl) (.obj fb) ∨ Eqv peq (.obj fl) (.obj fr)
    · exact ⟨_, merge_first peq c, by
        rcases c with c | c
        · exact eqv_trans ht _ _ _ h (eqv_symm hs _ _ c)
        · exact eqv_symm hs _ _ c⟩
    · have hk : ∀ k, ∃ m, merge peq (fb k) (fl k) (fr k) = some m ∧ Eqv peq m (fl k) :=
        fun k => ih k _ _ (h k)
      rw [merge_obj peq c]
      split
      · refine ⟨_, rfl, fun k => ?_⟩
        obtain ⟨m, hm, he⟩ := hk k
        simpa [hm] using he
      · rename_i hn; exact absurd (fun k => by obtain ⟨m, hm, _⟩ := hk k; simp [hm]) hn

include hr hs ht in
theorem merge_base_remote (b l : J A) : ∃ m, merge peq b l b = some m ∧ Eqv peq m l :=
  merge_remote_base hr hs ht b l b (eqv_refl hr b)

end Laws2

/-- Two merge outcomes agree: both conflict, or both succeed with
Python-equal results. -/
def SymOk : Option (J A) → Option (J A) → Prop
  | none, none => True
  | some a, some b => Eqv peq a b
  | _, _ => False

theorem symOk_isSome {x y : Option (J A)} (h : SymOk peq x y) : x.isSome = y.isSome := by
  cases x <;> cases y <;> simp_all [SymOk]

theorem symOk_getD {x y : Option (J A)} (h : SymOk peq x y) (hx : x.isSome) :
    Eqv peq (x.getD .absent) (y.getD .absent) := by
  cases x <;> cases y <;> simp_all [SymOk]

theorem allObj_swap {b l r : J A} (h : AllObj b l r) : AllObj b r l := by
  cases b <;> cases l <;> cases r <;> simp_all [AllObj]

section Symm
variable {peq}
variable (hr : ∀ a, peq a a) (hs : ∀ a b, peq a b → peq b a)
  (ht : ∀ a b c, peq a b → peq b c → peq a c)

include hr hs ht in
theorem symm_step {b l r : J A}
    (hobj : ¬(Eqv peq l b ∨ Eqv peq l r) → ¬(Eqv peq r b ∨ Eqv peq r l) → AllObj b l r →
      SymOk peq (merge peq b l r) (merge peq b r l)) :
    SymOk peq (merge peq b l r) (merge peq b r l) := by
  by_cases c1 : Eqv peq l b ∨ Eqv peq l r
  · rw [merge_first peq c1]
    by_cases hlr : Eqv peq l r
    · rw [merge_first peq (Or.inr (eqv_symm hs _ _ hlr))]; exact eqv_symm hs _ _ hlr
    · have hlb : Eqv peq l b := c1.resolve_right hlr
      obtain ⟨m, hm, he⟩ := merge_remote_base hr hs ht b r l hlb
      rw [hm]; exact eqv_symm hs _ _ he
  · by_cases c2 : Eqv peq r b ∨ Eqv peq r l
    · have hrb : Eqv peq r b :=
        c2.resolve_right (fun h => c1 (Or.inr (eqv_symm hs _ _ h)))
      rw [merge_first peq c2]
      obtain ⟨m, hm, he⟩ := merge_remote_base hr hs ht b l r hrb
      rw [hm]; exact he
    · by_cases ho : AllObj b l r
      · exact hobj c1 c2 ho
      · have ho' : ¬AllObj b r l := fun h => ho (allObj_swap h)
        rw [merge_tail peq c1 ho, merge_tail peq c2 ho']
        have h1 : ¬Eqv peq r b := fun h => c2 (Or.inl h)
        have h2 : ¬Eqv peq l b := fun h => c1 (Or.inl h)
        simp only [h1, h2, ite_false]; trivial

include hr hs ht in
/-- **Symmetry, including conflict symmetry.** Swapping the local and remote
sides never turns a conflict into a success or back, and a success into a
Python-equal result. (Not equal on the nose: see `bool_one_asymmetric`.) -/
theorem merge_symm (b : J A) : ∀ l r, SymOk peq (merge peq b l r) (merge peq b r l) := by
  induction b with
  | absent => intro l r; exact symm_step hr hs ht (by intro _ _ h; cases l <;> cases r <;> simp [AllObj] at h)
  | atom a => intro l r; exact symm_step hr hs ht (by intro _ _ h; cases l <;> cases r <;> simp [AllObj] at h)
  | obj fb ih =>
    intro l r
    refine symm_step hr hs ht ?_
    intro c1 c2 ho
    obtain ⟨_, fl, fr, h0, rfl, rfl⟩ := allObj_iff.mp ho
    cases h0
    rw [merge_obj peq c1, merge_obj peq c2]
    have hk := fun k => ih k (fl k) (fr k)
    split <;> split
    · exact fun k => symOk_getD peq (hk k) (by rename_i h _; exact h k)
    · rename_i h1 h2; exact absurd (fun k => (symOk_isSome peq (hk k)) ▸ h1 k) h2
    · rename_i h1 h2; exact absurd (fun k => (symOk_isSome peq (hk k)).symm ▸ h2 k) h1
    · trivial

end Symm

/-! ### `guard_projection` soundness

`guard_projection` computes `merged = merge_values(base, current, proposed)`
(base: the last projection; current: the Org file a human may have edited;
proposed: the ledger's projection) and refuses unless `merged == proposed`.
`Subsumed b l r` says: at every key path, the authored side is either
unchanged from the base or already equal to the proposal — i.e. writing the
proposal loses no authored change. The guard passes **iff** that holds. -/

def Subsumed : J A → J A → J A → Prop
  | .obj fb, .obj fl, .obj fr =>
    Eqv peq (.obj fl) (.obj fb) ∨ Eqv peq (.obj fl) (.obj fr) ∨ ∀ k, Subsumed (fb k) (fl k) (fr k)
  | b, l, r => Eqv peq l b ∨ Eqv peq l r

theorem subsumed_nonobj {b l r : J A} (ho : ¬AllObj b l r) :
    Subsumed peq b l r ↔ Eqv peq l b ∨ Eqv peq l r := by
  cases b <;> cases l <;> cases r <;> simp_all [AllObj, Subsumed]

section Guard
variable {peq}
variable (hr : ∀ a, peq a a) (hs : ∀ a b, peq a b → peq b a)
  (ht : ∀ a b c, peq a b → peq b c → peq a c)

/-- **Soundness.** If the guard passes, no authored change is lost. Holds for
EVERY equality relation `peq` — even a non-reflexive one (Python `NaN`). -/
theorem guard_sound (b : J A) : ∀ l r m, merge peq b l r = some m → Eqv peq m r →
    Subsumed peq b l r := by
  induction b with
  | obj fb ih =>
    intro l r m hm he
    by_cases c : Eqv peq l (.obj fb) ∨ Eqv peq l r
    · cases l <;> cases r <;> simp only [Subsumed] <;>
        first | exact c | exact c.elim Or.inl (fun h => Or.inr (Or.inl h))
    · by_cases ho : AllObj (.obj fb) l r
      · obtain ⟨_, fl, fr, h0, rfl, rfl⟩ := allObj_iff.mp ho
        cases h0
        rw [merge_obj peq c] at hm
        split at hm
        · rename_i hall
          cases hm
          refine Or.inr (Or.inr fun k => ?_)
          obtain ⟨mk, hmk⟩ := Option.isSome_iff_exists.mp (hall k)
          have := he k
          simp only [hmk, Option.getD_some] at this
          exact ih k _ _ _ hmk this
        · cases hm
      · rw [merge_tail peq c ho] at hm
        split at hm
        · cases hm; exact (subsumed_nonobj peq ho).mpr (Or.inr he)
        · cases hm
  | absent =>
    intro l r m hm he
    have ho : ¬AllObj (J.absent : J A) l r := by simp [AllObj]
    rw [subsumed_nonobj peq ho]
    by_cases c : Eqv peq l .absent ∨ Eqv peq l r
    · exact c
    · rw [merge_tail peq c ho] at hm; split at hm <;> cases hm; exact Or.inr he
  | atom a =>
    intro l r m hm he
    have ho : ¬AllObj (J.atom a) l r := by simp [AllObj]
    rw [subsumed_nonobj peq ho]
    by_cases c : Eqv peq l (.atom a) ∨ Eqv peq l r
    · exact c
    · rw [merge_tail peq c ho] at hm; split at hm <;> cases hm; exact Or.inr he

include hr in
/-- **Completeness.** If no authored change would be lost, the guard passes. -/
theorem guard_complete (b : J A) : ∀ l r, Subsumed peq b l r →
    ∃ m, merge peq b l r = some m ∧ Eqv peq m r := by
  induction b with
  | obj fb ih =>
    intro l r hsub
    by_cases c : Eqv peq l (.obj fb) ∨ Eqv peq l r
    · exact ⟨r, merge_first peq c, eqv_refl hr r⟩
    · by_cases ho : AllObj (.obj fb) l r
      · obtain ⟨_, fl, fr, h0, rfl, rfl⟩ := allObj_iff.mp ho
        cases h0
        have hk : ∀ k, Subsumed peq (fb k) (fl k) (fr k) := by
          simp only [Subsumed] at hsub
          rcases hsub with h | h | h
          · exact absurd (Or.inl h) c
          · exact absurd (Or.inr h) c
          · exact h
        have hm := fun k => ih k _ _ (hk k)
        rw [merge_obj peq c]
        split
        · refine ⟨_, rfl, fun k => ?_⟩
          obtain ⟨m, hm, he⟩ := hm k
          simpa [hm] using he
        · rename_i hn; exact absurd (fun k => by obtain ⟨m, h, _⟩ := hm k; simp [h]) hn
      · exact absurd ((subsumed_nonobj peq ho).mp hsub) c
  | absent =>
    intro l r hsub
    have ho : ¬AllObj (J.absent : J A) l r := by simp [AllObj]
    exact ⟨r, merge_first peq ((subsumed_nonobj peq ho).mp hsub), eqv_refl hr r⟩
  | atom a =>
    intro l r hsub
    have ho : ¬AllObj (J.atom a) l r := by simp [AllObj]
    exact ⟨r, merge_first peq ((subsumed_nonobj peq ho).mp hsub), eqv_refl hr r⟩

end Guard

/-! ### Python equality is coarser than JSON identity -/

/-- The atoms that matter: Python's `True == 1` (and `== 1.0`). -/
inductive PyAtom | bool (b : Bool) | int (n : Int)
  deriving DecidableEq

def PyAtom.num : PyAtom → Int
  | .bool b => if b then 1 else 0
  | .int n => n

/-- Python `==` on these atoms. An equivalence, so every law above applies. -/
def pyEq (a b : PyAtom) : Prop := a.num = b.num

theorem pyEq_equiv : (∀ a, pyEq a a) ∧ (∀ a b, pyEq a b → pyEq b a) ∧
    (∀ a b c, pyEq a b → pyEq b c → pyEq a c) :=
  ⟨fun _ => rfl, fun _ _ h => h.symm, fun _ _ _ h1 h2 => h1.trans h2⟩

/-- **An edit from `1` to `True` is lost**: it reads as "local unchanged", and
the remote `1` is kept. `merge_values(1, True, 1) == 1` (replayed). -/
theorem bool_one_edit_lost :
    merge pyEq (.atom (.int 1)) (.atom (.bool true)) (.atom (.int 1)) = some (.atom (.int 1)) ∧
    (J.atom (PyAtom.int 1) : J PyAtom) ≠ .atom (.bool true) := by
  refine ⟨merge_first pyEq (Or.inl rfl), by simp⟩

/-- Symmetry holds only up to `==`: the two orders return different JSON. -/
theorem bool_one_asymmetric :
    merge pyEq (.atom (.int 0)) (.atom (.int 1)) (.atom (.bool true)) = some (.atom (.bool true)) ∧
    merge pyEq (.atom (.int 0)) (.atom (.bool true)) (.atom (.int 1)) = some (.atom (.int 1)) :=
  ⟨merge_first pyEq (Or.inr rfl), merge_first pyEq (Or.inr rfl)⟩

/-! ### Protocol version 2: type-strict equality (owner decision L7)

`merge_values(..., strict=True)` compares values by their canonical JSON bytes
(`edits.strict_equal`). Canonical JSON is injective on JSON values up to dict
key order, and dicts are `obj` here, so on atoms that is plain equality `=`:
`1`, `True` and `1.0` are three atoms. With `peq := Eq`, `Eqv` is equality
itself, so every law above holds ON THE NOSE — the caveats `bool_one_edit_lost`
and `bool_one_asymmetric` disappear. Version 1 (`peq := pyEq`) is kept for
every event written before L7 (`mergeV_one`). -/

theorem eqv_eq {x y : J A} (h : Eqv (@Eq A) x y) : x = y := by
  induction x generalizing y with
  | absent => cases y <;> simp_all [Eqv]
  | atom a => cases y <;> simp_all [Eqv]
  | obj f ih =>
    cases y with
    | obj g => exact congrArg J.obj (funext fun k => ih k (h k))
    | _ => simp [Eqv] at h

theorem strict_equiv : (∀ a : A, a = a) ∧ (∀ a b : A, a = b → b = a) ∧
    (∀ a b c : A, a = b → b = c → a = c) :=
  ⟨fun _ => rfl, fun _ _ h => h.symm, fun _ _ _ h1 h2 => h1.trans h2⟩

theorem symOk_eq {x y : Option (J A)} (h : SymOk (@Eq A) x y) : x = y := by
  cases x <;> cases y <;> simp_all [SymOk]
  exact eqv_eq h

/-- **Symmetry, exact, under strict equality.** Swapping local and remote gives
the SAME outcome: both conflict, or both return the identical JSON value. -/
theorem merge_symm_strict (b l r : J A) : merge (@Eq A) b l r = merge (@Eq A) b r l :=
  symOk_eq (merge_symm strict_equiv.1 strict_equiv.2.1 strict_equiv.2.2 b l r)

/-- **Identity law 2, exact, under strict equality.** `merge(b, l, b) = l`. -/
theorem merge_base_remote_strict (b l : J A) : merge (@Eq A) b l b = some l := by
  obtain ⟨m, hm, he⟩ := merge_base_remote strict_equiv.1 strict_equiv.2.1 strict_equiv.2.2 b l
  rw [hm, eqv_eq he]

/-- The fix for `bool_one_edit_lost`: an edit from `1` to `True` is kept.
`merge_values(1, True, 1, strict=True) is True` (tests/test_decisions_ledger_b.py). -/
theorem strict_edit_kept :
    merge (@Eq PyAtom) (.atom (.int 1)) (.atom (.bool true)) (.atom (.int 1)) =
      some (.atom (.bool true)) :=
  merge_base_remote_strict _ _

/-- The fold's dispatch on `_merge.version`: `2` is strict, anything the
historical check accepts as `1` keeps Python `==`. -/
noncomputable def mergeV (v : Nat) : J PyAtom → J PyAtom → J PyAtom → Option (J PyAtom) :=
  if v = 2 then merge (@Eq PyAtom) else merge pyEq

/-- Every event written before L7 is version 1 and folds exactly as before. -/
theorem mergeV_one : mergeV 1 = merge pyEq := by simp [mergeV]

theorem mergeV_two_symm (b l r : J PyAtom) : mergeV 2 b l r = mergeV 2 b r l := by
  simp only [mergeV, ite_true]; exact merge_symm_strict b l r

/-! ### Changed-field detection (owner follow-up Q3, 2026-09-23)

`strict_edit_kept` only helps once an edit reaches `conditional_payload`.
`projection_state.sync_generated` and `ledger_phase1_prepare.plan` first decide
WHICH fields to propose: they reconcile base / authored / ledger (`mergeV`) and
keep a field iff the result differs from the live projection
(`changed_fields`). Under protocol 2 both steps are type-strict now; under 1
both keep Python `==`. `proposeV v b l r` is one field: `none` = nothing
proposed (unchanged, or the merge conflicted and is raised separately),
`some m` = an edit to `m` is planned. -/

open Classical in
noncomputable def eqV (v : Nat) (a b : J PyAtom) : Bool :=
  if v = 2 then decide (Eqv (@Eq PyAtom) a b) else decide (Eqv pyEq a b)

noncomputable def proposeV (v : Nat) (b l r : J PyAtom) : Option (J PyAtom) :=
  match mergeV v b l r with
  | some m => if eqV v m r then none else some m
  | none => none

open Classical in
/-- As found: loose reconciliation and loose change detection, any protocol. -/
noncomputable def proposeOld (b l r : J PyAtom) : Option (J PyAtom) :=
  match merge pyEq b l r with
  | some m => if decide (Eqv pyEq m r) then none else some m
  | none => none

/-- Protocol 1 plans exactly what it planned before Q3. -/
theorem proposeV_one : proposeV 1 = proposeOld := by
  funext b l r; simp [proposeV, proposeOld, mergeV, eqV]

/-- **Protocol 2 proposes every authored edit to a field the ledger has not
moved**, exactly: the edit is planned, with the authored value. -/
theorem proposeV_two_kept (b l : J PyAtom) (hl : l ≠ b) : proposeV 2 b l b = some l := by
  have hm : mergeV 2 b l b = some l := by
    simp only [mergeV, ite_true]; exact merge_base_remote_strict b l
  have he : eqV 2 l b = false := by
    simp only [eqV, ite_true, decide_eq_false_iff_not]
    exact fun h => hl (eqv_eq h)
  simp [proposeV, hm, he]

/-- The 1 → True edit is now proposed under protocol 2 … -/
theorem changed_strict_sees_edit :
    proposeV 2 (.atom (.int 1)) (.atom (.bool true)) (.atom (.int 1)) = some (.atom (.bool true)) :=
  proposeV_two_kept _ _ (by simp)

/-- … and stays unproposed (lost, as before) under protocol 1. -/
theorem changed_v1_as_before :
    proposeV 1 (.atom (.int 1)) (.atom (.bool true)) (.atom (.int 1)) = none := by
  have hm : mergeV 1 (.atom (.int 1)) (.atom (.bool true)) (.atom (.int 1)) =
      some (.atom (.int 1)) := by rw [mergeV_one]; exact bool_one_edit_lost.1
  have he : eqV 1 (.atom (.int 1)) (.atom (.int 1)) = true := by
    simp [eqV, Eqv, pyEq]
  simp [proposeV, hm, he]

end Merge
/-! ## Hops — `claim_gate.check_create` and `ledger_claim.chain_follow_up`

The docstring's claim: "a delegation chain deeper than max_hops is refused
(agent A creates for B creates for A)". A chain is linked by `after`, which
only `chain_follow_up` writes. Humans are exempt from both rules (as they are
from `max_hops`), so the model is of agent creates. -/
namespace Hops

structure Create where
  id    : String
  after : Option String
  /-- `payload["hops"]`, already validated as a nonnegative int. -/
  hops  : Nat

/-- Before the fix: only the self-declared count is checked. -/
def gateOld (maxHops : Nat) (c : Create) : Prop := c.hops ≤ maxHops

/-- After the fix. `look id` is `claim_gate.parent_hops`: the recorded `hops`
of the (first) `item.create` for `id`. A follow-up naming no item is refused. -/
def gateNew (look : String → Option Nat) (maxHops : Nat) (c : Create) : Prop :=
  c.hops ≤ maxHops ∧
    match c.after with
    | none => True
    | some p => ∃ h, look p = some h ∧ h + 1 ≤ c.hops

/-- A delegation chain, newest first: each link names the previous in `after`,
and each link is the recorded create for its id. -/
def Chain (look : String → Option Nat) : List Create → Prop
  | [] => True
  | [c] => look c.id = some c.hops
  | c :: p :: rest => c.after = some p.id ∧ look c.id = some c.hops ∧ Chain look (p :: rest)

theorem chain_tail {look : String → Option Nat} {c : Create} {rest : List Create}
    (h : Chain look (c :: rest)) : Chain look rest := by
  cases rest with
  | nil => trivial
  | cons p rest => exact h.2.2

theorem chain_head {look : String → Option Nat} {c : Create} {rest : List Create}
    (h : Chain look (c :: rest)) : look c.id = some c.hops := by
  cases rest with
  | nil => exact h
  | cons p rest => exact h.2.1

/-- Depth is at most the declared count, when every link passed the gate. -/
theorem depth_le_hops (look : String → Option Nat) (maxHops : Nat) :
    ∀ (rest : List Create) (c : Create), Chain look (c :: rest) →
      (∀ x ∈ c :: rest, gateNew look maxHops x) → rest.length ≤ c.hops := by
  intro rest
  induction rest with
  | nil => intros; simp
  | cons p rest ih =>
    intro c hc hg
    have hp := ih p (chain_tail hc) (fun x hx => hg x (List.mem_cons_of_mem _ hx))
    have ⟨_, hfl⟩ := hg c (List.mem_cons_self ..)
    have hafter : c.after = some p.id := hc.1
    rw [hafter] at hfl
    obtain ⟨h, hlook, hle⟩ := hfl
    rw [chain_head (chain_tail hc)] at hlook
    cases hlook
    simp only [List.length_cons]; omega

/-- **The claim, proved for the fixed gate.** A chain whose every link passed
`check_create` has at most `max_hops` links after its root. -/
theorem chain_depth_bounded (look : String → Option Nat) (maxHops : Nat)
    (c : Create) (rest : List Create) (hc : Chain look (c :: rest))
    (hg : ∀ x ∈ c :: rest, gateNew look maxHops x) : rest.length ≤ maxHops :=
  Nat.le_trans (depth_le_hops look maxHops rest c hc hg) (hg c (List.mem_cons_self ..)).1

def link (n : Nat) : Create :=
  { id := toString n, after := (match n with | 0 => none | k + 1 => some (toString k)), hops := 0 }

/-- The chain `chain_follow_up` built before the fix: `then` spread as-is, so
every link declared `hops` 0 (A → B → A …). -/
def oldChain : Nat → List Create
  | 0 => [link 0]
  | n + 1 => link (n + 1) :: oldChain n

theorem oldChain_head (n : Nat) : ∃ rest, oldChain n = link n :: rest := by
  cases n <;> simp [oldChain]

theorem oldChain_ok (n : Nat) : Chain (fun _ => some 0) (oldChain n) := by
  induction n with
  | zero => simp [oldChain, Chain, link]
  | succ n ih =>
    obtain ⟨rest, hr⟩ := oldChain_head n
    simp only [oldChain]; rw [hr] at ih ⊢
    exact ⟨by simp [link], by simp [link], ih⟩

theorem oldChain_length (n : Nat) : (oldChain n).length = n + 1 := by
  induction n with
  | zero => rfl
  | succ n ih => simp [oldChain, ih]

/-- **Refutation of the old gate.** With `max_hops = 0`, chains of every
length pass it. (Replayed: a 5-link A→B→A chain with `max_hops: 1`.) -/
theorem old_chain_unbounded (n : Nat) :
    ∃ cs : List Create, cs.length = n + 1 ∧ Chain (fun _ => some 0) cs ∧
      ∀ c ∈ cs, gateOld 0 c := by
  refine ⟨oldChain n, oldChain_length n, oldChain_ok n, ?_⟩
  intro c hc
  induction n with
  | zero => simp [oldChain] at hc; subst hc; simp [gateOld, link]
  | succ n ih =>
    simp only [oldChain, List.mem_cons] at hc
    rcases hc with rfl | hc
    · simp [gateOld, link]
    · exact ih hc

/-- `chain_follow_up` after the fix: one hop deeper than the parent. -/
def followNew (parent then_ : Create) : Create :=
  { then_ with after := some parent.id, hops := parent.hops + 1 }

/-- The fixed writer always satisfies the fixed gate's floor, so the only
follow-ups it has refused are those past `max_hops`. -/
theorem follow_up_passes_floor (look : String → Option Nat) (parent then_ : Create)
    (hp : look parent.id = some parent.hops) :
    match (followNew parent then_).after with
    | none => True
    | some p => ∃ h, look p = some h ∧ h + 1 ≤ (followNew parent then_).hops := by
  exact ⟨parent.hops, hp, Nat.le_refl _⟩

end Hops

/-! ## Approval — `previously_approved` in `policy._guarded_append_locked` -/
namespace Approval

structure Payload where
  effects : List String
  ref     : Option String   -- `approval_ref`

/-- The raw `item.create` / `item.update` events carrying this item's id. -/
inductive Ev | create (p : Payload) | update (p : Payload)

def Ev.payload : Ev → Payload
  | .create p | .update p => p

/-- Before the fix: only an approved CREATE counted. -/
def prevOld (hist : List Ev) : Prop :=
  ∃ e ∈ hist, (match e with | .create p => p.ref.isSome | .update _ => false) = true

/-- After the fix: the current content, or any create or update, carried one. -/
def prevNew (hist : List Ev) (cur : Payload) : Prop :=
  cur.ref.isSome = true ∨ ∃ e ∈ hist, e.payload.ref.isSome = true

/-- `needs_cosign` then `validate_approval` on the resulting content (for
`item.update` the merged payload, for `item.claim` the current payload).
`cosign` is "effects ∩ cosign_effects ≠ ∅"; `valid` is `validate_approval`,
an oracle binding a grant to exact content. -/
def allowed (cosign : List String → Bool) (valid : Payload → Bool)
    (prev : Prop) [Decidable prev] (cur : Payload) : Bool :=
  if prev ∨ cosign cur.effects = true then valid cur else true

open Classical in
/-- **"An already approved item keeps its approval requirement even if an
unguarded update removes its effects" — proved for the fixed gate**, for every
cosign set, every grant-validity oracle and every history. -/
theorem claim_keeps_approval (cosign : List String → Bool) (valid : Payload → Bool)
    (hist : List Ev) (cur : Payload) (happroved : ∃ e ∈ hist, e.payload.ref.isSome = true)
    (h : allowed cosign valid (prevNew hist cur) cur = true) : valid cur = true := by
  have hp : prevNew hist cur := Or.inr happroved
  simpa [allowed, hp] using h

open Classical in
/-- **Refutation of the old gate** (replayed against a real `EventLog`):
create with no effects → guarded update adds `payment` with a grant →
unguarded update drops the effects → the claim needs no grant. -/
theorem old_update_approval_lost :
    let cosign := fun (es : List String) => es.contains "payment"
    let valid := fun (p : Payload) => p.effects == ["payment"] && p.ref == some "g"
    let hist := [Ev.create ⟨[], none⟩, .update ⟨["payment"], some "g"⟩, .update ⟨[], none⟩]
    let cur : Payload := ⟨[], none⟩
    (∃ e ∈ hist, e.payload.ref.isSome = true) ∧
      allowed cosign valid (prevOld hist) cur = true ∧ valid cur = false := by
  intro cosign valid hist cur
  have hno : ¬prevOld hist := by
    rintro ⟨e, he, h⟩
    simp only [hist, List.mem_cons, List.not_mem_nil, or_false] at he
    rcases he with rfl | rfl | rfl <;> simp at h
  refine ⟨⟨.update ⟨["payment"], some "g"⟩, by simp [hist], rfl⟩, ?_, rfl⟩
  simp [allowed, hno, cosign, cur]

end Approval

/-! ## Dispatch — `ledger_claim.main` -/
namespace Dispatch
open LedgerSpec.Item

/-- **Only the owner's release is an attempt** (the fixed counter counts
releases the fold APPLIED). For every oracle: an applied `item.release` was
written by the item's current owner. -/
theorem deadletter_counts_applied {P F : Type} (o : Oracle P F) (i : Item P) (e : Ev F)
    (hk : e.kind = .release) (h : note o i e = .applied) : i.owner = some e.actor := by
  unfold note at h
  rw [hk] at h
  simp only [Kind.gated, Bool.false_eq_true, false_and, ite_false] at h
  split at h <;> simp_all

/-- **Refutation of the old counter** (replayed through `ledger_claim.main`):
three releases by a stranger are three "no-op"s in the fold, change nothing,
and yet counted as three failed attempts, which dismisses real work for good. -/
theorem old_deadletter_griefable {F : Type} (o : Oracle Unit F) :
    let i := fresh Unit ()
    let rel := ev (F := F) "bridge" .release
    note o i rel = .noop ∧ run o i [rel, rel, rel] = i ∧
      ([rel, rel, rel].filter (fun e => match e.kind with | .release => true | _ => false)).length = 3 := by
  simp [note, run, step, fresh, ev, Kind.gated]

/-! ### The cross-host claim race (decision L4, 2026-09-23)

Before L4 the dispatcher asked `addressed_to(actor, assignee)`, which accepts any
writer of the assignee's principal, and the policy lock is per host. Two hosts
each running a writer of one principal both saw `created` in their own copy of
the log, both passed the guard, both ran the work; after convergence the fold
keeps the first claim and the other completion is a no-op. -/

/-- The OLD dispatcher + `guarded_append` claim guard, on one host's view. -/
def claimGuard {W Pr : Type} (principal : W → Option Pr) (st : Status) (actor assignee : W) : Prop :=
  st = .created ∧ (actor = assignee ∨ ∃ p, principal actor = some p ∧ principal assignee = some p)

theorem cross_host_race {W Pr : Type} (principal : W → Option Pr) (w1 w2 : W) (p : Pr)
    (h1 : principal w1 = some p) (h2 : principal w2 = some p) :
    claimGuard principal .created w1 w1 ∧ claimGuard principal .created w2 w1 :=
  ⟨⟨rfl, Or.inl rfl⟩, ⟨rfl, Or.inr ⟨p, h2, h1⟩⟩⟩

/-- After convergence the second claim is a no-op: the fold keeps one owner,
so the second host's run was duplicate work (for every oracle). -/
theorem second_claim_noop {P F : Type} (o : Oracle P F) (i : Item P) (a b : Actor)
    (hi : i.status = .created) (hc : i.conflicts = []) :
    note o (step o i (ev a .claim)) (ev (F := F) b .claim) = .noop := by
  simp [note, step, ev, Kind.gated, hi, hc]

/-- **The applied rule, `actor_identity.dispatchable_by`** (names already
`base_writer`-normalised). Registry abstraction:
* `isWriter a` — `a` is a writer name some principal declares (its `writes_as`,
  or the principal's own name when `writes_as` is empty);
* `principalWriters a` — `some ws` when `a` is a principal key, with `ws` its
  writer names as a duplicate-free list (Python: a `set`); `none` otherwise.

A declared writer, or an unregistered name, must be the exact actor. A name
that is only a principal is dispatched to its writer only when it has exactly
one (`ws = [actor]`, Python `len(ws) == 1 and me in ws`); otherwise to nobody
(`dispatch_ambiguity`). The empty assignee is refused separately by the
dispatcher ("addressed to NOBODY") and is not modelled here. -/
def dispatchOk {W : Type} (isWriter : W → Bool) (principalWriters : W → Option (List W))
    (actor assignee : W) : Prop :=
  if isWriter assignee then actor = assignee
  else match principalWriters assignee with
    | some ws => ws = [actor]
    | none => actor = assignee

/-- The claim guard after L4 (the dispatcher's filter; the gate still admits it). -/
def claimGuardExact {W : Type} (isWriter : W → Bool) (principalWriters : W → Option (List W))
    (st : Status) (actor assignee : W) : Prop :=
  st = .created ∧ dispatchOk isWriter principalWriters actor assignee

/-- **No race under the applied rule**: for every registry, two writers that
both pass the dispatcher for one assignee are the same writer. So if each writer
name runs on one host (the one-file-per-writer invariant), no two hosts race. -/
theorem exact_no_race {W : Type} (isWriter : W → Bool) (principalWriters : W → Option (List W))
    (w1 w2 s : W) (st1 st2 : Status)
    (h1 : claimGuardExact isWriter principalWriters st1 w1 s)
    (h2 : claimGuardExact isWriter principalWriters st2 w2 s) : w1 = w2 := by
  have d1 := h1.2
  have d2 := h2.2
  unfold dispatchOk at d1 d2
  by_cases hw : isWriter s = true
  · simp only [hw, ite_true] at d1 d2
    exact d1.trans d2.symm
  · simp only [hw] at d1 d2
    cases hp : principalWriters s with
    | none => simp only [hp] at d1 d2; exact d1.trans d2.symm
    | some ws =>
      simp only [hp] at d1 d2
      rw [d1] at d2
      exact (List.cons.inj d2).1

/-- The race the old guard allowed is refused: `nightshift` may not take work
addressed to `miles` (a declared writer), though both are miles' writers. -/
theorem sibling_refused {W : Type} (isWriter : W → Bool) (principalWriters : W → Option (List W))
    (w1 w2 : W) (hw : isWriter w1 = true) (hne : w2 ≠ w1) :
    ¬ dispatchOk isWriter principalWriters w2 w1 := by
  unfold dispatchOk; simp [hw, hne]

/-- Today's behaviour is kept for a principal-only name with exactly one writer
(`gregor`, who writes as `mac`). -/
theorem single_writer_principal_kept {W : Type} (isWriter : W → Bool)
    (principalWriters : W → Option (List W)) (a w : W)
    (hn : isWriter a = false) (hp : principalWriters a = some [w]) :
    dispatchOk isWriter principalWriters w a := by
  unfold dispatchOk; simp [hn, hp]

end Dispatch

end DatacoreSpec.LedgerPolicy
