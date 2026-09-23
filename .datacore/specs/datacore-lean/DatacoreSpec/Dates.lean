/-!
# Dates -- weekday arithmetic and the day-name fixers

Models `.datacore/lib/date_utils.py`, `org_date_hook.py` (coordinator-fixed,
read-only here), `org_date_validator.py`, `validate_org_dates.py` and
`decay_and_review.month_keys`. Findings: `findings/dates.md`.

1. **Weekday.** `daysFromCivil` is Hinnant's days-from-civil (proleptic
   Gregorian, floor division = Lean's `Int` `/`). Proved: it agrees with a
   table of dates computed by Python's `date.weekday()`; consecutive days and
   consecutive months (with the leap rule) step correctly; weekday is
   7-periodic and 400-year periodic.
2. **The day-fix rewrite** (`re.sub` with DATE_PATTERN). Characters, the blank
   class and the lookahead are parameters (`Pat`); the date->weekday answer is
   an oracle, and theorems hold for every oracle that answers a day
   abbreviation. Proved: with the lookahead, only whole stamp tokens change
   (`fix_rw`); the fix is idempotent (`fix_idem`); with `[ \t]` blanks no line
   is joined (`Rw.newlines`). Refuted by replay: the old pattern
   (`old_monitor`, `old_monday`), and the `\s` blank class still in the hook
   (`hook_joins_lines`).
   Abstraction: `\d` and `\s` are modelled on ASCII (Python's are Unicode).
3. **parse_relative**: "next month" was next Monday (`old_month_is_monday`);
   fixed day words are sound (`dayNew_sound`); `addMonths_spec`.
4. **org_date_validator** suspect year (`suspect_old_stale`).
5. **validate_org_dates** frontmatter: verdict is independent of the body
   (`fmNew_frame`); the old one read the body (`old_reads_body`).
6. **month_keys**: the 30-day stepping skipped February
   (`old_keys_skip_february`); calendar-month keys are N distinct consecutive
   months (`month_keys_distinct`).
7. **The hook's write (decision G8).** `org_date_hook` now re-reads and writes
   under the org transaction lock. Over every interleaving with an adapter
   commit, the file ends as `f (g s0)` or `g (f s0)` (`locked_no_lost_update`),
   or `g s0` when the hook skips (`skipped_keeps_adapter`); the unlocked hook
   loses the commit on the schedule read, commit, write
   (`unlocked_loses_adapter_commit`).
-/

namespace DatacoreSpec.Dates

/-! ## 1. Weekday -/

/-- Days since 1970-01-01 (Howard Hinnant, `days_from_civil`). -/
def daysFromCivil (y m d : Int) : Int :=
  let y' := if m ≤ 2 then y - 1 else y
  let era := y' / 400
  let yoe := y' - era * 400
  let mp := (m + 9) % 12
  let doy := (153 * mp + 2) / 5 + d - 1
  let doe := yoe * 365 + yoe / 4 - yoe / 100 + doy
  era * 146097 + doe - 719468

/-- Monday = 0, as Python's `date.weekday()`; 1970-01-01 was a Thursday. -/
def weekday (y m d : Int) : Int := (daysFromCivil y m d + 3) % 7

def isLeap (y : Int) : Bool := y % 4 == 0 && (y % 100 != 0 || y % 400 == 0)

def daysIn (y m : Int) : Int :=
  if m = 2 then (if isLeap y then 29 else 28)
  else if m = 4 ∨ m = 6 ∨ m = 9 ∨ m = 11 then 30 else 31

def nextMonth (y m : Int) : Int × Int := if m = 12 then (y + 1, 1) else (y, m + 1)

/-- (y, m, d, weekday) computed by `python3 -c "date(y,m,d).weekday()"`. -/
def table : List (Int × Int × Int × Int) :=
  [(1, 1, 1, 0), (1600, 3, 1, 2), (1900, 2, 28, 2), (1900, 3, 1, 3), (1970, 1, 1, 3),
   (2000, 2, 29, 1), (2000, 3, 1, 2), (2024, 2, 29, 3), (2025, 1, 6, 0), (2026, 1, 5, 0),
   (2026, 3, 31, 1), (2026, 9, 23, 2), (2026, 9, 24, 3), (2026, 9, 28, 0), (2100, 3, 1, 0),
   (9999, 12, 31, 4)]

theorem table_ok : table.all (fun (y, m, d, w) => weekday y m d == w) = true := by decide
theorem epoch : daysFromCivil 1970 1 1 = 0 := by decide

theorem weekday_range (y m d : Int) : 0 ≤ weekday y m d ∧ weekday y m d < 7 := by
  unfold weekday; omega

/-- 7-periodicity: shifting the day count by whole weeks keeps the weekday. -/
theorem weekday_shift (y m d k : Int) :
    (daysFromCivil y m d + 7 * k + 3) % 7 = weekday y m d := by
  unfold weekday; omega

theorem next_day (y m d : Int) : daysFromCivil y m (d + 1) = daysFromCivil y m d + 1 := by
  unfold daysFromCivil; split <;> dsimp only <;> omega

/-- The first of the next month is `daysIn y m` days after the first of this
one, with the Gregorian leap rule. With `next_day` and `epoch`, this pins
`daysFromCivil` to the calendar for every date, not only the table. -/
theorem month_step (y m : Int) (h1 : 1 ≤ m) (h2 : m ≤ 12) :
    daysFromCivil (nextMonth y m).1 (nextMonth y m).2 1 = daysFromCivil y m 1 + daysIn y m := by
  have : m = 1 ∨ m = 2 ∨ m = 3 ∨ m = 4 ∨ m = 5 ∨ m = 6 ∨ m = 7 ∨ m = 8 ∨ m = 9 ∨ m = 10 ∨ m = 11 ∨ m = 12 := by omega
  rcases this with rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl <;>
    simp [nextMonth, daysFromCivil, daysIn, isLeap] <;> (try split) <;> omega

theorem cycle400 (y m d : Int) : daysFromCivil (y + 400) m d = daysFromCivil y m d + 146097 := by
  unfold daysFromCivil
  split <;> simp only [] <;> omega

theorem weekday_cycle400 (y m d : Int) : weekday (y + 400) m d = weekday y m d := by
  unfold weekday; rw [cycle400]; omega

/-! ## 2. The day-fix rewrite -/

def abbrs : List (List Char) :=
  ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map String.toList

/-- `\d{4}-\d{2}-\d{2}` on exactly ten characters. -/
def isDateShape : List Char → Bool
  | [a, b, c, d, e, f, g, h, i, j] =>
      a.isDigit && b.isDigit && c.isDigit && d.isDigit && e == '-' &&
      f.isDigit && g.isDigit && h == '-' && i.isDigit && j.isDigit
  | _ => false

structure Pat where
  isWs   : Char → Bool
  /-- `(?![A-Za-z])` after the abbreviation. -/
  strict : Bool

def notAlphaHead (t : List Char) : Bool := !(t.head?.any Char.isAlpha)

/-- One `re` match attempt at the start of `s`: (date, blanks, abbr, rest). -/
def matchAt (p : Pat) (s : List Char) : Option (List Char × List Char × List Char × List Char) :=
  let d := s.take 10
  let r1 := s.drop 10
  let ws := r1.takeWhile p.isWs
  let r2 := r1.dropWhile p.isWs
  let a := r2.take 3
  let rest := r2.drop 3
  if isDateShape d && !ws.isEmpty && abbrs.contains a && (!p.strict || notAlphaHead rest)
  then some (d, ws, a, rest) else none

/-- The replacement function of the hook: `f'{date} {actual}'` if the name is
wrong, the match unchanged otherwise (also when the date is impossible). -/
def repl (o : List Char → Option (List Char)) (d ws a : List Char) : List Char :=
  match o d with
  | some a' => if a' = a then d ++ ws ++ a else d ++ ' ' :: a'
  | none => d ++ ws ++ a

/-- `re.sub`: scan left to right, non-overlapping. Fuel = length. -/
def scan (p : Pat) (o : List Char → Option (List Char)) : Nat → List Char → List Char
  | 0, s => s
  | _ + 1, [] => []
  | n + 1, c :: cs =>
    match matchAt p (c :: cs) with
    | some (d, ws, a, rest) => repl o d ws a ++ scan p o n rest
    | none => c :: scan p o n cs

def fix (p : Pat) (o : List Char → Option (List Char)) (s : List Char) : List Char :=
  scan p o s.length s

/-! ### Structure of a match -/

theorem isDateShape_length {d : List Char} (h : isDateShape d = true) : d.length = 10 := by
  match d, h with
  | [_, _, _, _, _, _, _, _, _, _], _ => rfl

/-- Date characters are digits or `-`. -/
def good (c : Char) : Bool := c.isDigit || c == '-'

theorem isDateShape_good {d : List Char} (h : isDateShape d = true) : ∀ c ∈ d, good c = true := by
  match d, h with
  | [a, b, c, e, f, g, i, j, k, l], h =>
    simp only [isDateShape, Bool.and_eq_true, beq_iff_eq] at h
    intro x hx
    simp only [List.mem_cons, List.not_mem_nil, or_false] at hx
    rcases hx with rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl|rfl <;> simp [good, h]

theorem isDateShape_head {d : List Char} (h : isDateShape d = true) :
    ∃ c t, d = c :: t ∧ c.isDigit = true := by
  match d, h with
  | [a, b, c, e, f, g, i, j, k, l], h =>
    simp only [isDateShape, Bool.and_eq_true] at h
    exact ⟨a, _, rfl, h.1.1.1.1.1.1.1.1.1⟩

theorem abbrs_alpha {a : List Char} (h : abbrs.contains a = true) :
    a.length = 3 ∧ ∀ c ∈ a, c.isAlpha = true := by
  simp only [abbrs, List.map, List.contains_eq_any_beq, List.any_cons, List.any_nil,
    Bool.or_eq_true, beq_iff_eq, Bool.or_false] at h
  rcases h with h|h|h|h|h|h|h <;> subst h <;> refine ⟨rfl, ?_⟩ <;> intro c hc <;>
    simp at hc <;> rcases hc with rfl|rfl|rfl <;> decide

/-- Hypotheses on the blank class: never a digit, `-` or letter. -/
structure WsOk (p : Pat) : Prop where
  notDigit : ∀ c, p.isWs c = true → c.isDigit = false
  notDash  : p.isWs '-' = false
  notAlpha : ∀ c, p.isWs c = true → c.isAlpha = false
  space    : p.isWs ' ' = true

theorem takeWhile_append_stop (q : Char → Bool) (w : List Char) (c : Char) (t : List Char)
    (hw : ∀ x ∈ w, q x = true) (hc : q c = false) :
    (w ++ c :: t).takeWhile q = w ∧ (w ++ c :: t).dropWhile q = c :: t := by
  induction w with
  | nil => simp [hc]
  | cons x w ih =>
    have hx := hw x (by simp)
    have ih := ih (fun y hy => hw y (by simp [hy]))
    simp [hx, ih.1, ih.2]

theorem mem_takeWhile {q : Char → Bool} : ∀ {l : List Char} {c : Char}, c ∈ l.takeWhile q → q c = true
  | [], _, h => by simp at h
  | x :: l, c, h => by
    by_cases hx : q x = true
    · simp only [List.takeWhile_cons, hx, ite_true, List.mem_cons] at h
      rcases h with rfl|h
      · exact hx
      · exact mem_takeWhile h
    · simp [hx] at h

theorem matchAt_some {p : Pat} {s d ws a rest : List Char}
    (h : matchAt p s = some (d, ws, a, rest)) :
    s = d ++ ws ++ a ++ rest ∧ isDateShape d = true ∧ ws ≠ [] ∧ (∀ c ∈ ws, p.isWs c = true) ∧
    abbrs.contains a = true ∧ (p.strict = true → notAlphaHead rest = true) := by
  unfold matchAt at h
  simp only at h
  split at h
  · rename_i hc
    simp only [Option.some.injEq, Prod.mk.injEq] at h
    obtain ⟨rfl, rfl, rfl, rfl⟩ := h
    simp only [Bool.and_eq_true, Bool.not_eq_true', List.isEmpty_eq_false_iff, Bool.or_eq_true] at hc
    obtain ⟨⟨⟨h1, h2⟩, h3⟩, h4⟩ := hc
    refine ⟨?_, h1, h2, ?_, h3, ?_⟩
    · simp only [List.append_assoc, List.take_append_drop, List.takeWhile_append_dropWhile]
    · intro c hc; exact mem_takeWhile hc
    · intro hs; rcases h4 with h4|h4
      · simp [hs] at h4
      · exact h4
  · simp at h

theorem matchAt_token {p : Pat} (hp : WsOk p) {d ws a t : List Char}
    (hd : isDateShape d = true) (hws : ws ≠ []) (hw : ∀ c ∈ ws, p.isWs c = true)
    (ha : abbrs.contains a = true) (ht : p.strict = true → notAlphaHead t = true) :
    matchAt p (d ++ ws ++ a ++ t) = some (d, ws, a, t) := by
  have hl := isDateShape_length hd
  obtain ⟨hal, haa⟩ := abbrs_alpha ha
  match a, hal, haa with
  | [a0, a1, a2], _, haa =>
    have ha0 : p.isWs a0 = false := by
      cases h : p.isWs a0
      · rfl
      · have := hp.notAlpha a0 h; rw [haa a0 (by simp)] at this; cases this
    have hsplit := takeWhile_append_stop p.isWs ws a0 ([a1, a2] ++ t) hw ha0
    unfold matchAt
    simp only [List.append_assoc, List.take_left' hl, List.drop_left' hl]
    simp only [List.cons_append, List.nil_append] at hsplit ⊢
    rw [hsplit.1, hsplit.2]
    have hne : ws.isEmpty = false := by cases ws <;> simp_all
    simp [hd, hne]
    exact ⟨by simpa [List.contains_iff_mem] using ha, ht⟩


/-! ### `re.sub` equations (fuel is irrelevant once it covers the input) -/

theorem matchAt_len {p : Pat} {s d ws a rest : List Char}
    (h : matchAt p s = some (d, ws, a, rest)) : rest.length + 14 ≤ s.length := by
  obtain ⟨hs, hd, hws, -, ha, -⟩ := matchAt_some h
  have := isDateShape_length hd
  have := (abbrs_alpha ha).1
  have : ws.length ≠ 0 := by simpa using hws
  subst hs; simp only [List.length_append]; omega

theorem scan_fuel (p : Pat) (o : List Char → Option (List Char)) :
    ∀ n m s, s.length ≤ n → s.length ≤ m → scan p o n s = scan p o m s := by
  intro n
  induction n with
  | zero =>
    intro m s hn _
    have : s = [] := List.eq_nil_of_length_eq_zero (by omega)
    subst this; cases m <;> rfl
  | succ n ih =>
    intro m s hn hm
    match s, m with
    | [], 0 => rfl
    | [], _ + 1 => rfl
    | c :: cs, 0 => simp at hm
    | c :: cs, m + 1 =>
      simp only [scan]
      split
      · rename_i d ws a rest h
        have := matchAt_len h
        simp only [List.length_cons] at this hn hm
        rw [ih m rest (by omega) (by omega)]
      · rw [ih m cs (by simpa using hn) (by simpa using hm)]

theorem fix_nil (p : Pat) (o : List Char → Option (List Char)) : fix p o [] = [] := rfl

theorem fix_none {p : Pat} (o : List Char → Option (List Char)) {c : Char} {cs : List Char}
    (h : matchAt p (c :: cs) = none) : fix p o (c :: cs) = c :: fix p o cs := by
  simp only [fix, List.length_cons, scan, h]

theorem fix_match {p : Pat} (o : List Char → Option (List Char)) {s d ws a rest : List Char}
    (h : matchAt p s = some (d, ws, a, rest)) : fix p o s = repl o d ws a ++ fix p o rest := by
  have hl := matchAt_len h
  match s, hl, h with
  | c :: cs, hl, h =>
    simp only [fix, List.length_cons, scan, h]
    rw [scan_fuel p o cs.length rest.length rest (by simp at hl; omega) (Nat.le_refl _)]

theorem digit_not_alpha (c : Char) (h : c.isDigit = true) : c.isAlpha = false := by
  simp only [Char.isDigit, Char.isAlpha, Char.isUpper, Char.isLower, Bool.and_eq_true, decide_eq_true_eq] at *
  simp only [UInt32.le_iff_toNat_le, ge_iff_le] at *
  have : '0'.val.toNat = 48 := rfl
  have : '9'.val.toNat = 57 := rfl
  have : 'A'.val.toNat = 65 := rfl
  have : 'Z'.val.toNat = 90 := rfl
  have : 'a'.val.toNat = 97 := rfl
  have : 'z'.val.toNat = 122 := rfl
  simp only [Bool.or_eq_false_iff, Bool.and_eq_false_iff, decide_eq_false_iff_not] 
  omega

/-! ### Idempotence -/

/-- Characters after the date inside a match are never date characters. -/
theorem tail_not_good {p : Pat} (hp : WsOk p) {ws a : List Char}
    (hw : ∀ c ∈ ws, p.isWs c = true) (ha : abbrs.contains a = true) :
    ∀ c ∈ ws ++ a, good c = false := by
  intro c hc
  simp only [List.mem_append] at hc
  simp only [good, Bool.or_eq_false_iff, beq_eq_false_iff_ne]
  rcases hc with hc|hc
  · have h := hw c hc
    refine ⟨hp.notDigit c h, ?_⟩
    rintro rfl; rw [hp.notDash] at h; cases h
  · have h := (abbrs_alpha ha).2 c hc
    refine ⟨?_, ?_⟩
    · cases hd : c.isDigit
      · rfl
      · rw [digit_not_alpha c hd] at h; cases h
    · rintro rfl; cases h

/-- A match starting strictly before a date `d` ends before `d` begins. -/
theorem window {p : Pat} (hp : WsOk p) {v d X d0 ws0 a0 r0 : List Char}
    (hv : v ≠ []) (hd : isDateShape d = true)
    (h : matchAt p (v ++ d ++ X) = some (d0, ws0, a0, r0)) :
    (d0 ++ ws0 ++ a0).length ≤ v.length := by
  obtain ⟨hs, hd0, hws, hw, ha, -⟩ := matchAt_some h
  have hl0 := isDateShape_length hd0
  have hl := isDateShape_length hd
  have ha3 := (abbrs_alpha ha).1
  have hwl : ws0.length ≠ 0 := by simpa using hws
  have hvl : v.length ≠ 0 := by simpa using hv
  apply Classical.byContradiction
  intro hlt
  simp only [List.length_append] at hlt
  -- j := max k 10 lies inside the match and inside d.
  let k := v.length
  let j := if k ≤ 10 then 10 else k
  have hj1 : k ≤ j := by simp only [j]; split <;> omega
  have hj2 : j - k < 10 := by simp only [j]; split <;> omega
  have hj3 : 10 ≤ j := by simp only [j]; split <;> omega
  have hj4 : j < 10 + ws0.length + 3 := by simp only [j]; split <;> omega
  -- the character at j, read through the left side
  have eL : (v ++ d ++ X)[j]? = d[j - k]? := by
    rw [List.append_assoc, List.getElem?_append_right hj1, List.getElem?_append_left (by omega)]
  have eR : (v ++ d ++ X)[j]? = (ws0 ++ a0)[j - 10]? := by
    rw [hs, List.append_assoc, List.append_assoc, List.getElem?_append_right (by omega), hl0,
      ← List.append_assoc, List.getElem?_append_left (by simp; omega)]
  have hin : j - k < d.length := by omega
  rw [eL, List.getElem?_eq_getElem hin] at eR
  have g1 := isDateShape_good hd _ (List.getElem_mem hin)
  have g2 := tail_not_good hp hw ha _ (List.mem_of_getElem? eR.symm)
  rw [g1] at g2; cases g2

def NoM (p : Pat) (u t : List Char) : Prop :=
  ∀ i < u.length, matchAt p ((u ++ t).drop i) = none

theorem repl_prefix (o : List Char → Option (List Char)) (d ws a : List Char) :
    ∃ Z, repl o d ws a = d ++ Z := by
  unfold repl
  split
  · split
    · exact ⟨ws ++ a, by simp⟩
    · exact ⟨_, rfl⟩
  · exact ⟨ws ++ a, by simp⟩

theorem noM_fix {p : Pat} (hp : WsOk p) (o : List Char → Option (List Char)) :
    ∀ n t, t.length ≤ n → ∀ u, NoM p u t → NoM p u (fix p o t) := by
  intro n
  induction n with
  | zero =>
    intro t ht u h
    have : t = [] := List.eq_nil_of_length_eq_zero (by omega)
    subst this; exact h
  | succ n ih =>
    intro t ht u h
    match t, ht with
    | [], _ => exact h
    | c :: cs, ht =>
      cases hm : matchAt p (c :: cs) with
      | none =>
        rw [fix_none o hm]
        have h' : NoM p (u ++ [c]) cs := by
          intro i hi
          simp only [List.length_append, List.length_singleton] at hi
          rcases Nat.lt_succ_iff_lt_or_eq.mp hi with hi|hi
          · have := h i hi; simpa using this
          · subst hi; simpa using hm
        have := ih cs (by simp at ht; omega) (u ++ [c]) h'
        intro i hi
        have := this i (by simp; omega)
        simpa using this
      | some r =>
        obtain ⟨d, ws, a, rest⟩ := r
        rw [fix_match o hm]
        obtain ⟨hs, hd, -, -, -, -⟩ := matchAt_some hm
        obtain ⟨Z, hZ⟩ := repl_prefix o d ws a
        rw [hZ, List.append_assoc]
        intro i hi
        rw [List.drop_append_of_le_length (by omega)]
        have hv : u.drop i ≠ [] := by simp; omega
        cases hw : matchAt p (u.drop i ++ (d ++ (Z ++ fix p o rest))) with
        | none => rfl
        | some r =>
          obtain ⟨d0, ws0, a0, r0⟩ := r
          exfalso
          have hw' : matchAt p (u.drop i ++ d ++ (Z ++ fix p o rest)) = some (d0, ws0, a0, r0) := by
            simpa using hw
          have hle := window hp hv hd hw'
          obtain ⟨hs0, hd0, hws0, hw0, ha0, hla⟩ := matchAt_some hw'
          -- split u.drop i = (d0 ++ ws0 ++ a0) ++ v2
          have hs0' : (d0 ++ ws0 ++ a0) ++ r0 = u.drop i ++ (d ++ (Z ++ fix p o rest)) := by
            rw [← hs0]; simp
          rcases List.append_eq_append_iff.mp hs0' with ⟨v2, hv2, hr0⟩ | ⟨bs, hbs, hbs2⟩
          · -- the same token matches in the original text: contradiction with `h`
            have key : matchAt p (d0 ++ ws0 ++ a0 ++ (v2 ++ (c :: cs))) = some (d0, ws0, a0, v2 ++ (c :: cs)) := by
              apply matchAt_token hp hd0 hws0 hw0 ha0
              intro hstrict
              have hr := hla hstrict
              cases v2 with
              | nil =>
                obtain ⟨c1, t1, hc1, hdig⟩ := isDateShape_head hd
                have : c :: cs = c1 :: (t1 ++ ws ++ a ++ rest) := by rw [hs, hc1]; simp
                rw [List.nil_append, this]
                simp [notAlphaHead, digit_not_alpha c1 hdig]
              | cons x v2 =>
                rw [hr0] at hr
                simpa [notAlphaHead] using hr
            have := h i hi
            rw [List.drop_append_of_le_length (by omega), hv2] at this
            simp only [List.append_assoc] at this key
            rw [key] at this; cases this
          · -- the token would extend past u.drop i: excluded by `window`
            have : bs = [] := by
              have := congrArg List.length hbs
              simp only [List.length_append] at this hle
              exact List.eq_nil_of_length_eq_zero (by omega)
            subst this
            simp only [List.append_nil] at hbs
            -- reduce to the first case with v2 = []
            have key : matchAt p (d0 ++ ws0 ++ a0 ++ (c :: cs)) = some (d0, ws0, a0, c :: cs) := by
              apply matchAt_token hp hd0 hws0 hw0 ha0
              intro _
              obtain ⟨c1, t1, hc1, hdig⟩ := isDateShape_head hd
              have : c :: cs = c1 :: (t1 ++ ws ++ a ++ rest) := by rw [hs, hc1]; simp
              rw [this]; simp [notAlphaHead, digit_not_alpha c1 hdig]
            have := h i hi
            rw [List.drop_append_of_le_length (by omega), ← hbs] at this
            simp only [List.append_assoc] at this key
            rw [key] at this; cases this


def OracleOk (o : List Char → Option (List Char)) : Prop :=
  ∀ d a, o d = some a → abbrs.contains a = true

theorem fix_head {p : Pat} (o : List Char → Option (List Char)) {t : List Char}
    (h : notAlphaHead t = true) : notAlphaHead (fix p o t) = true := by
  match t with
  | [] => rfl
  | c :: cs =>
    cases hm : matchAt p (c :: cs) with
    | none => rw [fix_none o hm]; simpa [notAlphaHead] using h
    | some r =>
      obtain ⟨d, ws, a, rest⟩ := r
      rw [fix_match o hm]
      obtain ⟨Z, hZ⟩ := repl_prefix o d ws a
      obtain ⟨-, hd, -⟩ := matchAt_some hm
      obtain ⟨c1, t1, hc1, hdig⟩ := isDateShape_head hd
      rw [hZ, hc1]; simp [notAlphaHead, digit_not_alpha c1 hdig]

/-- **Idempotence**: a second pass of the day-fix changes nothing. Holds for
every blank class that excludes digits, `-` and letters, with or without the
lookahead, and for every oracle that answers with a day abbreviation. -/
theorem fix_idem {p : Pat} (hp : WsOk p) {o : List Char → Option (List Char)} (ho : OracleOk o) :
    ∀ n s, s.length ≤ n → fix p o (fix p o s) = fix p o s := by
  intro n
  induction n with
  | zero =>
    intro s hs
    have : s = [] := List.eq_nil_of_length_eq_zero (by omega)
    subst this; rfl
  | succ n ih =>
    intro s hs
    match s, hs with
    | [], _ => rfl
    | c :: cs, hs =>
      cases hm : matchAt p (c :: cs) with
      | none =>
        rw [fix_none o hm]
        have h0 : NoM p [c] cs := by
          intro i hi; simp at hi; subst hi; simpa using hm
        have h1 := noM_fix hp o _ cs (Nat.le_refl _) [c] h0 0 (by simp)
        simp only [List.drop_zero, List.singleton_append] at h1
        rw [fix_none o h1, ih cs (by simp at hs; omega)]
      | some r =>
        obtain ⟨d, ws, a, rest⟩ := r
        have hlen := matchAt_len hm
        rw [fix_match o hm]
        obtain ⟨-, hd, hws, hw, ha, hla⟩ := matchAt_some hm
        have hla' : p.strict = true → notAlphaHead (fix p o rest) = true :=
          fun hs => fix_head o (hla hs)
        have ihr := ih rest (by simp only [List.length_cons] at hlen hs; omega)
        unfold repl
        cases hod : o d with
        | none =>
          simp only
          rw [fix_match o (matchAt_token hp hd hws hw ha hla'), ihr]
          simp [repl, hod]
        | some a' =>
          simp only
          by_cases hae : a' = a
          · simp only [hae, ite_true]
            rw [fix_match o (matchAt_token hp hd hws hw ha hla'), ihr]
            simp [repl, hod, hae]
          · simp only [hae, ite_false]
            have ha' := ho d a' hod
            have := matchAt_token (ws := [' ']) hp hd (by simp) (by simp [hp.space]) ha' hla'
            simp only [List.append_assoc, List.cons_append, List.nil_append] at this
            simp only [List.append_assoc, List.cons_append]
            rw [fix_match o this, ihr]
            simp [repl, hod]

/-! ### Only whole stamp tokens change -/

/-- `Rw p s t`: `t` is `s` with some stamp tokens -- date, blanks, a day
abbreviation that is **not followed by a letter** -- replaced by
`date ++ " " ++ another day abbreviation`. Every other character is kept. -/
inductive Rw (p : Pat) : List Char → List Char → Prop
  | nil : Rw p [] []
  | keep (c : Char) {s t : List Char} : Rw p s t → Rw p (c :: s) (c :: t)
  | stamp {d ws a a' rest t : List Char} :
      isDateShape d = true → ws ≠ [] → (∀ c ∈ ws, p.isWs c = true) →
      abbrs.contains a = true → abbrs.contains a' = true → notAlphaHead rest = true →
      Rw p rest t → Rw p (d ++ ws ++ a ++ rest) (d ++ ' ' :: a' ++ t)

theorem Rw.keeps {p : Pat} {s t : List Char} (h : Rw p s t) : ∀ x, Rw p (x ++ s) (x ++ t)
  | [] => h
  | c :: x => Rw.keep c (Rw.keeps h x)

theorem fix_rw {p : Pat} (hs : p.strict = true) {o : List Char → Option (List Char)} (ho : OracleOk o) :
    ∀ n s, s.length ≤ n → Rw p s (fix p o s) := by
  intro n
  induction n with
  | zero =>
    intro s h
    have : s = [] := List.eq_nil_of_length_eq_zero (by omega)
    subst this; exact Rw.nil
  | succ n ih =>
    intro s h
    match s, h with
    | [], _ => exact Rw.nil
    | c :: cs, h =>
      cases hm : matchAt p (c :: cs) with
      | none => rw [fix_none o hm]; exact Rw.keep c (ih cs (by simp at h; omega))
      | some r =>
        obtain ⟨d, ws, a, rest⟩ := r
        have hlen := matchAt_len hm
        obtain ⟨he, hd, hws, hw, ha, hla⟩ := matchAt_some hm
        have ihr := ih rest (by simp only [List.length_cons] at hlen h; omega)
        rw [fix_match o hm, he]
        unfold repl
        split
        · rename_i a' hod
          split
          · have := Rw.keeps ihr (d ++ ws ++ a); simpa using this
          · exact Rw.stamp hd hws hw ha (ho d a' hod) (hla hs) ihr
        · have := Rw.keeps ihr (d ++ ws ++ a); simpa using this

/-- With blanks restricted to `[ \t]`, no line is ever joined to the next. -/
theorem Rw.newlines {p : Pat} (hn : p.isWs '\n' = false) {s t : List Char} (h : Rw p s t) :
    s.count '\n' = t.count '\n' := by
  induction h with
  | nil => rfl
  | keep c _ ih => simp [List.count_cons, ih]
  | @stamp d ws a a' rest t hd _ hw ha ha' _ _ ih =>
    have c0 : ∀ l : List Char, (∀ c ∈ l, c ≠ '\n') → l.count '\n' = 0 := fun l hl =>
      List.count_eq_zero.mpr (fun hm => hl _ hm rfl)
    have hdn : d.count '\n' = 0 := c0 d (fun c hc he => by
      have := isDateShape_good hd c hc; subst he; cases this)
    have hwn : ws.count '\n' = 0 := c0 ws (fun c hc he => by
      have := hw c hc; subst he; rw [hn] at this; cases this)
    have alpha0 : ∀ x, abbrs.contains x = true → x.count '\n' = 0 := fun x hx =>
      c0 x (fun c hc he => by have := (abbrs_alpha hx).2 c hc; subst he; cases this)
    simp [List.count_append, hdn, hwn, alpha0 a ha, alpha0 a' ha', ih]


def abbrOf (w : Int) : List Char :=
  match w with
  | 0 => "Mon".toList | 1 => "Tue".toList | 2 => "Wed".toList | 3 => "Thu".toList
  | 4 => "Fri".toList | 5 => "Sat".toList | _ => "Sun".toList

theorem abbrOf_mem (w : Int) : abbrs.contains (abbrOf w) = true := by
  unfold abbrOf; split <;> decide

def digitVal (c : Char) : Int := (c.toNat : Int) - 48

/-- `dow(date_str)` / `strftime('%a')`, or `none` where `strptime` raises. -/
def realDow : List Char → Option (List Char)
  | [y1, y2, y3, y4, _, m1, m2, _, d1, d2] =>
    let y := ((digitVal y1 * 10 + digitVal y2) * 10 + digitVal y3) * 10 + digitVal y4
    let m := digitVal m1 * 10 + digitVal m2
    let d := digitVal d1 * 10 + digitVal d2
    if 1 ≤ y ∧ 1 ≤ m ∧ m ≤ 12 ∧ 1 ≤ d ∧ d ≤ daysIn y m then some (abbrOf (weekday y m d)) else none
  | _ => none

theorem realDow_ok : OracleOk realDow := by
  intro d a h
  unfold realDow at h
  split at h
  · dsimp only at h
    split at h
    · simp only [Option.some.injEq] at h; subst h; exact abbrOf_mem _
    · cases h
  · cases h

def pyWs (c : Char) : Bool :=
  c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\x0b' || c == '\x0c'

/-- The pattern before 2026-09-23: `\s+(Mon|...|Sun)`, no lookahead. -/
def oldPat : Pat := ⟨pyWs, false⟩
/-- `org_date_hook.DATE_PATTERN` as fixed by the coordinator: lookahead, `\s+`. -/
def hookPat : Pat := ⟨pyWs, true⟩
/-- `date_utils.DATE_DOW_RE` / `org_date_validator.DATE_PATTERN` after this fix. -/
def newPat : Pat := ⟨fun c => c == ' ' || c == '\t', true⟩

theorem pyWs_ok : WsOk hookPat := by
  refine ⟨?_, by decide, ?_, by decide⟩ <;>
  · intro c h
    simp only [hookPat, pyWs, Bool.or_eq_true, beq_iff_eq] at h
    rcases h with (((((rfl|rfl)|rfl)|rfl)|rfl)|rfl) <;> decide

theorem oldPat_ok : WsOk oldPat := by
  refine ⟨?_, by decide, ?_, by decide⟩ <;>
  · intro c h
    simp only [oldPat, pyWs, Bool.or_eq_true, beq_iff_eq] at h
    rcases h with (((((rfl|rfl)|rfl)|rfl)|rfl)|rfl) <;> decide

theorem newPat_ok : WsOk newPat := by
  refine ⟨?_, by decide, ?_, by decide⟩ <;>
  · intro c h
    simp only [newPat, Bool.or_eq_true, beq_iff_eq] at h
    rcases h with rfl|rfl <;> decide

/-! ### Replayed counterexamples (2026-09-24 is a Thursday) -/

theorem old_monitor : fix oldPat realDow "2026-09-24 Monitor".toList = "2026-09-24 Thuitor".toList := by
  decide
theorem old_monday : fix oldPat realDow "2026-09-24 Monday".toList = "2026-09-24 Thuday".toList := by
  decide
/-- The old rewrite fired on an abbreviation followed by a letter. -/
theorem old_not_a_token :
    matchAt oldPat "2026-09-24 Monitor".toList =
      some ("2026-09-24".toList, [' '], "Mon".toList, "itor".toList) ∧
    notAlphaHead "itor".toList = false := by decide
theorem hook_monitor : fix hookPat realDow "2026-09-24 Monitor".toList = "2026-09-24 Monitor".toList := by
  decide
theorem hook_fixes : fix hookPat realDow "<2026-09-24 Mon>".toList = "<2026-09-24 Thu>".toList := by
  decide
/-- `\s` crosses a line break: the coordinator's hook still joins two lines. -/
theorem hook_joins_lines :
    fix hookPat realDow "Due 2026-09-24\nSat with Bob".toList = "Due 2026-09-24 Thu with Bob".toList := by
  decide
theorem new_keeps_lines :
    fix newPat realDow "Due 2026-09-24\nSat with Bob".toList = "Due 2026-09-24\nSat with Bob".toList := by
  decide

/-- **The fixed date_utils rule**: only whole stamp tokens change, no line is
joined, and a second pass is a no-op. -/
theorem new_fix_sound (s : List Char) :
    Rw newPat s (fix newPat realDow s) ∧
    s.count '\n' = (fix newPat realDow s).count '\n' ∧
    fix newPat realDow (fix newPat realDow s) = fix newPat realDow s :=
  have h := fix_rw rfl realDow_ok _ s (Nat.le_refl _)
  ⟨h, Rw.newlines (by decide) h, fix_idem newPat_ok realDow_ok _ s (Nat.le_refl _)⟩

/-- The coordinator's hook: whole tokens only, and idempotent. -/
theorem hook_fix_sound (s : List Char) :
    Rw hookPat s (fix hookPat realDow s) ∧
    fix hookPat realDow (fix hookPat realDow s) = fix hookPat realDow s :=
  ⟨fix_rw rfl realDow_ok _ s (Nat.le_refl _), fix_idem pyWs_ok realDow_ok _ s (Nat.le_refl _)⟩


/-! ## parse_relative: day words and calendar months -/

def fullNames : List (List Char) :=
  ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"].map String.toList
def lowAbbrs : List (List Char) := ["mon", "tue", "wed", "thu", "fri", "sat", "sun"].map String.toList

def firstIdx (f : List Char → Bool) : List (List Char) → Nat → Option Nat
  | [], _ => none
  | x :: xs, i => if f x then some i else firstIdx f xs (i + 1)

/-- Old: `re.match(r"(next|last)\s+(mon|tue|...)")` -- a PREFIX match on the word. -/
def dayOld (w : List Char) : Option Nat := firstIdx (fun a => a.isPrefixOf w) lowAbbrs 0
/-- New `_day_word`: at least 3 letters, and a prefix of a full day name. -/
def dayNew (w : List Char) : Option Nat :=
  if w.length < 3 then none else firstIdx (fun f => w.isPrefixOf f) fullNames 0

theorem old_month_is_monday : dayOld "month".toList = some 0 := by decide
theorem new_month_rejected : dayNew "month".toList = none ∧ dayNew "mongoose".toList = none ∧
    dayNew "week".toList = none := by decide
theorem new_accepts_days : (List.range 7).all (fun i =>
    dayNew (fullNames.getD i []) == some i && dayNew (lowAbbrs.getD i []) == some i) = true := by decide

theorem firstIdx_sound {f : List Char → Bool} :
    ∀ (l : List (List Char)) (k i : Nat), firstIdx f l k = some i →
      k ≤ i ∧ ∃ x, l[i - k]? = some x ∧ f x = true
  | [], _, _, h => by simp [firstIdx] at h
  | x :: xs, k, i, h => by
    unfold firstIdx at h
    split at h
    · simp only [Option.some.injEq] at h; subst h; exact ⟨Nat.le_refl _, x, by simp, by assumption⟩
    · obtain ⟨h1, y, hy, hf⟩ := firstIdx_sound xs (k + 1) i h
      refine ⟨by omega, y, ?_, hf⟩
      have : i - k = (i - (k + 1)) + 1 := by omega
      rw [this]; simpa using hy

/-- Soundness of the fix: an accepted word is a prefix (≥ 3 letters) of the
day name it resolves to. So "month" can never mean Monday. -/
theorem dayNew_sound {w : List Char} {i : Nat} (h : dayNew w = some i) :
    3 ≤ w.length ∧ ∃ f, fullNames[i]? = some f ∧ w.isPrefixOf f = true := by
  unfold dayNew at h
  split at h
  · cases h
  · obtain ⟨-, x, hx, hf⟩ := firstIdx_sound _ 0 i h
    exact ⟨by omega, x, by simpa using hx, hf⟩

/-- `add_months`: same day in the month `n` away, clamped to its last day. -/
def addMonths (y m d n : Int) : Int × Int × Int :=
  let idx := y * 12 + (m - 1) + n
  let y' := idx / 12
  let m' := idx % 12 + 1
  (y', m', min d (daysIn y' m'))

theorem daysIn_ge (y m : Int) : 28 ≤ daysIn y m := by
  unfold daysIn; split
  · split <;> omega
  · split <;> omega

theorem addMonths_spec (y m d n : Int) (hd : 1 ≤ d) :
    let r := addMonths y m d n
    r.1 * 12 + (r.2.1 - 1) = y * 12 + (m - 1) + n ∧ 1 ≤ r.2.1 ∧ r.2.1 ≤ 12 ∧
    1 ≤ r.2.2 ∧ r.2.2 ≤ daysIn r.1 r.2.1 ∧ r.2.2 ≤ d := by
  simp only [addMonths]
  have := daysIn_ge ((y * 12 + (m - 1) + n) / 12) ((y * 12 + (m - 1) + n) % 12 + 1)
  refine ⟨by omega, by omega, by omega, by omega, Int.min_le_right _ _, Int.min_le_left _ _⟩

theorem addMonths_examples :
    addMonths 2026 9 23 1 = (2026, 10, 23) ∧ addMonths 2026 1 31 1 = (2026, 2, 28) ∧
    addMonths 2024 3 31 (-1) = (2024, 2, 29) ∧ addMonths 2026 12 15 1 = (2027, 1, 15) ∧
    addMonths 2026 1 15 (-1) = (2025, 12, 15) := by decide

/-! ## decay_and_review.month_keys -/

/-- Hinnant `civil_from_days` (inverse of `daysFromCivil`). -/
def civilFromDays (z : Int) : Int × Int × Int :=
  let z := z + 719468
  let era := z / 146097
  let doe := z - era * 146097
  let yoe := (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365
  let y := yoe + era * 400
  let doy := doe - (365 * yoe + yoe / 4 - yoe / 100)
  let mp := (5 * doy + 2) / 153
  let d := doy - (153 * mp + 2) / 5 + 1
  let m := if mp < 10 then mp + 3 else mp - 9
  (if m ≤ 2 then y + 1 else y, m, d)

theorem civil_roundtrip_examples :
    [(2026, 3, 31), (2026, 3, 1), (2026, 1, 30), (2000, 2, 29), (1970, 1, 1)].all
      (fun (y, m, d) => civilFromDays (daysFromCivil y m d) == (y, m, d)) = true := by decide

/-- Old: month of `now - 30*i days`, then `sorted(set(...))`. -/
def oldKeys (y m d : Int) (n : Nat) : List (Int × Int) :=
  (List.range n).map (fun (i : Nat) =>
    let c := civilFromDays (daysFromCivil y m d - 30 * (i : Int)); (c.1, c.2.1))

def dedup : List (Int × Int) → List (Int × Int)
  | [] => []
  | x :: xs => if x ∈ xs then dedup xs else x :: dedup xs

/-- Mar 31: Mar 31, Mar 1, Jan 30 -- February is never read; `set` has 2 keys. -/
theorem old_keys_skip_february :
    oldKeys 2026 3 31 3 = [(2026, 3), (2026, 3), (2026, 1)] ∧
    (dedup (oldKeys 2026 3 31 3)).length = 2 := by decide

def ofIdx (k : Int) : Int × Int := (k / 12, k % 12 + 1)

/-- New: calendar months on a month index. -/
def newKeys (y m : Int) (n : Nat) : List (Int × Int) :=
  (List.range n).map (fun (i : Nat) => ofIdx (y * 12 + (m - 1) - (i : Int)))

theorem ofIdx_inj {a b : Int} (h : ofIdx a = ofIdx b) : a = b := by
  simp only [ofIdx, Prod.mk.injEq] at h; omega

/-- **month_keys_distinct**: N keys, pairwise distinct, each a real month,
and consecutive (key i is exactly i months back). -/
theorem month_keys_distinct (y m : Int) (n : Nat) :
    (newKeys y m n).length = n ∧ (newKeys y m n).Nodup ∧
    ∀ i < n, ∃ k, (newKeys y m n)[i]? = some k ∧
      k.1 * 12 + (k.2 - 1) = y * 12 + (m - 1) - i ∧ 1 ≤ k.2 ∧ k.2 ≤ 12 := by
  refine ⟨by simp [newKeys], ?_, ?_⟩
  · refine List.Pairwise.map _ ?_ List.nodup_range
    intro a b hab h; have := ofIdx_inj h; omega
  · intro i hi
    refine ⟨ofIdx (y * 12 + (m - 1) - i), by simp [newKeys, hi], ?_⟩
    simp only [ofIdx]; omega

theorem new_keys_march31 : newKeys 2026 3 3 = [(2026, 3), (2026, 2), (2026, 1)] := by decide

/-! ## org_date_validator: the "suspect year" -/

def suspectOld (y : Int) : Bool := y == 2025
def suspectNew (today y : Int) : Bool := y == today - 1

theorem suspect_new_is_last_year (today y : Int) : suspectNew today y = true ↔ y = today - 1 := by
  simp [suspectNew]
/-- In 2027 the literal no longer names last year. -/
theorem suspect_old_stale : suspectOld 2026 = false ∧ suspectNew 2027 2026 = true := by decide

/-! ## validate_org_dates: frontmatter keys -/

/-- Lines up to the closer, if there is one. -/
def untilClose (cl : String → Bool) : List String → Option (List String)
  | [] => none
  | l :: ls => if cl l then some [] else (untilClose cl ls).map (l :: ·)

def firstSome (f : String → Option String) : List String → Option String
  | [] => none
  | l :: ls => (f l).orElse (fun _ => firstSome f ls)

/-- Old: `FM_DATE.search(text)`, `FM_DAY.search(text)` over the whole file. -/
def fmOld (fd fy : String → Option String) (ls : List String) : Option (String × String) :=
  match firstSome fd ls, firstSome fy ls with
  | some a, some b => some (a, b) | _, _ => none

/-- New: only inside the leading `---` ... `---`/`...` block. -/
def fmNew (op cl : String → Bool) (fd fy : String → Option String) :
    List String → Option (String × String)
  | l :: ls =>
    if op l then
      match untilClose cl ls with
      | some fm => fmOld fd fy fm
      | none => none
    else none
  | [] => none

theorem untilClose_append {cl : String → Bool} {fm : List String} (h : ∀ l ∈ fm, cl l = false)
    (c : String) (hc : cl c = true) (body : List String) : untilClose cl (fm ++ c :: body) = some fm := by
  induction fm with
  | nil => simp [untilClose, hc]
  | cons l fm ih =>
    simp only [List.cons_append, untilClose, h l (by simp)]
    rw [ih (fun x hx => h x (by simp [hx]))]; rfl

/-- **Frame**: the frontmatter verdict never depends on the body. -/
theorem fmNew_frame (op cl : String → Bool) (fd fy : String → Option String) (o c : String)
    (fm : List String) (hfm : ∀ l ∈ fm, cl l = false) (hc : cl c = true) (body body' : List String) :
    fmNew op cl fd fy (o :: fm ++ c :: body) = fmNew op cl fd fy (o :: fm ++ c :: body') := by
  simp only [List.cons_append, fmNew, untilClose_append hfm c hc]

theorem fmNew_no_frontmatter (op cl : String → Bool) (fd fy : String → Option String)
    (l : String) (ls : List String) (h : op l = false) : fmNew op cl fd fy (l :: ls) = none := by
  simp [fmNew, h]

/-- Replayed counterexample: a body `date:` line and a later body `day:` line. -/
def exDate (l : String) : Option String := if l = "date: 2026-09-24" then some "2026-09-24" else none
def exDay (l : String) : Option String := if l = "day: Mon" then some "Mon" else none
def exOpen (l : String) : Bool := l == "---"
def exClose (l : String) : Bool := l == "---" || l == "..."

theorem old_reads_body :
    fmOld exDate exDay ["# Notes", "date: 2026-09-24", "later", "day: Mon"] = some ("2026-09-24", "Mon") ∧
    fmNew exOpen exClose exDate exDay ["# Notes", "date: 2026-09-24", "later", "day: Mon"] = none ∧
    fmNew exOpen exClose exDate exDay ["---", "date: 2026-09-24", "---", "```yaml", "day: Mon", "```"] = none ∧
    fmNew exOpen exClose exDate exDay ["---", "date: 2026-09-24", "day: Mon", "---", "body"] =
      some ("2026-09-24", "Mon") := by decide

namespace HookLock

/-! ## 7. Hook write under the org lock (Decision G8)

`org_date_hook.fix_dates` is a read-modify-write of one file. An adapter
commit is one atomic step under the org transaction lock (`serialized`). The
file's content type `α`, the hook's fix `f` and the adapter's edit `g` are
parameters. Events are micro-steps; any interleaving of them is a schedule.
Pre-G8 the hook took no lock, so a commit could land between its read and
its write. Post-G8 the hook's read and write happen while it holds the lock,
and the adapter cannot commit while the lock is held.

Decision Q12 (2026-09-23) puts the manual repair tools under the same lock:
org_union_merge --apply, org_dedup_within_file --apply,
org_resolve_id_conflicts, inbox_dedup --apply, stamp_seq_todo and
validate_org_dates --fix each watch, read and write one file inside one
`serialized` call. That is this model with `f` the tool's rewrite, so
`repair_tool_no_lost_update` is `locked_no_lost_update` restated for them.
How long `lock` may wait (`serialized(timeout=...)`, also Q12) is outside
the model: a lock that is not obtained is a schedule with no hook events,
covered by `skipped_keeps_adapter`. -/

inductive Ev | lock | read | write | unlock | adapter
  deriving DecidableEq, Repr

structure HS (α : Type) where
  file  : α
  buf   : Option α   -- what the hook read
  held  : Bool       -- the hook holds the org transaction lock
  wrote : Bool       -- the hook has written its fix
  adone : Bool       -- the adapter has committed
  deriving DecidableEq, Repr

variable {α : Type}

/-- Post-G8 semantics. `unlock` drops the buffer: a read only counts inside
the lock that the write happens under. -/
def stepL (f g : α → α) (s : HS α) : Ev → Option (HS α)
  | .lock => if s.held then none else some { s with held := true }
  | .read => if s.held then some { s with buf := some s.file } else none
  | .write =>
    match s.held, s.wrote, s.buf with
    | true, false, some b => some { s with file := f b, wrote := true }
    | _, _, _ => none
  | .unlock => if s.held then some { s with held := false, buf := none } else none
  | .adapter => if s.held || s.adone then none else some { s with file := g s.file, adone := true }

/-- Pre-G8 semantics: no lock at all. -/
def stepU (f g : α → α) (s : HS α) : Ev → Option (HS α)
  | .read => some { s with buf := some s.file }
  | .write =>
    match s.wrote, s.buf with
    | false, some b => some { s with file := f b, wrote := true }
    | _, _ => none
  | .adapter => if s.adone then none else some { s with file := g s.file, adone := true }
  | _ => some s

def run (step : HS α → Ev → Option (HS α)) : HS α → List Ev → Option (HS α)
  | s, [] => some s
  | s, e :: es => (step s e).bind (fun s' => run step s' es)

def init (s0 : α) : HS α := ⟨s0, none, false, false, false⟩

/-- The invariant of the locked semantics: the file holds exactly the effects
that happened, in an order consistent with the lock. -/
def Inv (f g : α → α) (s0 : α) (s : HS α) : Prop :=
  (s.held = false → s.buf = none) ∧
  (s.held = true → s.wrote = false → ∀ b, s.buf = some b → b = s.file) ∧
  (s.adone = false → s.wrote = false → s.file = s0) ∧
  (s.adone = true → s.wrote = false → s.file = g s0) ∧
  (s.adone = false → s.wrote = true → s.file = f s0) ∧
  (s.adone = true → s.wrote = true → s.file = f (g s0) ∨ s.file = g (f s0))

theorem inv_init (f g : α → α) (s0 : α) : Inv f g s0 (init s0) := by
  simp [Inv, init]

theorem inv_step (f g : α → α) (s0 : α) (s s' : HS α) (e : Ev)
    (hi : Inv f g s0 s) (h : stepL f g s e = some s') : Inv f g s0 s' := by
  obtain ⟨h1, h2, h3, h4, h5, h6⟩ := hi
  cases e with
  | lock =>
    simp only [stepL] at h
    split at h
    · cases h
    · rename_i hh; cases h
      refine ⟨by simp, ?_, h3, h4, h5, h6⟩
      intro _ _ b hb; simp at hh; rw [h1 hh] at hb; cases hb
  | read =>
    simp only [stepL] at h
    split at h
    · cases h
      exact ⟨by simp_all, by intro _ _ b hb; simp at hb; exact hb.symm, h3, h4, h5, h6⟩
    · cases h
  | write =>
    simp only [stepL] at h
    split at h
    · rename_i b hh hw hb
      cases h
      have hbf := h2 hh hw b hb
      subst hbf
      refine ⟨by simp_all, by simp, by simp, by simp, ?_, ?_⟩
      · intro ha _; simp at ha; rw [h3 ha hw]
      · intro ha _; simp at ha; left; rw [h4 ha hw]
    · cases h
  | unlock =>
    simp only [stepL] at h
    split at h
    · cases h; exact ⟨by simp, by simp, h3, h4, h5, h6⟩
    · cases h
  | adapter =>
    simp only [stepL] at h
    split at h
    · cases h
    · rename_i hh
      cases h
      simp only [Bool.or_eq_true, not_or, Bool.not_eq_true] at hh
      obtain ⟨hh, ha⟩ := hh
      refine ⟨by simp [h1 hh], by simp [hh], by simp, ?_, by simp, ?_⟩
      · intro _ hw; simp at hw; rw [h3 ha hw]
      · intro _ hw; simp at hw; right; rw [h5 ha hw]

theorem inv_run (f g : α → α) (s0 : α) : ∀ (es : List Ev) (s s' : HS α),
    Inv f g s0 s → run (stepL f g) s es = some s' → Inv f g s0 s' := by
  intro es
  induction es with
  | nil => intro s s' hi h; simp [run] at h; subst h; exact hi
  | cons e es ih =>
    intro s s' hi h
    simp only [run] at h
    cases hs : stepL f g s e with
    | none => rw [hs] at h; cases h
    | some t => rw [hs] at h; exact ih t s' (inv_step f g s0 s t e hi hs) h

/-- **Decision G8, proved.** Under every schedule of the locked hook and an
adapter commit, once both have happened the file is `f (g s0)` or `g (f s0)`:
the adapter's commit is never overwritten by a stale read. -/
theorem locked_no_lost_update (f g : α → α) (s0 : α) (es : List Ev) (s : HS α)
    (h : run (stepL f g) (init s0) es = some s) (ha : s.adone = true) (hw : s.wrote = true) :
    s.file = f (g s0) ∨ s.file = g (f s0) :=
  (inv_run f g s0 es _ s (inv_init f g s0) h).2.2.2.2.2 ha hw

/-- **Decision Q12.** The same guarantee for any repair tool whose read and
write run inside one `serialized` call (its rewrite is `f`): once the tool
has written and a concurrent adapter call has committed, the file is one of
the two serial orders. Replayed on the real tools in
`lib/tests/test_followups_org.py` (`*_does_not_lose_a_racing_adapter_commit`). -/
theorem repair_tool_no_lost_update (rewrite adapter : α → α) (s0 : α) (es : List Ev)
    (s : HS α) (h : run (stepL rewrite adapter) (init s0) es = some s)
    (ha : s.adone = true) (hw : s.wrote = true) :
    s.file = rewrite (adapter s0) ∨ s.file = adapter (rewrite s0) :=
  locked_no_lost_update rewrite adapter s0 es s h ha hw

/-- And when the hook skips (lock busy past its timeout: it never writes),
the adapter's commit stands alone. -/
theorem skipped_keeps_adapter (f g : α → α) (s0 : α) (es : List Ev) (s : HS α)
    (h : run (stepL f g) (init s0) es = some s) (ha : s.adone = true) (hw : s.wrote = false) :
    s.file = g s0 :=
  (inv_run f g s0 es _ s (inv_init f g s0) h).2.2.2.1 ha hw

/-- **Pre-G8, refuted by a schedule.** Read, adapter commit, write: the file
ends as the hook's fix of the stale read, and the commit is gone. With the
adapter appending `1` and a hook that has nothing to change, the file is
`[0]`, neither `f (g s0)` nor `g (f s0)` (both `[0, 1]`). -/
theorem unlocked_loses_adapter_commit :
    (run (stepU (α := List Nat) id (· ++ [1])) (init [0]) [.read, .adapter, .write]).map HS.file
      = some [0] := by decide

/-- Not vacuous: both orders are reachable under the lock. -/
theorem locked_schedules_reachable :
    (run (stepL (α := List Nat) (· ++ [2]) (· ++ [1])) (init [0])
      [.lock, .read, .write, .unlock, .adapter]).map HS.file = some [0, 2, 1] ∧
    (run (stepL (α := List Nat) (· ++ [2]) (· ++ [1])) (init [0])
      [.adapter, .lock, .read, .write, .unlock]).map HS.file = some [0, 1, 2] ∧
    run (stepL (α := List Nat) (· ++ [2]) (· ++ [1])) (init [0])
      [.lock, .read, .adapter, .write] = none := by decide

end HookLock

end DatacoreSpec.Dates
