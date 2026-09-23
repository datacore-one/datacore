/-!
# OrgTransaction — the journaled multi-file transaction in `lib/org_transaction.py`

The filesystem is a map `path → content hash` (`none` = file absent). A
`Transaction` keeps, per touched path, an `Entry` (`before`, `versions`,
`current`); `persist` writes the journal = every entry with more than one
version (the "owned" ones), keyed only by `before` and `versions` (the fields
`recover` reads). `recover` checks EVERY owned path's current hash is in its
version list, then restores `before` on all of them and deletes the journal;
otherwise it changes nothing and retains the journal.

Micro-steps, one per durable effect in the Python:

* `watch`  — `Transaction.watch` (first touch records the current hash);
* `prep`   — append the target version, then `persist()` (lines 157-160,
             183-185, 211-212);
* `apply`  — the atomic file effect (`atomic_write_text`, `unlink`, the
             rename). Over-approximated: ANY recorded version may be applied
             at any time, and a move's two effects are applied separately.

A crash may stop the run between ANY two micro-steps; `Reach` is exactly the
set of crash points. Theorems about `Reach` hold for every interleaving of
writes, moves and deletes.

What is left out: the directory fsyncs (they order durability, which `apply`
already treats as atomic), symlink refusals, and journal validation
(`version != 1` etc.). The TOCTOU window between `recover`'s check loop and
its restore loop is outside any cooperative model: an uncooperative writer
that edits a file inside that window is overwritten. `file_lock` only
serializes cooperative writers. How long `serialized` waits for the lock
(`timeout=`, decision Q12) is also left out: a timeout raises before
`recover` or any micro-step runs, so it adds no reachable state. The lock's
exclusion itself is modelled in `Dates.HookLock`.
-/

namespace DatacoreSpec.OrgTransaction

abbrev Path := Nat
/-- `none` = absent; `some h` = a file whose content has hash `h`. -/
abbrev Content := Option Nat
abbrev FS := Path → Content

structure Entry where
  before   : Content
  versions : List Content
  current  : Content

/-- `persist` keeps an entry iff `len(versions) > 1`. -/
def Entry.owned (e : Entry) : Bool := decide (1 < e.versions.length)

abbrev JEntry := Content × List Content
abbrev Journal := Path → Option JEntry

def Entry.key (e : Entry) : JEntry := (e.before, e.versions)

def persistOf (mem : Path → Option Entry) : Journal :=
  fun p => ((mem p).filter Entry.owned).map Entry.key

structure St where
  fs  : FS
  mem : Path → Option Entry
  jr  : Option Journal

def upd {α : Type} (f : Path → α) (p : Path) (v : α) : Path → α :=
  fun q => if q = p then v else f q

@[simp] theorem upd_same {α : Type} (f : Path → α) (p : Path) (v : α) : upd f p v p = v := by
  simp [upd]

@[simp] theorem upd_other {α : Type} (f : Path → α) (p q : Path) (v : α) (h : q ≠ p) :
    upd f p v q = f q := by
  simp [upd, h]

/-! ## `recover` -/

/-- The check loop (lines 103-118): every owned file's hash is a recorded version. -/
def JOk (j : Journal) (fs : FS) : Prop :=
  ∀ p b vs, j p = some (b, vs) → 1 < vs.length → fs p ∈ vs

/-- The restore loop (lines 119-127). -/
def restore (j : Journal) (fs : FS) : FS := fun p =>
  match j p with
  | some (b, vs) => if 1 < vs.length then b else fs p
  | none => fs p

inductive Recover : FS → Option Journal → FS → Option Journal → Prop
  | clean (fs : FS) : Recover fs none fs none
  | restored (fs : FS) (j : Journal) : JOk j fs → Recover fs (some j) (restore j fs) none
  | retained (fs : FS) (j : Journal) : ¬ JOk j fs → Recover fs (some j) fs (some j)

/-- **Safety of `recover`, for ANY filesystem (external writers allowed).**
Either nothing changed and the journal is retained, or the journal is gone and
every path either is unchanged or was an owned path that held one of the
transaction's own hashes and now holds exactly its `before`. So `recover`
never writes over a foreign hash, and it is all-or-nothing. -/
theorem recover_dichotomy {fs fs' : FS} {jr jr' : Option Journal}
    (h : Recover fs jr fs' jr') :
    (fs' = fs ∧ jr' = jr) ∨
    (jr' = none ∧ ∀ p, fs' p = fs p ∨
      ∃ j b vs, jr = some j ∧ j p = some (b, vs) ∧ 1 < vs.length ∧ fs p ∈ vs ∧ fs' p = b) := by
  cases h with
  | clean => exact Or.inl ⟨rfl, rfl⟩
  | retained => exact Or.inl ⟨rfl, rfl⟩
  | restored j hok =>
    refine Or.inr ⟨rfl, fun p => ?_⟩
    unfold restore
    cases hjp : j p with
    | none => exact Or.inl rfl
    | some be =>
      obtain ⟨b, vs⟩ := be
      simp only
      by_cases hl : 1 < vs.length
      · exact Or.inr ⟨j, b, vs, rfl, hjp, hl, hok p b vs hjp hl, by simp [hl]⟩
      · exact Or.inl (by simp [hl])

/-- A retained journal is retained again for as long as the offending file stays
foreign: `recover` is a function of the filesystem, so every later
`@serialized` call (which starts with `recover`) raises `RecoveryRequired`. -/
theorem retained_is_stuck {fs fs' : FS} {j : Journal} {jr' : Option Journal}
    (hbad : ¬ JOk j fs) (h : Recover fs (some j) fs' jr') : jr' = some j ∧ fs' = fs := by
  cases h with
  | restored _ hok => exact absurd hok hbad
  | retained => exact ⟨rfl, rfl⟩

/-! ## The transaction as micro-steps -/

def watchEntry (s : St) (p : Path) : Entry := ⟨s.fs p, [s.fs p], s.fs p⟩

def appendV (e : Entry) (c : Content) : Entry := { e with versions := e.versions ++ [c] }

inductive Step : St → St → Prop
  /-- `watch`: first touch records the file's present hash. -/
  | watch (s : St) (p : Path) : s.mem p = none →
      Step s { s with mem := upd s.mem p (some (watchEntry s p)) }
  /-- stale check passes, version appended, journal persisted BEFORE the file changes. -/
  | prep (s : St) (p : Path) (c : Content) (e : Entry) :
      s.mem p = some e → s.fs p = e.current →
      Step s { s with mem := upd s.mem p (some (appendV e c)),
                      jr := some (persistOf (upd s.mem p (some (appendV e c)))) }
  /-- the atomic file effect: any recorded version. -/
  | apply (s : St) (p : Path) (c : Content) (e : Entry) :
      s.mem p = some e → c ∈ e.versions →
      Step s { s with fs := upd s.fs p c, mem := upd s.mem p (some { e with current := c }) }

/-- Crash points: every state reachable from the start of a serialized call
(the lock is held and `recover` already left no journal). -/
inductive Reach (fs0 : FS) : St → Prop
  | init : Reach fs0 ⟨fs0, fun _ => none, none⟩
  | step {s t : St} : Reach fs0 s → Step s t → Reach fs0 t

structure Inv (fs0 : FS) (s : St) : Prop where
  untouched : ∀ p, s.mem p = none → s.fs p = fs0 p
  entry : ∀ p e, s.mem p = some e →
    e.before = fs0 p ∧ s.fs p = e.current ∧ e.current ∈ e.versions ∧
    (e.owned = false → e.current = e.before)
  noJournal : s.jr = none → ∀ p e, s.mem p = some e → e.owned = false
  journal : ∀ j, s.jr = some j → j = persistOf s.mem

theorem single_eq {a b : Content} {l : List Content} (ha : a ∈ l) (hb : b ∈ l)
    (hl : ¬ 1 < l.length) : a = b := by
  match l, ha, hb, hl with
  | [x], ha, hb, _ => simp at ha hb; rw [ha, hb]
  | _ :: _ :: _, _, _, hl => simp at hl

theorem persist_current (mem : Path → Option Entry) (p : Path) (e : Entry) (c : Content)
    (h : mem p = some e) :
    persistOf (upd mem p (some { e with current := c })) = persistOf mem := by
  funext q
  by_cases hq : q = p
  · subst hq; simp [persistOf, h, Entry.owned, Entry.key, Option.filter]
  · simp [persistOf, hq]

theorem persist_unowned (mem : Path → Option Entry) (p : Path) (e : Entry)
    (h : mem p = none) (hown : e.owned = false) :
    persistOf (upd mem p (some e)) = persistOf mem := by
  funext q
  by_cases hq : q = p
  · subst hq; simp [persistOf, h, Option.filter, hown]
  · simp [persistOf, hq]

theorem inv_init (fs0 : FS) : Inv fs0 ⟨fs0, fun _ => none, none⟩ where
  untouched := fun _ _ => rfl
  entry := fun _ _ h => by simp at h
  noJournal := fun _ _ _ h => by simp at h
  journal := fun _ h => by simp at h

theorem inv_step {fs0 : FS} {s t : St} (hi : Inv fs0 s) (hs : Step s t) : Inv fs0 t := by
  cases hs with
  | watch p hp =>
    have hown : (watchEntry s p).owned = false := by simp [watchEntry, Entry.owned]
    refine ⟨?_, ?_, ?_, ?_⟩
    · intro q hq
      by_cases hqp : q = p
      · subst hqp; simp at hq
      · simp [hqp] at hq; exact hi.untouched q hq
    · intro q e hq
      by_cases hqp : q = p
      · subst hqp; simp at hq; subst hq
        simp [watchEntry, hi.untouched q hp]
      · simp [hqp] at hq; exact hi.entry q e hq
    · intro hj q e hq
      by_cases hqp : q = p
      · subst hqp; simp at hq; subst hq; exact hown
      · simp [hqp] at hq; exact hi.noJournal hj q e hq
    · intro j hj
      rw [hi.journal j hj, persist_unowned s.mem p _ hp hown]
  | prep p c e hp hcur =>
    refine ⟨?_, ?_, ?_, ?_⟩
    · intro q hq
      by_cases hqp : q = p
      · subst hqp; simp at hq
      · simp [hqp] at hq; exact hi.untouched q hq
    · intro q e' hq
      by_cases hqp : q = p
      · subst hqp; simp at hq; subst hq
        obtain ⟨hb, hf, hc, _⟩ := hi.entry q e hp
        refine ⟨hb, hf, by simp [appendV, hc], ?_⟩
        intro hown
        exfalso
        simp [Entry.owned, appendV] at hown
        rw [hown] at hc; simp at hc
      · simp [hqp] at hq; exact hi.entry q e' hq
    · intro hj; simp at hj
    · intro j hj; simp at hj; exact hj.symm
  | apply p c e hp hc =>
    refine ⟨?_, ?_, ?_, ?_⟩
    · intro q hq
      by_cases hqp : q = p
      · subst hqp; simp at hq
      · simp [hqp] at hq; simp [hqp]; exact hi.untouched q hq
    · intro q e' hq
      by_cases hqp : q = p
      · subst hqp; simp at hq; subst hq
        obtain ⟨hb, _, hcur, hun⟩ := hi.entry q e hp
        refine ⟨hb, by simp, hc, ?_⟩
        intro hown
        have hl : ¬ 1 < e.versions.length := by simpa [Entry.owned] using hown
        rw [single_eq hc hcur hl]
        exact hun (by simpa [Entry.owned] using hown)
      · simp [hqp] at hq; simp [hqp]; exact hi.entry q e' hq
    · intro hj q e' hq
      by_cases hqp : q = p
      · subst hqp; simp at hq; subst hq
        have := hi.noJournal hj q e hp; simpa [Entry.owned] using this
      · simp [hqp] at hq; exact hi.noJournal hj q e' hq
    · intro j hj
      rw [hi.journal j hj, persist_current s.mem p e c hp]

theorem reach_inv {fs0 : FS} {s : St} (h : Reach fs0 s) : Inv fs0 s := by
  induction h with
  | init => exact inv_init fs0
  | step _ hs ih => exact inv_step ih hs

/-- Restoring the persisted journal of an invariant state yields `fs0` on every path. -/
theorem restore_inv {fs0 : FS} {s : St} (hi : Inv fs0 s) (p : Path) :
    restore (persistOf s.mem) s.fs p = fs0 p := by
  unfold restore persistOf
  cases hp : s.mem p with
  | none => simp; exact hi.untouched p hp
  | some e =>
    obtain ⟨hb, hf, hc, hun⟩ := hi.entry p e hp
    cases hown : e.owned with
    | false =>
      have hl : ¬ 1 < e.versions.length := by simpa [Entry.owned] using hown
      simp [Option.filter, hown, hf, hun hown, hb]
    | true =>
      have hl : 1 < e.versions.length := by simpa [Entry.owned] using hown
      simp [Option.filter, hown, Entry.key, hl, hb]

theorem jok_inv {fs0 : FS} {s : St} (hi : Inv fs0 s) : JOk (persistOf s.mem) s.fs := by
  intro p b vs hj _
  unfold persistOf at hj
  cases hp : s.mem p with
  | none => simp [hp] at hj
  | some e =>
    rw [hp] at hj
    obtain ⟨_, hf, hc, _⟩ := hi.entry p e hp
    cases hown : e.owned with
    | false => simp [Option.filter, hown] at hj
    | true =>
      simp [Option.filter, hown, Entry.key] at hj
      rw [hf, ← hj.2]; exact hc

/-- **Crash atomicity without external writers.** Wherever the process dies,
the next `recover` restores the exact pre-transaction filesystem and removes
the journal; it never retains it. -/
theorem crash_recovers_pre_state {fs0 : FS} {s : St} {fs' : FS} {jr' : Option Journal}
    (hr : Reach fs0 s) (h : Recover s.fs s.jr fs' jr') : fs' = fs0 ∧ jr' = none := by
  have hi := reach_inv hr
  cases hj : s.jr with
  | none =>
    rw [hj] at h; cases h
    refine ⟨funext fun p => ?_, rfl⟩
    cases hp : s.mem p with
    | none => exact hi.untouched p hp
    | some e =>
      obtain ⟨hb, hf, _, hun⟩ := hi.entry p e hp
      rw [hf, hun (hi.noJournal hj p e hp), hb]
  | some j =>
    rw [hj] at h
    have hjeq := hi.journal j hj
    cases h with
    | restored _ _ => exact ⟨funext fun p => by rw [hjeq]; exact restore_inv hi p, rfl⟩
    | retained _ hbad => exact absurd (hjeq ▸ jok_inv hi) hbad

/-! ## Candidate (a): the destination loses the rename race

`move` watches source and destination, appends `None` to the source and the
content hash to the destination, persists, then calls `rename_noreplace`. A
competitor creates the destination first; `FileExistsError` guarantees no
mutation. The state below is the Inv state `s` after both watches. -/

def movePrepMem (mem : Path → Option Entry) (src dst : Path) (es ed : Entry) (c : Content) :
    Path → Option Entry :=
  upd (upd mem src (some (appendV es none))) dst (some (appendV ed c))

/-- Shipped handler when `destination_was_watched` (lines 189-196 before the fix):
nothing is undone; the persisted journal still owns the destination. -/
def raceShipped (s : St) (src dst : Path) (es ed : Entry) (c : Content) (x : Nat) : St :=
  let m := movePrepMem s.mem src dst es ed c
  { fs := upd s.fs dst (some x), mem := m, jr := some (persistOf m) }

/-- Fixed handler: undo BOTH appends (drop the destination entry if the move
itself watched it) and persist again, whether or not it was watched before. -/
def raceFixed (s : St) (dst : Path) (x : Nat) (dropDst : Bool) : St :=
  let m := if dropDst then upd s.mem dst none else s.mem
  { fs := upd s.fs dst (some x), mem := m, jr := some (persistOf m) }

/-- **Refutation (shipped code).** A watched destination that loses the race to
any content other than the one being moved strands the journal: `recover`
retains it, and keeps retaining it for every later call — a global stuck lock
although the transaction changed nothing. -/
theorem race_shipped_strands {fs0 : FS} {s : St} {src dst : Path} {es ed : Entry}
    {c : Content} {x : Nat} (hi : Inv fs0 s) (hd : s.mem dst = some ed)
    (hed : ed.owned = false) (habs : s.fs dst = none) (hx : c ≠ some x)
    (fs'' : FS) (hstill : fs'' dst = some x) {fs' : FS} {jr' : Option Journal}
    (h : Recover fs'' (raceShipped s src dst es ed c x).jr fs' jr') :
    jr' = (raceShipped s src dst es ed c x).jr ∧ fs' = fs'' := by
  obtain ⟨_, hf, hcur, hun⟩ := hi.entry dst ed hd
  have hvs : ed.versions = [none] := by
    have hl : ¬ 1 < ed.versions.length := by simpa [Entry.owned] using hed
    match hv : ed.versions, hcur, hl with
    | [y], hcur, _ =>
      simp at hcur; rw [← hcur, ← hf, habs]
    | [], hcur, _ => simp at hcur
    | _ :: _ :: _, _, hl => simp at hl
  have hbad : ¬ JOk (persistOf (movePrepMem s.mem src dst es ed c)) fs'' := by
    intro hok
    have := hok dst ed.before (ed.versions ++ [c])
      (by simp [persistOf, movePrepMem, appendV, Option.filter, Entry.owned, Entry.key, hvs])
      (by simp [hvs])
    rw [hstill, hvs] at this
    simp at this
    exact hx this.symm
  have := retained_is_stuck hbad h
  exact ⟨this.1, this.2⟩

/-- **Fixed code: the race costs nothing.** The next `recover` succeeds, removes
the journal, rolls every owned path back to `fs0`, and leaves the competitor's
file alone. Precondition: the destination was not already written by this
transaction (otherwise a foreign file really sits on an owned path, which is
the retained-journal case by design). -/
theorem race_fixed_recovers {fs0 : FS} {s : St} {dst : Path} {ed : Entry} {x : Nat}
    {drop : Bool} (hi : Inv fs0 s) (hd : s.mem dst = some ed) (hed : ed.owned = false)
    {fs' : FS} {jr' : Option Journal}
    (h : Recover (raceFixed s dst x drop).fs (raceFixed s dst x drop).jr fs' jr') :
    jr' = none ∧ fs' dst = some x ∧ ∀ p, p ≠ dst → fs' p = fs0 p := by
  let m := if drop then upd s.mem dst none else s.mem
  have hm_other : ∀ p, p ≠ dst → m p = s.mem p := by
    intro p hp; cases drop <;> simp [m, hp]
  have hm_dst : persistOf m dst = none := by
    cases drop <;> simp [m, persistOf, hd, Option.filter, hed]
  have hok : JOk (persistOf m) (upd s.fs dst (some x)) := by
    intro p b vs hj hl
    by_cases hp : p = dst
    · subst hp; rw [hm_dst] at hj; simp at hj
    · have : persistOf m p = persistOf s.mem p := by simp [persistOf, hm_other p hp]
      rw [this] at hj
      have := jok_inv hi p b vs hj hl
      simpa [hp] using this
  cases h with
  | retained _ hbad => exact absurd hok hbad
  | restored _ _ =>
    refine ⟨rfl, ?_, ?_⟩
    · show restore (persistOf m) (upd s.fs dst (some x)) dst = some x
      simp [restore, hm_dst]
    · intro p hp
      show restore (persistOf m) (upd s.fs dst (some x)) p = fs0 p
      have e1 : persistOf m p = persistOf s.mem p := by simp [persistOf, hm_other p hp]
      have := restore_inv hi p
      unfold restore at this ⊢
      rw [e1]; simpa [hp] using this

/-! ## Candidate (b): the stale-overwrite check at first touch

`write()` calls `watch()` first; on first touch `watch` reads the file NOW,
so the check `digest(read_text(path)) != entry["current"]` compares the file
with itself. -/

def watchIfNew (s : St) (p : Path) : St :=
  match s.mem p with
  | some _ => s
  | none => { s with mem := upd s.mem p (some (watchEntry s p)) }

def StaleCheckPasses (s : St) (p : Path) : Prop :=
  ∃ e, (watchIfNew s p).mem p = some e ∧ s.fs p = e.current

/-- **Vacuous at first touch**: whatever the caller read, the check passes. -/
theorem stale_check_vacuous_first_touch (s : St) (p : Path) (h : s.mem p = none) :
    StaleCheckPasses s p :=
  ⟨watchEntry s p, by simp [watchIfNew, h], rfl⟩

/-- ...and effective once the path is watched: any change is refused. -/
theorem stale_check_detects_when_watched (s : St) (p : Path) (e : Entry)
    (h : s.mem p = some e) (hne : s.fs p ≠ e.current) : ¬ StaleCheckPasses s p := by
  rintro ⟨e', he', hf⟩
  simp [watchIfNew, h] at he'
  subst he'; exact hne hf

/-- Concrete lost update: the caller read hash 1 without watching, an
uncooperative writer put hash 2, and the unwatched write passes the check. -/
theorem lost_update_example :
    let s : St := ⟨fun _ => some 2, fun _ => none, none⟩
    StaleCheckPasses s 0 ∧ s.fs 0 ≠ some 1 :=
  ⟨stale_check_vacuous_first_touch _ 0 rfl, by simp⟩

end DatacoreSpec.OrgTransaction
