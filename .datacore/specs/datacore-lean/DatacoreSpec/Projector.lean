/-!
# Projector — the Phase-1 org round-trip of one space (ledger upgrade O4)

A Phase-1 space renders `org/next_actions.org` from the folded ledger and takes
human edits back in on the next cycle. This file models that loop **as the code
behaves today** (2026-10-04), so the two Phase 4 guarantees can be checked
against it:

* **Idempotence** — rendering after fold is a fixed point: re-ingesting an
  unedited projection changes nothing and renders the same file.
* **Isolation** — a recorded conflict never blocks other items: neither the
  render nor the ingest of the rest of the space may depend on one item's
  conflict.

| Python | Model |
|---|---|
| `ledger/projector.py` `project` — refuses if ANY item has `edit_conflicts` (l. 462-465); items ordered by id (l. 493) | `render` |
| `ledger/projection_state.py` `sync_generated` — three-way title merge, "concurrent edit" (via `edits.merge_values`), "edit to a terminal item", "removed heading needs explicit archive/deletion evidence" | `step`, `ingest` |
| `ledger_ingest_org.ensure_ids` / `SafeOrgWorkspace` — duplicate `:ID:` refuses | `ingest` (first guard) |
| `ledger/genesis.import_space` — a new heading is admitted, closed ones as closed (ffd08f0) | `ingest` (admission) |

Every refusal in `ingest` is `none` for the WHOLE space: that is the cycle's
per-space `FAILED:` line, after which the space is not projected
(`ledger_phase1_cycle.sh`). The deterministic replay of the same behaviour
against the real code is `.datacore/lib/tests/ledger_upgrade/` (O1-O3).

Abstracted: titles, bodies, properties and tags are one value per item
(`title`); the retention window for closed items is not modelled (closed items
render while they are in it); the projection base is the last rendered file.

The target theorems are NOT stated here as `sorry`. They are stated by the O4
eval (`.datacore/lib/tests/ledger_upgrade/test_o4_projector_formal.py`) against
this model, so the formal gap check stays clean while they are false. What this
file proves is where today's behaviour violates them (§3).
-/

namespace DatacoreSpec.Projector

inductive St | live | closed
  deriving DecidableEq, Repr

/-- A folded ledger item. `conflict` is a retained edit conflict (`edit_conflicts` non-empty). -/
structure Item where
  id : Nat
  title : Nat
  st : St
  conflict : Bool
  deriving DecidableEq, Repr

/-- One heading of the rendered file. -/
structure Heading where
  id : Nat
  title : Nat
  st : St
  deriving DecidableEq, Repr

abbrev Ledger := List Item
abbrev File := List Heading

def Item.heading (i : Item) : Heading := ⟨i.id, i.title, i.st⟩

/-! ## 1. Render -/

def insertById (h : Heading) : File → File
  | [] => [h]
  | x :: xs => if h.id ≤ x.id then h :: x :: xs else x :: insertById h xs

/-- `bucket.sort(key=lambda i: i.id)`: the file's order is the ids' order, whatever the human typed. -/
def sortById (f : File) : File := f.foldr insertById []

/-- `projector.project`: any retained conflict refuses the whole projection. -/
def render (l : Ledger) : Option File :=
  if l.any (·.conflict) then none else some (sortById (l.map Item.heading))

/-! ## 2. Ingest -/

def lookupH (f : File) (id : Nat) : Option Heading := f.find? (·.id == id)
def lookupI (l : Ledger) (id : Nat) : Option Item := l.find? (·.id == id)

def nodupB : List Nat → Bool
  | [] => true
  | x :: xs => !xs.contains x && nodupB xs

def distinctIds (l : Ledger) : Bool := nodupB (l.map (·.id))

/-- `sync_generated` for one heading that the ledger knows. `none` = refused. -/
def step (base : Option Heading) (h : Heading) (i : Item) : Option Item :=
  let b := (base.map (·.title)).getD i.title
  let title? : Option Nat :=
    if h.title == b then some i.title                       -- unchanged in the file: the ledger's value stands
    else if i.title == b || i.title == h.title then some h.title   -- only the file changed
    else none                                                -- both changed: "concurrent edit at …title"
  match title? with
  | none => none
  | some t =>
    match i.st, h.st with
    | .closed, .closed => if t == i.title then some i else none   -- "edit to a terminal item"
    | .closed, .live => none                                       -- reopen: "edit to a terminal item"
    | .live, s => some { i with title := t, st := s }              -- edit, or close (item.dismiss)

/-- One ingest of the whole space. Any single refusal refuses the space. -/
def ingest (base file : File) (l : Ledger) : Option Ledger :=
  if !nodupB (file.map (·.id)) then none                     -- duplicate :ID: (ensure_ids)
  else if l.any (fun i => (lookupH base i.id).isSome && (lookupH file i.id).isNone) then none
                                                             -- "removed heading needs explicit archive/deletion evidence"
  else
    let admitted : Ledger := (file.filter (fun h : Heading => (lookupI l h.id).isNone)).map
      (fun h : Heading => (⟨h.id, h.title, h.st, false⟩ : Item))   -- new headings admitted (import_space)
    (l.mapM (fun i : Item => match lookupH file i.id with
                             | none => some i
                             | some h => step (lookupH base i.id) h i)).map (· ++ admitted)

/-- One cycle: ingest against the last rendered file, then render. -/
def cycle (base file : File) (l : Ledger) : Option File := (ingest base file l).bind render

/-! ## 3. Where today's behaviour violates the Phase 4 guarantees

Each is a concrete space, evaluated by the kernel. They are the counterexamples
the O4 eval's statements fail on; O1 replays each against the real code. -/

def a0 : Item := ⟨0, 0, .live, false⟩
def a1 : Item := ⟨1, 0, .live, false⟩

/-- Isolation, render side: one retained conflict on item 0 and the clean item 1 is not rendered either. -/
theorem render_blocked_by_another_items_conflict :
    render [{ a0 with conflict := true }, a1] = none := by decide

/-- Isolation, ingest side (O1 gesture 3, reopen): reopening closed item 0 refuses the
space, so a retitle of item 1 in the same save is lost. Item 1's edit alone ingests. -/
theorem reopen_blocks_another_items_edit :
    let l : Ledger := [{ a0 with st := .closed }, a1]
    let base : File := [⟨0, 0, .closed⟩, ⟨1, 0, .live⟩]
    ingest base [⟨0, 0, .closed⟩, ⟨1, 1, .live⟩] l = some [{ a0 with st := .closed }, { a1 with title := 1 }] ∧
    ingest base [⟨0, 0, .live⟩, ⟨1, 1, .live⟩] l = none := by decide

/-- O1 gesture 5 (delete / archive / refile): removing heading 0 refuses the space. -/
theorem removal_blocks_another_items_edit :
    ingest [a0.heading, a1.heading] [⟨1, 1, .live⟩] [a0, a1] = none := by decide

/-- O1 gesture 10: human and agent retitle item 0 differently; item 1's edit is lost with it. -/
theorem concurrent_retitle_blocks_another_items_edit :
    ingest [a0.heading, a1.heading] [⟨0, 1, .live⟩, ⟨1, 1, .live⟩] [{ a0 with title := 2 }, a1] = none := by decide

/-- O1 gesture 12: a copy of heading 0 with its :ID: refuses the space. -/
theorem template_copy_blocks_the_space :
    ingest [a0.heading, a1.heading] [a0.heading, a0.heading, ⟨1, 1, .live⟩] [a0, a1] = none := by decide

/-- O1 gesture 13: a hand reorder is accepted by ingest and undone by the next render. -/
theorem hand_order_is_reverted :
    cycle [a0.heading, a1.heading] [a1.heading, a0.heading] [a0, a1] = some [a0.heading, a1.heading] := by decide

/-- Idempotence holds on this two-item space today (the O4 eval asks for every space). -/
theorem idempotent_on_a_small_space :
    render [a0, a1] = some [a0.heading, a1.heading] ∧
    cycle [a0.heading, a1.heading] [a0.heading, a1.heading] [a0, a1] = some [a0.heading, a1.heading] := by decide

end DatacoreSpec.Projector
