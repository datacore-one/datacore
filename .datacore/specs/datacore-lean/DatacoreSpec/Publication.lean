/-!
# Publication cluster — reclaim, repo lock, prepared write-backs, retirement CAS

Models, branch for branch, of:

* `lib/publication_workspace_gc.py` `reclaimable` / `reclaim`         (§1)
* `lib/ledger_transport.py` `_repo_lock` / `_lock_name`               (§2)
* `lib/writeback_store.py` `queue` / `_predicted` / `_apply` / `process` (§3)
* `lib/worktree_lifecycle.py` `retire_worktree`'s HEAD compare-and-swap (§4)
* every holder of `_repo_lock`, now including `lib/git_fleet_sync.py`
  (decision P1, 2026-09-23), its pull too (decision Q6): mutual
  exclusion, one critical section per repository, no deadlock     (§5)

Abstractions, and why they are safe for the stated properties:

* Git trees are association lists `List (Nat × Nat)` (path ↦ blob). Only
  equality of trees matters to every property below.
* "Is this commit on origin/<default>" is an oracle `pub`; the tree of a commit
  is an oracle `treeOf`. Theorems quantify over all oracles.
* The Python's `published_trees` dict is an oracle `lookup : Tree → Option Nat`
  with the one property git gives it (`LookupSound`): a hit is a published
  commit with exactly that tree.
* `git diff --quiet` + `git ls-files --others` (untracked AND ignored) are
  modelled as the exact observation `files = index`. The snapshot caveat (a
  writer after the check) is excluded by the `retired` precondition, which is a
  cooperative fact, not something git can prove; see findings/publication.md.
* File texts in §3 are an arbitrary type with decidable equality; sha256 is
  modelled as the identity (collision freedom).
-/

namespace DatacoreSpec.Publication

/-! ## §1 Reclaiming a publication worktree -/

abbrev Tree := List (Nat × Nat)

structure Oracle where
  pub    : Nat → Bool      -- commit is an ancestor of origin/<default>
  treeOf : Nat → Tree
  lookup : Tree → Option Nat

def LookupSound (o : Oracle) : Prop :=
  ∀ t c, o.lookup t = some c → o.pub c = true ∧ o.treeOf c = t

structure Worktree where
  retired : Bool
  head    : Nat
  index   : Tree
  files   : Tree   -- every byte on disk: tracked, untracked and ignored

/-- Nothing unreproducible: HEAD's history is published and the bytes on disk
are exactly a published commit's tree. -/
def Lossless (o : Oracle) (w : Worktree) : Prop :=
  o.pub w.head = true ∧ ∃ c, o.pub c = true ∧ o.treeOf c = w.files

/-- The code before 2026-09-23: `published(repo, worktree, branch)` only. -/
def oldReclaim (o : Oracle) (w : Worktree) : Bool := o.pub w.head

/-- `reclaimable` after the fix: retired, HEAD published, files = index, and the
index is a published tree. Returns the commit HEAD is CAS-moved to. -/
def newReclaim (o : Oracle) (w : Worktree) : Option Nat :=
  if w.retired && o.pub w.head && decide (w.files = w.index) then o.lookup w.index else none

/-- The survey's scenario: a failed publication commit leaves HEAD at the
published base with the captured content staged (and a late untracked file). -/
theorem old_reclaim_loses_work :
    ∃ (o : Oracle) (w : Worktree), LookupSound o ∧ oldReclaim o w = true ∧ ¬ Lossless o w := by
  refine ⟨⟨fun _ => true, fun _ => [], fun _ => none⟩,
          ⟨true, 0, [(1, 7)], [(1, 7), (2, 9)]⟩, ?_, rfl, ?_⟩
  · intro t c h; cases h
  · intro ⟨_, c, _, h⟩; cases h

theorem new_reclaim_sound (o : Oracle) (hs : LookupSound o) (w : Worktree) (c : Nat)
    (h : newReclaim o w = some c) : Lossless o w := by
  unfold newReclaim at h
  split at h
  · rename_i hc
    simp only [Bool.and_eq_true, decide_eq_true_eq] at hc
    obtain ⟨⟨_, hp⟩, hf⟩ := hc
    obtain ⟨hpc, ht⟩ := hs _ _ h
    exact ⟨hp, c, hpc, by rw [ht, hf]⟩
  · cases h

/-- The removal step: HEAD is compare-and-swapped to the commit whose tree is
the index, so `git worktree remove` without --force sees a clean checkout.
Both the old and the new HEAD are published: no history loses its anchor. -/
theorem reclaim_cas_keeps_anchor (o : Oracle) (hs : LookupSound o) (w : Worktree) (c : Nat)
    (h : newReclaim o w = some c) :
    o.pub w.head = true ∧ o.pub c = true ∧ o.treeOf c = w.files := by
  obtain ⟨hp, c', hc', ht'⟩ := new_reclaim_sound o hs w c h
  unfold newReclaim at h
  split at h
  · rename_i hc
    simp only [Bool.and_eq_true, decide_eq_true_eq] at hc
    obtain ⟨hpc, ht⟩ := hs _ _ h
    exact ⟨hp, hpc, by rw [ht, hc.2]⟩
  · cases h

/-- Not retired ⇒ never reclaimed, however clean (a live candidate mid-publication). -/
theorem live_workspace_kept (o : Oracle) (w : Worktree) (h : w.retired = false) :
    newReclaim o w = none := by
  simp [newReclaim, h]

/-! ## §2 The per-repository lock -/

/-- What `git rev-parse --git-common-dir` resolves to. -/
structure CommonDir where
  name       : String   -- final component, e.g. ".git" or "foo.git"
  parentName : String   -- basename of its parent directory

structure Checkout where
  basename : String
  common   : Option CommonDir   -- none: git cannot resolve it

def oldKey (p : Checkout) : String := p.basename

def nameOf (c : CommonDir) : String := if c.name = ".git" then c.parentName else c.name

/-- `_lock_name` after the fix. -/
def newKey (p : Checkout) : String :=
  match p.common with
  | some c => nameOf c
  | none   => p.basename

/-- The property the pending record needs: it lives in the common dir, so every
checkout of one repository must take one lock. -/
def Exclusive (key : Checkout → String) : Prop :=
  ∀ p q c, p.common = some c → q.common = some c → key p = key q

theorem basename_key_not_exclusive : ¬ Exclusive oldKey := by
  intro h
  have := h ⟨"0-personal", some ⟨".git", "0-personal"⟩⟩ ⟨"worktree-7", some ⟨".git", "0-personal"⟩⟩
    ⟨".git", "0-personal"⟩ rfl rfl
  simp [oldKey] at this

theorem common_dir_key_exclusive : Exclusive newKey := by
  intro p q c hp hq
  simp [newKey, hp, hq]

/-- A main checkout (`<dir>/.git`) keeps the lock name it always had, so a
cooperating process running the old code still excludes the new one. -/
theorem main_checkout_key_unchanged (p : Checkout)
    (h : p.common = some ⟨".git", p.basename⟩) : newKey p = oldKey p := by
  simp [newKey, oldKey, h, nameOf]

/-! ## §3 Prepared write-backs -/

section Writeback
variable {T : Type} [DecidableEq T]

structure Plan (T : Type) where
  before : T
  after  : T

inductive Status | pending | completed | conflict
  deriving DecidableEq

/-- `_apply` for one pending write: the file is its before (apply) or its after
(already applied: acknowledge), anything else is a conflict. -/
def applyOne (file : T) (p : Plan T) : T × Status :=
  if file = p.before ∨ file = p.after then (p.after, .completed) else (file, .conflict)

/-- The docstring's claim: retry recognizes the applied bytes, and a third
version is a conflict, never an overwrite target. -/
theorem third_version_never_overwritten (file : T) (p : Plan T)
    (h : file ≠ p.before) (h' : file ≠ p.after) : applyOne file p = (file, .conflict) := by
  simp [applyOne, h, h']

theorem retry_idempotent (file : T) (p : Plan T) (h : (applyOne file p).2 = .completed) :
    applyOne (applyOne file p).1 p = applyOne file p := by
  unfold applyOne at *
  by_cases hc : file = p.before ∨ file = p.after <;> simp_all

/-- Processing pending writes in queue order (`process` runs predecessors first). -/
def run (file : T) : List (Plan T) → T × List Status
  | [] => (file, [])
  | p :: ps =>
    let (f', s) := applyOne file p
    let (f'', ss) := run f' ps
    (f'', s :: ss)

/-- `_predicted`: `_apply`'s rule run on text over the pending plans. -/
def predict (file : T) : List (Plan T) → T
  | [] => file
  | p :: ps => predict (if file = p.before then p.after else file) ps

/-- `queue` before the fix: every plan is rendered from the file as it is now. -/
def queueOld (file : T) (plans : List (Plan T)) (r : T → T) : List (Plan T) :=
  plans ++ [⟨file, r file⟩]

/-- `queue` after the fix: rendered from the file as the pending plans leave it. -/
def queueNew (file : T) (plans : List (Plan T)) (r : T → T) : List (Plan T) :=
  let b := predict file plans
  plans ++ [⟨b, r b⟩]

/-- Two task completions queued against one org file, then processed. -/
theorem unchained_second_write_conflicts :
    (run (0 : Nat) (queueOld 0 (queueOld 0 [] (· + 1)) (· + 2))).2
      = [.completed, .conflict] := by decide

/-- The chain the fixed queue builds, stated forward. -/
def chain (file : T) : List (T → T) → List (Plan T)
  | [] => []
  | r :: rs => ⟨file, r file⟩ :: chain (r file) rs

def final (file : T) : List (T → T) → T
  | [] => file
  | r :: rs => final (r file) rs

theorem predict_chain (file : T) (rs : List (T → T)) :
    predict file (chain file rs) = final file rs := by
  induction rs generalizing file with
  | nil => rfl
  | cons r rs ih => simp [chain, predict, final, ih]

omit [DecidableEq T] in
theorem chain_snoc (file : T) (rs : List (T → T)) (r : T → T) :
    chain file (rs ++ [r]) = chain file rs ++ [⟨final file rs, r (final file rs)⟩] := by
  induction rs generalizing file with
  | nil => rfl
  | cons r' rs ih => simp [chain, final, ih]

/-- Queueing with `queueNew` from an empty queue produces exactly `chain`. -/
theorem queueNew_extends_chain (file : T) (rs pref : List (T → T)) :
    rs.foldl (queueNew file) (chain file pref) = chain file (pref ++ rs) := by
  induction rs generalizing pref with
  | nil => simp
  | cons r rs ih =>
    have step : queueNew file (chain file pref) r = chain file (pref ++ [r]) := by
      simp [queueNew, predict_chain, chain_snoc]
    simp only [List.foldl_cons, step, ih]
    simp

/-- Queueing with `queueNew` from an empty queue produces exactly `chain`. -/
theorem queueNew_is_chain (file : T) (rs : List (T → T)) :
    rs.foldl (queueNew file) [] = chain file rs := by
  have h := queueNew_extends_chain file rs []
  simpa [chain] using h

/-- The fixed property: writes queued against one file with no external edit
all apply, in queue order, and the file ends as every change composed. -/
theorem chained_writes_all_apply (file : T) (rs : List (T → T)) :
    run file (chain file rs) = (final file rs, rs.map fun _ => .completed) := by
  induction rs generalizing file with
  | nil => rfl
  | cons r rs ih => simp [chain, run, applyOne, final, ih]

end Writeback

/-! ## §4 Retirement's HEAD compare-and-swap -/

inductive Head
  | sym (branch : String)
  | det (commit : Nat)

structure Refs where
  head     : Head
  branches : String → Option Nat

def resolve (s : Refs) : Option Nat :=
  match s.head with
  | .sym b => s.branches b
  | .det c => some c

def Anchored (s : Refs) (c : Nat) : Prop := resolve s = some c ∨ ∃ b, s.branches b = some c

/-- `git update-ref --no-deref HEAD <head> <head>` (replayed: the old value is
checked against the symref's resolved value). `none` = refused, nothing changed. -/
def casDetach (s : Refs) (captured : Nat) : Option Refs :=
  if resolve s = some captured then some { s with head := .det captured } else none

/-- Whatever a late writer did between capture and CAS, a successful CAS loses
no anchored commit. -/
theorem retire_cas_keeps_anchors (s s' : Refs) (captured c : Nat)
    (h : casDetach s captured = some s') (ha : Anchored s c) : Anchored s' c := by
  unfold casDetach at h
  split at h
  · rename_i hr
    cases h
    rcases ha with h1 | ⟨b, hb⟩
    · left; rw [hr] at h1; simp [resolve, h1]
    · right; exact ⟨b, hb⟩
  · cases h

/-- Without the CAS (unconditional detach at the captured HEAD), a commit a late
writer made on a detached HEAD loses its only anchor. -/
theorem forced_detach_loses_late_commit :
    ∃ s : Refs, Anchored s 2 ∧ ¬ Anchored { s with head := .det 1 } 2 := by
  refine ⟨⟨.det 2, fun _ => none⟩, Or.inl rfl, ?_⟩
  intro h
  rcases h with h | ⟨_, h⟩ <;> simp [resolve] at h

/-! ## §5 One writer per repository: the transport, publications and the sweep

Decision P1 (2026-09-23): `git_fleet_sync.sync_repo` takes
`ledger_transport._repo_lock` from its inventory through its push, like
`ledger_transport.converge`/`publish` and `publication_state.reserve`.
Decision Q6 (2026-09-23): the sweep's pull (fetch + merge) runs under the same
lock, in ONE critical section per repository with the stage/commit/push.

Model. Processes (threads of the transport, a publication, the sweep) are
`Nat`s; a lock is named by `newKey` of the checkout it is taken for (§2). A
process is idle, waiting for one lock, holding one while it performs an
operation `Op` (`pull` = fetch + merge, `land` = inventory/stage/commit/fetch/
push), or `bare`: performing an operation in a repository WITHOUT the lock.
Which operations may run bare is the parameter `bareOk : Op → Bool`:
* `preP1` — everything (the sweep before P1);
* `p1`    — the pull only (P1: the sweep locked its land, not its pull);
* `locked` — nothing (Q6).
The sweep acquires for `pull` and `advance`s to `land` WITHOUT releasing: that
is the one critical section. Each holder takes exactly one repository lock and
takes no other lock inside it (`hold` has no step to `wait`): the "they don't
nest it" fact read from the call sites. A same-thread re-entry (`_HELD`) is a
no-op on a lock the thread already holds and adds no step, so it is not
modelled. `Mutating s p k`: process p may be changing repository k's
index/branch/refs/remote.

Proved for `locked`: at most one process mutates a repository in every reachable
state (`lock_mutual_exclusion`), across checkouts that share a git-common-dir
(`fleet_sync_excludes_publication`); every waiter can make progress or waits on
a holder that can release (`lock_no_deadlock`); and nobody else takes the lock
between the sweep's pull and its land (`holder_stable`).
Refuted: the pre-P1 sweep commits beside the transport (`old_sweep_races`), and
the P1 sweep merges beside it (`p1_pull_races`).
-/

section Lock

inductive Op | pull | land
  deriving DecidableEq

inductive PS
  | idle
  | wait (k : String)
  | hold (k : String) (o : Op)
  | bare (k : String) (o : Op)
  deriving DecidableEq

structure Sys where
  st    : Nat → PS
  owner : String → Option Nat

def upd {α β : Type} [DecidableEq α] (f : α → β) (a : α) (b : β) : α → β :=
  fun x => if x = a then b else f x

def Sys.init : Sys := ⟨fun _ => .idle, fun _ => none⟩

def preP1 : Op → Bool := fun _ => true
def p1 : Op → Bool | .pull => true | .land => false
def locked : Op → Bool := fun _ => false

/-- One transition. `bareOk o` = operation `o` may run without the lock. -/
inductive Step (bareOk : Op → Bool) : Sys → Sys → Prop
  | request (s : Sys) (p : Nat) (k : String) : s.st p = .idle →
      Step bareOk s ⟨upd s.st p (.wait k), s.owner⟩
  | acquire (s : Sys) (p : Nat) (k : String) (o : Op) : s.st p = .wait k → s.owner k = none →
      Step bareOk s ⟨upd s.st p (.hold k o), upd s.owner k (some p)⟩
  | advance (s : Sys) (p : Nat) (k : String) : s.st p = .hold k .pull →
      Step bareOk s ⟨upd s.st p (.hold k .land), s.owner⟩
  | release (s : Sys) (p : Nat) (k : String) (o : Op) : s.st p = .hold k o →
      Step bareOk s ⟨upd s.st p .idle, upd s.owner k none⟩
  | bareStart (s : Sys) (p : Nat) (k : String) (o : Op) : bareOk o = true → s.st p = .idle →
      Step bareOk s ⟨upd s.st p (.bare k o), s.owner⟩
  | bareEnd (s : Sys) (p : Nat) (k : String) (o : Op) : s.st p = .bare k o →
      Step bareOk s ⟨upd s.st p .idle, s.owner⟩

inductive Reach (bareOk : Op → Bool) : Sys → Prop
  | init : Reach bareOk Sys.init
  | step {s t : Sys} : Reach bareOk s → Step bareOk s t → Reach bareOk t

def Holds (s : Sys) (p : Nat) (k : String) : Prop := ∃ o, s.st p = .hold k o

def Mutating (s : Sys) (p : Nat) (k : String) : Prop := ∃ o, s.st p = .hold k o ∨ s.st p = .bare k o

/-- The lock table and the process states agree, and nobody is bare. -/
def Inv (s : Sys) : Prop :=
  (∀ k p, s.owner k = some p ↔ Holds s p k) ∧ (∀ p k o, s.st p ≠ .bare k o)

theorem inv_init : Inv Sys.init := by
  refine ⟨fun k p => ?_, fun p k o => ?_⟩ <;> simp [Sys.init, Holds]

theorem inv_step (s t : Sys) (hi : Inv s) (hs : Step locked s t) : Inv t := by
  obtain ⟨ho, hb⟩ := hi
  cases hs with
  | request p k hp =>
    refine ⟨fun k' p' => ?_, fun p' k' o' => ?_⟩
    · simp only [Holds, upd]
      by_cases e : p' = p
      · subst e; simp only [↓reduceIte, reduceCtorEq, exists_false, iff_false]
        intro h; obtain ⟨o, ho'⟩ := (ho k' p').mp h; rw [hp] at ho'; cases ho'
      · simp only [e, ↓reduceIte]; exact ho k' p'
    · simp only [upd]; by_cases e : p' = p
      · subst e; simp
      · simp only [e, ↓reduceIte]; exact hb p' k' o'
  | acquire p k o hp hfree =>
    refine ⟨fun k' p' => ?_, fun p' k' o' => ?_⟩
    · simp only [Holds, upd]
      by_cases ek : k' = k <;> by_cases ep : p' = p
      · subst ek; subst ep; simp
      · subst ek; simp only [↓reduceIte, ep, Option.some.injEq]
        constructor
        · intro h; exact absurd h.symm ep
        · intro h; have := (ho k' p').mpr h; rw [hfree] at this; cases this
      · subst ep; simp only [ek, ↓reduceIte, PS.hold.injEq]
        constructor
        · intro h; obtain ⟨o', ho'⟩ := (ho k' p').mp h; rw [hp] at ho'; cases ho'
        · rintro ⟨_, h, _⟩; exact absurd h.symm ek
      · simp only [ek, ep, ↓reduceIte]; exact ho k' p'
    · simp only [upd]; by_cases e : p' = p
      · subst e; simp
      · simp only [e, ↓reduceIte]; exact hb p' k' o'
  | advance p k hp =>
    refine ⟨fun k' p' => ?_, fun p' k' o' => ?_⟩
    · simp only [Holds, upd]
      by_cases ep : p' = p
      · subst ep; simp only [↓reduceIte, PS.hold.injEq]
        constructor
        · intro h; obtain ⟨o', ho'⟩ := (ho k' p').mp h; rw [hp] at ho'
          cases ho'; exact ⟨.land, rfl, rfl⟩
        · rintro ⟨_, hk, _⟩; subst hk; exact (ho _ _).mpr ⟨.pull, hp⟩
      · simp only [ep, ↓reduceIte]; exact ho k' p'
    · simp only [upd]; by_cases e : p' = p
      · subst e; simp
      · simp only [e, ↓reduceIte]; exact hb p' k' o'
  | release p k o hp =>
    refine ⟨fun k' p' => ?_, fun p' k' o' => ?_⟩
    · simp only [Holds, upd]
      by_cases ek : k' = k <;> by_cases ep : p' = p
      · subst ek; subst ep; simp
      · subst ek; simp only [↓reduceIte, ep, reduceCtorEq, false_iff, not_exists]
        intro o' h
        have h1 := (ho k' p').mpr ⟨o', h⟩
        have h2 := (ho k' p).mpr ⟨o, hp⟩
        rw [h1] at h2; exact ep (Option.some.inj h2)
      · subst ep; simp only [ek, ↓reduceIte, reduceCtorEq, exists_false, iff_false]
        intro h; obtain ⟨o', ho'⟩ := (ho k' p').mp h; rw [hp] at ho'; cases ho'; exact ek rfl
      · simp only [ek, ep, ↓reduceIte]; exact ho k' p'
    · simp only [upd]; by_cases e : p' = p
      · subst e; simp
      · simp only [e, ↓reduceIte]; exact hb p' k' o'
  | bareStart p k o hok _ => simp [locked] at hok
  | bareEnd p k o hp => exact absurd hp (hb p k o)

theorem reach_inv {s : Sys} (h : Reach locked s) : Inv s := by
  induction h with
  | init => exact inv_init
  | step _ hs ih => exact inv_step _ _ ih hs

/-- **Mutual exclusion.** With the sweep's pull and land under `_repo_lock`,
no two processes ever fetch/merge, stage, commit or push in one repository at
the same time. -/
theorem lock_mutual_exclusion {s : Sys} (h : Reach locked s) (p q : Nat) (k : String)
    (hp : Mutating s p k) (hq : Mutating s q k) : p = q := by
  obtain ⟨ho, hb⟩ := reach_inv h
  obtain ⟨op, hp⟩ := hp
  obtain ⟨oq, hq⟩ := hq
  rcases hp with hp | hp
  · rcases hq with hq | hq
    · have a := (ho k p).mpr ⟨op, hp⟩; have b := (ho k q).mpr ⟨oq, hq⟩
      rw [a] at b; exact Option.some.inj b
    · exact absurd hq (hb q k oq)
  · exact absurd hp (hb p k op)

/-- The keys are §2's: the sweep in the main checkout and a publication
reserved from a linked worktree of the same repository exclude each other. -/
theorem fleet_sync_excludes_publication {s : Sys} (h : Reach locked s)
    (sweep pub : Checkout) (c : CommonDir) (hs : sweep.common = some c) (hp : pub.common = some c)
    (a b : Nat) (ha : Mutating s a (newKey sweep)) (hb : Mutating s b (newKey pub)) : a = b := by
  rw [common_dir_key_exclusive sweep pub c hs hp] at ha
  exact lock_mutual_exclusion h a b _ ha hb

/-- **One critical section (Q6).** No step by anyone hands a held lock to
another process: from any step, the owner of `k` is still `p` or `k` is free
(only `p`'s release frees it). With `holder_of_hold`, between the sweep's pull
and its land (`advance`, which does not release) the owner stays the sweep, so
no other writer's commit can land between them. -/
theorem holder_stable {s t : Sys} {bareOk : Op → Bool} (p : Nat) (k : String)
    (hown : s.owner k = some p) (hs : Step bareOk s t) :
    t.owner k = some p ∨ t.owner k = none := by
  cases hs with
  | request => exact Or.inl hown
  | advance => exact Or.inl hown
  | bareStart => exact Or.inl hown
  | bareEnd => exact Or.inl hown
  | acquire q k' o _ hfree =>
    by_cases e : k = k'
    · subst e; rw [hfree] at hown; cases hown
    · left; simp only [upd, e, ↓reduceIte]; exact hown
  | release q k' o _ =>
    by_cases e : k = k'
    · right; simp [upd, e]
    · left; simp only [upd, e, ↓reduceIte]; exact hown

/-- In the locked system a process in its section is the recorded owner. -/
theorem holder_of_hold {s : Sys} (h : Reach locked s) (p : Nat) (k : String) (o : Op)
    (hh : s.st p = .hold k o) : s.owner k = some p :=
  ((reach_inv h).1 k p).mpr ⟨o, hh⟩

/-- **No deadlock.** Every waiter either can acquire now or waits on a process
that holds the lock and can release it; holders never wait (no nesting), so
the wait-for relation has no cycle. In particular some step is enabled. -/
theorem lock_no_deadlock {s : Sys} (h : Reach locked s) (p : Nat) (k : String) (o : Op)
    (hw : s.st p = .wait k) :
    (s.owner k = none ∧ Step locked s ⟨upd s.st p (.hold k o), upd s.owner k (some p)⟩) ∨
    (∃ q o', s.st q = .hold k o' ∧ Step locked s ⟨upd s.st q .idle, upd s.owner k none⟩) := by
  obtain ⟨ho, _⟩ := reach_inv h
  cases e : s.owner k with
  | none => exact Or.inl ⟨rfl, Step.acquire s p k o hw e⟩
  | some q =>
    obtain ⟨o', hq⟩ := (ho k q).mp e
    exact Or.inr ⟨q, o', hq, Step.release s q k o' hq⟩

/-- Non-vacuity: the sweep does pull and then land inside one section. -/
theorem lock_reachable_pull_then_land :
    ∃ s t, Reach locked s ∧ s.st 0 = .hold "r" .pull ∧ Reach locked t ∧
      t.st 0 = .hold "r" .land ∧ Step locked s t := by
  let s1 : Sys := ⟨upd Sys.init.st 0 (.wait "r"), Sys.init.owner⟩
  let s2 : Sys := ⟨upd s1.st 0 (.hold "r" .pull), upd s1.owner "r" (some 0)⟩
  let s3 : Sys := ⟨upd s2.st 0 (.hold "r" .land), s2.owner⟩
  have r1 : Reach locked s1 := Reach.step Reach.init (Step.request _ 0 "r" rfl)
  have t2 : Step locked s1 s2 := Step.acquire _ 0 "r" .pull (by simp [s1, upd]) rfl
  have t3 : Step locked s2 s3 := Step.advance _ 0 "r" (by simp [s2, upd])
  exact ⟨s2, s3, Reach.step r1 t2, by simp [s2, upd], Reach.step (Reach.step r1 t2) t3,
    by simp [s3, upd], t3⟩

/-- **Refutation of the pre-P1 sweep.** The transport (0) holds the lock and
commits; the sweep (1) stages and commits in the same repository without it. -/
theorem old_sweep_races : ∃ s, Reach preP1 s ∧ Mutating s 0 "r" ∧ Mutating s 1 "r" := by
  let s1 : Sys := ⟨upd Sys.init.st 0 (.wait "r"), Sys.init.owner⟩
  let s2 : Sys := ⟨upd s1.st 0 (.hold "r" .land), upd s1.owner "r" (some 0)⟩
  let s3 : Sys := ⟨upd s2.st 1 (.bare "r" .land), s2.owner⟩
  have r1 : Reach preP1 s1 := Reach.step Reach.init (Step.request _ 0 "r" rfl)
  have r2 : Reach preP1 s2 := Reach.step r1 (Step.acquire _ 0 "r" .land (by simp [s1, upd]) rfl)
  have r3 : Reach preP1 s3 :=
    Reach.step r2 (Step.bareStart _ 1 "r" .land rfl (by simp [s2, s1, upd, Sys.init]))
  exact ⟨s3, r3, ⟨.land, Or.inl (by simp [s3, s2, upd])⟩, ⟨.land, Or.inr (by simp [s3, upd])⟩⟩

/-- **Refutation of the P1 sweep (what Q6 closes).** Its land was locked but
its pull was not: the transport (0) holds the lock and commits while the sweep
(1) fetches and merges into the same checkout. -/
theorem p1_pull_races : ∃ s, Reach p1 s ∧ Mutating s 0 "r" ∧ Mutating s 1 "r" := by
  let s1 : Sys := ⟨upd Sys.init.st 0 (.wait "r"), Sys.init.owner⟩
  let s2 : Sys := ⟨upd s1.st 0 (.hold "r" .land), upd s1.owner "r" (some 0)⟩
  let s3 : Sys := ⟨upd s2.st 1 (.bare "r" .pull), s2.owner⟩
  have r1 : Reach p1 s1 := Reach.step Reach.init (Step.request _ 0 "r" rfl)
  have r2 : Reach p1 s2 := Reach.step r1 (Step.acquire _ 0 "r" .land (by simp [s1, upd]) rfl)
  have r3 : Reach p1 s3 :=
    Reach.step r2 (Step.bareStart _ 1 "r" .pull rfl (by simp [s2, s1, upd, Sys.init]))
  exact ⟨s3, r3, ⟨.land, Or.inl (by simp [s3, s2, upd])⟩, ⟨.pull, Or.inr (by simp [s3, upd])⟩⟩

/-- P1's land alone was already exclusive: `p1` never lets a land run bare. -/
theorem p1_land_never_bare : p1 .land = false := rfl

end Lock

end DatacoreSpec.Publication
