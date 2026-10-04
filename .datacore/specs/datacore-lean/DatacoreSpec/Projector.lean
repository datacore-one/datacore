/-!
# Projector — the Phase-1 org round-trip of one space (ledger upgrade O4)

A Phase-1 space renders `org/next_actions.org` from the folded ledger. Under
owner decision 8 (2026-10-04, "one door in") only `inbox.org` is written by
hand; the rendered file is a VIEW. When a view is edited anyway, the next cycle
diffs it per task against what the ledger last wrote: a plain change is applied
to the ledger, an unclear one is HELD as one inbox entry, and the view is
regenerated. This file models that loop and proves the two Phase 4 guarantees
the O4 eval (`.datacore/evals/ledger-upgrade/phase4/O4Targets.lean`) states:

* **Idempotence** (`render_idempotent`) — rendering after fold is a fixed
  point: re-ingesting an unedited view changes nothing and renders the same file.
* **Isolation** (`render_conflict_isolated`, `ingest_conflict_isolated`) — a
  retained conflict never blocks rendering another item, and whatever happens to
  one item's edit (applied, held or unclear), every other task the ledger
  already knows ends up exactly as its own edit alone would leave it.

| Python | Model |
|---|---|
| `ledger/projector.py` `project` — renders every projected item; a retained conflict no longer refuses the projection (the conflicted item renders its accepted ledger value); items ordered by id | `render` |
| `ledger/projection_state.py` `sync_generated` — per item: three-way merge against the last rendered file; a plain change (close, reopen via `item.reopen`, a note on a closed task, retitle, ...) is applied; a clash with an agent's edit is held as an inbox entry, the ledger value stands | `step`, `stepI` |
| same — a heading removed from the view is held as an inbox entry, its ledger item unchanged | `stepI` (`none` branch) |
| `ledger_ingest_org.ensure_ids` — a duplicate `:ID:` (a copied subtree) is held as an inbox entry; the first occurrence keeps the id, the copy is never admitted | `lookupH` (first occurrence), `admitted` |
| `ledger/genesis.import_space` — a new heading is admitted, closed ones as closed (ffd08f0) | `admitted` |

`ingest` never refuses: there is no per-space failure left in the model.

Abstracted: titles, bodies, properties and tags are one value per item
(`title`); the retention window for closed items is not modelled (closed items
render while they are in it); the projection base is the last rendered file.
Archived items (`item.archive`) are outside the projection's item set, and
evidence from an `*_archive.org` file (which the Python may turn into an
archive instead of a held entry) is not modelled. A hand reorder is held as an
inbox entry by the Python; the model has no order beyond id order
(`hand_order_is_regenerated`). The inbox entries themselves are not modelled:
`held` names the ids a cycle holds, for documentation.
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

/-- `bucket.sort(key=lambda i: i.id)`: the file's order is the ids' order. -/
def sortById (f : File) : File := f.foldr insertById []

/-- `projector.project`: every item renders; a retained conflict blocks nothing. -/
def render (l : Ledger) : Option File := some (sortById (l.map Item.heading))

/-! ## 2. Ingest -/

/-- The FIRST heading with this id: a later copy (same `:ID:`) is held, never read. -/
def lookupH (f : File) (id : Nat) : Option Heading := f.find? (·.id == id)
def lookupI (l : Ledger) (id : Nat) : Option Item := l.find? (·.id == id)

def nodupB : List Nat → Bool
  | [] => true
  | x :: xs => !xs.contains x && nodupB xs

def distinctIds (l : Ledger) : Bool := nodupB (l.map (·.id))

/-- `sync_generated` for one heading the ledger knows. A plain change is applied;
"both changed" (a clash with an agent's edit) leaves the item as the ledger has
it and the human's value is held in the inbox. The status always follows the
view: closed→closed (a note on a closed task), closed→live (`item.reopen`),
live→closed (`item.dismiss`), live→live. -/
def step (base : Option Heading) (h : Heading) (i : Item) : Item :=
  let b := (base.map (·.title)).getD i.title
  if h.title == b then { i with st := h.st }                     -- title untouched in the view
  else if i.title == b || i.title == h.title then { i with title := h.title, st := h.st }
  else i                                                          -- clash: held, ledger value stands

/-- One task the ledger knows: removed from the view → held, unchanged. -/
def stepI (base file : File) (i : Item) : Item :=
  match lookupH file i.id with
  | none => i
  | some h => step (lookupH base i.id) h i

/-- Keep the first heading of each id (later copies are held). -/
def dedupe (f : File) : File :=
  f.foldl (fun (acc : File) h => if acc.any (·.id == h.id) then acc else acc ++ [h]) []

/-- New headings (ids the ledger does not know), admitted by `import_space`. -/
def admitted (file : File) (l : Ledger) : Ledger :=
  (dedupe (file.filter (fun h => (lookupI l h.id).isNone))).map
    (fun h : Heading => (⟨h.id, h.title, h.st, false⟩ : Item))

/-- One ingest of the whole space. It never refuses. -/
def ingest (base file : File) (l : Ledger) : Option Ledger :=
  some (l.map (stepI base file) ++ admitted file l)

/-- One cycle: ingest against the last rendered file, then render. -/
def cycle (base file : File) (l : Ledger) : Option File := (ingest base file l).bind render

/-- Documentation only: the ids a cycle holds as inbox entries (removed, clashing,
and copies of an id seen earlier in the file). -/
def held (base file : File) (l : Ledger) : List Nat :=
  (l.filter fun i => match lookupH file i.id with
    | none => (lookupH base i.id).isSome
    | some h =>
      let b := ((lookupH base i.id).map (·.title)).getD i.title
      h.title != b && i.title != b && i.title != h.title).map (·.id)
  ++ ((file.zipIdx.filter fun (h, k) => (file.take k).any (·.id == h.id)).map (·.1.id))

/-- `base` with only item `j`'s heading taken from `file` (dropped if `file` lacks it).
The same definition as the O4 eval's `O4.onlyEdit`. -/
def onlyEdit (base file : File) (j : Nat) : File :=
  match lookupH file j with
  | some h => base.map (fun g => if g.id == j then h else g)
  | none => base.filter (fun g => g.id != j)

/-! ## 3. Lemmas -/

theorem mem_insertById (x h : Heading) (f : File) : x ∈ insertById h f ↔ x = h ∨ x ∈ f := by
  induction f with
  | nil => simp [insertById]
  | cons y ys ih =>
    unfold insertById
    split
    · simp
    · simp only [List.mem_cons, ih]; constructor <;> intro hx <;> rcases hx with hx | hx | hx <;> simp_all

theorem mem_sortById (x : Heading) (f : File) : x ∈ sortById f ↔ x ∈ f := by
  induction f with
  | nil => simp [sortById]
  | cons y ys ih =>
    simp only [sortById, List.foldr_cons] at *
    rw [mem_insertById, ih]; simp

theorem stepI_id (base file : File) (i : Item) : (stepI base file i).id = i.id := by
  unfold stepI step
  split
  · rfl
  · simp only; split
    · rfl
    · split <;> rfl

/-- For an id-preserving map, the item found by id is the mapped item found by id,
whatever is appended, as long as the id is already in the ledger. -/
theorem lookupI_map_append (g : Item → Item) (hg : ∀ i, (g i).id = i.id)
    (l r : Ledger) (j : Nat) (hj : j ∈ l.map (·.id)) :
    lookupI (l.map g ++ r) j = (lookupI l j).map g := by
  unfold lookupI
  have hp : ((fun i : Item => i.id == j) ∘ g) = (fun i : Item => i.id == j) := by
    funext i; simp [Function.comp, hg]
  have hsome : (l.find? (fun i => i.id == j)).isSome := by
    rw [List.find?_isSome]
    obtain ⟨i, hi, hid⟩ := List.mem_map.mp hj
    exact ⟨i, hi, by simp [hid]⟩
  rw [List.find?_append, List.find?_map, hp]
  cases h : l.find? (fun i => i.id == j) with
  | none => simp [h] at hsome
  | some a => simp

theorem nodup_unique {l : Ledger} (hd : distinctIds l = true) {i i' : Item}
    (hi : i ∈ l) (hi' : i' ∈ l) (h : i.id = i'.id) : i = i' := by
  induction l with
  | nil => simp at hi
  | cons a t ih =>
    simp only [distinctIds, List.map_cons, nodupB, Bool.and_eq_true, Bool.not_eq_true'] at hd
    obtain ⟨hnot, hrest⟩ := hd
    have notin : ∀ x ∈ t, x.id ≠ a.id := by
      intro x hx hxa
      have hm : a.id ∈ t.map (·.id) := List.mem_map.mpr ⟨x, hx, hxa⟩
      have hc := List.contains_iff_mem.mpr hm
      rw [hnot] at hc
      exact Bool.false_ne_true hc
    rcases List.mem_cons.mp hi with ha | hi2 <;> rcases List.mem_cons.mp hi' with ha' | hi2'
    · rw [ha, ha']
    · subst ha; exact absurd h.symm (notin _ hi2')
    · subst ha'; exact absurd h (notin _ hi2)
    · exact ih hrest hi2 hi2'

theorem lookupH_render (l : Ledger) (hd : distinctIds l = true) (i : Item) (hi : i ∈ l) :
    lookupH (sortById (l.map Item.heading)) i.id = some i.heading := by
  unfold lookupH
  have hmem : i.heading ∈ sortById (l.map Item.heading) :=
    (mem_sortById _ _).mpr (List.mem_map_of_mem hi)
  cases h : (sortById (l.map Item.heading)).find? (·.id == i.id) with
  | none =>
    rw [List.find?_eq_none] at h
    exact absurd (by simp [Item.heading]) (h _ hmem)
  | some x =>
    have hx := List.mem_of_find?_eq_some h
    have hxid := List.find?_some h
    rw [mem_sortById] at hx
    obtain ⟨i', hi', rfl⟩ := List.mem_map.mp hx
    have : i' = i := nodup_unique hd hi' hi (by simpa [Item.heading] using hxid)
    rw [this]

theorem lookupH_onlyEdit (base file : File) (j : Nat) (hb : ∃ g ∈ base, g.id = j) :
    lookupH (onlyEdit base file j) j = lookupH file j := by
  unfold onlyEdit
  split
  · rename_i h hfile
    have hhid : h.id = j := by simpa using List.find?_some hfile
    unfold lookupH
    rw [List.find?_map]
    have hp : ((fun g : Heading => g.id == j) ∘ (fun g => if g.id == j then h else g))
        = (fun g : Heading => g.id == j) := by
      funext g; by_cases hg : g.id = j <;> simp [Function.comp, hg, hhid]
    rw [hp]
    obtain ⟨g, hg, hgid⟩ := hb
    cases hf : base.find? (fun g => g.id == j) with
    | none => rw [List.find?_eq_none] at hf; exact absurd (by simp [hgid]) (hf g hg)
    | some g0 =>
      have := List.find?_some hf
      simp only [Option.map_some]
      rw [show (g0.id == j) = true from this]; simp [lookupH] at hfile; simp [hfile]
  · rename_i hfile
    unfold lookupH at hfile ⊢
    rw [hfile, List.find?_eq_none]
    intro x hx
    simp only [List.mem_filter] at hx
    simpa using hx.2

theorem dedupe_nil : dedupe [] = [] := rfl

/-! ## 4. The Phase 4 guarantees -/

theorem render_conflict_isolated : ∀ (l : Ledger) (j : Item),
    distinctIds l = true → j ∈ l → j.conflict = false → ∃ f, render l = some f ∧ j.heading ∈ f := by
  intro l j _ hj _
  exact ⟨_, rfl, (mem_sortById _ _).mpr (List.mem_map_of_mem hj)⟩

theorem render_idempotent : ∀ (l : Ledger) (f : File),
    distinctIds l = true → render l = some f → cycle f f l = some f := by
  intro l f hd hr
  simp only [render, Option.some.injEq] at hr
  subst hr
  have hsteps : l.map (stepI (sortById (l.map Item.heading)) (sortById (l.map Item.heading))) = l := by
    conv => rhs; rw [← List.map_id l]
    apply List.map_congr_left
    intro i hi
    unfold stepI
    rw [lookupH_render l hd i hi]
    simp only [step, Option.map_some, Option.getD_some, Item.heading, beq_self_eq_true, ite_true]
    cases i; rfl
  have hadm : admitted (sortById (l.map Item.heading)) l = [] := by
    unfold admitted
    have : (sortById (l.map Item.heading)).filter (fun h => (lookupI l h.id).isNone) = [] := by
      rw [List.filter_eq_nil_iff]
      intro h hh
      rw [mem_sortById] at hh
      obtain ⟨i, hi, rfl⟩ := List.mem_map.mp hh
      have : (lookupI l i.id).isSome := by
        unfold lookupI; rw [List.find?_isSome]; exact ⟨i, hi, by simp⟩
      simp [Item.heading, Option.isSome_iff_ne_none] at this ⊢; exact this
    rw [this]; rfl
  simp [cycle, ingest, hsteps, hadm, render]

theorem ingest_conflict_isolated : ∀ (l : Ledger) (base file : File) (j : Nat) (l1 : Ledger),
    distinctIds l = true → render l = some base → j ∈ l.map (·.id) →
    ingest base (onlyEdit base file j) l = some l1 →
    ∃ l2, ingest base file l = some l2 ∧ lookupI l2 j = lookupI l1 j := by
  intro l base file j l1 _ hr hj h1
  simp only [ingest, Option.some.injEq] at h1
  subst h1
  refine ⟨_, rfl, ?_⟩
  rw [lookupI_map_append _ (stepI_id base file) _ _ _ hj,
      lookupI_map_append _ (stepI_id base (onlyEdit base file j)) _ _ _ hj]
  cases hf : lookupI l j with
  | none => rfl
  | some i =>
    have hid : i.id = j := by simpa using List.find?_some hf
    have hi : i ∈ l := List.mem_of_find?_eq_some hf
    have hb : ∃ g ∈ base, g.id = j := by
      simp only [render, Option.some.injEq] at hr
      subst hr
      exact ⟨i.heading, (mem_sortById _ _).mpr (List.mem_map_of_mem hi), hid⟩
    simp only [Option.map_some, Option.some.injEq]
    unfold stepI
    rw [hid, lookupH_onlyEdit base file j hb]

/-! ## 5. The new behaviour on concrete spaces

Each is evaluated by the kernel; O1 replays the same gestures against the real code. -/

def a0 : Item := ⟨0, 0, .live, false⟩
def a1 : Item := ⟨1, 0, .live, false⟩

/-- A retained conflict on item 0 no longer blocks rendering item 1 (before: `none`). -/
theorem conflict_renders_the_other_items :
    render [{ a0 with conflict := true }, a1] = some [⟨0, 0, .live⟩, a1.heading] := by decide

/-- O1 gesture 3 (reopen) beside a retitle of item 1: both are applied. -/
theorem reopen_beside_another_items_edit :
    let l : Ledger := [{ a0 with st := .closed }, a1]
    let base : File := [⟨0, 0, .closed⟩, ⟨1, 0, .live⟩]
    ingest base [⟨0, 0, .live⟩, ⟨1, 1, .live⟩] l = some [a0, { a1 with title := 1 }] := by decide

/-- O1 gesture 5 (delete): the removed heading is held, its item unchanged; item 1's edit lands. -/
theorem removal_is_held :
    ingest [a0.heading, a1.heading] [⟨1, 1, .live⟩] [a0, a1] = some [a0, { a1 with title := 1 }] ∧
    held [a0.heading, a1.heading] [⟨1, 1, .live⟩] [a0, a1] = [0] := by decide

/-- O1 gesture 10: human and agent retitle item 0 differently; the agent's value stands, the
human's is held, and item 1's edit lands. -/
theorem clash_is_held :
    ingest [a0.heading, a1.heading] [⟨0, 1, .live⟩, ⟨1, 1, .live⟩] [{ a0 with title := 2 }, a1]
      = some [{ a0 with title := 2 }, { a1 with title := 1 }] := by decide

/-- O1 gesture 12: a copy of heading 0 with its :ID: is held; the original is kept, nothing admitted. -/
theorem template_copy_is_held :
    ingest [a0.heading, a1.heading] [a0.heading, ⟨0, 1, .live⟩, ⟨1, 1, .live⟩] [a0, a1]
      = some [a0, { a1 with title := 1 }] ∧
    held [a0.heading, a1.heading] [a0.heading, ⟨0, 1, .live⟩, ⟨1, 1, .live⟩] [a0, a1] = [0] := by decide

/-- O1 gesture 13: the view is regenerated in id order (the Python holds the reorder in the inbox). -/
theorem hand_order_is_regenerated :
    cycle [a0.heading, a1.heading] [a1.heading, a0.heading] [a0, a1] = some [a0.heading, a1.heading] := by
  decide

end DatacoreSpec.Projector
