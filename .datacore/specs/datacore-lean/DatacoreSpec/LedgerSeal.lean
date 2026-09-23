import LedgerSpec.Chain

/-!
# Seals, checkpoints, restores and the dismissal sweeps around the ledger

Models, branch for branch where it matters:

* `lib/ledger/seal.py` `latest_seal` / `verify_seal` / `Seal.includes`, the
  regression check added 2026-09-23 (`regressions`, `seal_regressions`), and
  the sequencer-only reader of decision L1 (`_seal_events`, `bySeq`);
* `lib/ledger_ingest_org.py` `_dismiss_archived`;
* `lib/ledger_restore_prefix.py` `restore` (read, prefix check, write);
* `lib/ledger_dismiss_orphans.py` `confirm_and_dismiss` (two-sweep rule);
* `lib/ledger_checkpoint.py` `write` (append-only replacement);
* `lib/ledger_invariants.py` `_accepted` (baseline allowlist) and the verdict
  (`verdict`, decision L2: could-not-tell is UNKNOWN, exit 2).

Abstractions: HLC strings are `Nat` (only their order matters, `Hlc.lean`
proves the string order is the tuple order); log text is `List Char` / event
lists (`LedgerSpec.Chain.ser_prefix_iff` proves text prefix = event prefix);
file contents are the id lists a regex would find.
-/

namespace DatacoreSpec.LedgerSeal

/-! ## 1. Seal finality -/

/-- Seal frontier: `log -> highest seq included` (a Python dict; keys unique). -/
abbrev WM := List (String × Nat)

structure Ev where
  log : String
  seq : Nat
  deriving DecidableEq

theorem lookup_mem {β : Type} : ∀ (l : List (String × β)) (k : String) (v : β),
    l.lookup k = some v → (k, v) ∈ l
  | [], _, _, h => by simp [List.lookup] at h
  | (k', b) :: es, k, v, h => by
    by_cases hk : k = k'
    · subst hk; simp [List.lookup] at h; subst h; simp
    · have : (k == k') = false := by simp [hk]
      simp only [List.lookup, this] at h
      exact List.mem_cons_of_mem _ (lookup_mem es k v h)

/-- `Seal.includes` (version 2): the log has a watermark and `seq ≤` it. -/
def includes (wm : WM) (e : Ev) : Bool :=
  match wm.lookup e.log with
  | some w => decide (e.seq ≤ w)
  | none => false

/-- `regressions(earlier, later) == []`: `later.get(k, -1) >= v` for every `k: v`. -/
def dominates (earlier later : WM) : Bool :=
  earlier.all fun kv => match later.lookup kv.1 with
    | some w => decide (kv.2 ≤ w)
    | none => false

theorem dominates_includes {earlier later : WM} (h : dominates earlier later = true)
    {e : Ev} (he : includes earlier e = true) : includes later e = true := by
  unfold includes at *
  cases hl : earlier.lookup e.log with
  | none => simp [hl] at he
  | some w =>
    simp [hl] at he
    have hmem : (e.log, w) ∈ earlier := lookup_mem _ _ _ hl
    have := List.all_eq_true.1 h _ hmem
    simp only at this
    cases hl' : later.lookup e.log with
    | none => simp [hl'] at this
    | some w' => simp [hl'] at this ⊢; omega

structure SealEv where
  hlc   : Nat
  actor : String
  wm    : WM
  deriving DecidableEq

/-- `latest_seal`: `max(seals, key=hlc)`. -/
def IsLatest (seals : List SealEv) (l : SealEv) : Prop :=
  l ∈ seals ∧ ∀ s ∈ seals, s.hlc ≤ l.hlc

/-- The regression gate of `verify_seal`. `checked = false` is the code before
2026-09-23 (no gate); `true` is the fix. -/
def regressionFree (checked : Bool) (seals : List SealEv) (l : SealEv) : Bool :=
  !checked || seals.all fun s => !(decide (s.hlc < l.hlc)) || dominates s.wm l.wm

/-- **Finality only moves forward (fixed code).** If a reader's event set grows
from `E` to `E'`, the seal `l'` that verifies on `E'` settles every event the
seal `l` that was latest on `E` settled. Precondition: two distinct seal events
never share an HLC (the HLC string ends in the actor, and one actor's stamps are
strictly increasing — `Hlc.tick_strict`). -/
theorem seal_settled_monotone {E E' : List SealEv} {l l' : SealEv}
    (hsub : ∀ s ∈ E, s ∈ E') (hl : IsLatest E l) (hl' : IsLatest E' l')
    (huniq : ∀ s ∈ E', ∀ t ∈ E', s.hlc = t.hlc → s = t)
    (hok : regressionFree true E' l' = true) :
    ∀ e, includes l.wm e = true → includes l'.wm e = true := by
  intro e he
  have hin : l ∈ E' := hsub _ hl.1
  have hle : l.hlc ≤ l'.hlc := hl'.2 _ hin
  rcases Nat.lt_or_eq_of_le hle with hlt | heq
  · have hall : ∀ s ∈ E', l'.hlc ≤ s.hlc ∨ dominates s.wm l'.wm = true := by
      simpa [regressionFree] using hok
    rcases hall l hin with h | h
    · omega
    · exact dominates_includes h he
  · rw [huniq _ hin _ hl'.1 heq] at he; exact he

/-- **Counterexample (code before the fix).** Seal 1 settles `mac` 0..2; a later
seal by another writer names `mac: 0`; it was latest, it verified, and `mac`
seq 2 left settled state. Replayed: `test_a_later_seal_cannot_unsettle_...`. -/
def s1 : SealEv := ⟨1, "winston", [("mac", 2)]⟩
def s2 : SealEv := ⟨2, "mallory", [("mac", 0)]⟩

theorem seal_regression_cex :
    IsLatest [s1] s1 ∧ IsLatest [s1, s2] s2 ∧ regressionFree false [s1, s2] s2 = true ∧
    includes s1.wm ⟨"mac", 2⟩ = true ∧ includes s2.wm ⟨"mac", 2⟩ = false := by
  refine ⟨⟨by simp, by simp [s1]⟩, ⟨by simp, by simp [s1, s2]⟩, rfl, by decide, by decide⟩

/-- The fixed gate rejects exactly that seal. -/
theorem seal_regression_rejected : regressionFree true [s1, s2] s2 = false := by decide

/-- **Code before decision L1: "the latest seal is by the sequencer" did not hold.**
`IsLatest` over ALL seals is the reader before 2026-09-23 L1: a seal by any
writer that dominates its predecessor was latest and passed the gate, and
`verify_seal` never compared `sequencer` with the designated role. -/
def s3 : SealEv := ⟨3, "mallory", [("mac", 2)]⟩

theorem latest_seal_not_by_sequencer :
    IsLatest [s1, s3] s3 ∧ regressionFree true [s1, s3] s3 = true ∧ s3.actor ≠ "winston" := by
  refine ⟨⟨by simp, by simp [s1, s3]⟩, by decide, by decide⟩

/-! ### Decision L1 (2026-09-23): readers accept only the sequencer's seals

`seal._seal_events`: `[e for e in events if e.type == "ledger.seal" and
e.actor == sequencer()]`. `latest_seal`, the regression gate
(`seal_regressions`) and therefore `verify_seal` / `settled_events` all read
this filtered list; every other writer's seal is only reported. -/

/-- `_seal_events`. -/
def bySeq (sq : String) (seals : List SealEv) : List SealEv :=
  seals.filter fun s => s.actor = sq

/-- `latest_seal` after L1. -/
def IsLatestSeq (sq : String) (seals : List SealEv) (l : SealEv) : Prop :=
  IsLatest (bySeq sq seals) l

/-- **The latest seal is by the sequencer** — the claim the old code refuted. -/
theorem latest_is_sequencer {sq : String} {seals : List SealEv} {l : SealEv}
    (h : IsLatestSeq sq seals l) : l.actor = sq := by
  have := h.1
  simp only [bySeq, List.mem_filter, decide_eq_true_eq] at this
  exact this.2

/-- **A foreign seal is inert**: inserting a seal by any other writer anywhere
in the event list changes neither the seals readers see nor, hence, the latest
seal, the regression gate or settlement. -/
theorem foreign_seal_inert (sq : String) (pre post : List SealEv) (f : SealEv)
    (hf : f.actor ≠ sq) : bySeq sq (pre ++ f :: post) = bySeq sq (pre ++ post) := by
  simp [bySeq, List.filter_append, hf]

theorem foreign_latest_unchanged (sq : String) (pre post : List SealEv) (f l : SealEv)
    (hf : f.actor ≠ sq) :
    IsLatestSeq sq (pre ++ f :: post) l ↔ IsLatestSeq sq (pre ++ post) l := by
  unfold IsLatestSeq; rw [foreign_seal_inert sq pre post f hf]

/-- **Finality only moves forward, under L1.** The monotonicity theorem, for the
reader that sees only the sequencer's seals. The uniqueness precondition is now
about ONE actor's seals, which `Hlc.tick_strict` gives directly. -/
theorem seq_settled_monotone {sq : String} {E E' : List SealEv} {l l' : SealEv}
    (hsub : ∀ s ∈ E, s ∈ E') (hl : IsLatestSeq sq E l) (hl' : IsLatestSeq sq E' l')
    (huniq : ∀ s ∈ bySeq sq E', ∀ t ∈ bySeq sq E', s.hlc = t.hlc → s = t)
    (hok : regressionFree true (bySeq sq E') l' = true) :
    ∀ e, includes l.wm e = true → includes l'.wm e = true := by
  have hsub' : ∀ s ∈ bySeq sq E, s ∈ bySeq sq E' := by
    intro s hs
    simp only [bySeq, List.mem_filter] at hs ⊢
    exact ⟨hsub _ hs.1, hs.2⟩
  exact seal_settled_monotone hsub' hl hl' huniq hok

/-- The two old counterexamples, replayed under L1: `mallory`'s regressing seal
(`s2`) and its dominating seal (`s3`) are both ignored; winston's `s1` stays
latest. Replayed: `test_decisions_ledger_a.py::test_a_seal_by_another_writer_*`. -/
theorem old_cex_ignored :
    IsLatestSeq "winston" [s1, s2] s1 ∧ IsLatestSeq "winston" [s1, s3] s1 ∧
      ¬ IsLatestSeq "winston" [s1, s3] s3 := by
  refine ⟨⟨by simp [bySeq, s1], by simp [bySeq, s1, s2]⟩,
          ⟨by simp [bySeq, s1], by simp [bySeq, s1, s3]⟩, ?_⟩
  intro h
  exact absurd (latest_is_sequencer h) (by decide)

/-! ## 2. `ledger_ingest_org._dismiss_archived` -/

structure OrgFile where
  archive  : Bool    -- `"archive" in name`
  nested   : Bool    -- below `org/`, not directly in it
  readable : Bool
  ids      : List String

/-- Before the fix: `org/*.org` only; an unreadable file is `continue`d. -/
def dismissOld (fs : List OrgFile) : List String :=
  let top := fs.filter fun f => !f.nested && f.readable
  let live := (top.filter fun f => !f.archive).flatMap OrgFile.ids
  let arch := (top.filter fun f => f.archive).flatMap OrgFile.ids
  arch.filter fun i => i ∉ live

/-- After the fix: liveness from every non-archive file at any depth; any
unreadable live file declines the whole pass; evidence stays top-level. -/
def dismissNew (fs : List OrgFile) : List String :=
  if fs.any (fun f => !f.archive && !f.readable) then [] else
  let live := (fs.filter fun f => !f.archive && f.readable).flatMap OrgFile.ids
  let arch := (fs.filter fun f => f.archive && !f.nested && f.readable).flatMap OrgFile.ids
  arch.filter fun i => i ∉ live

/-- **Nothing still authored in ANY live org file is dismissed** — whether or
not the scan could read that file, and at any depth. -/
theorem ingest_never_dismisses_live (fs : List OrgFile) (i : String)
    (hi : i ∈ dismissNew fs) : ∀ f ∈ fs, f.archive = false → i ∉ f.ids := by
  intro f hf harch hid
  unfold dismissNew at hi
  split at hi
  · simp at hi
  · rename_i hany
    have hr : f.readable = true := by
      cases hfr : f.readable
      · exact absurd (List.any_eq_true.2 ⟨f, hf, by simp [harch, hfr]⟩) hany
      · rfl
    simp only [List.mem_filter, decide_eq_true_eq] at hi
    apply hi.2
    exact List.mem_flatMap.2 ⟨f, List.mem_filter.2 ⟨hf, by simp [harch, hr]⟩, hid⟩

/-- Dismissal still needs positive evidence: the id is in a readable archive. -/
theorem ingest_needs_evidence (fs : List OrgFile) (i : String) (hi : i ∈ dismissNew fs) :
    ∃ f ∈ fs, f.archive = true ∧ f.readable = true ∧ i ∈ f.ids := by
  unfold dismissNew at hi
  split at hi
  · simp at hi
  · simp only [List.mem_filter, List.mem_flatMap, Bool.and_eq_true, Bool.not_eq_true',
      decide_eq_true_eq] at hi
    obtain ⟨⟨f, ⟨hf, ⟨ha, _⟩, hr⟩, hid⟩, _⟩ := hi
    exact ⟨f, hf, ha, hr, hid⟩

/-- **Counterexample (before the fix):** the live file holding `X` is
unreadable, the archive also holds `X`, and `X` is dismissed (terminally). -/
theorem ingest_unreadable_cex :
    "X" ∈ dismissOld [⟨false, false, false, ["X"]⟩, ⟨true, false, true, ["X"]⟩] := by
  simp [dismissOld]

/-- ... and so is an id still authored in a nested live file (`org/inboxes/`). -/
theorem ingest_nested_cex :
    "X" ∈ dismissOld [⟨false, true, true, ["X"]⟩, ⟨true, false, true, ["X"]⟩] := by
  simp [dismissOld]

/-! ## 3. `ledger_restore_prefix.restore` versus a concurrent append

The log is a list of events. `restore` reads `cur`, checks `cur <+: rec`, and
writes `rec`; an append adds `a` at the end. Without the lock the append can
land between the read and the write (`between`); with the append flock held
across read, check and write only `before` and `after` remain. -/

inductive Sched | before | between | after
  deriving DecidableEq

variable {α : Type} [DecidableEq α]

def restoreStep (cur rec : List α) : List α :=
  if cur <+: rec then rec else cur

def run (s : Sched) (file rec : List α) (a : α) : List α :=
  match s with
  | .before  => restoreStep (file ++ [a]) rec
  | .between => if file <+: rec then rec else file ++ [a]   -- checked OLD bytes
  | .after   => restoreStep file rec ++ [a]

/-- **Locked restore loses nothing**: every event on disk before, and the
concurrent append, are in the final log, for both schedules the lock allows. -/
theorem restore_locked_no_loss (file rec : List α) (a : α) (s : Sched) (hs : s ≠ .between) :
    (∀ x ∈ file, x ∈ run s file rec a) ∧ a ∈ run s file rec a := by
  cases s with
  | between => exact absurd rfl hs
  | before =>
    simp only [run, restoreStep]
    split
    · rename_i h
      obtain ⟨t, ht⟩ := h
      subst ht; exact ⟨fun x hx => by simp [hx], by simp⟩
    · exact ⟨fun x hx => by simp [hx], by simp⟩
  | after =>
    simp only [run, restoreStep]
    split
    · rename_i h
      obtain ⟨t, ht⟩ := h
      subst ht; exact ⟨fun x hx => by simp [hx], by simp⟩
    · exact ⟨fun x hx => by simp [hx], by simp⟩

/-- **Counterexample (unlocked):** the append lands between read and write and
is gone, and `rec` reuses its position (seq) for a different event. -/
theorem restore_race_loses_append :
    run .between [0, 1] [0, 1, 2, 3] (9 : Nat) = [0, 1, 2, 3] ∧ 9 ∉ run .between [0, 1] [0, 1, 2, 3] (9 : Nat) := by
  decide

/-! ## 4. `ledger_dismiss_orphans.confirm_and_dismiss`: the two-sweep rule -/

structure Scan where
  now        : Int
  unreadable : Bool
  found      : List String   -- live ids absent from every org file
  live       : Nat

/-- The watch file: `(at, orphans)` of the last recorded sweep. After the fix a
non-finite `at` reads as 0, so an `Int` models it; `none` is "no file". -/
abbrev Watch := Option (Int × List String)

/-- Confirmed = in this scan AND in the watch, if the watch is far enough back. -/
def conf0 (gap : Int) (w : Watch) (s : Scan) : List String :=
  match w with
  | some (at_, ids) => if at_ ≠ 0 ∧ s.now - at_ ≥ gap then s.found.filter (· ∈ ids) else []
  | none => []

/-- The ceiling: `if confirmed and live and len(confirmed)/live > max_fraction`
(`max_fraction = num/den`). -/
def ceil (num den : Nat) (s : Scan) (c : List String) : List String :=
  if c ≠ [] ∧ s.live ≠ 0 ∧ c.length * den > num * s.live then [] else c

/-- One executed sweep: `(new watch, dismissed ids)`. -/
def sweep (gap : Int) (num den : Nat) (w : Watch) (s : Scan) : Watch × List String :=
  if s.unreadable then (w, []) else (some (s.now, s.found), ceil num den s (conf0 gap w s))

/-- The watch always records a real, readable earlier sweep (or nothing). -/
def Witnessed (hist : List Scan) : Watch → Prop
  | none => True
  | some (at_, ids) => ∃ s ∈ hist, s.unreadable = false ∧ at_ = s.now ∧ ids = s.found

theorem ceil_sub {num den : Nat} {s : Scan} {c : List String} {i : String}
    (h : i ∈ ceil num den s c) : i ∈ c := by
  unfold ceil at h; split at h
  · simp at h
  · exact h

theorem ceil_bound (num den : Nat) (s : Scan) (c : List String) (hc : c.length ≤ s.live) :
    (ceil num den s c).length * den ≤ num * s.live := by
  unfold ceil; split
  · simp
  · rename_i hn
    by_cases h0 : c = []
    · simp [h0]
    · by_cases hl : s.live = 0
      · have : c.length = 0 := by omega
        exact absurd (List.length_eq_zero_iff.1 this) h0
      · have := not_and.1 (not_and.1 hn h0) hl
        omega

theorem conf0_spec {gap : Int} {hist : List Scan} {w : Watch} {s : Scan} {i : String}
    (hw : Witnessed hist w) (hi : i ∈ conf0 gap w s) :
    i ∈ s.found ∧ ∃ s0 ∈ hist, s0.unreadable = false ∧ i ∈ s0.found ∧ s.now - s0.now ≥ gap := by
  cases w with
  | none => simp [conf0] at hi
  | some p =>
    obtain ⟨at_, ids⟩ := p
    obtain ⟨s0, hs0, hr0, hat, hids⟩ := hw
    simp only [conf0] at hi
    split at hi
    · rename_i hg
      rw [List.mem_filter, decide_eq_true_eq] at hi
      exact ⟨hi.1, s0, hs0, hr0, hids ▸ hi.2, hat ▸ hg.2⟩
    · simp at hi

theorem conf0_length (gap : Int) (w : Watch) (s : Scan) :
    (conf0 gap w s).length ≤ s.found.length := by
  unfold conf0
  split
  · split
    · exact List.length_filter_le _ _
    · simp
  · simp

theorem sweep_sound (gap : Int) (num den : Nat) (hist : List Scan) (w : Watch) (s : Scan)
    (hw : Witnessed hist w) (hwf : s.found.length ≤ s.live) :
    Witnessed (hist ++ [s]) (sweep gap num den w s).1 ∧
    ∀ i ∈ (sweep gap num den w s).2,
      s.unreadable = false ∧ i ∈ s.found ∧
      (∃ s0 ∈ hist, s0.unreadable = false ∧ i ∈ s0.found ∧ s.now - s0.now ≥ gap) ∧
      (sweep gap num den w s).2.length * den ≤ num * s.live := by
  unfold sweep
  by_cases hu : s.unreadable = true
  · simp only [hu, ite_true]
    refine ⟨?_, by simp⟩
    cases w with
    | none => trivial
    | some p =>
      obtain ⟨s0, hs0, h⟩ := hw
      exact ⟨s0, List.mem_append_left _ hs0, h⟩
  · have hu' : s.unreadable = false := by simpa using hu
    simp only [hu', Bool.false_eq_true, ite_false]
    refine ⟨⟨s, by simp, hu', rfl, rfl⟩, fun i hi => ?_⟩
    obtain ⟨hf, hs0⟩ := conf0_spec hw (ceil_sub hi)
    exact ⟨trivial, hf, hs0, ceil_bound num den s _ (Nat.le_trans (conf0_length gap w s) hwf)⟩

/-- **Two observations, far enough apart, under the ceiling** — for every
history of sweeps starting from any witnessed watch. -/
def runSweeps (gap : Int) (num den : Nat) : List Scan → List Scan → Watch →
    List (List Scan × Scan × List String)
  | _, [], _ => []
  | hist, s :: ss, w =>
    let r := sweep gap num den w s
    (hist, s, r.2) :: runSweeps gap num den (hist ++ [s]) ss r.1

theorem orphans_two_sweeps (gap : Int) (num den : Nat) :
    ∀ (ss hist : List Scan) (w : Watch), Witnessed hist w →
    (∀ s ∈ ss, s.found.length ≤ s.live) →
    ∀ x ∈ runSweeps gap num den hist ss w, ∀ i ∈ x.2.2,
      x.2.1.unreadable = false ∧ i ∈ x.2.1.found ∧
      (∃ s0 ∈ x.1, s0.unreadable = false ∧ i ∈ s0.found ∧ x.2.1.now - s0.now ≥ gap) ∧
      x.2.2.length * den ≤ num * x.2.1.live
  | [], _, _, _, _ => by simp [runSweeps]
  | s :: ss, hist, w, hw, hwf => by
    intro x hx i hi
    have hs := sweep_sound gap num den hist w s hw (hwf s (by simp))
    simp only [runSweeps, List.mem_cons] at hx
    rcases hx with rfl | hx
    · exact hs.2 i hi
    · exact orphans_two_sweeps gap num den ss (hist ++ [s]) _ hs.1
        (fun t ht => hwf t (by simp [ht])) x hx i hi

/-! ## 5. `ledger_checkpoint.write`: the restore point only grows -/

/-- A saved snapshot: chain file name -> chain text. -/
abbrev Chains := List (String × List Char)

/-- `for f, old in previous.chains: chains.get(f, '').startswith(old)`. -/
def replaceOk (prev new : Chains) : Bool :=
  prev.all fun p => decide (p.2 <+: (new.lookup p.1).getD [])

/-- Every chain of the previous snapshot is a prefix of the new one's
(non-empty saved chains: `_restore` requires `text.endswith('\n')`). -/
theorem checkpoint_monotone {prev new : Chains} (h : replaceOk prev new = true)
    {f : String} {old : List Char} (hf : prev.lookup f = some old) (hne : old ≠ []) :
    ∃ t, new.lookup f = some t ∧ old <+: t := by
  have := List.all_eq_true.1 h _ (lookup_mem _ _ _ hf)
  simp only [decide_eq_true_eq] at this
  cases hl : new.lookup f with
  | none => simp [hl] at this; exact absurd this hne
  | some t => simp [hl] at this; exact ⟨t, rfl, this⟩

/-- Successive checkpoints, each accepted over the one before. -/
def Succ : List Chains → Prop
  | a :: b :: rest => replaceOk a b = true ∧ Succ (b :: rest)
  | _ => True

/-- Over any succession of accepted checkpoints, the first one's chains survive
as prefixes in the last — and, by `LedgerSpec.Chain.ser_prefix_iff`, as EVENT
prefixes: a checkpoint never forgets an event it once saved. -/
theorem checkpoint_history_grows : ∀ (cs : List Chains) (c0 : Chains), Succ (c0 :: cs) →
    ∀ f old, c0.lookup f = some old → old ≠ [] →
    ∃ t, ((c0 :: cs).getLast (by simp)).lookup f = some t ∧ old <+: t
  | [], c0, _, f, old, hf, _ => ⟨old, by simpa using hf, List.prefix_refl _⟩
  | c1 :: cs, c0, hch, f, old, hf, hne => by
    obtain ⟨h01, hrest⟩ := hch
    obtain ⟨t1, ht1, hp1⟩ := checkpoint_monotone h01 hf hne
    have hne1 : t1 ≠ [] := by
      intro h; subst h; exact hne (List.prefix_nil.1 hp1)
    obtain ⟨t, ht, hp⟩ := checkpoint_history_grows cs c1 hrest f t1 ht1 hne1
    exact ⟨t, by simpa [List.getLast_cons] using ht, hp1.trans hp⟩

/-! ## 6. `ledger_invariants`: baseline allowlist and verdict -/

/-- Characters abstracted; `word c` is `c.isalnum() or c == "_"`. -/
def acceptedOld (d p : List Nat) : Bool := decide (p <+: d)

def acceptedNew (word : Nat → Bool) (d p : List Nat) : Bool :=
  !p.isEmpty && decide (p <+: d) &&
    (match p.getLast?, d.drop p.length with
     | _, [] => true
     | some lc, c :: _ => !(word lc && word c)
     | none, _ => true)

/-- **Before:** an entry with no `detail_startswith` ("") accepted every detail. -/
theorem baseline_empty_mutes_all (d : List Nat) : acceptedOld d [] = true := by
  simp [acceptedOld]

/-- Toy alphabet: 0 = ' ', 1..9 letters/digits. "seq 5" vs "seq 50". -/
def word (c : Nat) : Bool := c ≠ 0

theorem baseline_seq5_mutes_seq50_old : acceptedOld [7, 0, 5, 10] [7, 0, 5] = true := by decide

theorem baseline_seq5_spares_seq50_new :
    acceptedNew word [7, 0, 5, 10] [7, 0, 5] = false ∧
    acceptedNew word [7, 0, 5, 0, 3] [7, 0, 5] = true := by decide

/-- **After:** an accepted entry is a non-empty prefix ending on a token boundary. -/
theorem baseline_accept_sound (word : Nat → Bool) (d p : List Nat)
    (h : acceptedNew word d p = true) : p ≠ [] ∧ p <+: d := by
  simp only [acceptedNew, Bool.and_eq_true, Bool.not_eq_true', decide_eq_true_eq,
    List.isEmpty_eq_false_iff] at h
  exact ⟨h.1.1, h.1.2⟩

/-- `ledger_invariants.verdict`. Before decision L2 the verdict was `SOUND` iff
no non-unknown, non-accepted finding, so a run whose only findings were
could-not-tell printed SOUND and exited 0 (`exitCodeOld`). Since L2
(2026-09-23): BROKEN/1 if any new finding, else UNKNOWN/2 if any could-not-tell,
else SOUND/0. Unknown findings are never matched against the baseline, so an
unknown finding is not sound whatever `accepted` says. -/
structure Finding where
  unknown  : Bool
  accepted : Bool

def exitCodeOld (fs : List Finding) : Nat :=
  if (fs.filter fun f => !f.unknown && !f.accepted).isEmpty then 0 else 1

theorem unknown_only_exits_sound : exitCodeOld [⟨true, false⟩] = 0 := by decide

def exitCode (fs : List Finding) : Nat :=
  if fs.any (fun f => !f.unknown && !f.accepted) then 1
  else if fs.any (fun f => f.unknown) then 2 else 0

theorem unknown_only_exits_unknown : exitCode [⟨true, false⟩] = 2 := by decide

/-- **SOUND means every finding was known and accepted** — "a space it cannot
read is reported as unknown, never as sound". -/
theorem exit_zero_iff_all_sound (fs : List Finding) :
    exitCode fs = 0 ↔ ∀ f ∈ fs, f.unknown = false ∧ f.accepted = true := by
  unfold exitCode
  constructor
  · intro h f hf
    by_cases hb : fs.any (fun f => !f.unknown && !f.accepted) = true
    · simp [hb] at h
    · by_cases hu : fs.any (fun f => f.unknown) = true
      · simp [hb, hu] at h
      · simp only [Bool.not_eq_true, List.any_eq_false] at hb hu
        have h1 := hb f hf
        have h2 := hu f hf
        cases hu' : f.unknown <;> cases ha : f.accepted <;> simp_all
  · intro h
    have hb : fs.any (fun f => !f.unknown && !f.accepted) = false := by
      rw [List.any_eq_false]; intro f hf; simp [(h f hf).1, (h f hf).2]
    have hu : fs.any (fun f => f.unknown) = false := by
      rw [List.any_eq_false]; intro f hf; simp [(h f hf).1]
    simp [hb, hu]

end DatacoreSpec.LedgerSeal
