/-!
# Detectors — alarms whose failure mode is a false "ok"

Models of five classifiers in `.datacore/lib`:

* `detectors/actor_presence.py` — `classify`, `next_baseline`, `acknowledge` (D4)
* `detectors/seq_gap.py`        — `head_seq` and the per-log verdict in `scan_space`
* `detectors/id_churn.py`       — `apply_baseline`, the noise floor (D2)
* `reliability_scoreboard.py`   — `r5_reachable`'s journal branch, the streak chain
* `today_registry.py`           — `validate` against `plan`

Each section has the pre-fix function (`…Old`), a concrete counterexample that
was replayed against the real Python, the fixed function, and the soundness
theorem "reports ok ⇒ the healthy condition holds".

Abstractions (all safe for the stated properties):
* Space names and ids are `Nat`; only equality matters.
* A log is the list of per-line parse results: `some seq` for a line with an
  integer `seq`, `none` for a torn/corrupt line. Blank lines are dropped.
* Timestamps, sleep accounting and file IO are outside the model. The seq-gap
  grace ("pending") is an input bit `young`.
-/

namespace DatacoreSpec.Detectors

/-! ## 1. actor_presence -/

abbrev Space := Nat

inductive APStatus | ok | missing | stalled | noLogYet
  deriving DecidableEq, Repr

/-- Python `dict.get` on a space → seq map. `none` = absent OR unreadable. -/
def lookup (l : List (Space × Option Nat)) (s : Space) : Option Nat :=
  match l.find? (fun p => p.1 == s) with
  | some p => p.2
  | none => none

/-- Pre-fix `main` loop body: only spaces present NOW are visited, and a `None`
seq is skipped by the comparison. -/
def classifyOld (here : List (Space × Option Nat))
    (prev : Option (List (Space × Option Nat))) : APStatus :=
  if here = [] then (if prev.isSome then .missing else .noLogYet)
  else if here.any (fun p => match p.2, lookup (prev.getD []) p.1 with
                             | some c, some w => decide (c < w)
                             | _, _ => false) then .stalled
  else .ok

/-- Pre-fix baseline update: `ok` and `stalled` both overwrite it. -/
def nextOld (prev : Option (List (Space × Option Nat))) (st : APStatus)
    (here : List (Space × Option Nat)) : Option (List (Space × Option Nat)) :=
  if st = .ok ∨ st = .stalled then some here else prev

def lostAny (here prevL : List (Space × Option Nat)) : Bool :=
  prevL.any fun p => p.2.isSome && (lookup here p.1).isNone

def backAny (here prevL : List (Space × Option Nat)) : Bool :=
  prevL.any fun p => match p.2, lookup here p.1 with
    | some q, some c => decide (c < q)
    | _, _ => false

/-- Post-fix `classify`. -/
def classify (here : List (Space × Option Nat))
    (prev : Option (List (Space × Option Nat))) : APStatus :=
  if !(here.any fun p => p.2.isSome) then (if prev.isSome then .missing else .noLogYet)
  else if lostAny here (prev.getD []) then .missing
  else if backAny here (prev.getD []) then .stalled
  else .ok

/-- Post-fix `next_baseline`: only a healthy observation moves it. -/
def next (prev : Option (List (Space × Option Nat))) (st : APStatus)
    (here : List (Space × Option Nat)) : Option (List (Space × Option Nat)) :=
  if st = .ok then some (here.filter fun p => p.2.isSome) else prev

/-- (a) a log with no readable events, previously at seq 4: reported ok. -/
theorem ap_old_unreadable_is_ok :
    classifyOld [(0, none)] (some [(0, some 4)]) = .ok ∧
    classify [(0, none)] (some [(0, some 4)]) = .missing := by decide

/-- (b) the log deleted from space 1 while space 0 still holds it: ok. -/
theorem ap_old_deletion_elsewhere_is_ok :
    classifyOld [(0, some 5)] (some [(0, some 5), (1, some 5)]) = .ok ∧
    classify [(0, some 5)] (some [(0, some 5), (1, some 5)]) = .missing := by decide

/-- (c) STALLED rewrote the baseline, so the same truncated log reads ok on the
next run: the detector healed the damage it had just reported. -/
theorem ap_old_stalled_self_heals :
    let here := [(0, some 3)]
    let prev := some [(0, some 9)]
    classifyOld here prev = .stalled ∧
    classifyOld here (nextOld prev (classifyOld here prev) here) = .ok := by decide

/-- **Soundness.** `ok` means every space in the baseline still has a readable
log, at or past its baseline seq. -/
theorem ap_ok_sound (here : List (Space × Option Nat)) (prev : Option (List (Space × Option Nat)))
    (h : classify here prev = .ok) :
    ∀ s q, (s, some q) ∈ prev.getD [] → ∃ c, lookup here s = some c ∧ q ≤ c := by
  intro s q hm
  unfold classify at h
  split at h
  · split at h <;> cases h
  · split at h
    · cases h
    · split at h
      · cases h
      · rename_i _ hl hb
        simp only [lostAny, Bool.not_eq_true, List.any_eq_false] at hl
        simp only [backAny, Bool.not_eq_true, List.any_eq_false] at hb
        have h1 := hl _ hm
        have h2 := hb _ hm
        cases hc : lookup here s with
        | none => simp [hc] at h1
        | some c => simp [hc] at h2; exact ⟨c, rfl, h2⟩

/-- `ok` also means the actor has at least one readable log now: a log with no
readable events is never "ok" (at best "no-log-yet" for a never-seen actor). -/
theorem ap_ok_has_readable (here : List (Space × Option Nat))
    (prev : Option (List (Space × Option Nat))) (h : classify here prev = .ok) :
    ∃ p ∈ here, p.2.isSome = true := by
  unfold classify at h
  split at h
  · split at h <;> cases h
  · rename_i hc
    simpa [Option.isSome_iff_ne_none] using hc

/-- **No self-healing.** A failing verdict keeps the baseline, so the same
observation on the next run gives the same verdict. -/
theorem ap_failing_is_sticky (here : List (Space × Option Nat))
    (prev : Option (List (Space × Option Nat)))
    (h : classify here prev = .missing ∨ classify here prev = .stalled) :
    classify here (next prev (classify here prev) here) = classify here prev := by
  unfold next
  rcases h with h | h <;> simp [h]

/-! ### 1b. `--acknowledge` (owner decision D4, 2026-09-23)

`acknowledge` makes the actor's CURRENT readable state the baseline and marks
the entry acknowledged. `classify(here, prev, acknowledged)` differs from
`classify` in exactly one case: an acknowledged EMPTY baseline (a retired actor
whose logs are all gone) with nothing readable now is `ok`, since an empty
baseline requires nothing. -/

/-- `acknowledge(...)["spaces"]`: the readable spaces now. -/
def ackBase (here : List (Space × Option Nat)) : List (Space × Option Nat) :=
  here.filter fun p => p.2.isSome

/-- Post-D4 `classify` with its `acknowledged` flag. -/
def classifyA (acked : Bool) (here : List (Space × Option Nat))
    (prev : Option (List (Space × Option Nat))) : APStatus :=
  if acked && prev.isSome && !((prev.getD []).any fun p => p.2.isSome)
      && !(here.any fun p => p.2.isSome) then .ok
  else classify here prev

/-- In a Python dict each space occurs once; then `lookup` finds its value. -/
theorem lookup_of_mem : ∀ (l : List (Space × Option Nat)) (s : Space) (c : Nat),
    (l.map Prod.fst).Nodup → (s, some c) ∈ l → lookup l s = some c
  | [], _, _, _, h => by cases h
  | (s', v) :: t, s, c, hn, hm => by
    simp only [List.map_cons, List.nodup_cons] at hn
    simp only [List.mem_cons] at hm
    by_cases hs : s' = s
    · subst hs
      rcases hm with hm | hm
      · cases hm; simp [lookup, List.find?]
      · exact absurd (List.mem_map.mpr ⟨_, hm, rfl⟩) hn.1
    · rcases hm with hm | hm
      · cases hm; exact absurd rfl hs
      · have := lookup_of_mem t s c hn.2 hm
        have hb : (s' == s) = false := by simpa using hs
        simpa [lookup, List.find?, hb] using this

/-- **D4.** Acknowledged, then unchanged, reads ok: whatever the actor's state
was (MISSING, STALLED, or no log left at all), accepting it clears the verdict
until something changes again. -/
theorem ap_ack_then_unchanged_ok (here : List (Space × Option Nat))
    (hn : (here.map Prod.fst).Nodup) :
    classifyA true here (some (ackBase here)) = .ok := by
  unfold classifyA
  by_cases hr : (here.any fun p => p.2.isSome) = true
  · have hbase : ((ackBase here).any fun p => p.2.isSome) = true := by
      obtain ⟨p, hp, hs⟩ := List.any_eq_true.mp hr
      exact List.any_eq_true.mpr ⟨p, List.mem_filter.mpr ⟨hp, hs⟩, hs⟩
    simp only [hr, hbase, Option.getD_some, Option.isSome_some, Bool.true_and,
      Bool.not_true, Bool.and_false, Bool.false_eq_true, ↓reduceIte]
    unfold classify
    have hl : lostAny here (ackBase here) = false := by
      simp only [lostAny, List.any_eq_false, Bool.and_eq_true, not_and]
      intro p hp hs
      obtain ⟨q, hq⟩ := Option.isSome_iff_exists.mp hs
      have hm : (p.1, some q) ∈ here := by
        have := (List.mem_filter.mp hp).1; rw [← hq]; exact this
      simp [lookup_of_mem here p.1 q hn hm]
    have hb : backAny here (ackBase here) = false := by
      simp only [backAny, List.any_eq_false]
      intro p hp
      cases hq : p.2 with
      | none => simp
      | some q =>
        have hm : (p.1, some q) ∈ here := by
          have := (List.mem_filter.mp hp).1; rw [← hq]; exact this
        simp [lookup_of_mem here p.1 q hn hm]
    simp [hr, hl, hb]
  · have he : ackBase here = [] := by
      simp only [ackBase, List.filter_eq_nil_iff]
      intro p hp hs
      exact hr (List.any_eq_true.mpr ⟨p, hp, hs⟩)
    simp [he, hr]

/-- **D4, no blanket pass.** After acknowledging, the actor is held to the
state it was acknowledged in: a later ok means every space readable at the
acknowledgement is still readable, at or past that seq. -/
theorem ap_ack_then_held (here here' : List (Space × Option Nat))
    (h : classifyA true here' (some (ackBase here)) = .ok) :
    ∀ s q, (s, some q) ∈ here → ∃ c, lookup here' s = some c ∧ q ≤ c := by
  intro s q hm
  have hm' : (s, some q) ∈ ackBase here := List.mem_filter.mpr ⟨hm, rfl⟩
  have hne : ((ackBase here).any fun p => p.2.isSome) = true :=
    List.any_eq_true.mpr ⟨_, hm', rfl⟩
  have hc : classify here' (some (ackBase here)) = .ok := by
    unfold classifyA at h; simpa [hne] using h
  exact ap_ok_sound here' _ hc s q (by simpa using hm')

/-- Without the flag nothing changes: every earlier theorem about `classify`
(soundness, stickiness) is about `classifyA false`. -/
theorem ap_unacked_is_classify (here : List (Space × Option Nat))
    (prev : Option (List (Space × Option Nat))) :
    classifyA false here prev = classify here prev := by
  simp [classifyA]

/-- The flag never weakens a non-empty baseline: an acknowledged actor is then
held to exactly the rules of `classify` (so `ap_ok_sound` applies to it). -/
theorem ap_ack_nonempty_is_classify (b : Bool) (here : List (Space × Option Nat))
    (prev : Option (List (Space × Option Nat)))
    (h : ((prev.getD []).any fun p => p.2.isSome) = true) :
    classifyA b here prev = classify here prev := by
  simp [classifyA, h]

/-- Unacknowledged failures stay sticky: `next` never sets the flag, so this is
`ap_failing_is_sticky` restated for the flagged classifier. -/
theorem ap_unacked_failing_is_sticky (here : List (Space × Option Nat))
    (prev : Option (List (Space × Option Nat)))
    (h : classifyA false here prev = .missing ∨ classifyA false here prev = .stalled) :
    classifyA false here (next prev (classifyA false here prev) here) =
      classifyA false here prev := by
  simp only [ap_unacked_is_classify] at h ⊢
  exact ap_failing_is_sticky here prev h

/-! ## 2. seq_gap -/

/-- Pre-fix `head_seq`: the LAST parseable line. -/
def headOld : List (Option Nat) → Option Nat
  | [] => none
  | x :: t => match headOld t with
    | some v => some v
    | none => x

/-- Post-fix `head_seq`: the highest parseable seq. -/
def headMax : List (Option Nat) → Option Nat
  | [] => none
  | none :: t => headMax t
  | some q :: t => some (match headMax t with | none => q | some m => max q m)

theorem headMax_ge : ∀ (l : List (Option Nat)) (q : Nat), some q ∈ l →
    ∃ m, headMax l = some m ∧ q ≤ m
  | [], _, h => by simp at h
  | none :: t, q, h => by
    simp only [List.mem_cons, reduceCtorEq, false_or] at h
    exact headMax_ge t q h
  | some x :: t, q, h => by
    simp only [List.mem_cons, Option.some.injEq] at h
    simp only [headMax]
    rcases h with rfl | h
    · cases headMax t <;> simp <;> omega
    · obtain ⟨m, hm, hq⟩ := headMax_ge t q h
      simp [hm]; omega

theorem headMax_none : ∀ l : List (Option Nat), headMax l = none → ∀ q, some q ∉ l
  | [], _, _, h => by simp at h
  | none :: t, h, q, hm => by
    simp only [List.mem_cons, reduceCtorEq, false_or] at hm
    exact headMax_none t h q hm
  | some _ :: _, h, _, _ => by simp [headMax] at h

/-- `sg_head_is_max`: the fixed head is an upper bound of every readable seq. -/
theorem sg_head_is_max (l : List (Option Nat)) (q : Nat) (h : some q ∈ l) :
    ∃ m, headMax l = some m ∧ q ≤ m := headMax_ge l q h

inductive Verdict | ok | gap (n : Nat) | error
  deriving DecidableEq, Repr

/-- Pre-fix verdict. `local None` gave gap `0` or `None`; both print "ok" and
exit 0. `young` is the grace (all unpublished events younger than 90 min). -/
def verdictOld (loc rem : List (Option Nat)) (young : Bool) : Verdict :=
  match headOld loc, headOld rem with
  | none, _ => .ok
  | some l, none => .gap (l + 1)
  | some l, some r => if l ≤ r then .ok else if young then .ok else .gap (l - r)

/-- Post-fix verdict: a log with lines but no readable event is an error. -/
def verdict (loc rem : List (Option Nat)) (young : Bool) : Verdict :=
  match headMax loc, headMax rem with
  | none, _ => if loc = [] then .ok else .error
  | some l, none => .gap (l + 1)
  | some l, some r => if l ≤ r then .ok else if young then .ok else .gap (l - r)

/-- (a) local unreadable, remote at 4: "ok … published". -/
theorem sg_old_unreadable_local_ok :
    verdictOld [none] [some 0, some 4] false = .ok ∧
    verdict [none] [some 0, some 4] false = .error := by decide

/-- (b) local 0..6 then a stray 3, remote 0..4: "ok" with 5 and 6 unpublished. -/
theorem sg_old_last_line_hides_gap :
    let loc := [some 0, some 1, some 2, some 3, some 4, some 5, some 6, some 3]
    let rem := [some 0, some 1, some 2, some 3, some 4]
    verdictOld loc rem false = .ok ∧ verdict loc rem false = .gap 2 := by decide

/-- **Soundness.** `ok` means every readable local seq is on the remote, unless
the only unpublished events are inside the grace window (the documented
"pending" case, which is reported by count). -/
theorem sg_ok_sound (loc rem : List (Option Nat)) (young : Bool)
    (h : verdict loc rem young = .ok) :
    (∀ q, some q ∈ loc → ∃ r, headMax rem = some r ∧ q ≤ r) ∨
    (young = true ∧ (headMax rem).isSome) := by
  unfold verdict at h
  split at h
  · rename_i hl
    split at h
    · left; intro q hq; subst_vars; simp at hq
    · cases h
  · cases h
  · rename_i l r hl hr
    split at h
    · left; intro q hq
      obtain ⟨m, hm, hqm⟩ := headMax_ge loc q hq
      rw [hl] at hm; cases hm
      exact ⟨r, hr, by omega⟩
    · split at h
      · right; simp_all
      · cases h

/-! ## 3. id_churn -/

/-- Pre-fix: growth above a COUNT. -/
def growthCount (cur : List Nat) (ack : Nat) : Nat := cur.length - ack

/-- Post-fix: the orphaned ids not acknowledged. -/
def growthSet (cur ack : List Nat) : List Nat := cur.filter (· ∉ ack)

/-- Two acknowledged ids repaired, two new ids churned: count growth 0. -/
theorem ic_count_masks_churn :
    growthCount [2, 3, 10, 11] [0, 1, 2, 3].length = 0 ∧ 10 ∉ [0, 1, 2, 3] ∧
    growthSet [2, 3, 10, 11] [0, 1, 2, 3] = [10, 11] := by decide

/-- **Soundness.** No growth means every orphaned id was acknowledged. -/
theorem ic_set_sound (cur ack : List Nat) (h : growthSet cur ack = []) :
    ∀ i ∈ cur, i ∈ ack := by
  intro i hi
  simp [growthSet] at h
  exact h i hi

/-! ### 3b. The noise floor (owner decision D2, 2026-09-23)

`scan_space` used to zero the orphans when they were under 25 % of the open
ledger ids, BEFORE the baseline was applied. With a set baseline that hid any
new churn of fewer than a quarter of the ids. D2 drops the floor (it survives
only for a legacy COUNT baseline, which is `growthCount`, unchanged). -/

/-- Open ledger ids that org no longer holds. -/
def orphans (led org : List Nat) : List Nat := led.filter (· ∉ org)

/-- Pre-D2: `orphaned / len(led) < 0.25` ⇒ nothing, then the set baseline. -/
def reportFloored (led org ack : List Nat) : List Nat :=
  if (orphans led org).length * 4 < led.length then [] else growthSet (orphans led org) ack

/-- Post-D2, set baseline (an absent baseline is `ack = []`). -/
def report (led org ack : List Nat) : List Nat := growthSet (orphans led org) ack

/-- One id in ten lost, none acknowledged: the floor reported nothing. -/
theorem ic_floor_hides_new_churn :
    reportFloored [0,1,2,3,4,5,6,7,8,9] [1,2,3,4,5,6,7,8,9] [] = [] ∧
    report [0,1,2,3,4,5,6,7,8,9] [1,2,3,4,5,6,7,8,9] [] = [0] := by decide

/-- **D2, completeness.** Every open ledger id missing from org and not
acknowledged is reported, however few there are. -/
theorem ic_report_complete (led org ack : List Nat) (i : Nat)
    (hl : i ∈ led) (ho : i ∉ org) (ha : i ∉ ack) : i ∈ report led org ack := by
  simp [report, growthSet, orphans, hl, ho, ha]

/-- **D2, soundness.** Only such ids are reported. -/
theorem ic_report_sound (led org ack : List Nat) (i : Nat) (h : i ∈ report led org ack) :
    i ∈ led ∧ i ∉ org ∧ i ∉ ack := by
  simp only [report, growthSet, orphans, List.mem_filter, decide_eq_true_eq] at h
  exact ⟨h.1.1, h.1.2, h.2⟩

/-! ## 4. reliability_scoreboard -/

/-- Pre-fix R5, journal branch. -/
def r5Old (hits expected : Nat) : Bool :=
  if expected < 4 then true else decide (hits ≥ expected - max 1 (expected / 200))

/-- Post-fix R5. -/
def r5 (hits expected : Nat) : Bool :=
  expected ≠ 0 && decide (hits ≥ expected - expected / 200)

/-- 3/4 = 75 %, 95/96 = 98.96 % and 0/3 all passed "≥ 99.5 %". -/
theorem r5_old_passes_below_slo :
    r5Old 3 4 = true ∧ ¬ (1000 * 3 ≥ 995 * 4) ∧
    r5Old 95 96 = true ∧ ¬ (1000 * 95 ≥ 995 * 96) ∧
    r5Old 0 3 = true := by decide

/-- **Soundness and completeness.** R5 passes exactly when at least one probe
was due and hits/expected ≥ 99.5 %. -/
theorem r5_ok_sound (hits expected : Nat) :
    r5 hits expected = true ↔ (expected > 0 ∧ 1000 * hits ≥ 995 * expected) := by
  simp only [r5, Bool.and_eq_true, decide_eq_true_eq, ne_eq]
  omega

/-! ### The streak chain -/

structure Line where
  day : Nat
  pass : Bool
  streak : Nat
  deriving DecidableEq, Repr

/-- `_chain`: a line's streak from the line for the day before. -/
def chain (p l : Line) : Line :=
  { l with streak := if l.pass then (if p.pass ∧ p.day + 1 = l.day then p.streak else 0) + 1 else 0 }

/-- The re-chaining loop in `write_day_lines`. -/
def rechainFrom : Line → List Line → List Line
  | _, [] => []
  | p, l :: t => chain p l :: rechainFrom (chain p l) t

/-- A log (after its first, seed, line) is consistent when every line's streak is
what `_chain` computes from its predecessor. -/
def ChainOK : Line → List Line → Prop
  | _, [] => True
  | p, l :: t => l.streak = (chain p l).streak ∧ ChainOK l t

theorem rechain_ok : ∀ (p : Line) (t : List Line), ChainOK p (rechainFrom p t)
  | _, [] => trivial
  | p, l :: t => ⟨rfl, rechain_ok (chain p l) t⟩

/-- `rechain_step`: in a consistent log a positive streak is backed by a PASS,
and a streak of n ≥ 2 by a PASS on the previous calendar day carrying n - 1. -/
theorem rechain_step (p l : Line) (t : List Line) (h : ChainOK p (l :: t)) :
    (l.streak ≥ 1 → l.pass = true) ∧
    (l.streak ≥ 2 → p.pass = true ∧ p.day + 1 = l.day ∧ p.streak = l.streak - 1) := by
  obtain ⟨h, -⟩ := h
  simp only [chain] at h
  cases hp : l.pass
  · simp [hp] at h; omega
  · simp only [hp, ite_true] at h
    refine ⟨fun _ => rfl, fun h2 => ?_⟩
    split at h
    · rename_i hc; exact ⟨hc.1, hc.2, by omega⟩
    · omega

/-- `compute`: yesterday found BY DATE (the fix); the file's last line is not
necessarily yesterday. -/
def computeStreak (log : List Line) (d : Nat) (pass : Bool) : Nat :=
  if pass then
    (match log.find? (fun y => y.day + 1 == d) with
     | some y => if y.pass then y.streak else 0
     | none => 0) + 1
  else 0

def computeStreakOld (log : List Line) (d : Nat) (pass : Bool) : Nat :=
  if pass then
    (match log.getLast? with
     | some y => if y.pass ∧ y.day + 1 = d then y.streak else 0
     | none => 0) + 1
  else 0

theorem streak_old_file_order :
    computeStreakOld [⟨4, true, 29⟩, ⟨2, false, 0⟩] 5 true = 1 ∧
    computeStreak [⟨4, true, 29⟩, ⟨2, false, 0⟩] 5 true = 30 := by decide

/-- Pre-fix: a backfilled FAIL left the later line's stale streak in place, so
the next day chained from 29 and reached level 5 two days after a failure. -/
theorem streak_old_stale_after_backfill :
    ¬ ChainOK ⟨3, false, 0⟩ [⟨4, true, 29⟩] ∧
    computeStreakOld [⟨3, false, 0⟩, ⟨4, true, 29⟩] 5 true = 30 ∧
    rechainFrom ⟨3, false, 0⟩ [⟨4, true, 29⟩] = [⟨4, true, 1⟩] ∧
    computeStreak [⟨3, false, 0⟩, ⟨4, true, 1⟩] 5 true = 2 := by
  refine ⟨?_, by decide, by decide, by decide⟩
  simp [ChainOK, chain]

/-! ## 5. today_registry: `validate() == []` ⇒ `plan` respects `depends_on`

`plan` runs the stages in order and, within a stage, emits in rounds: a round is
every pending registration whose in-stage dependencies are already emitted; a
dependency OUTSIDE the stage (`d not in have`) counts as satisfied. The round is
sorted by the section's position in `briefing.yaml` — modelled as an arbitrary
permutation oracle `ord`, so the theorem holds for any sort. When no
registration is ready (a cycle) the Python emits everything (`ready =
pending[:]`); the theorem shows that never happens on a validated registry.

Only registered, wanted registrations enter `plan`, so `Reg` models those plus
the ones `validate` sees. A declared stage ≥ 3 is an unknown string; `stage`
is the coercion `_parse` applies. Acyclicity (rule 5's DFS) is taken as its
standard equivalent: a rank on sections that every dependency strictly
decreases. That the DFS decides exactly this is assumed, not modelled. -/

structure Reg where
  sec : Nat
  declared : Nat
  deps : List Nat
  deriving DecidableEq, Repr

/-- `_parse`: an unknown stage becomes gather (0). -/
def Reg.stage (r : Reg) : Nat := if r.declared < 3 then r.declared else 0

theorem Reg.stage_lt (r : Reg) : r.stage < 3 := by
  unfold Reg.stage; split <;> omega

def secs (l : List Reg) : List Nat := l.map Reg.sec

def isReady (hav seen : List Nat) (r : Reg) : Bool :=
  r.deps.all fun d => d ∈ seen || d ∉ hav

/-- The `while pending` loop of `plan`, with fuel. -/
def sched (ord : List Reg → List Reg) (hav : List Nat) : Nat → List Reg → List Reg → List Reg
  | 0, acc, pending => acc ++ pending
  | _ + 1, acc, [] => acc
  | n + 1, acc, p :: ps =>
    let pending := p :: ps
    let ready := pending.filter (isReady hav (secs acc))
    if ready = [] then acc ++ ord pending          -- the cycle fallback
    else sched ord hav n (acc ++ ord ready) (pending.filter fun r => !isReady hav (secs acc) r)

def group (regs : List Reg) (wanted : List Nat) (i : Nat) : List Reg :=
  regs.filter fun r => r.stage == i && r.sec ∈ wanted

def stagePlan (ord : List Reg → List Reg) (regs : List Reg) (wanted : List Nat) (i : Nat) : List Reg :=
  let g := group regs wanted i
  sched ord (secs g) g.length [] g

def plan (ord : List Reg → List Reg) (regs : List Reg) (wanted : List Nat) : List Reg :=
  stagePlan ord regs wanted 0 ++ stagePlan ord regs wanted 1 ++ stagePlan ord regs wanted 2

/-- Rules 2 (a producer exists), 5 (dependencies are sections) and 5's cycle
check — the pre-fix gate, as far as ordering is concerned. -/
structure ValidOld (regs : List Reg) (wanted : List Nat) (rank : Nat → Nat) : Prop where
  producer : ∀ s ∈ wanted, ∃ p ∈ regs, p.sec = s
  depsWanted : ∀ r ∈ regs, ∀ d ∈ r.deps, d ∈ wanted
  acyclic : ∀ r ∈ regs, ∀ d ∈ r.deps, rank d < rank r.sec

/-- The fixed gate adds rule 4b (stage is known) and 5b (no later-stage dep). -/
structure Valid (regs : List Reg) (wanted : List Nat) (rank : Nat → Nat) : Prop
    extends ValidOld regs wanted rank where
  knownStage : ∀ r ∈ regs, r.declared < 3
  noLaterDep : ∀ r ∈ regs, ∀ d ∈ r.deps, ∀ p ∈ regs, p.sec = d → p.stage ≤ r.stage

/-- "Every dependency in `have` was emitted before me", front to back. -/
def Resp (hav : List Nat) : List Nat → List Reg → Prop
  | _, [] => True
  | seen, r :: t => (∀ d ∈ r.deps, d ∈ hav → d ∈ seen) ∧ Resp hav (seen ++ [r.sec]) t

theorem resp_append (h : List Nat) : ∀ (s : List Nat) (a b : List Reg),
    Resp h s (a ++ b) ↔ Resp h s a ∧ Resp h (s ++ secs a) b
  | s, [], b => by simp [Resp, secs]
  | s, r :: a, b => by
    simp only [List.cons_append, Resp, resp_append h (s ++ [r.sec]) a b, secs, List.map_cons,
      List.append_assoc, List.nil_append, List.cons_append, and_assoc]

/-- Change `have` and `seen` at once: every dependency the new `have` asks
for is either in the old `have` (and so handled) or already in the new `seen`. -/
theorem resp_weaken (h h' : List Nat) : ∀ (s s' : List Nat) (l : List Reg),
    Resp h s l → (∀ x ∈ s, x ∈ s') →
    (∀ r ∈ l, ∀ d ∈ r.deps, d ∈ h' → d ∉ h → d ∈ s') → Resp h' s' l
  | _, _, [], _, _, _ => trivial
  | s, s', r :: t, ⟨hr, ht⟩, hs, hx => by
    refine ⟨fun d hd hh' => ?_, resp_weaken h h' _ _ t ht ?_ ?_⟩
    · by_cases hh : d ∈ h
      · exact hs _ (hr d hd hh)
      · exact hx r (by simp) d hd hh' hh
    · intro x hxm; simp only [List.mem_append, List.mem_singleton] at hxm ⊢
      rcases hxm with hxm | hxm
      · exact Or.inl (hs x hxm)
      · exact Or.inr hxm
    · intro q hq d hd h1 h2
      exact List.mem_append_left _ (hx q (by simp [hq]) d hd h1 h2)

/-- A batch whose dependencies are all already in `seen`. -/
theorem resp_batch (h : List Nat) : ∀ (s : List Nat) (b : List Reg),
    (∀ r ∈ b, ∀ d ∈ r.deps, d ∈ h → d ∈ s) → Resp h s b
  | _, [], _ => trivial
  | s, r :: t, hb => ⟨hb r (by simp), resp_batch h _ t fun q hq d hd hh =>
      List.mem_append_left _ (hb q (by simp [hq]) d hd hh)⟩

theorem exists_min (f : Reg → Nat) : ∀ l : List Reg, l ≠ [] → ∃ m ∈ l, ∀ y ∈ l, f m ≤ f y
  | [], h => absurd rfl h
  | [x], _ => ⟨x, by simp⟩
  | x :: y :: t, _ => by
    obtain ⟨m, hm, hmin⟩ := exists_min f (y :: t) (by simp)
    by_cases hx : f x ≤ f m
    · refine ⟨x, by simp, fun z hz => ?_⟩
      simp only [List.mem_cons] at hz
      rcases hz with rfl | hz
      · exact Nat.le_refl _
      · exact Nat.le_trans hx (hmin z (by simp_all))
    · refine ⟨m, List.mem_cons_of_mem _ hm, fun z hz => ?_⟩
      simp only [List.mem_cons] at hz
      rcases hz with rfl | hz
      · omega
      · exact hmin z (by simp_all)

theorem length_filter_not_lt {α} (f : α → Bool) (l : List α) (x : α) (hx : x ∈ l)
    (hf : f x = true) : (l.filter fun r => !f r).length < l.length := by
  induction l with
  | nil => simp at hx
  | cons y t ih =>
    simp only [List.mem_cons] at hx
    rcases hx with rfl | hx
    · simp only [List.filter_cons, hf, Bool.not_true]
      exact Nat.lt_succ_of_le (List.length_filter_le _ _)
    · have := ih hx
      rw [List.filter_cons]
      split
      · simp only [List.length_cons]; omega
      · have := List.length_filter_le (fun r => !f r) t
        simp only [List.length_cons]; omega

/-- The scheduler, on an acyclic group with enough fuel: it never takes the
cycle fallback, emits every registration of the group and nothing else, and
emits each one after every in-group dependency. -/
theorem sched_correct (ord : List Reg → List Reg) (hord : ∀ l, List.Perm (ord l) l)
    (g : List Reg) (rank : Nat → Nat)
    (hrank : ∀ r ∈ g, ∀ d ∈ r.deps, d ∈ secs g → rank d < rank r.sec) :
    ∀ (n : Nat) (acc pending : List Reg),
    pending.length ≤ n → Resp (secs g) [] acc →
    (∀ x ∈ g, x ∈ acc ∨ x ∈ pending) → (∀ x ∈ acc, x ∈ g) → (∀ x ∈ pending, x ∈ g) →
    let out := sched ord (secs g) n acc pending
    Resp (secs g) [] out ∧ (∀ x ∈ g, x ∈ out) ∧ (∀ x ∈ out, x ∈ g)
  | 0, acc, pending, hl, hr, hcov, hacc, hpen => by
    have : pending = [] := List.eq_nil_of_length_eq_zero (by omega)
    subst this
    simp only [sched, List.append_nil]
    exact ⟨hr, fun x hx => (hcov x hx).resolve_right (by simp), hacc⟩
  | _ + 1, acc, [], _, hr, hcov, hacc, _ => by
    simp only [sched]
    exact ⟨hr, fun x hx => (hcov x hx).resolve_right (by simp), hacc⟩
  | n + 1, acc, p :: ps, hl, hr, hcov, hacc, hpen => by
    -- the minimum-rank pending registration is always ready
    obtain ⟨m, hm, hmin⟩ := exists_min (fun r => rank r.sec) (p :: ps) (by simp)
    have hready : isReady (secs g) (secs acc) m = true := by
      simp only [isReady, List.all_eq_true, Bool.or_eq_true, decide_eq_true_eq]
      intro d hd
      by_cases hin : d ∈ secs g
      · left
        obtain ⟨x, hxg, rfl⟩ := List.mem_map.mp hin
        rcases hcov x hxg with hxa | hxp
        · exact List.mem_map_of_mem hxa
        · have h1 := hrank m (hpen m hm) x.sec hd hin
          have h2 := hmin x hxp
          omega
      · right; exact hin
    have hne : (p :: ps).filter (isReady (secs g) (secs acc)) ≠ [] := by
      intro h
      have : m ∈ (p :: ps).filter (isReady (secs g) (secs acc)) := List.mem_filter.mpr ⟨hm, hready⟩
      rw [h] at this; simp at this
    simp only [sched, hne, ite_false]
    apply sched_correct ord hord g rank hrank n
    · have := length_filter_not_lt (isReady (secs g) (secs acc)) (p :: ps) m hm hready
      simp only [List.length_cons] at hl this ⊢; omega
    · rw [resp_append]
      refine ⟨hr, resp_batch _ _ _ fun r hrm d hd hdg => ?_⟩
      have hrm' := (hord _).mem_iff.mp hrm
      have := (List.mem_filter.mp hrm').2
      simp only [isReady, List.all_eq_true, Bool.or_eq_true, decide_eq_true_eq] at this
      rcases this d hd with h | h
      · simpa using h
      · exact absurd hdg h
    · intro x hx
      rcases hcov x hx with h | h
      · exact Or.inl (List.mem_append_left _ h)
      · cases hrx : isReady (secs g) (secs acc) x
        · right; exact List.mem_filter.mpr ⟨h, by simp [hrx]⟩
        · left; exact List.mem_append_right _ ((hord _).mem_iff.mpr (List.mem_filter.mpr ⟨h, hrx⟩))
    · intro x hx
      rcases List.mem_append.mp hx with h | h
      · exact hacc x h
      · exact hpen x (List.mem_filter.mp ((hord _).mem_iff.mp h)).1
    · intro x hx; exact hpen x (List.mem_filter.mp hx).1

/-- **Soundness of the fixed gate.** On a registry `validate` accepts, every
registration in the plan comes after a producer of each of its dependencies —
in any sort order of the rounds. -/
theorem tr_valid_plan_respects_deps (ord : List Reg → List Reg) (hord : ∀ l, List.Perm (ord l) l)
    (regs : List Reg) (wanted : List Nat) (rank : Nat → Nat) (hv : Valid regs wanted rank) :
    Resp wanted [] (plan ord regs wanted) := by
  have stage_ok : ∀ i, let o := stagePlan ord regs wanted i
      Resp (secs (group regs wanted i)) [] o ∧ (∀ x ∈ group regs wanted i, x ∈ o) ∧
      (∀ x ∈ o, x ∈ group regs wanted i) := by
    intro i
    apply sched_correct ord hord _ rank
      (fun r hr d hd _ => hv.acyclic r (List.mem_filter.mp hr).1 d hd) _ [] _ (Nat.le_refl _) trivial
    · intro x hx; exact Or.inr hx
    · intro x hx; simp at hx
    · intro x hx; exact hx
  have mem_group : ∀ x, x ∈ regs → x.sec ∈ wanted → x ∈ group regs wanted x.stage := by
    intro x hx hw; simp [group, hx, hw]
  -- a dependency outside stage i has its producer in an EARLIER stage
  have earlier : ∀ i, ∀ r ∈ group regs wanted i, ∀ d ∈ r.deps,
      d ∉ secs (group regs wanted i) → ∃ p ∈ regs, p.sec = d ∧ p.stage < i ∧ p.sec ∈ wanted := by
    intro i r hr d hd hn
    have hrg := List.mem_filter.mp hr
    have hri : r.stage = i := by
      have h2 := hrg.2; simp only [Bool.and_eq_true, beq_iff_eq] at h2; exact h2.1
    have hdw := hv.depsWanted r hrg.1 d hd
    obtain ⟨p, hp, hps⟩ := hv.producer d hdw
    have hle := hv.noLaterDep r hrg.1 d hd p hp hps
    have hpw : p.sec ∈ wanted := by rw [hps]; exact hdw
    refine ⟨p, hp, hps, ?_, hpw⟩
    rcases Nat.lt_or_eq_of_le hle with h | h
    · omega
    · exfalso; apply hn
      have hpg := mem_group p hp hpw
      rw [h, hri] at hpg
      exact List.mem_map.mpr ⟨p, hpg, hps⟩
  obtain ⟨r0, c0, i0⟩ := stage_ok 0
  obtain ⟨r1, c1, i1⟩ := stage_ok 1
  obtain ⟨r2, c2, i2⟩ := stage_ok 2
  -- producers of earlier stages are in the earlier outputs
  have inOut : ∀ p ∈ regs, p.sec ∈ wanted → p.sec ∈ secs (stagePlan ord regs wanted p.stage) :=
    fun p hp hw => List.mem_map_of_mem (by
      have := stage_ok p.stage
      exact this.2.1 p (mem_group p hp hw))
  unfold plan
  rw [resp_append, resp_append]
  refine ⟨⟨?_, ?_⟩, ?_⟩
  · refine resp_weaken _ _ [] [] _ r0 (fun _ h => h) fun r hr d hd _ hn => ?_
    obtain ⟨p, -, -, hlt, -⟩ := earlier 0 r (i0 r hr) d hd hn
    omega
  · refine resp_weaken _ _ [] _ _ r1 (by simp) fun r hr d hd _ hn => ?_
    obtain ⟨p, hp, rfl, hlt, hw⟩ := earlier 1 r (i1 r hr) d hd hn
    have h0 : p.stage = 0 := by omega
    have := inOut p hp hw; rw [h0] at this; simpa using this
  · refine resp_weaken _ _ [] _ _ r2 (by simp) fun r hr d hd _ hn => ?_
    obtain ⟨p, hp, rfl, hlt, hw⟩ := earlier 2 r (i2 r hr) d hd hn
    have hin := inOut p hp hw
    have h01 : p.stage = 0 ∨ p.stage = 1 := by omega
    simp only [secs, List.map_append, List.nil_append, List.mem_append]
    rcases h01 with h | h
    · left; rw [h] at hin; exact hin
    · right; rw [h] at hin; exact hin

/-- **Unknown stages are refused.** On a validated registry every section runs
in the stage it declared. -/
theorem tr_valid_stage_honoured (regs : List Reg) (wanted : List Nat) (rank : Nat → Nat)
    (hv : Valid regs wanted rank) : ∀ r ∈ regs, r.stage = r.declared := by
  intro r hr; simp [Reg.stage, hv.knownStage r hr]

/-- The pre-fix gate accepted a gather section depending on a compose section,
and `plan` ran the dependent first. -/
theorem tr_old_later_stage_dep :
    let a : Reg := ⟨0, 0, [1]⟩
    let b : Reg := ⟨1, 1, []⟩
    ValidOld [a, b] [0, 1] (fun s => if s = 0 then 1 else 0) ∧
    plan id [a, b] [0, 1] = [a, b] ∧ ¬ Resp [0, 1] [] [a, b] := by
  refine ⟨⟨?_, ?_, ?_⟩, by decide, ?_⟩
  · intro s hs; simp at hs; rcases hs with rfl | rfl <;> simp
  · intro r hr d hd; simp at hr; rcases hr with rfl | rfl <;> simp_all
  · intro r hr d hd; simp at hr; rcases hr with rfl | rfl <;> simp_all
  · simp [Resp]

/-- …and a typo'd stage ("narate", here 7) ran as gather without a word. -/
theorem tr_old_unknown_stage_coerced :
    let c : Reg := ⟨0, 7, []⟩
    ValidOld [c] [0] (fun _ => 0) ∧ c.stage ≠ c.declared := by
  refine ⟨⟨?_, ?_, ?_⟩, by decide⟩
  · intro s hs; simp at hs; subst hs; simp
  · intro r hr d hd; simp at hr; subst hr; simp at hd
  · intro r hr d hd; simp at hr; subst hr; simp at hd

end DatacoreSpec.Detectors
