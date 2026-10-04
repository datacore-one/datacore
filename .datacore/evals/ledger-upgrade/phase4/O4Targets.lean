import DatacoreSpec.Projector

/-!
# O4 targets — the Phase 4 projector guarantees, stated against the model

Ledger upgrade Phase 4, eval O4 (PLAN.md). Compiled by `test_o4_projector_formal.py`
with `lake env lean` inside `.datacore/specs/datacore-lean`. It lives here, not in
`DatacoreSpec/`, so the formal gap check of the model library stays clean while
these statements are false. It is an EVAL: the implementer of Phase 4 changes the
model (`DatacoreSpec/Projector.lean`) and proves the three named theorems there;
this file changes only with the owner's approval.

RE-STATED 2026-10-04 for owner decision 8 ("one door in"): an edit to a view is
applied to the ledger when plain, and captured (held as an inbox entry) when
unclear; either way it never blocks another item.

Green when this file compiles with no error:
  (1) `render_idempotent`        rendering after fold is a fixed point, for every space;
  (2) `render_conflict_isolated` a recorded conflict never blocks rendering another item;
  (3) `ingest_conflict_isolated` whatever happens to one item's edit (applied, captured or
                                 unclear), every other task the ledger already knows ends
                                 up exactly as its own edit alone would leave it;
  (4) the bounded model check of (2) and (3) over every two-item space.

(3) is about tasks the ledger already knows (`j ∈ l.map (·.id)`). The stage-1
statement had no such hypothesis and was false for ANY design that admits new
headings: for a brand-new heading `j`, "only j's edit" is the unchanged base,
while the full file admits it.
-/

namespace O4
open DatacoreSpec.Projector

/-- `base` with only item `j`'s heading taken from `file` (dropped if `file` lacks it). -/
def onlyEdit (base file : File) (j : Nat) : File :=
  match lookupH file j with
  | some h => base.map (fun g => if g.id == j then h else g)
  | none => base.filter (fun g => g.id != j)

/-! ## The general theorems (proved in the model by Phase 4) -/

theorem idempotent : ∀ (l : Ledger) (f : File),
    distinctIds l = true → render l = some f → cycle f f l = some f :=
  render_idempotent

theorem isolated_render : ∀ (l : Ledger) (j : Item),
    distinctIds l = true → j ∈ l → j.conflict = false → ∃ f, render l = some f ∧ j.heading ∈ f :=
  render_conflict_isolated

theorem isolated_ingest : ∀ (l : Ledger) (base file : File) (j : Nat) (l1 : Ledger),
    distinctIds l = true → render l = some base → j ∈ l.map (·.id) →
    ingest base (onlyEdit base file j) l = some l1 →
    ∃ l2, ingest base file l = some l2 ∧ lookupI l2 j = lookupI l1 j :=
  ingest_conflict_isolated

/-! ## Bounded model check: every space of two items, every per-heading edit -/

def flip : St → St
  | .live => .closed
  | .closed => .live

def items (id : Nat) : List Item :=
  [0, 1].flatMap fun t => [St.live, St.closed].flatMap fun s => [false, true].map fun c => ⟨id, t, s, c⟩

def ledgers : List Ledger := (items 0).flatMap fun a => (items 1).map fun b => [a, b]

/-- Keep, retitle, flip open/closed (close or reopen), or remove the heading. -/
def edits (h : Heading) : List (Option Heading) :=
  [some h, some { h with title := 1 - h.title }, some { h with st := flip h.st }, none]

def files : File → List File
  | [h0, h1] => (edits h0).flatMap fun e0 => (edits h1).map fun e1 => [e0, e1].filterMap id
  | _ => []

def idempotentBounded : Bool :=
  ledgers.all fun l => (render l).all fun f => cycle f f l == some f

def renderIsolatedBounded : Bool :=
  ledgers.all fun l => l.all fun j => j.conflict || (render l).any (fun f => f.contains j.heading)

def ingestIsolatedBounded : Bool :=
  ledgers.all fun l => (render l).all fun base => (files base).all fun file => [0, 1].all fun j =>
    match ingest base (onlyEdit base file j) l with
    | none => true
    | some l1 => (ingest base file l).any fun l2 => lookupI l2 j == lookupI l1 j

example : idempotentBounded = true := by decide
example : renderIsolatedBounded = true := by decide
example : ingestIsolatedBounded = true := by decide

end O4
