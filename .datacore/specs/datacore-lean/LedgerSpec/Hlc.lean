/-!
# Hybrid logical clock — `ledger/hlc.py` and the stamp floor in `ledger/log.py`

Python stamps are `f"{pt:013d}.{c:04d}.{actor}"` and every reader orders events
by comparing those STRINGS (`read_events` sorts on `e.hlc`). Two things must hold
for that to be sound:

1. `tick` never goes backwards: the new stamp is strictly after its floor.
2. String order on the encoding IS the intended order on `(pt, c, actor)`.
   That is only true because both numbers are zero-padded to a fixed width
   and stay inside it (`c ≤ 9999` is enforced by `tick`; `pt < 10^13` holds
   until the year 2286).

Characters are modelled as their code points (`Nat`), strings as `List Nat`,
and Python's string `<` as `lexLt` (code-point lexicographic, a proper prefix is
smaller — exactly Python's rule).
-/

namespace LedgerSpec.Hlc

/-! ## Stamps and `tick` -/

structure Stamp where
  pt : Nat
  c  : Nat
  deriving DecidableEq, Repr

/-- The order the fixed-width `pt.c` prefix is meant to encode. -/
def Stamp.lt (a b : Stamp) : Prop := a.pt < b.pt ∨ (a.pt = b.pt ∧ a.c < b.c)
def Stamp.le (a b : Stamp) : Prop := a = b ∨ a.lt b

def maxCounter : Nat := 9999

/-- `hlc.tick(actor, last, _now_ms=now)`. `none` models the `ValueError` on
counter overflow. -/
def tick (now : Nat) : Option Stamp → Option Stamp
  | none => some ⟨now, 0⟩
  | some l =>
    let s : Stamp := if now > l.pt then ⟨now, 0⟩ else ⟨l.pt, l.c + 1⟩
    if s.c > maxCounter then none else some s

/-- **Strict monotonicity**: a stamp ticked from a floor sorts after the floor. -/
theorem tick_strict {now : Nat} {l s : Stamp} (h : tick now (some l) = some s) :
    l.lt s := by
  unfold tick at h
  by_cases hn : now > l.pt
  · simp [hn] at h
    subst h; exact Or.inl hn
  · simp [hn] at h
    obtain ⟨_, rfl⟩ := h
    exact Or.inr ⟨rfl, Nat.lt_succ_self _⟩

/-- The physical component never falls behind the local clock. -/
theorem tick_ge_now {now : Nat} {l : Option Stamp} {s : Stamp}
    (h : tick now l = some s) : now ≤ s.pt := by
  cases l with
  | none => simp [tick] at h; subst h; exact Nat.le_refl _
  | some l =>
    unfold tick at h
    by_cases hn : now > l.pt
    · simp [hn] at h; subst h; exact Nat.le_refl _
    · simp [hn] at h; obtain ⟨_, rfl⟩ := h; simp; omega

/-- Every emitted stamp keeps its counter inside the 4-digit field. -/
theorem tick_counter_bounded {now : Nat} {l : Option Stamp} {s : Stamp}
    (h : tick now l = some s) : s.c ≤ maxCounter := by
  cases l with
  | none => simp [tick] at h; subst h; simp [maxCounter]
  | some l =>
    unfold tick at h
    by_cases hn : now > l.pt
    · simp [hn] at h; subst h; simp [maxCounter]
    · simp [hn] at h; obtain ⟨hc, rfl⟩ := h; simp [maxCounter] at hc ⊢; omega

/-- `tick` refuses exactly when the clock has not advanced AND the counter is
full: the only failure is the deliberate overflow refusal. -/
theorem tick_none_iff {now : Nat} {l : Stamp} :
    tick now (some l) = none ↔ (now ≤ l.pt ∧ maxCounter ≤ l.c) := by
  unfold tick
  by_cases hn : now > l.pt
  · simp [hn, maxCounter] <;> omega
  · simp [hn, maxCounter]; omega

/-! ## The cross-actor floor (`EventLog.append`)

`append` takes the max tail stamp over its own file and every sibling file,
then ticks from it. So an append that STARTS after another append has landed
sorts after it: same-machine ordering is append-causal. -/

theorem Stamp.lt_of_le_of_lt {a b c : Stamp} (h1 : a.le b) (h2 : b.lt c) : a.lt c := by
  rcases h1 with rfl | h1
  · exact h2
  · unfold Stamp.lt at *; omega

theorem append_after_all_tails {now : Nat} {tails : List Stamp} {floor s : Stamp}
    (hfloor : ∀ t ∈ tails, t.le floor)
    (htick : tick now (some floor) = some s) :
    ∀ t ∈ tails, t.lt s :=
  fun t ht => Stamp.lt_of_le_of_lt (hfloor t ht) (tick_strict htick)

/-! ## Encoding: fixed-width decimal preserves order -/

/-- Python's `str.__lt__` on code points. -/
def lexLt : List Nat → List Nat → Prop
  | [], [] => False
  | [], _ :: _ => True
  | _ :: _, [] => False
  | x :: xs, y :: ys => x < y ∨ (x = y ∧ lexLt xs ys)

/-- `f"{n:0{w}d}"` as digit values, most significant first. -/
def digits : Nat → Nat → List Nat
  | 0, _ => []
  | w + 1, n => n / 10 ^ w :: digits w (n % 10 ^ w)

theorem digits_length (w n : Nat) : (digits w n).length = w := by
  induction w generalizing n with
  | zero => rfl
  | succ w ih => simp [digits, ih]

theorem pow10_pos (w : Nat) : 0 < 10 ^ w := Nat.pow_pos (by decide)

/-- Every element really is one decimal digit when the number fits the width.
(If `pt ≥ 10^13` the leading "digit" is ≥ 10 — i.e. Python would emit a longer
string and the fixed-width argument below would no longer apply.) -/
theorem digits_are_digits : ∀ (w n : Nat), n < 10 ^ w → ∀ d ∈ digits w n, d < 10
  | 0, _, _, d, hd => by simp [digits] at hd
  | w + 1, n, hn, d, hd => by
    simp only [digits, List.mem_cons] at hd
    rcases hd with rfl | hd
    · rw [Nat.pow_succ] at hn
      exact Nat.div_lt_of_lt_mul hn
    · exact digits_are_digits w _ (Nat.mod_lt _ (pow10_pos w)) d hd

/-- Division/remainder decomposition of `<` for a positive base. -/
theorem lt_iff_div_mod {a b p : Nat} (hp : 0 < p) :
    a < b ↔ a / p < b / p ∨ (a / p = b / p ∧ a % p < b % p) := by
  have ha := Nat.div_add_mod a p
  have hb := Nat.div_add_mod b p
  constructor
  · intro h
    rcases Nat.lt_trichotomy (a / p) (b / p) with hq | hq | hq
    · exact Or.inl hq
    · refine Or.inr ⟨hq, ?_⟩
      rw [hq] at ha
      generalize p * (b / p) = k at ha hb
      omega
    · have := Nat.div_le_div_right (c := p) (Nat.le_of_lt h)
      omega
  · rintro (hq | ⟨hq, hr⟩)
    · apply Nat.lt_of_not_le
      intro hba
      have := Nat.div_le_div_right (c := p) hba
      omega
    · rw [hq] at ha
      generalize p * (b / p) = k at ha hb
      omega

/-- **Fixed-width zero padding preserves order**: for numbers that fit the
width, comparing the padded strings is comparing the numbers. -/
theorem digits_lt_iff : ∀ (w a b : Nat), a < 10 ^ w → b < 10 ^ w →
    (lexLt (digits w a) (digits w b) ↔ a < b)
  | 0, a, b, ha, hb => by simp at ha hb; subst ha; subst hb; simp [digits, lexLt]
  | w + 1, a, b, _, _ => by
    simp only [digits, lexLt]
    rw [digits_lt_iff w _ _ (Nat.mod_lt _ (pow10_pos w)) (Nat.mod_lt _ (pow10_pos w))]
    exact (lt_iff_div_mod (pow10_pos w)).symm

/-- Padding is injective on numbers that fit. -/
theorem digits_inj : ∀ (w a b : Nat), a < 10 ^ w → b < 10 ^ w →
    digits w a = digits w b → a = b
  | 0, a, b, ha, hb, _ => by simp at ha hb; omega
  | w + 1, a, b, _, _, h => by
    simp only [digits, List.cons.injEq] at h
    obtain ⟨hq, ht⟩ := h
    have hr := digits_inj w _ _ (Nat.mod_lt _ (pow10_pos w)) (Nat.mod_lt _ (pow10_pos w)) ht
    have ha := Nat.div_add_mod a (10 ^ w)
    have hb := Nat.div_add_mod b (10 ^ w)
    rw [hq, hr] at ha
    omega

/-- Comparing two strings that share a fixed-width prefix length. -/
theorem lexLt_append : ∀ (xs ys u v : List Nat), xs.length = ys.length →
    (lexLt (xs ++ u) (ys ++ v) ↔ lexLt xs ys ∨ (xs = ys ∧ lexLt u v))
  | [], [], u, v, _ => by simp [lexLt]
  | [], _ :: _, _, _, h => by simp at h
  | _ :: _, [], _, _, h => by simp at h
  | x :: xs, y :: ys, u, v, h => by
    simp only [List.length_cons, Nat.add_right_cancel_iff] at h
    simp only [List.cons_append, lexLt, List.cons.injEq]
    rw [lexLt_append xs ys u v h]
    constructor
    · rintro (h1 | ⟨rfl, h2 | ⟨rfl, h3⟩⟩)
      · exact Or.inl (Or.inl h1)
      · exact Or.inl (Or.inr ⟨rfl, h2⟩)
      · exact Or.inr ⟨⟨rfl, rfl⟩, h3⟩
    · rintro ((h1 | ⟨rfl, h2⟩) | ⟨⟨rfl, rfl⟩, h3⟩)
      · exact Or.inl h1
      · exact Or.inr ⟨rfl, Or.inl h2⟩
      · exact Or.inr ⟨rfl, Or.inr ⟨rfl, h3⟩⟩

/-- Code point of `.`. -/
def dot : Nat := 46

/-- `f"{pt:013d}.{c:04d}.{actor}"`. -/
def encode (s : Stamp) (actor : List Nat) : List Nat :=
  digits 13 s.pt ++ ([dot] ++ (digits 4 s.c ++ ([dot] ++ actor)))

/-- In range: the physical time fits 13 digits (true until 2286) and the
counter fits 4 (enforced by `tick`, see `tick_counter_bounded`). -/
def InRange (s : Stamp) : Prop := s.pt < 10 ^ 13 ∧ s.c < 10 ^ 4

/-- **The sort `read_events` performs is the intended order.** For in-range
stamps, comparing encoded strings compares `(pt, c)` first and the actor name
only on an exact `(pt, c)` tie. -/
theorem encode_lt_iff {s t : Stamp} {a b : List Nat} (hs : InRange s) (ht : InRange t) :
    lexLt (encode s a) (encode t b) ↔ s.lt t ∨ (s = t ∧ lexLt a b) := by
  unfold encode
  rw [lexLt_append _ _ _ _ (by simp [digits_length]),
      digits_lt_iff 13 _ _ hs.1 ht.1]
  have hdot : ∀ u v, lexLt ([dot] ++ u) ([dot] ++ v) ↔ lexLt u v := by
    intro u v; simp [lexLt, dot]
  rw [hdot, lexLt_append _ _ _ _ (by simp [digits_length]),
      digits_lt_iff 4 _ _ hs.2 ht.2, hdot]
  constructor
  · rintro (h | ⟨hp, (h | ⟨hc, h⟩)⟩)
    · exact Or.inl (Or.inl h)
    · exact Or.inl (Or.inr ⟨digits_inj 13 _ _ hs.1 ht.1 hp, h⟩)
    · refine Or.inr ⟨?_, h⟩
      have := digits_inj 13 _ _ hs.1 ht.1 hp
      have := digits_inj 4 _ _ hs.2 ht.2 hc
      cases s; cases t; simp_all
  · rintro ((h | ⟨hp, hc⟩) | ⟨rfl, h⟩)
    · exact Or.inl h
    · exact Or.inr ⟨by rw [hp], Or.inl hc⟩
    · exact Or.inr ⟨rfl, Or.inr ⟨rfl, h⟩⟩

/-- Corollary: a ticked stamp's STRING sorts after its floor's string, whatever
the two actor names are. This is what makes the floor argument in `log.py`
survive the round-trip through string comparison. -/
theorem tick_encoded_after {now : Nat} {l s : Stamp} {a b : List Nat}
    (hl : InRange l) (hs : InRange s) (h : tick now (some l) = some s) :
    lexLt (encode l a) (encode s b) :=
  (encode_lt_iff hl hs).2 (Or.inl (tick_strict h))

end LedgerSpec.Hlc
