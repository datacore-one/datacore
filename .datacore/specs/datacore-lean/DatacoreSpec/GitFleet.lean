import LedgerSpec.Chain

/-!
# GitFleet — every path that publishes a commit, and "NEVER PUSH A FORK"

Python modelled (`.datacore/lib/`):

* `git_relay.py`        `relay`, `ledger_forks`, `publication_forks`, `_park`
* `git_fleet_sync.py`   `sync_repo`, `in_progress`, `range_deletions` (P2),
                        `fetch_default` (Q8), `main`'s exit status (Q7)
* `state_loop_rollout.py` `_actor`, the `--commit` push
* `cron_install.py`     `reconcile`
* `git_branch_hygiene.py` `landed_earlier`, `classify`
* `visitor_join.py`     `join`, `due`, `_exclusive`
* `ledger_transport._push_with_retry`, `knowledge_commit._push_commit` (and
  its non-fast-forward integration): ungated before owner decision L9, gated
  by `publication_forks` since (`never_push_a_fork_all_pushers`)
* `ledger/log.EventLog.append`'s declared-actor check (decision L10, §5)

A per-writer log is a `LedgerSpec.Chain` chain. A publication is safe when the
log origin holds is an EVENT PREFIX of the log in the pushed commit: that is
"no fork" (no `(actor, seq)` changes hash) plus "no rewind" (no event goes
missing). `publication_forks` checks exactly fork.py's per-key predicate plus
the missing-key test plus chain validity (`KGuard` + `Valid` below);
`guard_sound` proves that those checks imply the prefix relation, and
`never_push_a_fork` lifts it to every trace of gated pushes.
-/

namespace DatacoreSpec.GitFleet

open LedgerSpec.Chain

variable {B H : Type}

instance [DecidableEq B] [DecidableEq H] : DecidableEq (CE B H) := fun a b =>
  decidable_of_iff (a.seq = b.seq ∧ a.prev = b.prev ∧ a.hash = b.hash ∧ a.body = b.body)
    (by cases a; cases b; simp)

/-! ## 1. The publication guard is sound -/

/-- In a valid chain the event at position `i` carries `seq = n + i`
(verify.py: "seq must run 0, 1, 2, ..."). -/
theorem valid_seq (hf : Nat → H → B → H) :
    ∀ (L : List (CE B H)) (p : H) (n i : Nat) (h : i < L.length),
      Valid hf p n L → L[i].seq = n + i
  | [], _, _, _, h, _ => absurd h (by simp)
  | e :: _, _, _, 0, _, hv => by simpa using hv.1
  | _ :: L, _, n, i + 1, h, hv => by
    have := valid_seq hf L _ (n + 1) i (by simpa using h) hv.2.2.2
    simp only [List.getElem_cons_succ]; omega

/-- fork.py's predicate plus the rewind test, as `publication_forks` runs them:
every event origin holds is present in the candidate under the same key with
the same hash. (Collisions = same key, other hash; rewind = key missing.) -/
def KGuard (T O : List (CE B H)) : Prop :=
  ∀ e ∈ T, ∃ e' ∈ O, e'.seq = e.seq ∧ e'.hash = e.hash

/-- The same check by position, which is what it means on valid chains. -/
def IGuard (T O : List (CE B H)) : Prop :=
  ∀ i (h : i < T.length), ∃ h' : i < O.length, O[i].hash = T[i].hash

theorem kguard_iguard (hf : Nat → H → B → H) {T O : List (CE B H)} {p : H} {n : Nat}
    (hT : Valid hf p n T) (hO : Valid hf p n O) (hg : KGuard T O) : IGuard T O := by
  intro i hi
  obtain ⟨e', he', hs, hh⟩ := hg T[i] (List.getElem_mem hi)
  obtain ⟨j, hj, rfl⟩ := List.getElem_of_mem he'
  have h1 := valid_seq hf O p n j hj hO
  have h2 := valid_seq hf T p n i hi hT
  have hij : j = i := by omega
  subst hij
  exact ⟨hj, hh⟩

theorem iguard_prefix (hf : Nat → H → B → H)
    (hinj : ∀ s1 p1 b1 s2 p2 b2, hf s1 p1 b1 = hf s2 p2 b2 → s1 = s2 ∧ p1 = p2 ∧ b1 = b2) :
    ∀ (T O : List (CE B H)) (p : H) (n : Nat),
      Valid hf p n T → Valid hf p n O → IGuard T O → T <+: O
  | [], _, _, _, _, _, _ => List.nil_prefix
  | t :: T, O, p, n, hT, hO, hg => by
    obtain ⟨h0, hh0⟩ := hg 0 (by simp)
    cases O with
    | nil => simp at h0
    | cons o O =>
      obtain ⟨ts, tp, th, hT'⟩ := hT
      obtain ⟨os, op, oh, hO'⟩ := hO
      simp only [List.getElem_cons_zero] at hh0
      rw [oh, th] at hh0
      obtain ⟨_, _, hb⟩ := hinj _ _ _ _ _ _ hh0
      have hto : t = o := by
        cases t; cases o; simp_all
      subst hto
      have hg' : IGuard T O := by
        intro i hi
        obtain ⟨h', hh⟩ := hg (i + 1) (by simp; omega)
        exact ⟨by simpa using h', by simpa using hh⟩
      exact List.cons_prefix_cons.2 ⟨rfl, iguard_prefix hf hinj T O _ _ hT' hO' hg'⟩

/-- **The guard is sound.** Origin's copy and the candidate both valid chains
from GENESIS, and fork.py's per-key comparison plus the rewind test find
nothing ⇒ origin's log is an event prefix of the candidate: publishing it can
neither fork nor rewind anyone's copy. -/
theorem guard_sound (hf : Nat → H → B → H)
    (hinj : ∀ s1 p1 b1 s2 p2 b2, hf s1 p1 b1 = hf s2 p2 b2 → s1 = s2 ∧ p1 = p2 ∧ b1 = b2)
    {T O : List (CE B H)} {g : H} (hT : Valid hf g 0 T) (hO : Valid hf g 0 O)
    (hk : KGuard T O) : T <+: O :=
  iguard_prefix hf hinj T O g 0 hT hO (kguard_iguard hf hT hO hk)

/-! ## 2. The system property over all pushers -/

/-- A repository snapshot: one log per writer file. -/
abbrev Snap (F B H : Type) := F → List (CE B H)

/-- What `publication_forks(repo, commit, ref) == []` establishes, given
origin's logs are valid (the invariant): per file, KGuard and a valid chain
(the chain check fires exactly when origin's copy was valid). -/
def SafePub (hf : Nat → H → B → H) (g : H) {F : Type} (o c : Snap F B H) : Prop :=
  ∀ f, KGuard (o f) (c f) ∧ Valid hf g 0 (c f)

/-- A push through a gate: FF-only on commits (never `--force`), and it lands
only if the gate passes. -/
def step {F : Type} (gate : Snap F B H → Snap F B H → Prop) [∀ o c, Decidable (gate o c)]
    (o c : Snap F B H) : Snap F B H :=
  if gate o c then c else o

/-- A trace: each push chooses its own pusher (gate) and candidate. -/
def run {F : Type} : Snap F B H →
    List ((g : Snap F B H → Snap F B H → Prop) × (∀ o c, Decidable (g o c)) × Snap F B H) →
    Snap F B H
  | o, [] => o
  | o, ⟨gate, dec, c⟩ :: rest => run (@step B H F gate dec o c) rest

/-- **NEVER PUSH A FORK, as a system property.** If every pusher's gate implies
`SafePub`, then from a valid origin every log on origin only ever extends:
the initial copy of each log is an event prefix of the final one, which stays
a valid chain. Holds for every interleaving of relay, fleet sync and rollout. -/
theorem never_push_a_fork (hf : Nat → H → B → H)
    (hinj : ∀ s1 p1 b1 s2 p2 b2, hf s1 p1 b1 = hf s2 p2 b2 → s1 = s2 ∧ p1 = p2 ∧ b1 = b2)
    (g : H) {F : Type} :
    ∀ (tr : List ((gt : Snap F B H → Snap F B H → Prop) × (∀ o c, Decidable (gt o c)) × Snap F B H))
      (o : Snap F B H),
      (∀ x ∈ tr, ∀ o c, x.1 o c → SafePub hf g o c) →
      (∀ f, Valid hf g 0 (o f)) →
      ∀ f, o f <+: run o tr f ∧ Valid hf g 0 (run o tr f)
  | [], o, _, hv, f => ⟨List.prefix_refl _, hv f⟩
  | ⟨gate, dec, c⟩ :: rest, o, hs, hv, f => by
    simp only [run, step]
    by_cases hg : gate o c
    · simp only [hg, ↓reduceIte]
      have hsafe := hs _ (List.mem_cons_self) o c hg
      have hvc : ∀ f, Valid hf g 0 (c f) := fun f => (hsafe f).2
      have ih := never_push_a_fork hf hinj g rest c (fun x hx => hs x (List.mem_cons_of_mem _ hx)) hvc f
      exact ⟨(guard_sound hf hinj (hv f) (hvc f) (hsafe f).1).trans ih.1, ih.2⟩
    · simp only [hg, ↓reduceIte]
      exact never_push_a_fork hf hinj g rest o (fun x hx => hs x (List.mem_cons_of_mem _ hx)) hv f

/-! ### The fixed pushers are gated by `SafePub`

Each gate is `SafePub ∧ extra`, where `extra` is whatever else the Python
checks (a clean tree, a converged pull, review gates). -/

def relayGate (hf : Nat → H → B → H) (g : H) {F} (extra : Snap F B H → Snap F B H → Prop)
    (o c : Snap F B H) : Prop := SafePub hf g o c ∧ extra o c
def fleetGate := @relayGate
def rolloutGate := @relayGate

theorem fixed_gates_sound (hf : Nat → H → B → H) (g : H) {F} (extra : Snap F B H → Snap F B H → Prop)
    (o c : Snap F B H) : relayGate hf g extra o c → SafePub hf g o c := And.left

/-! ## 3. Counterexamples: the pre-fix gates and the read-only pushers -/

/-- Concrete chains: body `Nat`, hash = `seq :: body :: prev` (injective). -/
def hfC : Nat → List Nat → Nat → List Nat := fun s p b => s :: b :: p
theorem hfC_inj : ∀ s1 p1 b1 s2 p2 b2, hfC s1 p1 b1 = hfC s2 p2 b2 → s1 = s2 ∧ p1 = p2 ∧ b1 = b2 := by
  intro s1 p1 b1 s2 p2 b2 h; simp [hfC] at h; exact ⟨h.1, h.2.2, h.2.1⟩

def mk (s : Nat) (p : List Nat) (b : Nat) : CE Nat (List Nat) := ⟨s, p, hfC s p b, b⟩
def e0 := mk 0 [] 10
def e1 := mk 1 e0.hash 11
def e2 := mk 2 e1.hash 12
def x2 := mk 2 e1.hash 99      -- a host that rewound to seq 1 and re-appended

/-- origin's copy -/
def originLog : List (CE Nat (List Nat)) := [e0, e1, e2]
/-- the host's rewritten copy: a VALID chain -/
def hostLog : List (CE Nat (List Nat)) := [e0, e1, x2]

theorem originLog_valid : Valid hfC [] 0 originLog :=
  ⟨rfl, rfl, rfl, rfl, rfl, rfl, rfl, rfl, rfl, trivial⟩
theorem hostLog_valid : Valid hfC [] 0 hostLog :=
  ⟨rfl, rfl, rfl, rfl, rfl, rfl, rfl, rfl, rfl, trivial⟩

theorem host_not_extension : ¬ originLog <+: hostLog := by
  rintro ⟨t, ht⟩
  have := congrArg (List.map CE.body) ht
  simp [originLog, hostLog, e0, e1, e2, x2, mk] at this

/-- Three-way merge of ONE file as git does it when a side left it alone
(the only case where git takes a whole side without a line merge). The
both-changed case is git's line merge, abstracted as `lm`. -/
def merge3 {α} [DecidableEq α] (lm : α → α → α → Option α) (base ours theirs : α) : Option α :=
  if ours = base then some theirs
  else if theirs = base then some ours
  else if ours = theirs then some ours
  else lm base ours theirs

/-- **"A merge is a union" is false for a rewritten log.** Local left the log
alone; the host rewrote it. The merge is clean, takes the host's copy whole,
and local's events are not all in the result (git_fleet_sync.py:289-290 and
git_relay's pre-fix comment "can only have combined two per-writer logs"). -/
theorem merge_rewrite_is_not_union (lm) :
    merge3 lm originLog originLog hostLog = some hostLog ∧ ¬ originLog <+: hostLog :=
  ⟨by simp [merge3], host_not_extension⟩

/-- The union claim holds exactly under its precondition: when each side only
APPENDED to the log, every clean one-sided merge contains both sides. -/
theorem merge_union_of_appends {α} [DecidableEq α]
    (base ours theirs r : List α) (ho : base <+: ours) (ht : base <+: theirs)
    (hm : merge3 (fun _ _ _ => none) base ours theirs = some r) : ours <+: r ∧ theirs <+: r := by
  unfold merge3 at hm
  by_cases h1 : ours = base
  · simp [h1] at hm; subst hm; subst h1; exact ⟨ht, List.prefix_refl _⟩
  · by_cases h2 : theirs = base
    · simp [h1, h2] at hm; subst hm; subst h2; exact ⟨List.prefix_refl _, ho⟩
    · by_cases h3 : ours = theirs
      · simp [h2, h3] at hm; subst hm; subst h3; exact ⟨List.prefix_refl _, List.prefix_refl _⟩
      · simp [h1, h2, h3] at hm

/-- Pre-fix relay gate: `ledger_forks` only (chain validity of the merged tree). -/
def relayGateOld (hf : Nat → H → B → H) (g : H) {F} (_o c : Snap F B H) : Prop :=
  ∀ f, Valid hf g 0 (c f)

/-- Pre-fix git_fleet_sync, and the read-only ledger_transport clean-merge path
and knowledge_commit: no ledger check at all. -/
def ungated {F} (_ _ : Snap F B H) : Prop := True

def snapO : Snap Unit Nat (List Nat) := fun _ => originLog
def snapH : Snap Unit Nat (List Nat) := fun _ => hostLog

/-- **Refuted for the pre-fix relay:** its chain-only check admits the host's
rewrite, and the push replaces origin's `(w,2)`. Replayed:
`test_relay_does_not_publish_a_host_rewrite` ("RELAYED 1 commit(s)"). -/
theorem relay_old_publishes_fork :
    relayGateOld hfC [] snapO snapH ∧ ¬ (snapO () <+: snapH ()) :=
  ⟨fun _ => hostLog_valid, host_not_extension⟩

/-- **Refuted for every ungated pusher** (pre-fix git_fleet_sync; ledger_transport's
clean-merge path and knowledge_commit, which are read-only here — NEEDS-OWNER). -/
theorem ungated_publishes_fork :
    ungated snapO snapH ∧ ¬ (snapO () <+: snapH ()) := ⟨trivial, host_not_extension⟩

/-- The fixed gate rejects that candidate. -/
theorem safepub_rejects_rewrite : ¬ SafePub hfC [] snapO snapH := by
  intro h
  exact host_not_extension (guard_sound hfC hfC_inj originLog_valid hostLog_valid (h ()).1)

/-! ### L9: every pusher is gated — `never_push_a_fork` over all five

Owner decision L9 (2026-09-23) gates the last two pushers with the same
`publication_forks` check: `ledger_transport._push_with_retry` on every
attempt (HEAD against `origin/<db>`), and `knowledge_commit._push_commit`
(`sha` against `origin/<branch>` and the freshly fetched destination) plus its
non-fast-forward integration (the merged tree it pushes). -/

def transportGate := @relayGate
def knowledgeGate := @relayGate

/-- Every path in the fleet that publishes a commit. -/
inductive Pusher | relay | fleet | rollout | transport | knowledge
  deriving DecidableEq

/-- Each pusher's gate as the Python runs it now: `SafePub ∧ extra`. -/
def gateOf (hf : Nat → H → B → H) (g : H) {F}
    (extra : Pusher → Snap F B H → Snap F B H → Prop) :
    Pusher → Snap F B H → Snap F B H → Prop
  | .relay => relayGate hf g (extra .relay)
  | .fleet => fleetGate hf g (extra .fleet)
  | .rollout => rolloutGate hf g (extra .rollout)
  | .transport => transportGate hf g (extra .transport)
  | .knowledge => knowledgeGate hf g (extra .knowledge)

theorem gateOf_sound (hf : Nat → H → B → H) (g : H) {F}
    (extra : Pusher → Snap F B H → Snap F B H → Prop) (p : Pusher) (o c : Snap F B H) :
    gateOf hf g extra p o c → SafePub hf g o c := by
  cases p <;> exact And.left

/-- **NEVER PUSH A FORK, for every pusher in the fleet.** Any interleaving of
relay, fleet sweep, rollout, ledger transport and knowledge publication, each
pushing any candidate it likes, only ever extends origin's logs, which stay
valid chains. (Before L9, `transport`/`knowledge` were `ungated`, and
`ungated_publishes_fork` is the counterexample.) -/
theorem never_push_a_fork_all_pushers (hf : Nat → H → B → H)
    (hinj : ∀ s1 p1 b1 s2 p2 b2, hf s1 p1 b1 = hf s2 p2 b2 → s1 = s2 ∧ p1 = p2 ∧ b1 = b2)
    (g : H) {F : Type} (extra : Pusher → Snap F B H → Snap F B H → Prop)
    (tr : List (Pusher × Snap F B H)) (o : Snap F B H) (hv : ∀ f, Valid hf g 0 (o f)) :
    ∀ f, o f <+: run o (tr.map fun x =>
        ⟨gateOf hf g extra x.1, fun _ _ => Classical.propDecidable _, x.2⟩) f ∧
      Valid hf g 0 (run o (tr.map fun x =>
        ⟨gateOf hf g extra x.1, fun _ _ => Classical.propDecidable _, x.2⟩) f) := by
  refine never_push_a_fork hf hinj g _ o ?_ hv
  intro x hx o' c' hgate
  obtain ⟨⟨p, c⟩, _, rfl⟩ := List.mem_map.mp hx
  exact gateOf_sound hf g extra p o' c' hgate

/-! ### The relay's refusal must not leave the merge on the branch -/

structure RelayOut (S : Type) where
  pushed : Option S
  branch : S

/-- Pre-fix: on refusal the merge stays on the branch ("Local history retained"). -/
def relayOld {S} (_pre merged : S) (ok : Bool) : RelayOut S :=
  if ok then ⟨some merged, merged⟩ else ⟨none, merged⟩

/-- Fixed (`_park`): on refusal the branch returns to `pre`; the merge is kept
under `refs/relay-refused/`, which no pusher publishes. -/
def relayNew {S} (pre merged : S) (ok : Bool) : RelayOut S :=
  if ok then ⟨some merged, merged⟩ else ⟨none, pre⟩

/-- git_fleet_sync publishes the branch tip (plus its own files, which do not
touch ledger logs here). -/
def fleetPushOld {S} (branch : S) : S := branch

/-- **The survey chain, refuted then fixed.** Relay refuses (`ok = false`), and
the next sweep publishes the branch: pre-fix that is the refused merge. -/
theorem survey_chain_old :
    (relayOld snapO snapH false).pushed = none ∧
    fleetPushOld (relayOld snapO snapH false).branch = snapH ∧ ¬ SafePub hfC [] snapO snapH :=
  ⟨rfl, rfl, safepub_rejects_rewrite⟩

theorem relay_refusal_restores {S} (pre merged : S) :
    (relayNew pre merged false).branch = pre ∧ (relayNew pre merged false).pushed = none :=
  ⟨rfl, rfl⟩

/-! ## 4. git_fleet_sync guards -/

/-- Where the git directory lives: `.git` is a directory, or (linked worktree,
submodule) a FILE pointing elsewhere. -/
inductive Layout | dir | file

structure Checkout where
  layout : Layout
  merging : Bool           -- MERGE_HEAD exists in the real git dir
  resolution : Nat         -- a human's in-progress hand resolution

/-- Pre-fix guard: `(repo / '.git' / 'MERGE_HEAD').exists()`. -/
def guardOld (c : Checkout) : Bool :=
  match c.layout with
  | .dir => c.merging
  | .file => false          -- repo/.git is a file: repo/.git/MERGE_HEAD never exists

/-- Fixed guard: `git rev-parse --git-path MERGE_HEAD` resolves the real dir. -/
def guardNew (c : Checkout) : Bool := c.merging

/-- `sync_repo(pull=True)`: guarded → skip; else a pull during a merge fails
and the failure path runs `git merge --abort` (resolution discarded → 0). -/
def syncPull (guard : Checkout → Bool) (c : Checkout) : Checkout :=
  if guard c then c
  else if c.merging then { c with merging := false, resolution := 0 }
  else c

theorem guard_old_misses_worktree :
    ∃ c : Checkout, c.merging = true ∧ guardOld c = false ∧
      (syncPull guardOld c).resolution = 0 ∧ c.resolution ≠ 0 :=
  ⟨⟨.file, true, 7⟩, rfl, rfl, rfl, by decide⟩

theorem guard_new_exact (c : Checkout) : guardNew c = true ↔ c.merging = true := Iff.rfl

theorem sync_new_keeps_resolution (c : Checkout) : syncPull guardNew c = c := by
  unfold syncPull guardNew; cases c.merging <;> simp

/-- **Continue-after-failed-pull (DOWNGRADED).** Pushes are FF-only
(`push_arguments` without a lease never forces), so on the commit graph origin
either stays or moves to a descendant; content-level forks are what SafePub
covers. `anc` is the ancestor relation. -/
theorem ff_push_monotone {C} (anc : C → C → Prop) (o c : C) [Decidable (anc o c)] :
    let o' := if anc o c then c else o
    o' = o ∨ anc o o' := by
  intro o'
  by_cases h : anc o c
  · right; simp [o', h]
  · left; simp [o', h]

/-- **"must NEVER propagate a deletion" — for the sweep's OWN commit (REFUTED
that it deletes).** `git commit -- <to_add>` takes each listed path's working
copy; a path lands in `to_add` only if its porcelain status has no `D`, and
such a path exists in the working tree (hypothesis `hex`). So every path the
parent tree had is still in the sweep's tree. -/
theorem sweep_never_deletes {P V : Type} [DecidableEq P]
    (parent work : P → Option V) (toAdd : List P)
    (hex : ∀ p ∈ toAdd, work p ≠ none) :
    let tree := fun p => if p ∈ toAdd then work p else parent p
    ∀ p, parent p ≠ none → tree p ≠ none := by
  intro tree p hp
  simp only [tree]
  split
  · exact hex p ‹_›
  · exact hp

/-- **Decision P2 (2026-09-23): refuse a range that deletes.** The push
publishes all of origin/<default>..HEAD, not only the sweep's commit. The range
is the chain of trees from origin's tip `t0` to HEAD; `git log --cc
--diff-filter=D` reports each commit's own deletions (a merge's only where the
result lacks a path a parent had and it did not just take the other side, so
a deletion merged in from origin is origin's, and not in the range). -/
def Deletes {P V : Type} (a b : P → Option V) (p : P) : Prop := a p ≠ none ∧ b p = none

/-- `range_deletions` is empty: no step of the chain deletes a path. -/
def rangeGate {P V : Type} : (P → Option V) → List (P → Option V) → Prop
  | _, [] => True
  | a, b :: rest => (∀ p, ¬ Deletes a b p) ∧ rangeGate b rest

/-- The tree the push publishes: the chain's last tree (HEAD). -/
def rangeTip {P V : Type} : (P → Option V) → List (P → Option V) → (P → Option V)
  | a, [] => a
  | _, b :: rest => rangeTip b rest

/-- With the P2 gate, every path origin has is still in what the push
publishes: no deletion reaches origin through the sweep. -/
theorem range_gate_keeps_origin_paths {P V : Type} (t0 : P → Option V)
    (chain : List (P → Option V)) (hg : rangeGate t0 chain) :
    ∀ p, t0 p ≠ none → rangeTip t0 chain p ≠ none := by
  induction chain generalizing t0 with
  | nil => intro p hp; exact hp
  | cons b rest ih =>
    intro p hp
    obtain ⟨hstep, hrest⟩ := hg
    apply ih b hrest p
    intro hb; exact hstep p ⟨hp, hb⟩

/-- Counterexample to the pre-P2 sweep (its own commit is clean, the range is
not; replayed: tests/test_decisions_sync.py): an earlier local commit removed
path 1, the sweep's commit only adds path 2, and the push would remove path 1
from origin. The P2 gate rejects the range. -/
def cexT0 : Nat → Option Nat := fun p => if p = 1 then some 7 else none
def cexT1 : Nat → Option Nat := fun _ => none
def cexT2 : Nat → Option Nat := fun p => if p = 2 then some 9 else none

theorem old_range_publishes_deletion :
    (∀ p, ¬ Deletes cexT1 cexT2 p) ∧ cexT0 1 ≠ none ∧ rangeTip cexT0 [cexT1, cexT2] 1 = none ∧
      ¬ rangeGate cexT0 [cexT1, cexT2] := by
  refine ⟨?_, by simp [cexT0], by simp [rangeTip, cexT2], ?_⟩
  · intro p ⟨h, _⟩; exact h (by simp [cexT1])
  · intro ⟨h, _⟩; exact h 1 ⟨by simp [cexT0], by simp [cexT1]⟩

/-! ### Q8: fetch before the deletion check; Q7: a refusal fails the run

Decision Q8 (2026-09-23): the range is `origin/<default>..HEAD` with
`origin/<default>` freshly fetched (`fetch_default`), with or without `--pull`.
`fetched = none` is a failed fetch: nothing to judge against, so the push is
refused. `fetched = some (t0, chain)`: `t0` is origin's tip as the fetch
returned it and `chain` the trees from there to HEAD. -/
def Q8Pass {P V : Type} : Option ((P → Option V) × List (P → Option V)) → Prop
  | none => False
  | some (t0, chain) => rangeGate t0 chain

/-- A failed fetch refuses the push. -/
theorem q8_fetch_failure_refuses {P V : Type} :
    ¬ Q8Pass (none : Option ((P → Option V) × List (P → Option V))) := id

/-- **A push that passes Q8 keeps every path origin has NOW** (the P2
guarantee, stated against the freshly fetched tip rather than a stale ref). -/
theorem q8_pass_sound {P V : Type} (t0 : P → Option V) (chain : List (P → Option V))
    (h : Q8Pass (some (t0, chain))) : ∀ p, t0 p ≠ none → rangeTip t0 chain p ≠ none :=
  range_gate_keeps_origin_paths t0 chain h

/-- Why fetch first: the verdict depends on which tip the range starts at.
Read the three trees as: `cexT0` = a STALE `origin/<default>` (has path 1),
`cexT1` = origin now (another machine deleted path 1; this checkout already
merged that by URL), `cexT2` = HEAD (adds path 2). Judged from the stale ref
the range "deletes" path 1, which origin already published: a false refusal.
Judged from the fetched tip it passes. Replayed:
tests/test_followups_sync.py::test_q8_a_stale_tracking_ref_does_not_refuse_origins_own_deletion. -/
theorem stale_ref_false_refusal :
    ¬ rangeGate cexT0 [cexT1, cexT2] ∧ Q8Pass (some (cexT1, [cexT2])) := by
  refine ⟨fun ⟨h, _⟩ => h 1 ⟨by simp [cexT0], by simp [cexT1]⟩, ?_⟩
  refine ⟨fun p ⟨h, _⟩ => h (by simp [cexT1]), trivial⟩

/-- `main`'s exit status, branch for branch: a fork or deletion refusal (a
failed Q8 fetch is recorded as one) fails the run before anything else is
consulted (decision Q7: keep exit 1). -/
def exitCode (forked deleting conflicts unrelated stuck : Bool) : Nat :=
  if forked || deleting then 1
  else if conflicts then 1
  else if unrelated then 1
  else if stuck then 1
  else 0

theorem deletion_refusal_fails_run (f c u st : Bool) : exitCode f true c u st = 1 := by
  simp [exitCode]

/-- Non-vacuity: a clean run exits 0. -/
theorem clean_run_exits_zero : exitCode false false false false false = 0 := rfl

/-! ## 5. state_loop_rollout: whose log does it append to? -/

structure Host where
  hostname : Nat
  declared : Option Nat

def actorOld (h : Host) : Nat := h.hostname
def actorNew (h : Host) : Nat := h.declared.getD h.hostname

/-- Each host appends its first event at seq 0 of `<actor>.jsonl`. Same actor
on two hosts ⇒ same `(actor, 0)` key with different contents: a fork. -/
theorem actor_old_forks :
    ∃ a b : Host, a ≠ b ∧ actorOld a = actorOld b ∧ actorNew a ≠ actorNew b :=
  ⟨⟨5, some 1⟩, ⟨5, some 2⟩, by simp, rfl, by decide⟩

theorem actor_new_distinct (a b : Host) (x y : Nat)
    (ha : a.declared = some x) (hb : b.declared = some y) (hxy : x ≠ y) :
    actorNew a ≠ actorNew b := by
  simp [actorNew, ha, hb, hxy]

/-- The residue, owned by actor_identity's policy: two UNDECLARED hosts with one
short hostname still share a writer (this_actor() falls back to the hostname). -/
theorem actor_new_residue : ∃ a b : Host, a ≠ b ∧ actorNew a = actorNew b :=
  ⟨⟨5, none⟩, ⟨5, some 5⟩, by simp, rfl⟩

/-! ### L10: the residue is closed at append

Owner decision L10 (2026-09-23): `EventLog.append` refuses an actor equal to
the host's short hostname unless the host declares an actor
(`actor_identity.this_actor(strict=True)`, via env, identity.env or the
registry). `actorNew` is what non-strict `this_actor()` returns, so a host can
append under it only when it declared it. -/

def mayAppend (h : Host) (actor : Nat) : Prop := h.declared.isSome ∨ actor ≠ h.hostname

/-- An append under `this_actor()` is only ever under a DECLARED actor. -/
theorem strict_no_guess (h : Host) (hm : mayAppend h (actorNew h)) :
    ∃ x, h.declared = some x ∧ actorNew h = x := by
  unfold mayAppend actorNew at *
  cases hd : h.declared with
  | some x => exact ⟨x, rfl, by simp⟩
  | none => simp [hd] at hm

/-- **The residue closed.** Two distinct hosts that both append under
`this_actor()` share a log only if they DECLARED the same actor — an operator's
explicit choice, never a hostname collision. -/
theorem strict_actor_no_residue (a b : Host)
    (ha : mayAppend a (actorNew a)) (hb : mayAppend b (actorNew b))
    (hab : actorNew a = actorNew b) : a.declared = b.declared := by
  obtain ⟨x, hx, hax⟩ := strict_no_guess a ha
  obtain ⟨y, hy, hby⟩ := strict_no_guess b hb
  rw [hx, hy, ← hax, ← hby, hab]

/-- `actor_new_residue`'s undeclared host is exactly the one refused. -/
theorem residue_refused : ¬ mayAppend ⟨5, none⟩ (actorNew ⟨5, none⟩) := by
  simp [mayAppend, actorNew]

/-! ### Q2: identity resolved strictly at startup (owner follow-up, 2026-09-23)

Every ledger writer's entry point (ledger_claim, ledger_cli append/approve,
ledger_seal emit, ledger_ingest_org, ledger_phase1_prepare, the repair tools,
nightshift run.py, and the library callers the adapter/executor/cos use)
resolves its actor ONCE, before any work: an explicit `--actor` wins (so does
DATACORE_ACTOR, which `resolve` folds into `declared`), else
`this_actor(strict=True)`, which is `declared` or a refusal. `none` = the entry
point exits (2) naming identity.env and the registry, having written nothing.
As found (`runOld`) it resolved non-strictly and every event carried
`actorNew`, which L10 then refused one append at a time. -/

def startupActor (h : Host) (explicit : Option Nat) : Option Nat :=
  match explicit with
  | some a => some a
  | none => h.declared

/-- The events an entry point writes for `work`: all under the startup actor, or
none at all when startup refused. -/
def runEntry {α : Type} (h : Host) (explicit : Option Nat) (work : List α) : List (Nat × α) :=
  match startupActor h explicit with
  | some a => work.map (a, ·)
  | none => []

/-- As found: resolve non-strictly (hostname fallback) and find out at append. -/
def runOld {α : Type} (h : Host) (work : List α) : List (Nat × α) := work.map (actorNew h, ·)

/-- An explicit actor is the caller's choice and is never resolved. -/
theorem explicit_wins (h : Host) (a : Nat) : startupActor h (some a) = some a := rfl

/-- Startup refuses exactly the undeclared hosts. -/
theorem startup_resolves_or_refuses (h : Host) :
    startupActor h none = none ↔ h.declared = none := by
  simp [startupActor]

/-- Never a guess: the startup actor is the explicit one or the declared one. -/
theorem startup_never_guesses (h : Host) (e : Option Nat) (a : Nat)
    (hs : startupActor h e = some a) : e = some a ∨ h.declared = some a := by
  cases e with
  | some x => exact Or.inl hs
  | none => exact Or.inr hs

/-- A refused startup writes nothing. -/
theorem refused_writes_nothing {α : Type} (h : Host) (w : List α) (hd : h.declared = none) :
    runEntry h none w = [] := by
  simp [runEntry, startupActor, hd]

/-- **Fail fast, not late.** Every event an entry point writes under its resolved
actor passes the L10 append check: no append can fail on identity after work
has started. -/
theorem no_late_identity_failure {α : Type} (h : Host) (w : List α) :
    ∀ p ∈ runEntry h none w, mayAppend h p.1 := by
  intro p hp
  unfold runEntry startupActor at hp
  cases hd : h.declared with
  | none => simp [hd] at hp
  | some x =>
    simp [hd] at hp
    obtain ⟨b, _, rfl⟩ := hp
    exact Or.inl (by simp [hd])

/-- As found: an undeclared host starts its work and every append then fails. -/
theorem late_failure_as_found :
    ∃ p ∈ runOld ⟨5, none⟩ [()], ¬ mayAppend ⟨5, none⟩ p.1 :=
  ⟨(5, ()), by simp [runOld, actorNew], by simp [mayAppend]⟩

/-! ## 6. cron_install.reconcile -/

section Cron
variable {L K : Type}

/-- `reconcile` over lines already split at '\n': keep the lines the
predicate keeps (comments, unmanaged jobs), in order, then emit each managed
entry. `keep` abstracts the marker/signature/retire tests; `emit k` is
`line.rstrip() + ' # datacore-job:' + k`. -/
def reconcile (keep : L → Bool) (emit : K → L) (keys : List K) (cur : List L) : List L :=
  cur.filter keep ++ keys.map emit

/-- **Idempotent**, given only that an emitted managed line is never kept (its
marker names a key in `entries`). -/
theorem reconcile_idempotent (keep : L → Bool) (emit : K → L) (keys : List K)
    (hemit : ∀ k ∈ keys, keep (emit k) = false) (cur : List L) :
    reconcile keep emit keys (reconcile keep emit keys cur) = reconcile keep emit keys cur := by
  unfold reconcile
  rw [List.filter_append, List.filter_filter]
  have : (keys.map emit).filter keep = [] := by
    rw [List.filter_eq_nil_iff]
    intro l hl
    obtain ⟨k, hk, rfl⟩ := List.mem_map.1 hl
    simp [hemit k hk]
  rw [this]; simp

/-- **Unmanaged lines are preserved verbatim, in order**: the kept lines are an
exact prefix of the output. -/
theorem reconcile_preserves_unmanaged (keep : L → Bool) (emit : K → L) (keys : List K)
    (cur : List L) : cur.filter keep <+: reconcile keep emit keys cur :=
  List.prefix_append _ _

/-- **The splitting defect (fixed).** Python's `str.splitlines` also splits at
`\x0c`, `\x85`, ` `, …; cron splits only at `\n`. A cron line `a ⧺ sep ⧺ b`
was judged as two fragments; a fragment `b` that looks like a managed job was
dropped from the middle of the unmanaged line. Modelled on fragments: the
output no longer contains the original line. -/
theorem split_fragments_drop (keep : L → Bool) (emit : K → L) (keys : List K)
    (a b : L) (ha : keep a = true) (hb : keep b = false) :
    reconcile keep emit keys [a, b] = a :: keys.map emit := by
  simp [reconcile, ha, hb]

end Cron

/-! ## 7. git_branch_hygiene.landed_earlier -/

/-- Trunk history as blobs of one path at commits `0..tip`; the branch forked
at `base`. -/
def landedOld (blob : Nat → Option Nat) (tip : Nat) (want : Nat) : Prop :=
  ∃ i ≤ tip, blob i = some want
def landedNew (blob : Nat → Option Nat) (base tip : Nat) (want : Nat) : Prop :=
  ∃ i, base < i ∧ i ≤ tip ∧ blob i = some want

/-- The revert: trunk v1 (commit 0) → v2 (commit 1 = fork point) → v3 (commit 2);
the branch reverts to v1. Pre-fix: "landed" (→ `built-on`, safe to delete).
Fixed: not landed (→ `outstanding`). Replayed:
`test_branch_reverting_to_old_content_is_outstanding`. -/
def revBlob : Nat → Option Nat
  | 0 => some 1 | 1 => some 2 | 2 => some 3 | _ => none

theorem hygiene_revert : landedOld revBlob 2 1 ∧ ¬ landedNew revBlob 1 2 1 := by
  refine ⟨⟨0, by omega, rfl⟩, ?_⟩
  rintro ⟨i, h1, h2, h3⟩
  have : i = 2 := by omega
  subst this; simp [revBlob] at h3

/-- Fixed verdict is sound: `built-on` now means the trunk held the branch's
blob at a commit after the fork. -/
theorem hygiene_new_sound (blob : Nat → Option Nat) (base tip want : Nat)
    (h : landedNew blob base tip want) : ∃ i, base < i ∧ blob i = some want := by
  obtain ⟨i, h1, _, h3⟩ := h; exact ⟨i, h1, h3⟩

/-! ## 8. visitor_join: one join at a time -/

/-- Events: a tick that finds the join due starts one; a join ends. -/
inductive JEv | start | finish

/-- Pre-fix: `due` consults the attempt marker, written only at the END, so
during a join a start is admitted (running counts joins in flight). -/
def jOld : Nat → JEv → Nat
  | n, .start => n + 1
  | n, .finish => n - 1

/-- Fixed: `_exclusive` — a start while one is running is refused. -/
def jNew : Nat → JEv → Nat
  | 0, .start => 1
  | n, .start => n
  | n, .finish => n - 1

theorem join_overlap_old : [JEv.start, .start].foldl jOld 0 = 2 := rfl

theorem join_mutual_exclusion : ∀ (tr : List JEv) (n : Nat), n ≤ 1 → tr.foldl jNew n ≤ 1
  | [], _, h => h
  | e :: tr, n, h => by
    apply join_mutual_exclusion tr
    cases e <;> cases n <;> simp [jNew] <;> omega

end DatacoreSpec.GitFleet
