/-!
# Item lifecycle — the per-item state machine in `ledger/fold.py`

One `step` per handler (`_handle_claim`, `_handle_grant`, ...), following the
Python control flow branch for branch, including the edit-conflict gate in
`_get_item_or_orphan` and the conditional-edit paths through `ledger/edits.py`.

What is abstracted, and why that is safe for the properties below:

* The item payload is an opaque type `P`; how fields merge is an oracle
  `upd`. None of the lifecycle properties depend on payload contents.
* Whether a conditional edit's precondition holds (`edits.apply_condition`
  returns instead of raising) is an oracle `condOk : Item P → Cond → Bool`.
  It sees the whole item, as the Python does. Theorems that hold "for every
  oracle" therefore hold whatever payloads and preconditions arrive.
* `edit_conflicts` is a dict keyed by event hash; modelled as a list of keys.
* Events are already resolved to their item: an event naming no known item goes
  to `orphans` in Python and never reaches a handler (see `Fold.lean` for the
  frame property that makes per-item reasoning sound).
-/

namespace LedgerSpec.Item

abbrev Actor := String
abbrev Hash := String

inductive Status
  | created | claimed | completed | verified | dismissed
  deriving DecidableEq, Repr

structure Item (P : Type) where
  status    : Status
  owner     : Option Actor
  grantedBy : Option Actor
  /-- Ghost state (not in Python): whose claim the grant was minted for. Lets
  the model state "a grant belongs to the current owner's claim". -/
  grantedTo : Option Actor
  closedAt  : Option String
  conflicts : List Hash
  payload   : P

/-- The `_merge` block of a conditional edit, as far as control flow sees it. -/
structure Cond where
  terminal : Bool
  resolves : List Hash

inductive Kind (F : Type)
  | claim | grant | release | complete | verify
  | update (fields : F) (cond : Option Cond)
  | dismiss (cond : Option Cond)
  | ownerSet (owner : Option Actor)

structure Ev (F : Type) where
  actor : Actor
  hlc   : String
  hash  : Hash
  kind  : Kind F

/-- Everything the fold depends on that is about payload contents. -/
structure Oracle (P F : Type) where
  condOk   : Item P → Cond → Bool
  nonempty : F → Bool
  upd      : P → F → Option Cond → P

variable {P F : Type}

def addConflict (h : Hash) (l : List Hash) : List Hash := h :: l.filter (· ≠ h)
def resolve (c : Cond) (l : List Hash) : List Hash := l.filter (· ∉ c.resolves)

/-- `_get_item_or_orphan`'s gate: these kinds are refused while edit conflicts
are unresolved. -/
def Kind.gated : Kind F → Bool
  | .claim | .grant | .complete | .verify => true
  | _ => false

def dismissNow (i : Item P) (e : Ev F) : Item P :=
  { i with status := .dismissed, closedAt := some e.hlc }

def clearGrant (i : Item P) : Item P := { i with grantedBy := none, grantedTo := none }

/-- One application of the matching `_handle_*` (post-fix semantics). -/
def step (o : Oracle P F) (i : Item P) (e : Ev F) : Item P :=
  if e.kind.gated ∧ i.conflicts ≠ [] then i else
  match e.kind with
  | .claim =>
    if i.status = .dismissed then i
    else if i.status ≠ .created then i
    else { i with owner := some e.actor, status := .claimed }
  | .grant =>
    if i.status = .dismissed then i
    else if i.status ≠ .claimed then i
    else if i.grantedBy.isSome then i
    else { i with grantedBy := some e.actor, grantedTo := i.owner }
  | .release =>
    if i.status = .dismissed then i
    else if i.owner ≠ some e.actor then i
    else if i.status ≠ .claimed then i
    else clearGrant { i with owner := none, status := .created }
  | .complete =>
    if i.status = .dismissed then i
    else if i.status ≠ .claimed then i
    else if i.owner ≠ some e.actor then i
    else { i with status := .completed, closedAt := some e.hlc }
  | .verify =>
    if i.status = .dismissed then i
    else if i.status ≠ .completed then i
    else if i.owner = some e.actor then i          -- completer cannot verify
    else { i with status := .verified }
  | .update f (some c) =>
    -- update_payload: apply_condition, then refuse a terminal precondition,
    -- then refuse a dismissed item. Any refusal lands in edit_conflicts and
    -- the handler returns WITHOUT reaching `_dismissed` (by design: this is
    -- how a conditional edit racing a dismissal stays visible).
    if o.condOk i c ∧ c.terminal = false ∧ i.status ≠ .dismissed then
      let i' := { i with conflicts := resolve c i.conflicts }
      if o.nonempty f then { i' with payload := o.upd i.payload f (some c) } else i'
    else { i with conflicts := addConflict e.hash i.conflicts }
  | .update f none =>
    if i.status = .dismissed then i
    else if o.nonempty f then { i with payload := o.upd i.payload f none } else i
  | .dismiss (some c) =>
    -- Precondition and `resolves` first: the route out of a conflict retained
    -- on a dismissed item (ledger_resolve_conflict.py).
    if c.terminal ∧ o.condOk i c then
      let i' := { i with conflicts := resolve c i.conflicts }
      if i'.status = .dismissed then i' else dismissNow i' e
    else { i with conflicts := addConflict e.hash i.conflicts }
  | .dismiss none =>
    if i.status = .dismissed then i else dismissNow i e
  | .ownerSet ow =>
    if i.status = .dismissed then i
    else if ow = i.owner then i
    else clearGrant { i with owner := ow }

/-- The history line each handler writes, reduced to its verdict. -/
inductive Note | applied | noop | conflict
  deriving DecidableEq

/-- `_note(...)`'s verdict, branch for branch with `step` (post-fix). -/
def note (o : Oracle P F) (i : Item P) (e : Ev F) : Note :=
  if e.kind.gated ∧ i.conflicts ≠ [] then .noop else
  match e.kind with
  | .claim => if i.status = .dismissed ∨ i.status ≠ .created then .noop else .applied
  | .grant =>
    if i.status = .dismissed ∨ i.status ≠ .claimed ∨ i.grantedBy.isSome then .noop else .applied
  | .release =>
    if i.status = .dismissed ∨ i.owner ≠ some e.actor ∨ i.status ≠ .claimed then .noop else .applied
  | .complete =>
    if i.status = .dismissed ∨ i.status ≠ .claimed ∨ i.owner ≠ some e.actor then .noop else .applied
  | .verify =>
    if i.status = .dismissed ∨ i.status ≠ .completed ∨ i.owner = some e.actor then .noop
    else .applied
  | .update f (some c) =>
    if o.condOk i c ∧ c.terminal = false ∧ i.status ≠ .dismissed then
      -- "no-op (no fields)" only when nothing was reconciled either
      if o.nonempty f ∨ resolve c i.conflicts ≠ i.conflicts then .applied else .noop
    else .conflict
  | .update f none => if i.status = .dismissed ∨ o.nonempty f = false then .noop else .applied
  | .dismiss (some c) =>
    if c.terminal ∧ o.condOk i c then
      if i.status = .dismissed ∧ resolve c i.conflicts = i.conflicts then .noop else .applied
    else .conflict
  | .dismiss none => if i.status = .dismissed then .noop else .applied
  | .ownerSet ow => if i.status = .dismissed ∨ ow = i.owner then .noop else .applied

/-! ## 1. Status moves only along the intended edges -/

/-- The lifecycle graph from the `fold.py` docstring: created → claimed,
claimed → created (release = un-claim), claimed → completed, completed →
verified, anything → dismissed, plus staying put. Nothing else. -/
def edge : Status → Status → Bool
  | .created,   .claimed   => true
  | .claimed,   .created   => true
  | .claimed,   .completed => true
  | .completed, .verified  => true
  | _,          .dismissed => true
  | s, t => s == t

@[simp] theorem edge_refl (s : Status) : edge s s = true := by cases s <;> rfl
@[simp] theorem edge_dismissed (s : Status) : edge s .dismissed = true := by cases s <;> rfl

theorem step_edge (o : Oracle P F) (i : Item P) (e : Ev F) :
    edge i.status (step o i e).status = true := by
  unfold step
  split
  · simp
  · cases e.kind <;> simp only <;> (repeat' split) <;> (try simp_all [dismissNow, clearGrant]) <;> decide

/-- Closed work never reopens: once completed, verified or dismissed, no event
returns an item to `created` or `claimed`. -/
def Status.closed : Status → Bool
  | .completed | .verified | .dismissed => true
  | _ => false

theorem edge_closed {s t : Status} (h : edge s t = true) (hs : s.closed = true) :
    t.closed = true := by
  cases s <;> cases t <;> simp_all [edge, Status.closed]

def run (o : Oracle P F) (i : Item P) (es : List (Ev F)) : Item P := es.foldl (step o) i

theorem run_closed (o : Oracle P F) (es : List (Ev F)) :
    ∀ (i : Item P), i.status.closed = true → (run o i es).status.closed = true := by
  induction es with
  | nil => intro i h; exact h
  | cons e es ih => intro i h; exact ih _ (edge_closed (step_edge o i e) h)

/-! ## 2. Dismissal is terminal; history never lies -/

/-- Status, owner, grant, closing stamp and payload are frozen once an item is
dismissed. -/
theorem dismissed_frozen (o : Oracle P F) (i : Item P) (e : Ev F)
    (h : i.status = .dismissed) :
    let j := step o i e
    j.status = .dismissed ∧ j.owner = i.owner ∧ j.grantedBy = i.grantedBy ∧
      j.closedAt = i.closedAt ∧ j.payload = i.payload := by
  unfold step
  split
  · simp [h]
  · cases e.kind <;> simp only <;> (repeat' split) <;> simp_all [clearGrant]

/-- **Honest history (fixes finding 1).** Whenever the fold writes a "no-op"
line, the item is exactly unchanged. Before the fix this failed on two
branches: a reconciling `item.dismiss` of a dismissed item, and a field-less
conditional `item.update` — both deleted conflicts and logged "no-op". -/
theorem noop_means_unchanged (o : Oracle P F) (i : Item P) (e : Ev F)
    (h : note o i e = .noop) : step o i e = i := by
  revert h
  unfold note step
  split
  · intro; rfl
  · cases e.kind <;> simp only <;> (repeat' split) <;> intro h <;>
      first | rfl | (simp_all; done) | (cases i; simp_all [resolve])

/-- The precise rule for `edit_conflicts` on a dismissed item: it changes only
through a conditional edit (recorded as `conflict`) or a reconciling dismiss
(recorded as `applied`), never silently. -/
theorem dismissed_conflict_rule (o : Oracle P F) (i : Item P) (e : Ev F)
    (hs : i.status = .dismissed) (hc : (step o i e).conflicts ≠ i.conflicts) :
    note o i e ≠ .noop := by
  intro hn; exact hc (by rw [noop_means_unchanged o i e hn])

/-- A conditional update on a dismissed item is recorded, for every oracle. -/
theorem dismissed_update_records_conflict (o : Oracle P F) (i : Item P) (e : Ev F)
    (f : F) (c : Cond) (hs : i.status = .dismissed) (hk : e.kind = .update f (some c)) :
    (step o i e).conflicts = addConflict e.hash i.conflicts ∧ note o i e = .conflict := by
  simp [step, note, hk, Kind.gated, hs]

/-! ## 3. Completion and verification -/

/-- Only the current owner of a claimed item can complete it. -/
theorem completed_only_by_owner (o : Oracle P F) (i : Item P) (e : Ev F)
    (hbefore : i.status ≠ .completed) (hafter : (step o i e).status = .completed) :
    i.status = .claimed ∧ i.owner = some e.actor ∧ i.conflicts = [] := by
  revert hafter
  unfold step
  split
  · intro h; exact absurd h hbefore
  · rename_i hgate
    cases hk : e.kind <;> simp only <;> (repeat' split) <;>
      simp_all [dismissNow, Kind.gated, clearGrant]

/-- **Separation of duties (fixes finding 2).** An item becomes `verified` only
by an actor other than its owner — the owner being the actor who completed it. -/
theorem verify_needs_second_actor (o : Oracle P F) (i : Item P) (e : Ev F)
    (hbefore : i.status ≠ .verified) (hafter : (step o i e).status = .verified) :
    i.status = .completed ∧ i.owner ≠ some e.actor := by
  revert hafter
  unfold step
  split
  · intro h; exact absurd h hbefore
  · cases hk : e.kind <;> simp only <;> (repeat' split) <;>
      simp_all [dismissNow, clearGrant]

/-- Composed with `completed_only_by_owner`: along any trace, whoever completed
an item is not whoever verifies it — unless an `owner.set` override moved
ownership in between, which is the one residue (an admin act, recorded). -/
theorem completer_is_not_verifier (o : Oracle P F) (i : Item P) (e1 e2 : Ev F)
    (h1 : i.status = .claimed) (hc : (step o i e1).status = .completed)
    (hv : (step o (step o i e1) e2).status = .verified) : e1.actor ≠ e2.actor := by
  have ⟨_, hown, _⟩ := completed_only_by_owner o i e1 (by simp [h1]) hc
  have ⟨_, hne⟩ := verify_needs_second_actor o _ e2 (by simp [hc]) hv
  -- completing does not change the owner
  have hsame : (step o i e1).owner = i.owner := by
    revert hc; unfold step; split
    · intro; rfl
    · cases e1.kind <;> simp only <;> (repeat' split) <;> simp_all [dismissNow, clearGrant]
  intro heq; apply hne; rw [hsame, hown, heq]

/-! ## 4. The edit-conflict gate -/

/-- While conflicts are unresolved, no item makes progress: its status can only
stay put, be released back to `created`, or be dismissed. -/
theorem conflict_gate (o : Oracle P F) (i : Item P) (e : Ev F) (h : i.conflicts ≠ []) :
    let t := (step o i e).status
    t = i.status ∨ t = .created ∨ t = .dismissed := by
  unfold step
  split
  · simp
  · rename_i hgate
    cases hk : e.kind <;> simp_all [Kind.gated] <;> (repeat' split) <;>
      simp_all [dismissNow, clearGrant]

/-! ## 5. Grants -/

/-- The grant invariant: a recorded grant was minted for the CURRENT owner, and
the item has been claimed. -/
def GrantOk (i : Item P) : Prop :=
  i.grantedBy.isSome → i.grantedTo = i.owner ∧ i.status ≠ .created

/-- **A grant belongs to the claim it was granted for (fixes finding 3).**
Preserved by every event. Before the fix, `release` and a reassigning
`owner.set` broke it. -/
theorem grant_belongs_to_claim (o : Oracle P F) (i : Item P) (e : Ev F)
    (h : GrantOk i) : GrantOk (step o i e) := by
  unfold GrantOk at *
  unfold step
  split
  · exact h
  · cases hk : e.kind <;> simp only <;> (repeat' split) <;>
      simp_all [dismissNow, clearGrant]

def fresh (P : Type) (p : P) : Item P :=
  { status := .created, owner := none, grantedBy := none, grantedTo := none,
    closedAt := none, conflicts := [], payload := p }

def ev {F : Type} (a : Actor) (k : Kind F) : Ev F := { actor := a, hlc := "", hash := a, kind := k }

/-- The finding-3 trace now ends ungranted, and the new claimant can be granted. -/
theorem regrant_after_release (o : Oracle Unit F) :
    let j := run o (fresh Unit ()) [ev "a" .claim, ev "approver" .grant,
                                    ev "a" .release, ev "b" .claim]
    j.owner = some "b" ∧ j.grantedBy = none ∧
      (step o j (ev "approver" .grant)).grantedTo = some "b" := by
  simp [run, step, fresh, ev, Kind.gated, clearGrant]

/-! ## 6. Spend is conserved: it only accumulates -/

/-- `_handle_spend`: invalid (negative) cents are orphaned, never applied. -/
def spendStep (spend : Actor → Int) (actor : Actor) (cents : Int) : Actor → Int :=
  if cents < 0 then spend else fun a => if a = actor then spend a + cents else spend a

theorem spend_monotone (spend : Actor → Int) (actor : Actor) (cents : Int) (a : Actor) :
    spend a ≤ spendStep spend actor cents a := by
  unfold spendStep
  split
  · exact Int.le_refl _
  · simp only; split <;> omega

end LedgerSpec.Item
