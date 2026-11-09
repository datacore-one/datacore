/-!
# Org tools — union merge, inbox cleanup, within-file dedup, id-conflict
resolution, triage body append, and keep-first dedup

Models of six small tools in `.datacore/lib`, each branch for branch with the
Python at the level the stated property needs. Org files are lists of headings
(`level`, optional `:ID:`, opaque content). Everything the properties do not
depend on (heading text, drawer contents) is an opaque `Nat`.

Survey: `survey/gtd-org.md` items 4, 6, 7, 8, 11, 12. Findings and replay
evidence: `findings/org-tools.md`. Python pins:
`lib/tests/test_org_tools_formal.py`.

Each section proves the FIXED behaviour and states the pre-fix behaviour as a
concrete counterexample theorem (checked by `decide` on a small term).

Owner decisions applied 2026-09-23 (`DECISIONS.md`): G12 (section 2a', the
union merge creates a missing heading), G9/G10 (section 4b, dropped or
discarded ids are dismissed as housekeeping), G11 (section 6, DEFERRED counts
as closed for the triage append). G8 (the live writers take the org lock) is
modelled in `Dates.lean`, section 7. Pins: `lib/tests/test_decisions_gtd_b.py`.
-/

namespace DatacoreSpec.OrgTools

/-! ## 1. `dedup.deduplicate` — keep-first under a non-transitive similarity -/

section Dedup
variable {α : Type} (dup : α → α → Bool)

/-- Pre-fix: drop an item when it duplicates ANY earlier item, kept or not. -/
def oldDedup (seen : List α) : List α → List α
  | [] => []
  | x :: xs =>
    if seen.any (dup · x) then oldDedup (seen ++ [x]) xs
    else x :: oldDedup (seen ++ [x]) xs

/-- Post-fix (greedy): drop an item only when it duplicates a KEPT item. -/
def greedy (kept : List α) : List α → List α
  | [] => kept
  | x :: xs => if kept.any (dup · x) then greedy kept xs else greedy (kept ++ [x]) xs

theorem greedy_extends (kept l : List α) : ∀ k ∈ kept, k ∈ greedy dup kept l := by
  induction l generalizing kept with
  | nil => intro k hk; simpa [greedy] using hk
  | cons x xs ih =>
    intro k hk
    simp only [greedy]
    split
    · exact ih kept k hk
    · exact ih (kept ++ [x]) k (by simp [hk])

/-- Nothing is lost without a representative: every input item is kept, or
duplicates an item that is kept. -/
theorem greedy_represents (kept l : List α) :
    ∀ x ∈ l, x ∈ greedy dup kept l ∨ ∃ k ∈ greedy dup kept l, dup k x = true := by
  induction l generalizing kept with
  | nil => intro x hx; cases hx
  | cons y ys ih =>
    intro x hx
    simp only [greedy]
    rcases List.mem_cons.mp hx with rfl | hx
    · split
      · rename_i h
        obtain ⟨k, hk, hd⟩ := List.any_eq_true.mp h
        exact Or.inr ⟨k, greedy_extends dup kept ys k hk, hd⟩
      · exact Or.inl (greedy_extends dup (kept ++ [x]) ys x (by simp))
    · split
      · exact ih kept x hx
      · exact ih (kept ++ [y]) x hx

/-- No two kept items are duplicates (earlier-vs-later). -/
theorem greedy_pairwise (kept l : List α)
    (h : kept.Pairwise (fun a b => dup a b = false)) :
    (greedy dup kept l).Pairwise (fun a b => dup a b = false) := by
  induction l generalizing kept with
  | nil => simpa [greedy] using h
  | cons x xs ih =>
    simp only [greedy]
    split
    · exact ih kept h
    · rename_i hn
      apply ih
      refine List.pairwise_append.mpr ⟨h, by simp, ?_⟩
      intro a ha b hb
      simp at hb; subst hb
      have : ¬ (kept.any (dup · b) = true) := hn
      simp [List.any_eq_true] at this
      simpa using this a ha

/-- The counterexample, replayed in Python: titles "a b c d", "a b c d e",
"b c d e f" at threshold 0.6 (A~B 0.8, B~C 0.67, A~C 0.5). -/
def chain : Nat → Nat → Bool := fun a b => a + 1 == b || b + 1 == a

theorem old_dedup_loses_representative :
    oldDedup chain [] [0, 1, 2] = [0] ∧ (∀ k ∈ [0], chain k 2 = false) := by decide

theorem greedy_keeps_it : greedy chain [] [0, 1, 2] = [0, 2] := by decide

end Dedup

/-! ## 2. `org_union_merge.reconcile` -/

structure Blk where
  level : Nat
  id    : Option Nat
  body  : Nat
  deriving DecidableEq, Repr

/-- The parent of a heading placed after `pre`: the nearest preceding heading
at a shallower level. -/
def parentOf (pre : List Blk) (x : Blk) : Option Blk :=
  pre.reverse.find? (fun b => decide (b.level < x.level))

/-! ### 2a. Structure: where a theirs-only block goes -/

/-- Pre-fix: identified theirs-only blocks are appended at end of file. -/
def oldAppendTheirs (ours theirs : List Blk) : List Blk :=
  ours ++ theirs.filter (fun b => b.id.isSome && !(ours.any (fun o => o.id == b.id)))

/-- The survey counterexample: theirs has child `c` under `p`; ours has `p, q`.
After the old merge `c` sits under `q`. -/
theorem old_union_reparents :
    let p : Blk := ⟨1, some 1, 0⟩
    let q : Blk := ⟨1, some 2, 0⟩
    let c : Blk := ⟨2, some 3, 0⟩
    oldAppendTheirs [p, q] [p, c, q] = [p, q, c] ∧
    parentOf [p] c = some p ∧ parentOf [p, q] c = some q := by decide

/-- `(descendants, rest)` of a heading at level `lvl`: the maximal run of
deeper headings. Mirrors `while _level(out[end]) > level: end += 1`. -/
def splitDesc (lvl : Nat) : List Blk → List Blk × List Blk
  | [] => ([], [])
  | b :: bs => if lvl < b.level then ((splitDesc lvl bs).1.cons b, (splitDesc lvl bs).2)
               else ([], b :: bs)

theorem splitDesc_append (lvl : Nat) (r : List Blk) :
    (splitDesc lvl r).1 ++ (splitDesc lvl r).2 = r := by
  induction r with
  | nil => rfl
  | cons b bs ih => simp only [splitDesc]; split <;> simp [ih]

theorem splitDesc_deep (lvl : Nat) (r : List Blk) : ∀ b ∈ (splitDesc lvl r).1, lvl < b.level := by
  induction r with
  | nil => intro b hb; cases hb
  | cons c cs ih =>
    simp only [splitDesc]; split
    · rename_i h; intro b hb
      rcases List.mem_cons.mp hb with rfl | hb
      · exact h
      · exact ih b hb
    · intro b hb; cases hb

theorem splitDesc_rest_head (lvl : Nat) (r : List Blk) (y : Blk) (ys : List Blk)
    (h : (splitDesc lvl r).2 = y :: ys) : y.level ≤ lvl := by
  induction r with
  | nil => simp [splitDesc] at h
  | cons c cs ih =>
    simp only [splitDesc] at h; split at h
    · exact ih h
    · simp at h; rw [← h.1]; omega

/-- Post-fix placement: the new block goes at the end of its anchor's subtree. -/
def insertUnder (pre : List Blk) (P : Blk) (rest : List Blk) (x : Blk) : List Blk :=
  pre ++ P :: ((splitDesc P.level rest).1 ++ x :: (splitDesc P.level rest).2)

/-- Inserting never loses or invents a block: the result is the old list with
`x` added. -/
theorem insertUnder_mem (pre rest : List Blk) (P x b : Blk) :
    b ∈ insertUnder pre P rest x ↔ b = x ∨ b ∈ pre ++ P :: rest := by
  have h := splitDesc_append P.level rest
  have e : b ∈ pre ++ P :: rest ↔ b ∈ pre ++ P :: ((splitDesc P.level rest).1 ++ (splitDesc P.level rest).2) := by
    rw [h]
  rw [e]
  simp only [insertUnder, List.mem_append, List.mem_cons]
  constructor
  · rintro (h | h | h | h | h) <;> simp [h]
  · rintro (h | h | h | h | h) <;> simp [h]

/-- The placed block's parent is its anchor (when it sits one level below it). -/
theorem insertUnder_parent (pre rest : List Blk) (P x : Blk) (hx : x.level = P.level + 1) :
    parentOf (pre ++ P :: (splitDesc P.level rest).1) x = some P := by
  unfold parentOf
  have hd := splitDesc_deep P.level rest
  have : (splitDesc P.level rest).1.reverse.find? (fun b => decide (b.level < x.level)) = none := by
    rw [List.find?_eq_none]
    intro b hb
    have := hd b (List.mem_reverse.mp hb)
    simp; omega
  rw [List.reverse_append, List.reverse_cons, List.append_assoc, List.find?_append, this]
  simp [hx]

/-- The same, stated on `insertUnder` itself: in the merged list, the heading
nearest before `x` at a shallower level is the anchor. -/
theorem insertUnder_places_under_anchor (pre rest : List Blk) (P x : Blk)
    (hx : x.level = P.level + 1) :
    ∃ A B, insertUnder pre P rest x = A ++ x :: B ∧ parentOf A x = some P :=
  ⟨pre ++ P :: (splitDesc P.level rest).1, (splitDesc P.level rest).2,
   by simp [insertUnder], insertUnder_parent pre rest P x hx⟩

theorem parentOf_skip (A B : List Blk) (x y : Blk)
    (h : ¬ x.level < y.level ∨ ∃ z ∈ B, z.level < y.level) :
    parentOf (A ++ x :: B) y = parentOf (A ++ B) y := by
  unfold parentOf
  simp only [List.reverse_append, List.reverse_cons, List.append_assoc, List.find?_append]
  rcases h with h | ⟨z, hz, hzy⟩
  · simp [List.find?, h]
  · cases hB : B.reverse.find? (fun b => decide (b.level < y.level)) with
    | some w => simp
    | none =>
      rw [List.find?_eq_none] at hB
      exact absurd (by simpa using hzy) (hB z (List.mem_reverse.mpr hz))

/-- Placing `x` changes no other block's parent: the prefix up to `x` is
untouched, and every block after `x` resolves to the same parent as before. -/
theorem insertUnder_keeps_later_parents (pre rest post1 post2 : List Blk) (P x y : Blk)
    (hx : x.level = P.level + 1)
    (hpost : (splitDesc P.level rest).2 = post1 ++ y :: post2) :
    parentOf (pre ++ P :: (splitDesc P.level rest).1 ++ x :: post1) y =
    parentOf (pre ++ P :: (splitDesc P.level rest).1 ++ post1) y := by
  apply parentOf_skip
  cases post1 with
  | nil =>
    have := splitDesc_rest_head P.level rest y post2 (by simpa using hpost)
    left; omega
  | cons z zs =>
    have hz := splitDesc_rest_head P.level rest z (zs ++ y :: post2) (by simpa using hpost)
    by_cases hy : y.level ≤ P.level
    · left; omega
    · right; exact ⟨z, by simp, by omega⟩

/-! ### 2a'. Decision G12: create the missing heading, never an unrelated parent

The owner's note: "a new heading should be created, don't put under random
heading". `insertUnder` keeps the parent only when the block sits exactly one
level below its anchor (`insertUnder_parent`). When theirs skips a level, or
the anchor sits at or below the block's level, `place` now creates the missing
heading(s). Proved: the placed block's parent always carries its
theirs-parent's text (`placeTheirs_parent_text`); nothing is lost
(`placeTheirs_keeps`) and nothing is invented beyond `x` and id-less created
headings (`placeTheirs_new`); the plain branch is the old placement
(`placeTheirs_plain`), and created headings change no later block's parent
(`placeTheirs_keeps_later_parents`). Heading text is `Blk.body` here; the
anchor stands for theirs' parent by identity, so `hA : A.body = pt`. -/


/-- Pre-G12, under a level skip: theirs `* P / *** x`, ours `* P / ** y`.
`x` goes to the end of `P`'s subtree and reads as a child of `y`, whose text
is not `P`'s. -/
theorem old_skip_under_unrelated :
    let P : Blk := ⟨1, none, 7⟩
    let y : Blk := ⟨2, none, 8⟩
    let x : Blk := ⟨3, some 1, 9⟩
    insertUnder [] P [y] x = [P, y, x] ∧ parentOf [P, y] x = some y ∧ y.body ≠ P.body := by
  decide

/-- A created heading: level `m`, no `:ID:`, text `t`. -/
def hdr (m t : Nat) : Blk := ⟨m, none, t⟩

/-- Created headings for levels `lo .. hi-1`. The intermediate ones take
`anc m` (theirs' ancestor text at level `m`, else the parent's), the last one
(level `hi - 1`) the parent's text `pt`. -/
def mkChainMid (anc : Nat → Nat) (lo hi : Nat) : List Blk :=
  (List.range (hi - 1 - lo)).map (fun i => hdr (lo + i) (anc (lo + i)))

def mkChain (anc : Nat → Nat) (pt lo hi : Nat) : List Blk :=
  mkChainMid anc lo hi ++ [hdr (hi - 1) pt]

/-- Mirrors `place` in `org_union_merge.reconcile` for one theirs-only block
`x`, with anchor `A` (the output entry standing for its theirs-parent, or the
created stand-in) in the current output `pre ++ A :: rest`:
* nothing in `A`'s subtree is shallower than `x`: insert at its end (as before);
* `A` is shallower but something is in the way: create levels `A+1 .. x-1`
  at the end of `A`'s subtree, then `x`;
* `A` is not shallower than `x`: create levels `1 .. x-1` at end of file. -/
def placeTheirs (anc : Nat → Nat) (pt : Nat) (pre : List Blk) (A : Blk)
    (rest : List Blk) (x : Blk) : List Blk :=
  if A.level < x.level then
    if (splitDesc A.level rest).1.all (fun b => decide (x.level ≤ b.level)) then
      pre ++ A :: ((splitDesc A.level rest).1 ++ x :: (splitDesc A.level rest).2)
    else
      pre ++ A :: ((splitDesc A.level rest).1 ++
        (mkChain anc pt (A.level + 1) x.level ++ x :: (splitDesc A.level rest).2))
  else pre ++ A :: rest ++ (mkChain anc pt 1 x.level ++ [x])

theorem parentOf_snoc (B : List Blk) (h x : Blk) (hl : h.level < x.level) :
    parentOf (B ++ [h]) x = some h := by
  simp [parentOf, hl]

theorem parentOf_over (pre d : List Blk) (A x : Blk) (hA : A.level < x.level)
    (hd : ∀ b ∈ d, x.level ≤ b.level) : parentOf (pre ++ A :: d) x = some A := by
  unfold parentOf
  have : d.reverse.find? (fun b => decide (b.level < x.level)) = none := by
    rw [List.find?_eq_none]
    intro b hb
    have := hd b (List.mem_reverse.mp hb)
    simp; omega
  rw [List.reverse_append, List.reverse_cons, List.append_assoc, List.find?_append, this]
  simp [hA]

/-- The plain branch is the pre-G12 placement. -/
theorem placeTheirs_plain (anc : Nat → Nat) (pt : Nat) (pre rest : List Blk) (A x : Blk)
    (hA : A.level < x.level)
    (hd : (splitDesc A.level rest).1.all (fun b => decide (x.level ≤ b.level)) = true) :
    placeTheirs anc pt pre A rest x = insertUnder pre A rest x := by
  simp [placeTheirs, insertUnder, hA, hd]

/-- **Decision G12.** Every theirs-only block ends up under a heading whose
text is its theirs-parent's: the anchor (which stands for that parent) or a
created heading carrying the parent's text. -/
theorem placeTheirs_parent_text (anc : Nat → Nat) (pt : Nat) (pre rest : List Blk)
    (A x : Blk) (hA : A.body = pt) (hx : 0 < x.level) :
    ∃ B C h, placeTheirs anc pt pre A rest x = B ++ x :: C ∧
      parentOf B x = some h ∧ h.body = pt := by
  unfold placeTheirs
  by_cases hl : A.level < x.level
  · by_cases hd : (splitDesc A.level rest).1.all (fun b => decide (x.level ≤ b.level)) = true
    · refine ⟨pre ++ A :: (splitDesc A.level rest).1, (splitDesc A.level rest).2, A, ?_, ?_, hA⟩
      · simp [hl, hd]
      · apply parentOf_over _ _ _ _ hl
        intro b hb
        have := List.all_eq_true.mp hd b hb
        simpa using this
    · refine ⟨pre ++ A :: ((splitDesc A.level rest).1 ++ mkChainMid anc (A.level + 1) x.level)
          ++ [hdr (x.level - 1) pt], (splitDesc A.level rest).2, hdr (x.level - 1) pt, ?_, ?_, rfl⟩
      · simp [hl, hd, mkChain]
      · exact parentOf_snoc _ _ _ (by simp [hdr]; omega)
  · refine ⟨pre ++ A :: rest ++ mkChainMid anc 1 x.level ++ [hdr (x.level - 1) pt], [],
        hdr (x.level - 1) pt, ?_, ?_, rfl⟩
    · simp [hl, mkChain]
    · exact parentOf_snoc _ _ _ (by simp [hdr]; omega)

theorem mkChainMid_mem (anc : Nat → Nat) (lo hi : Nat) (b : Blk) (hb : b ∈ mkChainMid anc lo hi) :
    b.id = none ∧ lo ≤ b.level := by
  simp [mkChainMid, hdr] at hb
  obtain ⟨i, _, rfl⟩ := hb
  simp

theorem mkChain_mem (anc : Nat → Nat) (pt lo hi : Nat) (b : Blk) (hb : b ∈ mkChain anc pt lo hi) :
    b.id = none ∧ (lo ≤ b.level ∨ b.level = hi - 1) := by
  simp only [mkChain, List.mem_append, List.mem_singleton] at hb
  rcases hb with hb | rfl
  · exact ⟨(mkChainMid_mem anc lo hi b hb).1, Or.inl (mkChainMid_mem anc lo hi b hb).2⟩
  · exact ⟨rfl, Or.inr rfl⟩

/-- Nothing is lost: every block of the output before placement is still there. -/
theorem placeTheirs_keeps (anc : Nat → Nat) (pt : Nat) (pre rest : List Blk) (A x b : Blk)
    (hb : b ∈ pre ++ A :: rest) : b ∈ placeTheirs anc pt pre A rest x := by
  have e := splitDesc_append A.level rest
  unfold placeTheirs
  split
  · split
    · rw [← e] at hb; simp at hb ⊢; rcases hb with h | h | h | h <;> simp [h]
    · rw [← e] at hb; simp at hb ⊢; rcases hb with h | h | h | h <;> simp [h]
  · simp at hb ⊢; rcases hb with h | h | h <;> simp [h]

/-- Nothing is invented but `x` and created headings, which carry no `:ID:`. -/
theorem placeTheirs_new (anc : Nat → Nat) (pt : Nat) (pre rest : List Blk) (A x b : Blk)
    (hb : b ∈ placeTheirs anc pt pre A rest x) :
    b = x ∨ b ∈ pre ++ A :: rest ∨ b.id = none := by
  have e := splitDesc_append A.level rest
  unfold placeTheirs at hb
  split at hb
  · split at hb
    · rw [← e]; simp at hb ⊢; rcases hb with h | h | h | h | h <;> simp [h]
    · rw [← e]
      simp only [List.mem_append, List.mem_cons] at hb
      rcases hb with h | h | h | h | h | h
      · simp [h]
      · simp [h]
      · simp [h]
      · exact Or.inr (Or.inr (mkChain_mem _ _ _ _ b h).1)
      · simp [h]
      · simp [h]
  · rcases List.mem_append.mp hb with h | h
    · exact Or.inr (Or.inl h)
    · rcases List.mem_append.mp h with h | h
      · exact Or.inr (Or.inr (mkChain_mem _ _ _ _ b h).1)
      · exact Or.inl (List.mem_singleton.mp h)

theorem parentOf_skipList (A M B : List Blk) (y : Blk)
    (h : (∀ m ∈ M, y.level ≤ m.level) ∨ ∃ z ∈ B, z.level < y.level) :
    parentOf (A ++ M ++ B) y = parentOf (A ++ B) y := by
  unfold parentOf
  simp only [List.reverse_append, List.append_assoc, List.find?_append]
  cases hB : B.reverse.find? (fun b => decide (b.level < y.level)) with
  | some w => simp
  | none =>
    rw [List.find?_eq_none] at hB
    rcases h with h | ⟨z, hz, hzy⟩
    · have hM : M.reverse.find? (fun b => decide (b.level < y.level)) = none := by
        rw [List.find?_eq_none]
        intro m hm
        have := h m (List.mem_reverse.mp hm)
        simp; omega
      simp [hM]
    · exact absurd (by simpa using hzy) (hB z (List.mem_reverse.mpr hz))

/-- In the create-heading branch, the created headings and `x` change no later
block's parent: they all sit deeper than the anchor, and the block after the
anchor's subtree is at most as deep as the anchor. -/
theorem placeTheirs_keeps_later_parents (anc : Nat → Nat) (pt : Nat)
    (rest post1 post2 : List Blk) (A x y : Blk)
    (hl : A.level < x.level)
    (hd : ¬ (splitDesc A.level rest).1.all (fun b => decide (x.level ≤ b.level)) = true)
    (hpost : (splitDesc A.level rest).2 = post1 ++ y :: post2) :
    parentOf ((splitDesc A.level rest).1 ++ (mkChain anc pt (A.level + 1) x.level ++ x :: post1)) y =
    parentOf ((splitDesc A.level rest).1 ++ post1) y := by
  -- `x` is at least two levels below the anchor, else nothing could be in the way.
  have h2 : A.level + 2 ≤ x.level := by
    refine Classical.byContradiction fun hn => hd ?_
    apply List.all_eq_true.mpr
    intro b hb
    have := splitDesc_deep A.level rest b hb
    simp; omega
  have hM : ∀ m ∈ mkChain anc pt (A.level + 1) x.level ++ [x], A.level < m.level := by
    intro m hm
    rcases List.mem_append.mp hm with hm | hm
    · rcases (mkChain_mem anc pt _ _ m hm).2 with h | h <;> omega
    · rw [List.mem_singleton.mp hm]; exact hl
  have e : (splitDesc A.level rest).1 ++ (mkChain anc pt (A.level + 1) x.level ++ x :: post1) =
      (splitDesc A.level rest).1 ++ (mkChain anc pt (A.level + 1) x.level ++ [x]) ++ post1 := by
    simp
  rw [e]
  apply parentOf_skipList
  cases post1 with
  | nil =>
    have := splitDesc_rest_head A.level rest y post2 (by simpa using hpost)
    left; intro m hm; have := hM m hm; omega
  | cons z zs =>
    have hz := splitDesc_rest_head A.level rest z (zs ++ y :: post2) (by simpa using hpost)
    by_cases hy : y.level ≤ A.level
    · left; intro m hm; have := hM m hm; omega
    · right; exact ⟨z, by simp, by omega⟩

/-! ### 2a''. Decision Q14: a parentless theirs-only block deeper than level 1

Theirs' file opens with `** x` (no heading shallower before it). Before Q14 it
went to end of file and read as a child of the last shallower heading there,
an unrelated one (`old_orphan_under_last`). Now `place` creates heading(s)
with one neutral text `N` (`UNFILED`, made unique against every heading of
both sides, no state keyword, no `:ID:`): a fresh chain `1 .. x-1` at end of
file, or, once a created root exists, the placement of a theirs-only child
under it (`placeTheirs` with `N` for every text). When the output has no
heading shallower than `x`, end of file gives it no parent, as in theirs, and
nothing is created. Proved: the parent, if any, carries `N`
(`placeOrphan_parent_neutral`), hence is never a heading of either side
(`placeOrphan_never_existing`), and nothing is lost (`placeOrphan_keeps`). -/

/-- Pre-Q14: a parentless block is appended at end of file. -/
def oldOrphan (out : List Blk) (x : Blk) : List Blk := out ++ [x]

/-- The counterexample: ours `* Q`, theirs `** x`; `x` lands under `Q`. -/
theorem old_orphan_under_last :
    let Q : Blk := ⟨1, none, 5⟩
    let x : Blk := ⟨2, some 1, 9⟩
    oldOrphan [Q] x = [Q, x] ∧ parentOf [Q] x = some Q := by
  decide

/-- Mirrors the `parent is None` branch of `place`. `root` is the output split
around the created stand-in (the level-1 root, or the created heading at
level `x-1`) once one exists; every created heading has text `N`. -/
def placeOrphan (N : Nat) (out : List Blk) (root : Option (List Blk × Blk × List Blk))
    (x : Blk) : List Blk :=
  if x.level ≤ 1 then out ++ [x] else
  match root with
  | some (pre, R, rest) => placeTheirs (fun _ => N) N pre R rest x
  | none =>
    if out.all (fun b => decide (x.level ≤ b.level)) then out ++ [x]
    else out ++ (mkChain (fun _ => N) N 1 x.level ++ [x])

theorem parentOf_none_of_all_deep (out : List Blk) (x : Blk)
    (h : out.all (fun b => decide (x.level ≤ b.level)) = true) : parentOf out x = none := by
  unfold parentOf
  rw [List.find?_eq_none]
  intro b hb
  have := List.all_eq_true.mp h b (List.mem_reverse.mp hb)
  simp at this ⊢; omega

/-- **Decision Q14.** A parentless theirs-only block deeper than level 1 has,
after placement, either no parent (as in theirs) or a created heading with
the neutral text `N` as its parent. -/
theorem placeOrphan_parent_neutral (N : Nat) (out : List Blk)
    (root : Option (List Blk × Blk × List Blk)) (x : Blk) (hx : 1 < x.level)
    (hR : ∀ pre R rest, root = some (pre, R, rest) → R.body = N) :
    ∃ B C, placeOrphan N out root x = B ++ x :: C ∧
      ∀ h, parentOf B x = some h → h.body = N := by
  have h1 : ¬ x.level ≤ 1 := by omega
  cases root with
  | some t =>
    obtain ⟨pre, R, rest⟩ := t
    obtain ⟨B, C, h, he, hp, hb⟩ :=
      placeTheirs_parent_text (fun _ => N) N pre rest R x (hR pre R rest rfl) (by omega)
    refine ⟨B, C, by simp [placeOrphan, h1, he], ?_⟩
    intro h' hh'
    rw [hp] at hh'
    cases hh'
    exact hb
  | none =>
    by_cases hd : out.all (fun b => decide (x.level ≤ b.level)) = true
    · refine ⟨out, [], by simp [placeOrphan, h1, hd], ?_⟩
      intro h hh
      rw [parentOf_none_of_all_deep out x hd] at hh
      cases hh
    · refine ⟨out ++ mkChainMid (fun _ => N) 1 x.level ++ [hdr (x.level - 1) N], [], ?_, ?_⟩
      · simp [placeOrphan, h1, hd, mkChain]
      · intro h hh
        rw [parentOf_snoc _ _ _ (by simp [hdr]; omega)] at hh
        cases hh
        rfl

/-- Hence never under a heading of either side, when `N` is fresh for them
(`_unfiled` picks the first `UNFILED (n)` no heading uses). -/
theorem placeOrphan_never_existing (N : Nat) (out orig : List Blk)
    (root : Option (List Blk × Blk × List Blk)) (x : Blk) (hx : 1 < x.level)
    (hR : ∀ pre R rest, root = some (pre, R, rest) → R.body = N)
    (hfresh : ∀ b ∈ orig, b.body ≠ N) :
    ∃ B C, placeOrphan N out root x = B ++ x :: C ∧
      ∀ h, parentOf B x = some h → h ∉ orig := by
  obtain ⟨B, C, he, hp⟩ := placeOrphan_parent_neutral N out root x hx hR
  exact ⟨B, C, he, fun h hh hm => hfresh h hm (hp h hh)⟩

/-- Nothing is lost: every block of the output is still there. -/
theorem placeOrphan_keeps (N : Nat) (out : List Blk) (x b : Blk)
    (root : Option (List Blk × Blk × List Blk))
    (hroot : ∀ pre' R' rest', root = some (pre', R', rest') → out = pre' ++ R' :: rest')
    (hb : b ∈ out) : b ∈ placeOrphan N out root x := by
  unfold placeOrphan
  split
  · simp [hb]
  · cases root with
    | some t =>
      obtain ⟨pre', R', rest'⟩ := t
      rw [hroot pre' R' rest' rfl] at hb
      exact placeTheirs_keeps _ _ _ _ _ _ _ hb
    | none =>
      simp only
      split <;> simp [hb]

/-! ### 2b. Loss: unidentified blocks (no `:ID:`) -/

/-- Pre-fix: theirs' unidentified block is kept only if it is not a SUBSTRING
of ours. A block is `(level, text)`; `"** T"` is a substring of `"*** T"`, so
`substr b c` holds when the text agrees and `b` has no more stars. -/
def substr (b c : Nat × Nat) : Bool := b.2 == c.2 && decide (b.1 ≤ c.1)

def oldUnid (ours theirs : List (Nat × Nat)) : List (Nat × Nat) :=
  ours ++ theirs.filter (fun b => !(ours.any (substr b)))

theorem old_union_drops_substring_block :
    (2, 7) ∈ [((2 : Nat), (7 : Nat))] ∧ (2, 7) ∉ oldUnid [(3, 7)] [(2, 7)] := by decide

/-- Post-fix: a multiset match; each ours block can stand for one theirs block. -/
def unidExtra {β : Type} [DecidableEq β] : List β → List β → List β
  | _, [] => []
  | pool, b :: bs => if b ∈ pool then unidExtra (pool.erase b) bs else b :: unidExtra pool bs

theorem unidExtra_count {β : Type} [DecidableEq β] (pool theirs : List β) (b : β) :
    theirs.count b ≤ pool.count b + (unidExtra pool theirs).count b := by
  induction theirs generalizing pool with
  | nil => simp
  | cons x xs ih =>
    simp only [unidExtra]
    split
    · rename_i hx
      have := ih (pool.erase x)
      by_cases hb : x = b
      · subst hb
        have hpos := List.count_pos_iff.mpr hx
        rw [List.count_erase_self] at this
        simp [List.count_cons]; omega
      · rw [List.count_erase_of_ne (Ne.symm hb)] at this
        simp [List.count_cons, hb]; omega
    · have := ih pool
      by_cases hb : x = b
      · subst hb; simp [List.count_cons]; omega
      · simp [List.count_cons, hb]; omega

/-- The merged unidentified blocks hold every block of EITHER side at least as
often as that side does. -/
theorem unid_union_loses_nothing {β : Type} [DecidableEq β] (ours theirs : List β) (b : β) :
    ours.count b ≤ (ours ++ unidExtra ours theirs).count b ∧
    theirs.count b ≤ (ours ++ unidExtra ours theirs).count b := by
  have := unidExtra_count ours theirs b
  simp [List.count_append]; omega

/-! ## 3. `inbox_cleanup.clean` -/

inductive St | todo | next | waiting | review | done | cancelled | deferred | plain
  deriving DecidableEq, Repr

/-- Must never leave the live file: open, and DEFERRED (closed-but-wakeable,
DIP-0009 lines 330-337). -/
def live : St → Bool
  | .todo | .next | .waiting | .review | .deferred => true
  | _ => false

structure Entry where
  lvl : Nat
  st  : St
  id  : Nat
  deriving DecidableEq, Repr

/-- Inside a subtree being moved (rooted at level `L`)? -/
def inSub : Option Nat → Nat → Bool
  | some L, l => decide (L < l)
  | none, _ => false

/-- `detach_open_descendants` on the lines after a closed root: a heading whose
state is `rescue` starts a subtree (it and every deeper heading after it) that
moves to the Inbox; everything else stays with the root. `cur` is the level
of the subtree being moved, if any. -/
def detach (rescue : St → Bool) : Option Nat → List Entry → List Entry × List Entry
  | _, [] => ([], [])
  | cur, e :: es =>
    if inSub cur e.lvl then
      ((detach rescue cur es).1, e :: (detach rescue cur es).2)
    else if rescue e.st then
      ((detach rescue (some e.lvl) es).1, e :: (detach rescue (some e.lvl) es).2)
    else
      (e :: (detach rescue none es).1, (detach rescue none es).2)

theorem detach_count (rescue : St → Bool) (l : List Entry) (x : Entry) : ∀ cur : Option Nat,
    (detach rescue cur l).1.count x + (detach rescue cur l).2.count x = l.count x := by
  induction l with
  | nil => intro cur; simp [detach]
  | cons e es ih =>
    intro cur
    simp only [detach]
    split
    · have := ih cur; simp [List.count_cons]; omega
    · split
      · have := ih (some e.lvl); simp [List.count_cons]; omega
      · have := ih none; simp [List.count_cons]; omega

theorem detach_rescues (rescue : St → Bool) (l : List Entry) : ∀ cur : Option Nat,
    ∀ e ∈ (detach rescue cur l).1, rescue e.st = false := by
  induction l with
  | nil => intro cur e he; cases he
  | cons f fs ih =>
    intro cur
    simp only [detach]
    split
    · exact ih cur
    · split
      · exact ih (some f.lvl)
      · rename_i h
        intro e he
        rcases List.mem_cons.mp he with rfl | he
        · simpa using h
        · exact ih none e he

/-- One top-level block `(root, descendants)`: kept, or archived with its
rescued subtrees moved into the Inbox. Returns (kept, archived, moved). -/
def cleanTop (arch rescue : St → Bool) (t : Entry × List Entry) :
    List (Entry × List Entry) × List Entry × List Entry :=
  if arch t.1.st then ([], t.1 :: (detach rescue none t.2).1, (detach rescue none t.2).2)
  else ([t], [], [])

def clean (arch rescue : St → Bool) :
    List (Entry × List Entry) → List (Entry × List Entry) × List Entry × List Entry
  | [] => ([], [], [])
  | t :: ts =>
    let a := cleanTop arch rescue t
    let b := clean arch rescue ts
    (a.1 ++ b.1, a.2.1 ++ b.2.1, a.2.2 ++ b.2.2)

def flat (ts : List (Entry × List Entry)) : List Entry := ts.flatMap (fun t => t.1 :: t.2)

/-- Post-fix sets: archive DONE/CANCELLED; rescue everything live. -/
def archNew : St → Bool | .done | .cancelled => true | _ => false
def rescueNew : St → Bool := live
/-- Pre-fix: DEFERRED archived with DONE/CANCELLED; only open states rescued. -/
def archOld : St → Bool | .done | .cancelled | .deferred => true | _ => false
def rescueOld : St → Bool | .todo | .next | .waiting | .review => true | _ => false

/-- Nothing live is archived — for any sets where archivable states are not
live and every live state is rescued. -/
theorem clean_never_archives_live (arch rescue : St → Bool)
    (hArch : ∀ s, arch s = true → live s = false)
    (hRescue : ∀ s, live s = true → rescue s = true) (ts : List (Entry × List Entry)) :
    ∀ e ∈ (clean arch rescue ts).2.1, live e.st = false := by
  induction ts with
  | nil => intro e he; cases he
  | cons t ts ih =>
    intro e he
    simp only [clean, List.mem_append] at he
    rcases he with he | he
    · simp only [cleanTop] at he
      split at he
      · rename_i ha
        rcases List.mem_cons.mp he with rfl | he
        · exact hArch _ ha
        · have := detach_rescues rescue t.2 none e he
          cases hl : live e.st
          · rfl
          · simp [hRescue _ hl] at this
      · cases he
    · exact ih e he

theorem new_never_archives_live (ts : List (Entry × List Entry)) :
    ∀ e ∈ (clean archNew rescueNew ts).2.1, live e.st = false :=
  clean_never_archives_live archNew rescueNew
    (by intro s; cases s <;> decide) (by intro s h; exact h) ts

/-- Every entry lands in exactly one output (kept / archive / Inbox arrivals). -/
theorem clean_count (arch rescue : St → Bool) (ts : List (Entry × List Entry)) (x : Entry) :
    (flat ts).count x = (flat (clean arch rescue ts).1).count x
      + (clean arch rescue ts).2.1.count x + (clean arch rescue ts).2.2.count x := by
  induction ts with
  | nil => simp [clean, flat]
  | cons t ts ih =>
    have hd := detach_count rescue t.2 x none
    simp only [flat, List.flatMap_cons, clean, cleanTop] at ih ⊢
    split <;> simp [List.count_append, List.count_cons, List.flatMap_append] at ih ⊢ <;> omega

/-- Idempotent: what a run keeps, a second run keeps unchanged, archiving
nothing. -/
theorem clean_idem (arch rescue : St → Bool) (ts : List (Entry × List Entry)) :
    clean arch rescue (clean arch rescue ts).1 = ((clean arch rescue ts).1, [], []) := by
  induction ts with
  | nil => rfl
  | cons t ts ih =>
    simp only [clean, cleanTop]
    split
    · simpa using ih
    · rename_i h
      simp only [List.singleton_append, clean, cleanTop, h]
      simp [ih]

/-- The pre-fix counterexample, replayed in Python: a wakeable DEFERRED at top
level and one under a DONE task both went to the dated archive. -/
theorem old_archives_deferred :
    let d : Entry := ⟨1, .deferred, 1⟩
    let x : Entry := ⟨1, .done, 2⟩
    let c : Entry := ⟨2, .deferred, 3⟩
    (clean archOld rescueOld [(x, [c]), (d, [])]).2.1 = [x, c, d] := by decide


/-! ### 3b. Reference: `org_archive_closed._spans` (survey item 11, no defect)

The selection rule inbox_cleanup now agrees with: a level-1/2 DONE/CANCELLED
node moves with its subtree only when no descendant carries any other state
(`child.todo and child.todo not in CLOSED` skips it; DEFERRED counts as
unfinished). An unselected node stays and the scan continues at its first
child, so a closed child of a live parent can still move. -/

def splitDescE (lvl : Nat) : List Entry → List Entry × List Entry
  | [] => ([], [])
  | b :: bs => if lvl < b.lvl then ((splitDescE lvl bs).1.cons b, (splitDescE lvl bs).2)
               else ([], b :: bs)

theorem splitDescE_append (lvl : Nat) (r : List Entry) :
    (splitDescE lvl r).1 ++ (splitDescE lvl r).2 = r := by
  induction r with
  | nil => rfl
  | cons b bs ih => simp only [splitDescE]; split <;> simp [ih]

theorem splitDescE_len (lvl : Nat) (r : List Entry) : (splitDescE lvl r).2.length ≤ r.length := by
  have := congrArg List.length (splitDescE_append lvl r)
  simp at this; omega

def settled (c : Entry) : Bool := c.st == .plain || archNew c.st

/-- `(archived, kept)` -/
def archSpans : List Entry → List Entry × List Entry
  | [] => ([], [])
  | e :: es =>
    if (e.lvl == 1 || e.lvl == 2) && archNew e.st && (splitDescE e.lvl es).1.all settled then
      (e :: (splitDescE e.lvl es).1 ++ (archSpans (splitDescE e.lvl es).2).1,
       (archSpans (splitDescE e.lvl es).2).2)
    else ((archSpans es).1, e :: (archSpans es).2)
termination_by l => l.length
decreasing_by
  all_goals first
    | (have := splitDescE_len e.lvl es; simp; omega)
    | simp

theorem archSpans_never_live (l : List Entry) : ∀ x ∈ (archSpans l).1, live x.st = false := by
  induction l using archSpans.induct with
  | case1 => intro x hx; simp [archSpans] at hx
  | case2 e es hsel ih =>
    intro x hx
    rw [archSpans] at hx; simp only [hsel, ↓reduceIte] at hx
    simp only [List.cons_append, List.mem_cons, List.mem_append] at hx
    have ha : archNew e.st = true := by simp at hsel; exact hsel.1.2
    have hall := (Bool.and_eq_true _ _ ▸ hsel).2
    rcases hx with rfl | hx | hx
    · cases h : x.st <;> simp_all [archNew, live]
    · have := List.all_eq_true.mp hall x hx
      cases h : x.st <;> simp_all [settled, archNew, live]
    · exact ih x hx
  | case3 e es hsel ih =>
    intro x hx
    rw [archSpans] at hx; simp only [hsel, ↓reduceIte, Bool.false_eq_true] at hx
    exact ih x hx

theorem archSpans_count (l : List Entry) (x : Entry) :
    (archSpans l).1.count x + (archSpans l).2.count x = l.count x := by
  induction l using archSpans.induct with
  | case1 => simp [archSpans]
  | case2 e es hsel ih =>
    rw [archSpans]; simp only [hsel, ↓reduceIte]
    have h := congrArg (List.count x) (splitDescE_append e.lvl es)
    simp only [List.count_append] at h
    simp only [List.cons_append, List.count_cons, List.count_append]
    omega
  | case3 e es hsel ih =>
    rw [archSpans]; simp only [hsel, ↓reduceIte, Bool.false_eq_true]
    simp only [List.count_cons]
    omega

/-! ## 4. `org_dedup_within_file.dedup` -/

inductive Ln | gen (v : Nat) | txt (v : Nat)
  deriving DecidableEq, Repr

/-- `identity()`: the subtree minus `:ID:` / `:DISPATCH_ID:` lines. -/
def identity (l : List Ln) : List Ln := l.filter (fun x => match x with | .gen _ => false | .txt _ => true)

/-- `(kept, dropped)` for subtrees `(key, lines)`; `seen` holds first copies. -/
def ddGo (seen : List (Nat × List Ln)) : List (Nat × List Ln) → List (Nat × List Ln) × List (Nat × List Ln)
  | [] => ([], [])
  | t :: ts =>
    match seen.find? (fun s => s.1 == t.1) with
    | none => ((ddGo (seen ++ [t]) ts).1.cons t, (ddGo (seen ++ [t]) ts).2)
    | some s =>
      if identity s.2 = identity t.2 then ((ddGo seen ts).1, (ddGo seen ts).2.cons t)
      else ((ddGo seen ts).1.cons t, (ddGo seen ts).2)

theorem mem_shuffle1 {β : Type} {a u : β} {seen rest : List β}
    (h : a ∈ seen ++ [u] ++ rest) : a ∈ seen ++ u :: rest := by simpa using h

theorem mem_shuffle2 {β : Type} {a u : β} {seen rest : List β}
    (h : a ∈ seen ++ rest) : a ∈ seen ++ u :: rest := by
  simp at h ⊢; rcases h with h | h <;> simp [h]

/-- What the tool DOES guarantee: a dropped subtree equals a kept copy with the
same heading key, modulo generated-id lines. -/
theorem dd_dropped_has_twin (seen ts : List (Nat × List Ln)) :
    ∀ t ∈ (ddGo seen ts).2, ∃ s ∈ seen ++ (ddGo seen ts).1, s.1 = t.1 ∧ identity s.2 = identity t.2 := by
  induction ts generalizing seen with
  | nil => intro t ht; cases ht
  | cons u us ih =>
    intro t ht
    unfold ddGo at ht ⊢
    cases hf : seen.find? (fun s => s.1 == u.1) with
    | none =>
      rw [hf] at ht
      dsimp only at ht ⊢
      obtain ⟨s, hs, h⟩ := ih (seen ++ [u]) t ht
      exact ⟨s, mem_shuffle1 hs, h⟩
    | some s =>
      rw [hf] at ht
      dsimp only at ht ⊢
      by_cases heq : identity s.2 = identity u.2
      · simp only [heq, ↓reduceIte] at ht ⊢
        rcases List.mem_cons.mp ht with rfl | ht
        · have hmem := List.mem_of_find?_eq_some hf
          have hp := List.find?_some hf
          exact ⟨s, by simp [hmem], by simpa using hp, heq⟩
        · obtain ⟨s', hs', h⟩ := ih seen t ht
          exact ⟨s', hs', h⟩
      · simp only [heq, ↓reduceIte] at ht ⊢
        obtain ⟨s', hs', h⟩ := ih seen t ht
        exact ⟨s', mem_shuffle2 hs', h⟩

/-- But not "byte-identical": two copies of one task with DIFFERENT `:ID:`s
(the 2026-08-15 incident shape) — the second is dropped, and its id appears
nowhere in what is kept, while the event ledger knows it as its own item. -/
theorem dd_drops_distinct_id :
    ddGo [] [(0, [.gen 1, .txt 5]), (0, [.gen 2, .txt 5])] =
      ([(0, [.gen 1, .txt 5])], [(0, [.gen 2, .txt 5])]) := by decide

/-! ### 4b. Decisions G9 / G10: a dropped id the ledger created is dismissed

`ledger_dismiss_housekeeping`, called by `org_dedup_within_file --apply` (G9)
and `org_resolve_id_conflicts` (G10), over the ids a repair removes. `known i`
abstracts "the ledger has an `item.create` for `i` and has not dismissed it"
(and the space has a ledger). Proved: every removed id is kept, dismissed, or
unknown to the ledger (`no_known_id_vanishes`); a dismissal never hits a kept
id or an unknown one (`dismissals_sound`). -/

def genIds : List Ln → List Nat
  | [] => []
  | .gen v :: r => v :: genIds r
  | .txt _ :: r => genIds r

def dismissals (known : Nat → Bool) (kept dropped : List Nat) : List Nat :=
  dropped.filter (fun i => !(kept.contains i) && known i)

theorem no_known_id_vanishes (known : Nat → Bool) (kept dropped : List Nat) :
    ∀ i ∈ dropped, i ∈ kept ∨ i ∈ dismissals known kept dropped ∨ known i = false := by
  intro i hi
  by_cases hk : i ∈ kept
  · exact Or.inl hk
  · cases hn : known i
    · exact Or.inr (Or.inr rfl)
    · exact Or.inr (Or.inl (List.mem_filter.mpr ⟨hi, by simp [hk, hn]⟩))

theorem dismissals_sound (known : Nat → Bool) (kept dropped : List Nat) :
    ∀ i ∈ dismissals known kept dropped, i ∈ dropped ∧ i ∉ kept ∧ known i = true := by
  intro i hi
  have ⟨hd, hc⟩ := List.mem_filter.mp hi
  simp at hc
  exact ⟨hd, hc.1, hc.2⟩

/-- G9 on the incident shape of `dd_drops_distinct_id`: id 2 is dismissed. -/
theorem dd_dismisses_distinct_id :
    dismissals (fun _ => true) (genIds [.gen 1, .txt 5]) (genIds [.gen 2, .txt 5]) = [2] := by
  decide

/-! ## 5. `org_resolve_id_conflicts.resolve` — which hunk side is upstream -/

inductive Op | merge | rebase | unknown
  deriving DecidableEq

structure Hunk where
  head     : List Nat
  incoming : List Nat
  deriving DecidableEq

/-- Git's labelling (the one external fact): in a merge `HEAD` is the local
branch and upstream is the incoming side; in a rebase `HEAD` is the upstream
commit being replayed onto. `git_fleet_sync` merges (DIP-0046). -/
def upstream : Op → Hunk → Option (List Nat)
  | .merge, h => some h.incoming
  | .rebase, h => some h.head
  | .unknown, _ => none

def oldResolve (_ : Op) (h : Hunk) : Option (List Nat) := some h.head

/-- Post-fix: `upstream_side()` reads the in-progress operation; unknown refuses. -/
def newResolve (op : Op) (h : Hunk) : Option (List Nat) :=
  match op with
  | .rebase => some h.head
  | .merge => some h.incoming
  | .unknown => none

theorem new_keeps_upstream (op : Op) (h : Hunk) : newResolve op h = upstream op h := by
  cases op <;> rfl

theorem old_keeps_local_on_merge : oldResolve .merge ⟨[1], [2]⟩ ≠ upstream .merge ⟨[1], [2]⟩ := by
  decide

/-- G10: in a merge the local (`HEAD`) id is discarded; when the ledger created
it, it is dismissed, and the kept upstream id is not. -/
theorem resolve_dismisses_discarded :
    dismissals (fun i => i == 1) [2] [1] = [1] ∧ upstream .merge ⟨[1], [2]⟩ = some [2] := by
  decide

/-! ## 6. `triage_utils._append_task_body` -/

structure Hd where
  target : Bool   -- heading contains the text and is a `**`+ heading
  closed : Bool   -- DONE / CANCELLED / CANCELED / DEFERRED (G11: `_CLOSED_STATES`)
  deriving DecidableEq, Repr

inductive L2 | endl | txt (v : Nat)
  deriving DecidableEq, Repr

/-- A heading and the lines up to the next heading. -/
structure Sec where
  hd   : Hd
  body : List L2
  deriving DecidableEq, Repr

def insAfterEnd (bl : List Nat) : List L2 → List L2
  | [] => []
  | .endl :: rest => .endl :: (bl.map .txt ++ rest)
  | l :: rest => l :: insAfterEnd bl rest

def eligible (s : Sec) : Bool := s.hd.target && !s.hd.closed && decide (L2.endl ∈ s.body)

/-- Post-fix: the first eligible section gets the body after its own first
`:END:`, unless THIS section already holds the first body line. -/
def appendBody (b0 : Nat) (bl : List Nat) : List Sec → List Sec
  | [] => []
  | s :: ss =>
    if eligible s then
      if L2.txt b0 ∈ s.body then s :: ss
      else ⟨s.hd, insAfterEnd (b0 :: bl) s.body⟩ :: ss
    else s :: appendBody b0 bl ss

theorem insAfterEnd_endl (bl : List Nat) (l : List L2) (h : L2.endl ∈ l) :
    L2.endl ∈ insAfterEnd bl l := by
  induction l with
  | nil => cases h
  | cons x xs ih =>
    cases x with
    | endl => simp [insAfterEnd]
    | txt v =>
      simp only [insAfterEnd]
      exact List.mem_cons_of_mem _ (ih (by simpa using h))

theorem insAfterEnd_b0 (b0 : Nat) (bl : List Nat) (l : List L2) (h : L2.endl ∈ l) :
    L2.txt b0 ∈ insAfterEnd (b0 :: bl) l := by
  induction l with
  | nil => cases h
  | cons x xs ih =>
    cases x with
    | endl => simp [insAfterEnd]
    | txt v =>
      simp only [insAfterEnd]
      exact List.mem_cons_of_mem _ (ih (by simpa using h))

theorem eligible_iff (s : Sec) : eligible s = true ↔ s.hd.target = true ∧ s.hd.closed = false ∧ L2.endl ∈ s.body := by
  simp [eligible, and_assoc]

theorem appendBody_idem (b0 : Nat) (bl : List Nat) (f : List Sec) :
    appendBody b0 bl (appendBody b0 bl f) = appendBody b0 bl f := by
  induction f with
  | nil => rfl
  | cons s ss ih =>
    simp only [appendBody]
    by_cases he : eligible s = true
    · by_cases hc : L2.txt b0 ∈ s.body
      · simp [he, hc, appendBody]
      · have he2 := (eligible_iff s).mp he
        have he' : eligible ⟨s.hd, insAfterEnd (b0 :: bl) s.body⟩ = true :=
          (eligible_iff _).mpr ⟨he2.1, he2.2.1, insAfterEnd_endl _ _ he2.2.2⟩
        have hc' := insAfterEnd_b0 b0 bl s.body he2.2.2
        simp [he, hc, appendBody, he', hc']
    · simp [he, appendBody, ih]

/-- Writes only inside the first eligible target's own section: everything
before it is untouched and ineligible, everything after it is untouched. -/
theorem appendBody_local (b0 : Nat) (bl : List Nat) (f : List Sec) :
    appendBody b0 bl f = f ∨
    ∃ pre s ss, f = pre ++ s :: ss ∧ eligible s = true ∧ (∀ p ∈ pre, eligible p = false) ∧
      appendBody b0 bl f = pre ++ ⟨s.hd, insAfterEnd (b0 :: bl) s.body⟩ :: ss := by
  induction f with
  | nil => exact Or.inl rfl
  | cons s ss ih =>
    by_cases he : eligible s = true
    · by_cases hc : L2.txt b0 ∈ s.body
      · left; simp [appendBody, he, hc]
      · right; exact ⟨[], s, ss, rfl, he, by simp, by simp [appendBody, he, hc]⟩
    · rcases ih with h | ⟨pre, t, ts, hf, ht, hpre, hr⟩
      · left; simp [appendBody, he, h]
      · right
        refine ⟨s :: pre, t, ts, by simp [hf], ht, ?_, by simp [appendBody, he, hr]⟩
        intro p hp
        rcases List.mem_cons.mp hp with rfl | hp
        · simpa using he
        · exact hpre p hp

/-- And it does write: after a run, the first eligible section carries the body. -/
theorem appendBody_effect (b0 : Nat) (bl : List Nat) (f : List Sec) :
    ∀ pre s ss, f = pre ++ s :: ss → eligible s = true → (∀ p ∈ pre, eligible p = false) →
      ∃ s', appendBody b0 bl f = pre ++ s' :: ss ∧ s'.hd = s.hd ∧ L2.txt b0 ∈ s'.body := by
  intro pre
  induction pre generalizing f with
  | nil =>
    intro s ss hf he _
    subst hf
    by_cases hc : L2.txt b0 ∈ s.body
    · exact ⟨s, by simp [appendBody, he, hc], rfl, hc⟩
    · exact ⟨⟨s.hd, insAfterEnd (b0 :: bl) s.body⟩, by simp [appendBody, he, hc], rfl,
        insAfterEnd_b0 b0 bl s.body ((eligible_iff s).mp he).2.2⟩
  | cons p ps ih =>
    intro s ss hf he hpre
    subst hf
    obtain ⟨s', h1, h2, h3⟩ := ih (ps ++ s :: ss) s ss rfl he (fun q hq => hpre q (by simp [hq]))
    have hp := hpre p (by simp)
    exact ⟨s', by simp [appendBody, hp, h1], h2, h3⟩

/-! Pre-fix, on the flat line list: the first `:END:` after the heading, with
no boundary, and "already recorded" searched over the whole file. -/

inductive FL | hd (target closed : Bool) | endl | txt (v : Nat)
  deriving DecidableEq, Repr

def oldAppendGo (whole : List FL) (b0 : Nat) (bl : List Nat) : Bool → List FL → List FL
  | _, [] => []
  | found, x :: xs =>
    match x with
    | .hd t c => if !found && t && !c then x :: oldAppendGo whole b0 bl true xs
                 else x :: oldAppendGo whole b0 bl found xs
    | .endl => if found then
                 (if whole.contains (.txt b0) then x :: xs else x :: ((b0 :: bl).map .txt ++ xs))
               else x :: oldAppendGo whole b0 bl found xs
    | .txt v => .txt v :: oldAppendGo whole b0 bl found xs

def oldAppend (b0 : Nat) (bl : List Nat) (f : List FL) : List FL := oldAppendGo f b0 bl false f

/-- The target (no drawer) is followed by another task; the body lands under
that other task's `:END:`. -/
theorem old_append_writes_into_next_task :
    oldAppend 9 [] [.hd true false, .hd false false, .endl] =
      [.hd true false, .hd false false, .endl, .txt 9] := by decide

/-- Another task already has the first body line; the target is never written. -/
theorem old_append_skips_on_foreign_line :
    oldAppend 9 [] [.hd false false, .endl, .txt 9, .hd true false, .endl] =
      [.hd false false, .endl, .txt 9, .hd true false, .endl] ∧
    appendBody 9 [] [⟨⟨false, false⟩, [.endl, .txt 9]⟩, ⟨⟨true, false⟩, [.endl]⟩] =
      [⟨⟨false, false⟩, [.endl, .txt 9]⟩, ⟨⟨true, false⟩, [.endl, .txt 9]⟩] := by decide

end DatacoreSpec.OrgTools
