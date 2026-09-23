/-!
# Research queue lifecycle and research router

Models `modules/research/lib/research_orchestrator.py` (queue selection,
fetch/analysis outcomes, `mark_done`, `note_failure` parking, auto-archive)
and `modules/research/lib/research_router.py` (`classify` relevance bounds
and the issue gate).

## What is abstracted

* The research queue is a list of items. An item carries what the control
  flow reads: priority cookie, whether a URL was found, TODO state, and the
  two attempt counters (`:FETCH_ATTEMPTS:`, `:ANALYSIS_ATTEMPTS:`).
* Fetch and Claude analysis are oracles `F A : Nat → Nat → Bool`
  (run number → item id → ok?). Every liveness theorem holds FOR EVERY oracle,
  so it holds whatever the network and the model do.
* Items are addressed by a key `id`. That is what the FIXED locator provides
  (`:ID:` when present, else a unique heading line, else the heading ordinal
  recorded at parse time). The OLD locator addressed items by heading text;
  its defects are modelled separately on flat line lists (section 3).
-/

namespace DatacoreSpec.Research

/-! ## 1. Queue model -/

inductive St | todo | waiting | done
  deriving DecidableEq, Repr

inductive Prio | A | B | C
  deriving DecidableEq, Repr

structure Item where
  id      : Nat
  prio    : Prio
  hasUrl  : Bool
  st      : St
  fa      : Nat          -- :FETCH_ATTEMPTS:
  aa      : Nat          -- :ANALYSIS_ATTEMPTS:
  created : Option Nat   -- :CREATED:/:RECEIVED: as a day number, if dated
  deriving DecidableEq, Repr

/-- `parse_research_items` keeps a TODO heading only when it found a URL. -/
def eligible (it : Item) : Bool := it.st == .todo && it.hasUrl

/-- `parse_research_items(limit)`: collect URL-bearing TODOs, stable sort by
priority cookie (A < B < C, missing = C), cut at `limit`. A stable sort on a
three-valued key is exactly the three-bucket concatenation. -/
def select (limit : Nat) (q : List Item) : List Item :=
  let e := q.filter eligible
  (e.filter (·.prio == .A) ++ e.filter (·.prio == .B) ++ e.filter (·.prio == .C)).take limit

/-- Claim: "URL-less TODOs … must not consume limit slots". Selection is the
same whether or not URL-less items are present anywhere in the file. -/
theorem urlless_consume_no_slots (limit : Nat) (q : List Item) :
    select limit q = select limit (q.filter (·.hasUrl)) := by
  have : (q.filter (·.hasUrl)).filter eligible = q.filter eligible := by
    rw [List.filter_filter]
    congr 1; funext it
    cases h : it.hasUrl <;> simp [eligible, h]
  simp only [select, this]

theorem select_sub {limit : Nat} {q : List Item} {x : Item} (h : x ∈ select limit q) :
    x ∈ q ∧ eligible x = true := by
  simp only [select] at h
  have h' := List.mem_of_mem_take h
  simp only [List.mem_append, List.mem_filter] at h'
  rcases h' with ((⟨⟨a, b⟩, _⟩ | ⟨⟨a, b⟩, _⟩) | ⟨⟨a, b⟩, _⟩) <;> exact ⟨a, b⟩

theorem select_nonempty {limit : Nat} {q : List Item} (hl : 0 < limit)
    (he : ∃ it ∈ q, eligible it = true) :
    ∃ y rest, select limit q = y :: rest := by
  obtain ⟨x, hx, hxe⟩ := he
  have hmem : x ∈ (q.filter eligible).filter (·.prio == .A)
      ++ (q.filter eligible).filter (·.prio == .B)
      ++ (q.filter eligible).filter (·.prio == .C) := by
    have hxf : x ∈ q.filter eligible := List.mem_filter.2 ⟨hx, hxe⟩
    cases hp : x.prio <;> simp [List.mem_filter, hx, hxe, hp]
  simp only [select]
  generalize (q.filter eligible).filter (·.prio == .A)
      ++ (q.filter eligible).filter (·.prio == .B)
      ++ (q.filter eligible).filter (·.prio == .C) = L at hmem
  cases L with
  | nil => simp at hmem
  | cons y ys =>
    obtain ⟨k, rfl⟩ : ∃ k, limit = k + 1 := ⟨limit - 1, by omega⟩
    exact ⟨y, ys.take k, rfl⟩

/-! ## 2. One run, and the drain theorem -/

def MAX : Nat := 3

/-- `note_failure(item, 'fetch')`: count, and at `MAX` park as WAITING. -/
def failFetch (it : Item) : Item :=
  if it.fa + 1 ≥ MAX then { it with fa := it.fa + 1, st := .waiting }
  else { it with fa := it.fa + 1 }

/-- Analysis failure. `countAna = false` is the OLD code (`failed.append(item);
continue` — nothing written); `countAna = true` is the fix
(`note_failure(item, 'analysis')`). -/
def failAna (countAna : Bool) (it : Item) : Item :=
  if countAna then
    if it.aa + 1 ≥ MAX then { it with aa := it.aa + 1, st := .waiting }
    else { it with aa := it.aa + 1 }
  else it

/-- The per-item body of `main()`'s loop. The locator only matches a TODO
heading, so a non-TODO item is untouched. -/
def stepItem (countAna : Bool) (fetchOk anaOk : Bool) (it : Item) : Item :=
  if it.st ≠ .todo then it
  else if !fetchOk then failFetch it
  else if !anaOk then failAna countAna it
  else { it with st := .done }

def upd (k : Nat) (g : Item → Item) (q : List Item) : List Item :=
  q.map (fun it => if it.id = k then g it else it)

/-- One nightly run: select once, then process each selected item. -/
def runOnce (countAna : Bool) (fetch ana : Nat → Bool) (limit : Nat) (q : List Item) :
    List Item :=
  (select limit q).foldl (fun acc s => upd s.id (stepItem countAna (fetch s.id) (ana s.id)) acc) q

/-- `n` consecutive runs; oracles are indexed by run number. -/
def runN (countAna : Bool) (F A : Nat → Nat → Bool) (limit : Nat) : Nat → List Item → List Item
  | 0, q => q
  | n + 1, q => runN countAna (fun i => F (i + 1)) (fun i => A (i + 1)) limit n
                  (runOnce countAna (F 0) (A 0) limit q)

/-- Remaining budget of an item: attempts it may still fail before parking. -/
def ipot (it : Item) : Nat :=
  if eligible it then (MAX - it.fa) + (MAX - it.aa) + 1 else 0

def pot (q : List Item) : Nat := (q.map ipot).sum

theorem ipot_pos {it : Item} (h : eligible it = true) : 0 < ipot it := by
  simp [ipot, h]

theorem step_le (c f a : Bool) (it : Item) : ipot (stepItem c f a it) ≤ ipot it := by
  unfold stepItem failFetch failAna ipot eligible MAX
  cases hs : it.st <;> cases hu : it.hasUrl <;> cases f <;> cases a <;> cases c <;>
    simp [hs, hu] <;> (try split) <;> simp <;> omega

theorem step_lt (f a : Bool) (it : Item) (h : eligible it = true) :
    ipot (stepItem true f a it) < ipot it := by
  have hs : it.st = .todo := by
    simp [eligible] at h; exact h.1
  have hu : it.hasUrl = true := by simp [eligible] at h; exact h.2
  unfold stepItem failFetch failAna ipot eligible MAX
  cases f <;> cases a <;> simp [hs, hu] <;> (try split) <;> simp <;> omega

theorem upd_le (k : Nat) (g : Item → Item) (hg : ∀ it, ipot (g it) ≤ ipot it) :
    ∀ q, pot (upd k g q) ≤ pot q
  | [] => by simp [upd, pot]
  | x :: xs => by
    have ih := upd_le k g hg xs
    simp only [upd, pot, List.map_cons, List.sum_cons] at ih ⊢
    split
    · have := hg x; omega
    · omega

theorem upd_lt (k : Nat) (g : Item → Item) (hg : ∀ it, ipot (g it) ≤ ipot it)
    (hs : ∀ it, eligible it = true → ipot (g it) < ipot it) :
    ∀ q, (∃ it ∈ q, it.id = k ∧ eligible it = true) → pot (upd k g q) < pot q
  | [], ⟨_, h, _⟩ => by simp at h
  | x :: xs, ⟨it, hmem, hid, he⟩ => by
    have hle := upd_le k g hg xs
    simp only [upd, pot, List.map_cons, List.sum_cons] at hle ⊢
    rcases List.mem_cons.1 hmem with rfl | hxs
    · simp only [hid, ↓reduceIte]; have := hs it he; omega
    · have ih := upd_lt k g hg hs xs ⟨it, hxs, hid, he⟩
      simp only [upd, pot] at ih
      split
      · have := hg x; omega
      · omega

theorem fold_le (c : Bool) (fetch ana : Nat → Bool) :
    ∀ (sel : List Item) (acc : List Item),
      pot (sel.foldl (fun acc s => upd s.id (stepItem c (fetch s.id) (ana s.id)) acc) acc)
        ≤ pot acc
  | [], _ => by simp
  | s :: rest, acc => by
    simp only [List.foldl_cons]
    have h1 := fold_le c fetch ana rest (upd s.id (stepItem c (fetch s.id) (ana s.id)) acc)
    have h2 := upd_le s.id _ (step_le c (fetch s.id) (ana s.id)) acc
    omega

theorem run_le (c : Bool) (fetch ana : Nat → Bool) (limit : Nat) (q : List Item) :
    pot (runOnce c fetch ana limit q) ≤ pot q :=
  fold_le c fetch ana _ q

/-- Every run with work to do strictly spends the queue's attempt budget. -/
theorem run_lt (fetch ana : Nat → Bool) {limit : Nat} (hl : 0 < limit) (q : List Item)
    (he : ∃ it ∈ q, eligible it = true) :
    pot (runOnce true fetch ana limit q) < pot q := by
  obtain ⟨y, rest, hsel⟩ := select_nonempty hl he
  have hy := select_sub (limit := limit) (q := q) (x := y) (by rw [hsel]; simp)
  unfold runOnce
  rw [hsel, List.foldl_cons]
  have h1 := fold_le true fetch ana rest (upd y.id (stepItem true (fetch y.id) (ana y.id)) q)
  have h2 := upd_lt y.id _ (step_le true (fetch y.id) (ana y.id))
    (step_lt (fetch y.id) (ana y.id)) q ⟨y, hy.1, rfl, hy.2⟩
  omega

theorem pot_pos_elig : ∀ q : List Item, 0 < pot q → ∃ it ∈ q, eligible it = true
  | [], h => by simp [pot] at h
  | x :: xs, h => by
    by_cases hx : eligible x = true
    · exact ⟨x, by simp, hx⟩
    · have : ipot x = 0 := by simp [ipot, hx]
      simp only [pot, List.map_cons, List.sum_cons, this, Nat.zero_add] at h
      obtain ⟨it, hm, he⟩ := pot_pos_elig xs h
      exact ⟨it, List.mem_cons_of_mem _ hm, he⟩

theorem elig_le_pot : ∀ (q : List Item) (it : Item), it ∈ q → ipot it ≤ pot q
  | [], _, h => by simp at h
  | x :: xs, it, h => by
    simp only [pot, List.map_cons, List.sum_cons]
    rcases List.mem_cons.1 h with rfl | hxs
    · omega
    · have := elig_le_pot xs it hxs; simp only [pot] at this; omega

/-- **Drain.** Claim: "The queue drains". With analysis failures counted, for
EVERY fetch/analysis oracle, after `pot q` runs no URL-bearing TODO remains:
each one is DONE or parked as WAITING. -/
theorem drain (F A : Nat → Nat → Bool) {limit : Nat} (hl : 0 < limit) :
    ∀ (n : Nat) (q : List Item), pot q ≤ n →
      ∀ it ∈ runN true F A limit n q, eligible it = false := by
  intro n
  induction n generalizing F A with
  | zero =>
    intro q hq it hit
    simp only [runN] at hit
    cases he : eligible it
    · rfl
    · have := ipot_pos he; have := elig_le_pot q it hit; omega
  | succ n ih =>
    intro q hq
    simp only [runN]
    apply ih
    by_cases h0 : pot q = 0
    · have := run_le true (F 0) (A 0) limit q; omega
    · have := run_lt (F 0) (A 0) hl q (pot_pos_elig q (by omega)); omega

/-- The bound is by attempts: at most `2*MAX+1` runs' worth per queued item. -/
theorem pot_le_len : ∀ q : List Item, pot q ≤ (2 * MAX + 1) * q.length
  | [] => by simp [pot]
  | x :: xs => by
    have := pot_le_len xs
    simp only [pot, List.map_cons, List.sum_cons, List.length_cons] at this ⊢
    have : ipot x ≤ 2 * MAX + 1 := by unfold ipot; split <;> simp [MAX] <;> omega
    rw [Nat.mul_succ]; omega

/-- **Refutation of the old code.** Analysis failure did not count, so one
[#A] item whose analysis always fails holds the only slot (limit 1) forever,
and the [#C] item behind it is never processed, however many runs happen. -/
def itA : Item := ⟨0, .A, true, .todo, 0, 0, none⟩
def itC : Item := ⟨1, .C, true, .todo, 0, 0, none⟩

theorem old_head_of_line_blocking :
    ∀ n, runN false (fun _ _ => true) (fun _ i => i != 0) 1 n [itA, itC] = [itA, itC] := by
  intro n
  induction n with
  | zero => rfl
  | succ n ih =>
    simp only [runN]
    have : runOnce false (fun _ => true) (fun i => i != 0) 1 [itA, itC] = [itA, itC] := by decide
    rw [this]; exact ih

theorem old_code_does_not_drain :
    ¬ ∀ (F A : Nat → Nat → Bool) (n : Nat) (q : List Item), pot q ≤ n →
        ∀ it ∈ runN false F A 1 n q, eligible it = false := by
  intro h
  have := h (fun _ _ => true) (fun _ i => i != 0) (pot [itA, itC]) [itA, itC] (Nat.le_refl _)
    itC (by rw [old_head_of_line_blocking]; simp)
  simp [eligible, itC] at this

/-! ### Auto-archive only removes. -/

/-- `auto_archive_stale_research`: open (TODO/WAITING) items with a known
date older than `maxAge` leave the file; undated items stay. -/
def stale (today maxAge : Nat) (it : Item) : Bool :=
  it.st != .done && (match it.created with | some d => decide (today - d > maxAge) | none => false)

def archive (today maxAge : Nat) (q : List Item) : List Item :=
  q.filter (fun it => !stale today maxAge it)

theorem archive_le (today maxAge : Nat) : ∀ q, pot (archive today maxAge q) ≤ pot q
  | [] => by simp [archive, pot]
  | x :: xs => by
    have ih := archive_le today maxAge xs
    simp only [archive, pot] at ih ⊢
    rw [List.filter_cons]
    split <;> simp only [List.map_cons, List.sum_cons] <;> omega

/-! ## 3. Locating an item in the flat org text -/

/-- A line of `research_learning.org`, as far as the locator sees it. -/
inductive Ln
  | head (text : String)      -- `^\*+\s…`
  | props                     -- `:PROPERTIES:`
  | endL                      -- `:END:`
  | idL (s : String)          -- `:ID: s`
  | other
  deriving DecidableEq, Repr

def Ln.isHead : Ln → Bool | .head _ => true | _ => false

def firstIdx {α} (p : α → Bool) : List α → Option Nat
  | [] => none
  | x :: xs => if p x then some 0 else (firstIdx p xs).map (· + 1)

theorem firstIdx_spec {α} (p : α → Bool) :
    ∀ (l : List α) (k : Nat), firstIdx p l = some k →
      ∃ h : k < l.length, p (l[k]'h) = true ∧ ∀ i (hi : i < k), p (l[i]'(by omega)) = false
  | [], k, h => by simp [firstIdx] at h
  | x :: xs, k, h => by
    unfold firstIdx at h
    by_cases hx : p x = true
    · simp [hx] at h; subst h; exact ⟨by simp, by simpa using hx, fun i hi => by omega⟩
    · simp [hx] at h
      obtain ⟨k', hk', rfl⟩ := h
      obtain ⟨hl, hp, hbefore⟩ := firstIdx_spec p xs k' hk'
      refine ⟨by simp; omega, by simpa using hp, fun i hi => ?_⟩
      cases i with
      | zero => simpa using hx
      | succ i => simpa using hbefore i (by omega)

/-- OLD `lines.index(heading)` / `line == new_heading`: the FIRST line whose
text equals the heading. -/
def locateText (ls : List Ln) (h : String) : Option Nat := firstIdx (· == .head h) ls

/-- Two captures with the same title: the old locator resolves the second to the first. -/
theorem old_duplicate_title_misdirects :
    let ls := [Ln.head "** TODO Read: same", .other, .head "** TODO Read: same", .other]
    locateText ls "** TODO Read: same" = some 0 ∧ ls[2]? = some (.head "** TODO Read: same") := by
  decide

/-- OLD `:END:` search in `mark_done`: first `:END:` in the 15 lines after the
heading, with no regard for the next heading. -/
def findEndOld (ls : List Ln) (start : Nat) : Option Nat :=
  ((ls.drop start).take 15 |> firstIdx (· == .endL)).map (· + start)

/-- An item with no drawer: its :OUTPUT:/:ZETTELS: go into the NEXT item's drawer. -/
theorem old_end_search_crosses_heading :
    let ls := [Ln.head "** DONE Read: nodrawer", .other, .head "** TODO Read: other",
               .props, .idL "other-id", .endL]
    findEndOld ls 1 = some 5 ∧ ((ls.drop 1).take 4).any Ln.isHead = true := by
  decide

/-- FIXED: scan the item's section only; stop at the next heading. Finds the
`:END:` of the item's own `:PROPERTIES:` drawer, or none. -/
def findEndSec (sec : List Ln) : Option Nat :=
  match firstIdx (fun l => l.isHead || l == .endL) sec with
  | some k => if sec[k]? = some .endL then some k else none
  | none => none

theorem findEndSec_in_section (sec : List Ln) (k : Nat) (h : findEndSec sec = some k) :
    ∃ hk : k < sec.length, sec[k] = .endL ∧ ∀ i (hi : i < k), (sec[i]'(by omega)).isHead = false := by
  unfold findEndSec at h
  split at h
  · rename_i k' hk'
    split at h
    · rename_i hend
      simp at h; subst h
      obtain ⟨hl, _, hb⟩ := firstIdx_spec _ sec k' hk'
      refine ⟨hl, by simpa [hl] using hend, fun i hi => ?_⟩
      have := hb i hi
      simp at this; exact this.1
    · simp at h
  · simp at h

/-- FIXED locator by `:ID:`: the item that owns the first `:ID: x` line is the
nearest heading ABOVE it. Modelled on the prefix up to the ID line, reversed,
so "nearest above" is `firstIdx isHead`; the theorem says no heading lies
strictly between the owner and the ID line. -/
def ownerOffset (above : List Ln) : Option Nat := firstIdx Ln.isHead above.reverse

theorem ownerOffset_nearest (above : List Ln) (k : Nat) (h : ownerOffset above = some k) :
    ∃ hk : k < above.reverse.length, (above.reverse[k]).isHead = true ∧
      ∀ i (hi : i < k), (above.reverse[i]'(by omega)).isHead = false :=
  firstIdx_spec _ _ k h

/-! ## 3b. Where the bookkeeping lives (owner decision D9, 2026-09-23)

`:FETCH_ATTEMPTS:`, `:ANALYSIS_ATTEMPTS:` and `:RESULT:` are now written in the
item's `:PROPERTIES:` drawer. Old items hold them outside it ("loose" lines).
An item's bookkeeping is modelled as the drawer's key/value pairs and the loose
ones, each in file order; keys and values are opaque (`Nat`).

* `read` is `get_item_prop`: the drawer wins, else the first loose line.
* `migrate` is `migrate_item`: a loose key the drawer lacks moves in (its first
  value, the one `read` returns); a loose key the drawer has is dropped.
-/

def lk (k : Nat) : List (Nat × Nat) → Option Nat
  | [] => none
  | (k', v) :: t => if k' = k then some v else lk k t

theorem lk_append (k : Nat) : ∀ (a b : List (Nat × Nat)), lk k (a ++ b) = (lk k a).or (lk k b)
  | [], b => by simp [lk]
  | (k', v) :: t, b => by
    simp only [List.cons_append, lk]
    split <;> simp [lk_append k t b]

def readProp (k : Nat) (drawer loose : List (Nat × Nat)) : Option Nat :=
  (lk k drawer).or (lk k loose)

def migStep (acc : List (Nat × Nat)) (p : Nat × Nat) : List (Nat × Nat) :=
  if (lk p.1 acc).isSome then acc else acc ++ [p]

/-- The drawer after migration; the loose list becomes empty. -/
def migrate (drawer loose : List (Nat × Nat)) : List (Nat × Nat) := loose.foldl migStep drawer

theorem migrate_lk (k : Nat) : ∀ (loose acc : List (Nat × Nat)),
    lk k (loose.foldl migStep acc) = (lk k acc).or (lk k loose)
  | [], acc => by simp [lk]
  | (k', v) :: t, acc => by
    simp only [List.foldl_cons]
    rw [migrate_lk k t (migStep acc (k', v))]
    unfold migStep
    by_cases hs : (lk k' acc).isSome = true
    · simp only [hs, ↓reduceIte, lk]
      by_cases hk : k' = k
      · subst hk
        obtain ⟨w, hw⟩ := Option.isSome_iff_exists.mp hs
        simp [hw]
      · simp [hk]
    · simp only [hs, Bool.false_eq_true, ↓reduceIte, lk_append, lk]
      by_cases hk : k' = k
      · subst hk
        have : lk k' acc = none := by simpa using hs
        simp [this]
      · simp [hk]

/-- **D9.** Migration changes no value any reader sees. -/
theorem mig_preserves_read (k : Nat) (drawer loose : List (Nat × Nat)) :
    readProp k (migrate drawer loose) [] = readProp k drawer loose := by
  simp [readProp, migrate, migrate_lk, lk]

/-- And it is idempotent: a migrated item has nothing loose, so a second run
moves nothing (`migrate d [] = d`). -/
theorem mig_idem (drawer : List (Nat × Nat)) : migrate drawer [] = drawer := rfl

/-- The pre-D9 reader looked only at the loose lines' text; the new reader
sees a drawer value too, which is what org-workspace `get_property` reads. -/
theorem drawer_value_is_read (k v : Nat) : readProp k [(k, v)] [] = some v := by
  simp [readProp, lk]

/-! ## 4. research_router.classify -/

def timeliness (hl : Nat) (age : Option Nat) : Nat :=
  match age with
  | none => 1
  | some a => if 2 * a ≤ hl then 3 else if a ≤ hl then 2 else if a ≤ 2 * hl then 1 else 0

/-- `ratio <= .5` is `2*age <= hl`, `ratio <= 1` is `age <= hl`, `ratio <= 2`
is `age <= 2*hl` (hl > 0 for every kind in HALF_LIFE_DAYS). Ages are ints;
negative ages would score 3, which the monotonicity below also covers since
they sit below 0. -/
theorem timeliness_le3 (hl : Nat) (a : Option Nat) : timeliness hl a ≤ 3 := by
  unfold timeliness; split
  · decide
  · repeat' split <;> try decide

theorem timeliness_antitone (hl a b : Nat) (hab : a ≤ b) :
    timeliness hl (some b) ≤ timeliness hl (some a) := by
  simp only [timeliness]
  repeat' split <;> (try omega)

def roadmap (hits : Nat) : Nat := if hits > 1 then 3 else if hits = 1 then 2 else 0
def strategy (ventures challenge : Bool) : Nat := if !ventures then 0 else if challenge then 3 else 2
def metaAx (n : Nat) : Nat := if n ≥ 2 then 3 else if n = 1 then 2 else 0

def score (t r s m : Nat) : Nat := t + s + 2 * r + 2 * m

theorem axes_le3 (hits n : Nat) (v c : Bool) :
    roadmap hits ≤ 3 ∧ strategy v c ≤ 3 ∧ metaAx n ≤ 3 := by
  refine ⟨?_, ?_, ?_⟩
  · unfold roadmap; repeat' split <;> try omega
  · unfold strategy; cases v <;> cases c <;> decide
  · unfold metaAx; repeat' split <;> try omega

/-- Claim: "score ≤ 18" (3 + 3 + 2·3 + 2·3). Discard returns score 0. -/
theorem score_le18 (hl : Nat) (age : Option Nat) (hits n : Nat) (v c : Bool) :
    score (timeliness hl age) (roadmap hits) (strategy v c) (metaAx n) ≤ 18 := by
  have := timeliness_le3 hl age
  have ⟨h1, h2, h3⟩ := axes_le3 hits n v c
  unfold score; omega

inductive Dest | issue | inbox | discard
  deriving DecidableEq, Repr

/-- `classify`'s destination, after the DISCARD check. -/
def dest (discard repo namesChange : Bool) (rm : Nat) : Dest :=
  if discard then .discard
  else if repo && namesChange && decide (rm > 0) then .issue
  else .inbox

/-- Claim: "Roadmap relevance is a necessary condition for an issue but never a
sufficient one -- … can never promote one on its own". An issue needs repo AND
change AND roadmap; and changing only the roadmap score never produces an issue
unless repo and change are already named. -/
theorem roadmap_necessary_not_sufficient (d r c : Bool) (rm rm' : Nat) :
    (dest d r c rm = .issue → r = true ∧ c = true ∧ rm > 0) ∧
    (dest d r c rm ≠ .issue → dest d r c rm' = .issue → r = true ∧ c = true) := by
  unfold dest
  constructor
  · intro h; cases d <;> cases r <;> cases c <;> simp at h ⊢ <;> omega
  · intro _ h; cases d <;> cases r <;> cases c <;> simp at h ⊢

end DatacoreSpec.Research
