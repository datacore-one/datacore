/-!
# Hash chain, fork detection and prefix-only conflict resolution

* `ledger/verify.py` / `seal._chain_issue`: a log is valid when seq runs
  0,1,2,…, the first `prev` is GENESIS, each `prev` is the previous event's
  hash, and each hash is recomputed from the body.
* `ledger/fork.py` compares `(actor, seq) → hash` between two copies and
  reports a fork on any disagreement.
* `resolve_ledger_conflicts.prefix_resolution` accepts a git conflict only when
  one side's TEXT is a prefix of the other's (`ours.startswith(theirs)`), and
  keeps the longer side.

Proved here, assuming the hash is collision-free (`hinj`):

1. A valid chain commits to its whole history: two valid chains of the same
   length with the same last hash are identical. Hence comparing hashes at a
   single seq compares entire histories up to it, which is why `fork.detect`
   need not compare bodies.
2. The TEXT prefix test is exactly the EVENT prefix test, for newline-terminated
   canonical JSON lines (which never contain a line break: `json.dumps` escapes
   every control character and `ensure_ascii` escapes the rest).
3. So the resolver never drops an event: the kept side contains the other, and
   is itself a valid chain.
-/

namespace LedgerSpec.Chain

/-! ## Chains -/

structure CE (B H : Type) where
  seq  : Nat
  prev : H
  hash : H
  body : B

variable {B H : Type}

def Valid (hf : Nat → H → B → H) : H → Nat → List (CE B H) → Prop
  | _, _, [] => True
  | p, n, e :: es => e.seq = n ∧ e.prev = p ∧ e.hash = hf e.seq e.prev e.body ∧
      Valid hf e.hash (n + 1) es

theorem valid_prefix (hf : Nat → H → B → H) :
    ∀ (A C : List (CE B H)) (p : H) (n : Nat), Valid hf p n A → C <+: A → Valid hf p n C
  | _, [], _, _, _, _ => trivial
  | [], _ :: _, _, _, _, hpre => absurd (List.prefix_nil.1 hpre) (by simp)
  | a :: A, c :: C, p, n, hv, hpre => by
    obtain ⟨hac, hrest⟩ := List.cons_prefix_cons.1 hpre
    subst hac
    exact ⟨hv.1, hv.2.1, hv.2.2.1, valid_prefix hf A C _ _ hv.2.2.2 hrest⟩

/-- **Commitment.** Same non-zero length, same last hash ⇒ same chain (and same start). -/
theorem commit (hf : Nat → H → B → H)
    (hinj : ∀ s1 p1 b1 s2 p2 b2, hf s1 p1 b1 = hf s2 p2 b2 → s1 = s2 ∧ p1 = p2 ∧ b1 = b2) :
    ∀ (A C : List (CE B H)) (pA pC : H) (n : Nat), A ≠ [] →
      Valid hf pA n A → Valid hf pC n C → A.length = C.length →
      A.getLast?.map CE.hash = C.getLast?.map CE.hash → A = C ∧ pA = pC
  | [], _, _, _, _, hne, _, _, _, _ => absurd rfl hne
  | _ :: _, [], _, _, _, _, _, _, hlen, _ => by simp at hlen
  | a :: A, c :: C, pA, pC, n, _, hvA, hvC, hlen, hlast => by
    obtain ⟨hsa, hpa, hha, hvA'⟩ := hvA
    obtain ⟨hsc, hpc, hhc, hvC'⟩ := hvC
    have hlen' : A.length = C.length := by simpa using hlen
    -- first establish that the heads carry the same hash
    have hheads : a.hash = c.hash ∧ A = C := by
      cases A with
      | nil =>
        cases C with
        | nil => simp at hlast; exact ⟨hlast, rfl⟩
        | cons _ _ => simp at hlen'
      | cons a2 A2 =>
        cases C with
        | nil => simp at hlen'
        | cons c2 C2 =>
          have hl : (a2 :: A2).getLast?.map CE.hash = (c2 :: C2).getLast?.map CE.hash := by
            simpa [List.getLast?_cons_cons] using hlast
          obtain ⟨hAC, hp⟩ := commit hf hinj _ _ _ _ _ (by simp) hvA' hvC' hlen' hl
          exact ⟨hp, hAC⟩
    obtain ⟨hh, rfl⟩ := hheads
    rw [hha, hhc] at hh
    obtain ⟨_, hprev, hbody⟩ := hinj _ _ _ _ _ _ hh
    have hac : a = c := by
      cases a; cases c; simp_all
    subst hac
    exact ⟨rfl, hpa.symm.trans hpc⟩

/-- **Fork visibility.** If two copies of one log agree on the hash at seq `k`,
they agree on every event up to and including `k`. So `fork.detect`'s
per-seq hash comparison sees any divergence at the first shared seq at or
after it. -/
theorem fork_visible (hf : Nat → H → B → H)
    (hinj : ∀ s1 p1 b1 s2 p2 b2, hf s1 p1 b1 = hf s2 p2 b2 → s1 = s2 ∧ p1 = p2 ∧ b1 = b2)
    {A C : List (CE B H)} {p : H} {n k : Nat}
    (hA : Valid hf p n A) (hC : Valid hf p n C) (hkA : k < A.length) (hkC : k < C.length)
    (hk : (A.take (k + 1)).getLast?.map CE.hash = (C.take (k + 1)).getLast?.map CE.hash) :
    A.take (k + 1) = C.take (k + 1) :=
  (commit hf hinj _ _ p p n
    (by intro h; rw [List.take_eq_nil_iff] at h
        rcases h with h | h
        · omega
        · subst h; simp at hkA)
    (valid_prefix hf _ _ _ _ hA (List.take_prefix _ _))
    (valid_prefix hf _ _ _ _ hC (List.take_prefix _ _))
    (by rw [List.length_take, List.length_take]; omega) hk).1

/-! ## Text prefix = event prefix -/

/-- Newline-terminated serialisation of a log, one event per line. -/
def ser : List (List Char) → List Char
  | [] => []
  | l :: ls => l ++ '\n' :: ser ls

theorem ser_append (A C : List (List Char)) : ser (A ++ C) = ser A ++ ser C := by
  induction A with
  | nil => rfl
  | cons a A ih => simp [ser, ih]

theorem line_prefix : ∀ (b a : List Char) (s t : List Char), '\n' ∉ b → '\n' ∉ a →
    (b ++ '\n' :: s) <+: (a ++ '\n' :: t) → b = a ∧ s <+: t
  | [], [], s, t, _, _, h => ⟨rfl, (List.cons_prefix_cons.1 h).2⟩
  | [], c :: a, s, t, _, ha, h => by
    have := (List.cons_prefix_cons.1 h).1
    subst this; simp at ha
  | c :: b, [], s, t, hb, _, h => by
    have := (List.cons_prefix_cons.1 h).1
    subst this; simp at hb
  | c :: b, d :: a, s, t, hb, ha, h => by
    obtain ⟨hcd, h'⟩ := List.cons_prefix_cons.1 h
    subst hcd
    obtain ⟨hba, hst⟩ := line_prefix b a s t (by simp_all) (by simp_all) h'
    exact ⟨by rw [hba], hst⟩

/-- **`ours.startswith(theirs)` decides event-prefix exactly** for logs whose
lines contain no newline. -/
theorem ser_prefix_iff : ∀ (C A : List (List Char)),
    (∀ l ∈ C, '\n' ∉ l) → (∀ l ∈ A, '\n' ∉ l) → (ser C <+: ser A ↔ C <+: A)
  | [], A, _, _ => by simp [ser]
  | c :: C, [], _, _ => by
    constructor
    · intro h; have := List.prefix_nil.1 h; simp [ser] at this
    · intro h; have := List.prefix_nil.1 h; simp at this
  | c :: C, a :: A, hC, hA => by
    constructor
    · intro h
      obtain ⟨hca, h'⟩ := line_prefix c a (ser C) (ser A) (hC c (by simp)) (hA a (by simp)) h
      exact List.cons_prefix_cons.2 ⟨hca, (ser_prefix_iff C A
        (fun l hl => hC l (by simp [hl])) (fun l hl => hA l (by simp [hl]))).1 h'⟩
    · rintro ⟨t, ht⟩
      rw [← ht, ser_append]; exact List.prefix_append _ _

/-- **The resolver is lossless.** If both conflict stages are valid chains and
the text test passes, every event of the shorter side is in the kept side, and
the kept side is a valid chain. -/
theorem resolution_lossless (hf : Nat → H → B → H) (enc : CE B H → List Char)
    (henc : ∀ e, '\n' ∉ enc e) (hencInj : ∀ e e', enc e = enc e' → e = e')
    (A C : List (CE B H)) (p : H) (hA : Valid hf p 0 A) (_hC : Valid hf p 0 C)
    (htext : ser (C.map enc) <+: ser (A.map enc)) :
    C <+: A ∧ Valid hf p 0 A := by
  refine ⟨?_, hA⟩
  have hl := (ser_prefix_iff _ _ (by simp [henc]) (by simp [henc])).1 htext
  -- map by an injective encoding reflects prefixes
  obtain ⟨t, ht⟩ := hl
  have : ∀ (X Y : List (CE B H)) (u : List (List Char)), X.map enc ++ u = Y.map enc →
      ∃ v, Y = X ++ v := by
    intro X
    induction X with
    | nil => intro Y _ _; exact ⟨Y, rfl⟩
    | cons x X ih =>
      intro Y u h
      cases Y with
      | nil => simp at h
      | cons y Y =>
        simp only [List.map_cons, List.cons_append, List.cons.injEq] at h
        obtain ⟨hxy, h⟩ := h
        obtain ⟨v, rfl⟩ := ih Y u h
        exact ⟨v, by rw [hencInj _ _ hxy]; rfl⟩
  obtain ⟨v, hv⟩ := this C A t ht
  exact ⟨v, hv.symm⟩

end LedgerSpec.Chain
