/-!
# Knowledge cluster — context layering, learning buffer, briefing dedupe,
# agent stream, learning-backup rotation

Python modelled (all under `.datacore/lib/`):

| § | File | Property |
|---|---|---|
| 1 | `context_merge.py` | private (`.local.md`) content never reaches a file git would track; SPACE/TEAM content never reaches a tracked file of a public (or unknown) repo (D6) |
| 2 | `prune_learning_buffer.py` | prune only against an ACTIVE engram; kept entries byte-preserved |
| 3 | `briefing/actions.py` | a dismissed id never comes back; concurrent calls create once |
| 4 | `agent_stream_store.py` | records are framed exactly; a successful append's retry is a no-op |
| 5 | `rotate_learning_backup.py` | purge deletes only this file's backups and keeps `min KEEP n` newest |

Abstractions, and why they are safe:

* Text is a list of code points (`Nat`), or a list of lines of an opaque type.
  The properties are about which pieces survive and in what order, not about
  what the pieces say.
* Similarity of a learning entry to an engram (`check_engram_exists`), the
  content hash of an agent row, and `git check-ignore` are oracles. Theorems
  hold for every oracle.
* A row's content hash is modelled as the row itself (`ts` is not modelled, as
  `_content_hash` drops it). So "same content" is equality, and a row's
  `dedup_key` is part of its content, as in Python.
-/

namespace DatacoreSpec.Knowledge

/-! ## 1. `context_merge.py` — DIP-0002 layering -/
namespace ContextMerge

inductive Level | pub | space | team | priv
  deriving DecidableEq, Repr

/-- `LAYERS`, in order. -/
def layers : List Level := [.pub, .space, .team, .priv]

/-- `merge_context`, content only: the existing layers concatenated in `LAYERS`
order (markers and `strip()` omitted: they add or trim whitespace around a
layer, never remove another layer's text). -/
def merge {α : Type} (present : Level → Option (List α)) : List α :=
  (layers.filterMap present).flatten

/-- DIP-0002 "Resolved Questions" 1: composition is additive. Every existing
layer's text appears in the output, contiguous; no layer overrides another.
(The module comment "later layers extend/override" was corrected to "extend".) -/
theorem layer_survives {α : Type} (present : Level → Option (List α)) (l : Level)
    (c : List α) (h : present l = some c) : c <:+: merge present := by
  apply List.infix_of_mem_flatten
  rw [List.mem_filterMap]
  exact ⟨l, by cases l <;> simp [layers], h⟩

/-- What `output_untracked_refusal` learns from git about the output path. -/
inductive Git
  | ignored     -- `check-ignore` exit 0 (a tracked path is never reported ignored)
  | notIgnored  -- exit 1: git would track it
  | noRepo      -- `rev-parse` says: not a git repository
  | failed      -- anything else, including git missing: unknown
  deriving DecidableEq, Repr

def hasPrivate {α : Type} (present : Level → Option (List α)) : Bool := (present .priv).isSome
def anyLayer {α : Type} (present : Level → Option (List α)) : Bool :=
  layers.any (fun l => (present l).isSome)

/-- Where a file holding PRIVATE text may live. -/
def allowed : Git → Bool
  | .ignored => true
  | .noRepo => true
  | _ => false

/-- `rebuild_context` before the fix: it writes whenever a layer exists. -/
def oldWrites {α : Type} (present : Level → Option (List α)) (_g : Git) : Bool := anyLayer present

/-- After the fix: a composed file with a PRIVATE layer is written only where
git will not track it. -/
def newWrites {α : Type} (present : Level → Option (List α)) (g : Git) : Bool :=
  anyLayer present && (!hasPrivate present || allowed g)

/-- A leak: the file is written, contains the PRIVATE layer (by
`layer_survives`), and git would (or might) track it. -/
def leaks {α : Type} (writes : Bool) (present : Level → Option (List α)) (g : Git) : Prop :=
  writes = true ∧ hasPrivate present = true ∧ allowed g = false

/-- Refutation of the old code: base + local in a repo without a `.gitignore`
line for `CLAUDE.md`. Replayed in `test_private_layer_is_not_written_where_git_would_track_it`. -/
theorem old_leaks :
    ∃ (present : Level → Option (List Nat)) (g : Git), leaks (oldWrites present g) present g :=
  ⟨fun l => if l = .priv then some [1] else none, .notIgnored, rfl, rfl, rfl⟩

theorem new_never_leaks {α : Type} (present : Level → Option (List α)) (g : Git) :
    ¬ leaks (newWrites present g) present g := by
  intro ⟨hw, hp, ha⟩
  simp [newWrites, hp, ha] at hw

/-- The fix costs nothing where there is nothing private, or where git ignores
the output (all four live composed files with a local layer, checked). -/
theorem new_writes_when_safe {α : Type} (present : Level → Option (List α)) (g : Git)
    (h : hasPrivate present = false ∨ allowed g = true) :
    newWrites present g = anyLayer present := by
  rcases h with h | h <;> simp [newWrites, h]

/-! ### SPACE/TEAM layers in public repositories (owner decision D6, 2026-09-23)

`shared_layer_refusal`: a composed file holding a SPACE or TEAM layer is not
written where git would track it in a repository with a public remote. Public
is the pre-push hook's test (a remote's org/name in `protected_repos`), and
when it cannot be told, the write is refused. `newWrites` above is the
previous fix (PRIVATE only); `newWritesD6` is the code now. -/

/-- What `public_remotes` learns. `unknown`: policy unreadable, git failing. -/
inductive Vis | pub | priv | unknown
  deriving DecidableEq, Repr

def hasShared {α : Type} (present : Level → Option (List α)) : Bool :=
  (present .space).isSome || (present .team).isSome

/-- Where a file holding SPACE/TEAM text may live. -/
def allowedShared : Git → Vis → Bool
  | .ignored, _ => true
  | .noRepo, _ => true
  | .notIgnored, v => v == .priv
  | .failed, _ => false

def newWritesD6 {α : Type} (present : Level → Option (List α)) (g : Git) (v : Vis) : Bool :=
  newWrites present g && (!hasShared present || allowedShared g v)

/-- A publication: the file is written, holds SPACE/TEAM text, git tracks it (or
might), and the repository is public (or might be). -/
def publishes {α : Type} (writes : Bool) (present : Level → Option (List α)) (g : Git) (v : Vis) : Prop :=
  writes = true ∧ hasShared present = true ∧ allowedShared g v = false

/-- The previous fix wrote a tracked base + space file in a public repo
(replayed: `test_d6_space_layer_refused_in_a_tracked_file_of_a_public_repo`). -/
theorem prev_fix_publishes_space :
    ∃ (present : Level → Option (List Nat)) (g : Git) (v : Vis),
      publishes (newWrites present g) present g v :=
  ⟨fun l => if l = .space then some [1] else none, .notIgnored, .pub, rfl, rfl, rfl⟩

/-- **D6.** Never publishes SPACE/TEAM text, and still never leaks PRIVATE. -/
theorem d6_never_publishes {α : Type} (present : Level → Option (List α)) (g : Git) (v : Vis) :
    ¬ publishes (newWritesD6 present g v) present g v ∧ ¬ leaks (newWritesD6 present g v) present g := by
  constructor
  · intro ⟨hw, hs, ha⟩
    simp [newWritesD6, hs, ha] at hw
  · intro ⟨hw, hp, ha⟩
    simp [newWritesD6, newWrites, hp, ha] at hw

/-- Unknown visibility of a tracked output refuses a SPACE/TEAM file. -/
theorem d6_unknown_refuses {α : Type} (present : Level → Option (List α))
    (h : hasShared present = true) : newWritesD6 present .notIgnored .unknown = false := by
  simp [newWritesD6, h, allowedShared]

/-- Both non-public layers count: a SPACE-only or a TEAM-only composition is
refused in a tracked file of a public repository. -/
theorem d6_space_and_team_count (c : List Nat) :
    newWritesD6 (fun l => if l = .space then some c else none) .notIgnored .pub = false ∧
    newWritesD6 (fun l => if l = .team then some c else none) .notIgnored .pub = false := by
  simp [newWritesD6, hasShared, allowedShared]

/-- D6 changes nothing where there is no SPACE/TEAM layer, where git ignores the
output, where there is no repository, or where the repository is known private. -/
theorem d6_writes_when_safe {α : Type} (present : Level → Option (List α)) (g : Git) (v : Vis)
    (h : hasShared present = false ∨ allowedShared g v = true) :
    newWritesD6 present g v = newWrites present g := by
  rcases h with h | h <;> simp [newWritesD6, h]

end ContextMerge

/-! ## 2. `prune_learning_buffer.py` -/
namespace Prune

inductive Status | active | retired | candidate | other
  deriving DecidableEq, Repr

structure Engram (S : Type) where
  status : Option Status
  stmt : S

/-- `PROMOTED_STATUSES`; a record with no status predates the field. -/
def promoted : Option Status → Bool
  | none => true
  | some .active => true
  | some _ => false

/-- `load_engram_index` before the fix: every `statement:`. -/
def oldIndex {S : Type} (es : List (Engram S)) : List S := es.map (·.stmt)
/-- After the fix: statements of promoted records only. -/
def newIndex {S : Type} (es : List (Engram S)) : List S :=
  (es.filter (fun e => promoted e.status)).map (·.stmt)

/-- `check_engram_exists … is True`, for any similarity oracle. -/
def found {S : Type} (sim : S → S → Bool) (idx : List S) (q : S) : Bool := idx.any (sim q)

theorem new_prune_needs_active {S : Type} (sim : S → S → Bool) (es : List (Engram S)) (q : S)
    (h : found sim (newIndex es) q = true) :
    ∃ e ∈ es, promoted e.status = true ∧ sim q e.stmt = true := by
  simp only [found, newIndex, List.any_map, List.any_eq_true, List.mem_filter,
    Function.comp] at h
  obtain ⟨e, ⟨he, hp⟩, hs⟩ := h
  exact ⟨e, he, hp, hs⟩

/-- Refutation: a retired engram alone licensed the prune (16 real entries
today would have been pruned against a retired engram only). -/
theorem old_prunes_on_retired :
    ∃ (es : List (Engram Nat)) (q : Nat),
      found (fun _ _ => true) (oldIndex es) q = true ∧ ∀ e ∈ es, promoted e.status = false :=
  ⟨[⟨some .retired, 0⟩], 0, by decide⟩

/-! Byte preservation. A file is its list of lines (`content.split("\n")`);
`"\n".join` of a line list is injective, so line-level equality is byte
equality. -/

/-- `parse_entries`: the lines before the first heading, then one block per
heading (the heading and every following non-heading line). -/
def parse {L : Type} (isHead : L → Bool) : List L → List L × List (List L)
  | [] => ([], [])
  | x :: xs =>
    let r := parse isHead xs
    if isHead x then ([], (x :: r.1) :: r.2) else (x :: r.1, r.2)

theorem parse_roundtrip {L : Type} (isHead : L → Bool) (xs : List L) :
    (parse isHead xs).1 ++ (parse isHead xs).2.flatten = xs := by
  induction xs with
  | nil => rfl
  | cons x xs ih =>
    by_cases h : isHead x = true
    · simp [parse, h, ih]
    · simp [parse, h, ih]

/-- The fixed rebuild: `"\n".join([header] + [raw of kept entries])` — the
original lines with the pruned blocks removed. (`has_header` omits an empty
header list, so no line is invented.) -/
def rebuild {L : Type} (keep : List L → Bool) (p : List L × List (List L)) : List L :=
  p.1 ++ (p.2.filter keep).flatten

theorem flatten_filter_sublist {L : Type} (keep : List L → Bool) :
    ∀ bs : List (List L), List.Sublist (bs.filter keep).flatten bs.flatten
  | [] => by simp
  | b :: bs => by
    by_cases h : keep b = true
    · simpa [List.filter_cons, h] using (flatten_filter_sublist keep bs).append_left b
    · simpa [List.filter_cons, h] using
        (flatten_filter_sublist keep bs).trans (List.sublist_append_right b bs.flatten)

/-- Nothing pruned: the file is unchanged, byte for byte. -/
theorem rebuild_identity {L : Type} (isHead : L → Bool) (keep : List L → Bool) (xs : List L)
    (h : ∀ b ∈ (parse isHead xs).2, keep b = true) : rebuild keep (parse isHead xs) = xs := by
  rw [rebuild, List.filter_eq_self.mpr h, parse_roundtrip]

/-- The output only deletes lines: it is a sublist of the input. -/
theorem rebuild_sublist {L : Type} (isHead : L → Bool) (keep : List L → Bool) (xs : List L) :
    List.Sublist (rebuild keep (parse isHead xs)) xs := by
  conv => rhs; rw [← parse_roundtrip isHead xs]
  exact (flatten_filter_sublist keep _).append_left _

/-- Every kept entry appears verbatim and contiguous in the output. -/
theorem kept_entry_verbatim {L : Type} (isHead : L → Bool) (keep : List L → Bool) (xs : List L)
    (b : List L) (hb : b ∈ (parse isHead xs).2) (hk : keep b = true) :
    b <:+: rebuild keep (parse isHead xs) :=
  (List.infix_of_mem_flatten (List.mem_filter.mpr ⟨hb, hk⟩)).trans
    (List.suffix_append _ _).isInfix

/-- `str.rstrip(c)` on code points. -/
def rstrip (c : Nat) (s : List Nat) : List Nat := (s.reverse.dropWhile (· == c)).reverse

/-- The old per-entry rewrite `raw.rstrip("\n").rstrip("-").rstrip("\n")`. -/
def oldKept (raw : List Nat) : List Nat := rstrip 10 (rstrip 45 (rstrip 10 raw))

/-- Refutation: a kept entry "#\nf --" lost its dashes (it was also given a
`---` separator it never had). Replayed in `test_kept_entries_and_header_are_byte_preserved`. -/
theorem old_mutates_kept_entry : oldKept [35, 10, 102, 32, 45, 45] = [35, 10, 102, 32] := by
  decide

end Prune

/-! ## 3. `briefing/actions.py` -/
namespace Briefing

/-- `item_id`: a hash of normalised text. For any collision-free hash, texts
that normalise differently get different ids: the "no matter how the briefing
rephrases it" claim is unachievable by hashing, and the docstring now says so. -/
theorem rephrase_changes_id {T H : Type} (h : T → H) (hinj : ∀ a b, h a = h b → a = b)
    (norm : T → T) (t₁ t₂ : T) (hn : norm t₁ ≠ norm t₂) : h (norm t₁) ≠ h (norm t₂) :=
  fun e => hn (hinj _ _ e)

inductive Status | created | dismissed
  deriving DecidableEq, Repr

/-- The events of one id, as `fold.py` sees them for this question. -/
inductive Ev | create | dismiss
  deriving DecidableEq, Repr

/-- `_handle_create` (existing id: no-op, dismissed or not) and `_handle_dismiss`. -/
def step : Option Status → Ev → Option Status
  | none, .create => some .created
  | some s, .create => some s
  | some _, .dismiss => some .dismissed
  | none, .dismiss => none

def fold (evs : List Ev) : Option Status := evs.foldl step none

theorem dismissed_absorbing (evs : List Ev) : evs.foldl step (some .dismissed) = some .dismissed := by
  induction evs with
  | nil => rfl
  | cons e es ih => cases e <;> simpa [List.foldl, step] using ih

/-- Dismissed means gone forever, for every later event sequence — enforced by
the fold, independent of `materialize`'s snapshot check. -/
theorem no_resurrection (pre post : List Ev) (h : fold pre = some .dismissed) :
    fold (pre ++ post) = some .dismissed := by
  simp only [fold, List.foldl_append] at *
  rw [h]; exact dismissed_absorbing post

/-- One `materialize` of one id: fold a snapshot, append a create iff absent. -/
def materialize (snapshot log : List Ev) : List Ev :=
  if fold snapshot = none then log ++ [.create] else log

/-- The fix: calls hold one per-space lock, so each snapshot is the current log. -/
def serialized (log : List Ev) : List Ev :=
  materialize (materialize log log) (materialize log log)

/-- Before: both calls fold the same snapshot, then both append. -/
def racing (log : List Ev) : List Ev := materialize log (materialize log log)

theorem serialized_one_create (log : List Ev) (h : fold log = none) :
    serialized log = log ++ [.create] := by
  have h2 : fold (log ++ [.create]) = some .created := by
    simp [fold, List.foldl_append] at *; simp [h, step]
  simp [serialized, materialize, h, h2]

/-- Refutation of "one create": replayed with two threads in
`test_concurrent_materialize_appends_one_create`. -/
theorem racing_two_creates (log : List Ev) (h : fold log = none) :
    racing log = log ++ [.create, .create] := by
  simp [racing, materialize, h]

/-- The race was never a resurrection: whatever follows, including a human's
dismissal between the two appends, the folded state equals the serialized one. -/
theorem race_state_benign (log post : List Ev) :
    fold (racing log ++ post) = fold (serialized log ++ post) := by
  by_cases h : fold log = none
  · rw [racing_two_creates log h, serialized_one_create log h]
    simp [fold, List.foldl_append] at *; simp [h, step]
  · simp [racing, serialized, materialize, h]

end Briefing

/-! ## 4. `agent_stream_store.py` -/
namespace AgentStream

/-! ### 4a. Record framing -/

def LF : Nat := 10
def LS : Nat := 0x2028

/-- `str.split(sep)` on code points. -/
def pieces (isSep : Nat → Bool) : List Nat → List (List Nat)
  | [] => [[]]
  | c :: cs =>
    if isSep c then [] :: pieces isSep cs
    else match pieces isSep cs with
      | p :: rest => (c :: p) :: rest
      | [] => [[c]]

/-- Split, then drop one trailing empty piece (`lines.pop()` if `lines[-1] == ""`). -/
def frame (isSep : Nat → Bool) (t : List Nat) : List (List Nat) :=
  let ps := pieces isSep t
  if ps.getLast? = some [] then ps.dropLast else ps

/-- Old reader: `splitlines()` also breaks on U+2028 (and U+2029, U+0085). -/
def oldFrame := frame (fun c => c == LF || c == LS)
/-- New reader: `"\n"` only. -/
def newFrame := frame (fun c => c == LF)

/-- What `append_events` writes: each record followed by `"\n"`. -/
def render (recs : List (List Nat)) : List Nat := recs.flatMap (· ++ [LF])

theorem pieces_record (rec rest : List Nat) (h : ∀ c ∈ rec, c ≠ LF) :
    pieces (fun c => c == LF) (rec ++ LF :: rest) = rec :: pieces (fun c => c == LF) rest := by
  induction rec with
  | nil => simp [pieces]
  | cons c cs ih =>
    have hc : (c == LF) = false := by simpa using h c (by simp)
    rw [List.cons_append, pieces, hc, ih (fun x hx => h x (by simp [hx]))]
    rfl

theorem pieces_render (recs : List (List Nat)) (h : ∀ r ∈ recs, ∀ c ∈ r, c ≠ LF) :
    pieces (fun c => c == LF) (render recs) = recs ++ [[]] := by
  induction recs with
  | nil => rfl
  | cons r rs ih =>
    have e : render (r :: rs) = r ++ LF :: render rs := by simp [render]
    rw [e, pieces_record r _ (h r (by simp)), ih (fun x hx => h x (by simp [hx]))]
    rfl

/-- JSON never writes a raw LF inside a record, so the fixed reader recovers
exactly the records written. -/
theorem newFrame_render (recs : List (List Nat)) (h : ∀ r ∈ recs, ∀ c ∈ r, c ≠ LF) :
    newFrame (render recs) = recs := by
  simp [newFrame, frame, pieces_render recs h]

/-- Refutation: a one-record log whose string holds U+2028 is read back as two
fragments; `json.loads` fails on the first, and every later append raises.
Replayed in `test_unicode_line_separator_does_not_brick_the_stream`. -/
theorem oldFrame_splits_record :
    oldFrame (render [[34, LS, 34]]) = [[34], [34]] := by decide

/-! ### 4b. Retry idempotence and batch atomicity -/

structure Row where
  id : Nat
  body : Nat
  key : Option Nat
  deriving DecidableEq, Repr

abbrev Known := Nat → Option Row

/-- `known.setdefault(row["id"], content)`. -/
def setd (m : Known) (r : Row) : Known :=
  fun i => if i = r.id ∧ m i = none then some r else m i

/-- `remember` over the retained history: first write wins. -/
def knownOf (d : List Row) : Known := d.foldl setd (fun _ => none)
def keysOf (d : List Row) : List Nat := d.filterMap (·.key)

structure St where
  known : Known
  seen : List Nat
  adds : List Row

def addOrNot (s : St) (r : Row) : St :=
  if s.known r.id = none then { s with adds := s.adds ++ [r], known := setd s.known r } else s

/-- The loop body after the conflict check. `reserve` is the fix: a row
skipped for its `dedup_key` still reserves its id. -/
def afterCheck (reserve : Bool) (s : St) (r : Row) : St :=
  match r.key with
  | some k =>
    if k ∈ s.seen then (if reserve then { s with known := setd s.known r } else s)
    else addOrNot { s with seen := k :: s.seen } r
  | none => addOrNot s r

def step (reserve : Bool) (s : St) (r : Row) : Except Unit St :=
  match s.known r.id with
  | some c => if c = r then .ok (afterCheck reserve s r) else .error ()
  | none => .ok (afterCheck reserve s r)

/-- `append_events`: the new history and the count, or an error and NO write
(the one `atomic_write_text` happens after the loop, so batch atomicity is
structural: `append_extends`). -/
def append (reserve : Bool) (d rows : List Row) : Except Unit (List Row × Nat) := do
  let s ← rows.foldlM (step reserve) ⟨knownOf d, keysOf d, []⟩
  pure (d ++ s.adds, s.adds.length)

theorem append_extends (reserve : Bool) (d rows d' : List Row) (n : Nat)
    (h : append reserve d rows = .ok (d', n)) : ∃ adds, d' = d ++ adds ∧ n = adds.length := by
  unfold append at h
  cases hf : rows.foldlM (step reserve) ⟨knownOf d, keysOf d, []⟩ with
  | error e => simp [hf, bind, Except.bind] at h
  | ok s =>
    simp [hf, bind, Except.bind, pure, Except.pure] at h
    exact ⟨s.adds, h.1.symm, h.2.symm⟩

section Refutation
def r0 : Row := ⟨0, 0, some 7⟩
def r1 : Row := ⟨1, 1, some 7⟩
def r2 : Row := ⟨1, 2, none⟩

/-- Refutation of the old loop: the batch is accepted, and its retry raises
`EventConflict`. Replayed in `test_dedup_skipped_row_still_reserves_its_id`. -/
theorem old_retry_conflicts :
    append false [] [r0, r1, r2] = .ok ([r0, r2], 2) ∧
    append false [r0, r2] [r0, r1, r2] = .error () := ⟨rfl, rfl⟩

/-- The fixed loop refuses that batch outright: id 1 names two contents. -/
theorem new_rejects_batch : append true [] [r0, r1, r2] = .error () := rfl
end Refutation

theorem setd_of_some {m : Known} {r : Row} {i : Nat} {c : Row} (h : m i = some c) :
    setd m r i = some c := by
  simp [setd, h]

theorem setd_self {m : Known} {r : Row} (h : m r.id = none) : setd m r r.id = some r := by
  simp [setd, h]

theorem setd_cases {m : Known} {r : Row} {i : Nat} {c : Row} (h : setd m r i = some c) :
    m i = some c ∨ (i = r.id ∧ m i = none ∧ c = r) := by
  unfold setd at h
  split at h
  · next hc => exact Or.inr ⟨hc.1, hc.2, (Option.some.inj h).symm⟩
  · exact Or.inl h

theorem setd_ne_none {m : Known} {r : Row} {i : Nat} (h : m i ≠ none) : setd m r i ≠ none := by
  obtain ⟨c, hc⟩ := Option.ne_none_iff_exists'.mp h
  rw [setd_of_some hc]; simp

theorem knownOf_append (d : List Row) (r : Row) : knownOf (d ++ [r]) = setd (knownOf d) r := by
  simp [knownOf, List.foldl_append]

theorem foldl_setd_mem : ∀ (d : List Row) (m : Known) (i : Nat) (c : Row),
    d.foldl setd m i = some c → m i = some c ∨ c ∈ d
  | [], _, _, _, h => Or.inl h
  | r :: rs, m, i, c, h => by
    rcases foldl_setd_mem rs (setd m r) i c h with h | h
    · rcases setd_cases h with h | ⟨_, _, rfl⟩
      · exact Or.inl h
      · exact Or.inr (by simp)
    · exact Or.inr (by simp [h])

theorem knownOf_mem {d : List Row} {i : Nat} {c : Row} (h : knownOf d i = some c) : c ∈ d := by
  rcases foldl_setd_mem d _ i c h with h | h
  · simp at h
  · exact h

theorem key_mem {d : List Row} {r : Row} {k : Nat} (hr : r ∈ d) (hk : r.key = some k) :
    k ∈ keysOf d := List.mem_filterMap.mpr ⟨r, hr, hk⟩

/-- Invariant of the first run over `done` rows, against history `d`. -/
structure Inv (d done : List Row) (s : St) : Prop where
  L : ∀ i c, knownOf (d ++ s.adds) i = some c → s.known i = some c
  P : ∀ i c, s.known i = some c →
    knownOf (d ++ s.adds) i = some c ∨ ∃ k, c.key = some k ∧ k ∈ s.seen
  Q : ∀ k ∈ s.seen, k ∈ keysOf (d ++ s.adds)
  F : ∀ r ∈ done, s.known r.id = some r

theorem step_ok {s : St} {r : Row} {s' : St} (h : step true s r = .ok s') :
    (s.known r.id = none ∨ s.known r.id = some r) ∧ s' = afterCheck true s r := by
  unfold step at h
  split at h
  · next c hk =>
    split at h
    · next hcr => subst hcr; exact ⟨Or.inr hk, (Except.ok.inj h).symm⟩
    · cases h
  · next hk => exact ⟨Or.inl hk, (Except.ok.inj h).symm⟩

theorem inv_add {d done : List Row} {s : St} {r : Row} (hI : Inv d done s)
    (hc : s.known r.id = none ∨ s.known r.id = some r) (seen' : List Nat)
    (hseen : ∀ k ∈ seen', k ∈ s.seen ∨ r.key = some k) (hsub : ∀ k ∈ s.seen, k ∈ seen') :
    Inv d (done ++ [r]) (addOrNot { s with seen := seen' } r) := by
  unfold addOrNot
  by_cases hn : s.known r.id = none
  · simp only [hn, ite_true]
    have hK : knownOf (d ++ s.adds) r.id = none := by
      cases hk : knownOf (d ++ s.adds) r.id with
      | none => rfl
      | some c => rw [hI.L _ _ hk] at hn; cases hn
    have eK : knownOf (d ++ (s.adds ++ [r])) = setd (knownOf (d ++ s.adds)) r := by
      rw [← List.append_assoc, knownOf_append]
    refine ⟨?_, ?_, ?_, ?_⟩
    · intro i c h
      rw [eK] at h
      rcases setd_cases h with h | ⟨rfl, _, rfl⟩
      · exact setd_of_some (hI.L _ _ h)
      · exact setd_self hn
    · intro i c h
      rw [eK]
      rcases setd_cases h with h | ⟨rfl, _, rfl⟩
      · rcases hI.P _ _ h with h | ⟨k, hk, hks⟩
        · exact Or.inl (setd_of_some h)
        · exact Or.inr ⟨k, hk, hsub k hks⟩
      · exact Or.inl (setd_self hK)
    · intro k hk
      rcases hseen k hk with hk | hk
      · have := hI.Q k hk
        simp only [keysOf, List.filterMap_append, List.mem_append] at this ⊢
        rcases this with h | h
        · exact Or.inl h
        · exact Or.inr (Or.inl h)
      · exact key_mem (by simp) hk
    · intro r0 hr0
      rcases List.mem_append.mp hr0 with h | h
      · exact setd_of_some (hI.F r0 h)
      · simp at h; subst h; exact setd_self hn
  · simp only [hn, ite_false]
    have hr : s.known r.id = some r := hc.resolve_left hn
    refine ⟨hI.L, ?_, ?_, ?_⟩
    · intro i c h
      rcases hI.P i c h with h | ⟨k, hk, hks⟩
      · exact Or.inl h
      · exact Or.inr ⟨k, hk, hsub k hks⟩
    · intro k hk
      rcases hseen k hk with hk | hk
      · exact hI.Q k hk
      · rcases hI.P _ _ hr with h | ⟨k', hk', hks⟩
        · exact key_mem (knownOf_mem h) hk
        · rw [hk] at hk'; cases hk'; exact hI.Q k hks
    · intro r0 hr0
      rcases List.mem_append.mp hr0 with h | h
      · exact hI.F r0 h
      · simp at h; subst h; exact hr

theorem inv_step {d done : List Row} {s : St} {r : Row} (hI : Inv d done s)
    (hc : s.known r.id = none ∨ s.known r.id = some r) :
    Inv d (done ++ [r]) (afterCheck true s r) := by
  unfold afterCheck
  cases hkey : r.key with
  | none =>
    have := inv_add hI hc s.seen (fun k hk => Or.inl hk) (fun k hk => hk)
    simpa using this
  | some k =>
    by_cases hks : k ∈ s.seen
    · simp only [hks, ite_true]
      refine ⟨fun i c h => setd_of_some (hI.L i c h), ?_, hI.Q, ?_⟩
      · intro i c h
        rcases setd_cases h with h | ⟨_, _, rfl⟩
        · exact hI.P i c h
        · exact Or.inr ⟨k, hkey, hks⟩
      · intro r0 hr0
        rcases List.mem_append.mp hr0 with h | h
        · exact setd_of_some (hI.F r0 h)
        · simp at h; subst h
          rcases hc with h | h
          · exact setd_self h
          · exact setd_of_some h
    · simp only [hks, ite_false]
      exact inv_add hI hc (k :: s.seen)
        (fun k' hk' => by
          rcases List.mem_cons.mp hk' with h | h
          · exact Or.inr (by rw [h, hkey])
          · exact Or.inl h)
        (fun k' hk' => List.mem_cons_of_mem _ hk')

theorem run_inv {d : List Row} : ∀ (xs done : List Row) (s sF : St), Inv d done s →
    xs.foldlM (step true) s = .ok sF → Inv d (done ++ xs) sF
  | [], done, s, sF, hI, h => by
    simp only [List.foldlM, pure, Except.pure] at h
    cases h; simpa using hI
  | r :: xs, done, s, sF, hI, h => by
    rw [List.foldlM_cons] at h
    cases hs : step true s r with
    | error e => rw [hs] at h; cases h
    | ok s1 =>
      rw [hs] at h
      obtain ⟨hc, rfl⟩ := step_ok hs
      have := run_inv xs (done ++ [r]) _ sF (inv_step hI hc) h
      simpa [List.append_assoc] using this

/-- The retry adds nothing, given what the first run established. -/
theorem run_noadd : ∀ (xs : List Row) (s : St), s.adds = [] →
    (∀ r ∈ xs, s.known r.id = none ∨ s.known r.id = some r) →
    (∀ r ∈ xs, s.known r.id ≠ none ∨ ∃ k, r.key = some k ∧ k ∈ s.seen) →
    (∀ r ∈ xs, ∀ r' ∈ xs, r.id = r'.id → r = r') →
    ∃ sF, xs.foldlM (step true) s = .ok sF ∧ sF.adds = []
  | [], s, ha, _, _, _ => ⟨s, rfl, ha⟩
  | r :: xs, s, ha, h1, h2, h3 => by
    have hstep : step true s r = .ok (afterCheck true s r) := by
      unfold step
      rcases h1 r (by simp) with h | h
      · rw [h]
      · rw [h]; simp
    rw [List.foldlM_cons, hstep]
    have h3' : ∀ r' ∈ xs, r'.id = r.id → r' = r :=
      fun r' hr' he => (h3 r (by simp) r' (by simp [hr']) he.symm).symm
    unfold afterCheck
    cases hkey : r.key with
    | none =>
      have hne : s.known r.id ≠ none := by
        rcases h2 r (by simp) with h | ⟨k, hk, _⟩
        · exact h
        · rw [hkey] at hk; cases hk
      simp only [addOrNot, hne, ite_false]
      exact run_noadd xs s ha (fun x hx => h1 x (by simp [hx]))
        (fun x hx => h2 x (by simp [hx])) (fun x hx y hy => h3 x (by simp [hx]) y (by simp [hy]))
    | some k =>
      by_cases hks : k ∈ s.seen
      · simp only [hks, ite_true]
        refine run_noadd xs _ ha ?_ ?_ (fun x hx y hy => h3 x (by simp [hx]) y (by simp [hy]))
        · intro x hx
          by_cases he : x.id = r.id
          · have := h3' x hx he; subst this
            rcases h1 x (by simp) with h | h
            · exact Or.inr (setd_self h)
            · exact Or.inr (setd_of_some h)
          · rcases h1 x (by simp [hx]) with h | h
            · left; simp [setd, he, h]
            · exact Or.inr (setd_of_some h)
        · intro x hx
          rcases h2 x (by simp [hx]) with h | h
          · exact Or.inl (setd_ne_none h)
          · exact Or.inr h
      · have hne : s.known r.id ≠ none := by
          rcases h2 r (by simp) with h | ⟨k', hk', hk's⟩
          · exact h
          · rw [hkey] at hk'; cases hk'; exact absurd hk's hks
        simp only [hks, ite_false, addOrNot, hne, ite_false]
        exact run_noadd xs _ ha (fun x hx => h1 x (by simp [hx]))
          (fun x hx => by
            rcases h2 x (by simp [hx]) with h | ⟨k', hk', hk's⟩
            · exact Or.inl h
            · exact Or.inr ⟨k', hk', List.mem_cons_of_mem _ hk's⟩)
          (fun x hx y hy => h3 x (by simp [hx]) y (by simp [hy]))

/-- Retry idempotence (fixed code): if a batch was accepted, sending the same
batch again appends nothing, returns 0, and does not raise. -/
theorem append_idempotent (d rows d' : List Row) (n : Nat)
    (h : append true d rows = .ok (d', n)) : append true d' rows = .ok (d', 0) := by
  unfold append at h
  cases hf : rows.foldlM (step true) ⟨knownOf d, keysOf d, []⟩ with
  | error e => simp [hf, bind, Except.bind] at h
  | ok sF =>
    simp [hf, bind, Except.bind, pure, Except.pure] at h
    obtain ⟨rfl, -⟩ := h
    have h0 : Inv d [] ⟨knownOf d, keysOf d, []⟩ :=
      ⟨fun i c h => by simpa using h, fun i c h => Or.inl (by simpa using h),
       fun k hk => by simpa using hk, fun r hr => by simp at hr⟩
    have hI := run_inv rows [] _ sF h0 hf
    simp only [List.nil_append] at hI
    obtain ⟨s2, hs2, ha2⟩ := run_noadd rows ⟨knownOf (d ++ sF.adds), keysOf (d ++ sF.adds), []⟩ rfl
      (fun r hr => by
        cases hk : knownOf (d ++ sF.adds) r.id with
        | none => exact Or.inl hk
        | some c =>
          have := hI.L _ _ hk
          rw [hI.F r hr] at this
          exact Or.inr (by show knownOf _ r.id = some r; rw [hk, ← Option.some.inj this]))
      (fun r hr => by
        rcases hI.P _ _ (hI.F r hr) with h | ⟨k, hk, hks⟩
        · exact Or.inl (by show knownOf _ r.id ≠ none; rw [h]; simp)
        · exact Or.inr ⟨k, hk, hI.Q k hks⟩)
      (fun r hr r' hr' he => by
        have a := hI.F r hr; have b := hI.F r' hr'
        rw [he, b] at a; exact (Option.some.inj a).symm)
    unfold append
    simp [hs2, ha2, bind, Except.bind, pure, Except.pure]

end AgentStream

/-! ## 5. `rotate_learning_backup.py` purge (fixed by the coordinator; modelled) -/
namespace Rotate

def isDigit (c : Nat) : Bool := 48 ≤ c && c ≤ 57
def allDigits (l : List Nat) : Bool := l.all isDigit

/-- `\d{8}-\d{6}(?:-\d{6})?` (ASCII digits; Python's `\d` also admits other
Unicode decimal digits, which no stamp contains). -/
def validStamp (s : List Nat) : Bool :=
  (s.length == 15 && allDigits (s.take 8) && s[8]? == some 45 && allDigits (s.drop 9)) ||
  (s.length == 22 && allDigits (s.take 8) && s[8]? == some 45 &&
    allDigits ((s.drop 9).take 6) && s[15]? == some 45 && allDigits (s.drop 16))

/-- Name shape shared by both matchers: `stem + "-" + middle + suffix`. -/
def framed (stem suf name : List Nat) : Bool :=
  (stem ++ [45]).isPrefixOf name && suf.isSuffixOf name &&
    decide (stem.length + 1 + suf.length ≤ name.length)

def middle (stem suf name : List Nat) : List Nat :=
  (name.drop (stem.length + 1)).take (name.length - (stem.length + 1) - suf.length)

/-- Old: `glob(f"{stem}-*{suffix}")` — the middle is anything. -/
def globMatch (stem suf name : List Nat) : Bool := framed stem suf name
/-- New: `_own_backups` — the middle must be a stamp (regex fullmatch). -/
def ownBackup (stem suf name : List Nat) : Bool :=
  framed stem suf name && validStamp (middle stem suf name)

/-- `for old in own[:-KEEP]: unlink` — the deleted names, given a sort. -/
def purge (sel : List Nat → Bool) (sort : List (List Nat) → List (List Nat)) (keep : Nat)
    (files : List (List Nat)) : List (List Nat) :=
  let own := sort (files.filter sel)
  own.take (own.length - keep)

def kept (sel : List Nat → Bool) (sort : List (List Nat) → List (List Nat)) (keep : Nat)
    (files : List (List Nat)) : List (List Nat) :=
  let own := sort (files.filter sel)
  own.drop (own.length - keep)

/-- New code: every deleted name is a backup of THIS file (stem, suffix,
stamp-shaped middle) that was in the directory. -/
theorem purge_only_own (stem suf : List Nat) (le : List Nat → List Nat → Bool) (keep : Nat)
    (files : List (List Nat)) (x : List Nat)
    (hx : x ∈ purge (ownBackup stem suf) (·.mergeSort le) keep files) :
    ownBackup stem suf x = true ∧ x ∈ files := by
  simp only [purge] at hx
  have := ((List.mergeSort_perm _ le).mem_iff).mp (List.mem_of_mem_take hx)
  simp only [List.mem_filter] at this
  exact ⟨this.2, this.1⟩

/-- New code keeps exactly `min KEEP n` of this file's `n` backups. -/
theorem kept_count (sel : List Nat → Bool) (le : List Nat → List Nat → Bool) (keep : Nat)
    (files : List (List Nat)) :
    (kept sel (·.mergeSort le) keep files).length = min keep (files.filter sel).length := by
  simp only [kept, List.length_drop, List.length_mergeSort]
  omega

/-- ... and they are the newest: every deleted backup sorts before every kept
one (the stamp order `_own_backups` sorts by is a total preorder). -/
theorem kept_newest (sel : List Nat → Bool) (le : List Nat → List Nat → Bool) (keep : Nat)
    (files : List (List Nat))
    (htrans : ∀ a b c, le a b = true → le b c = true → le a c = true)
    (htot : ∀ a b, (le a b || le b a) = true)
    (x y : List Nat) (hx : x ∈ purge sel (·.mergeSort le) keep files)
    (hy : y ∈ kept sel (·.mergeSort le) keep files) : le x y = true := by
  simp only [purge, kept] at hx hy
  have hp := List.pairwise_mergeSort htrans htot (files.filter sel)
  rw [← List.take_append_drop (((files.filter sel).mergeSort le).length - keep)
    ((files.filter sel).mergeSort le)] at hp
  exact (List.pairwise_append.mp hp).2.2 x hx y hy

/-! The survey's counterexample, with real code points: `e` stands for
`engrams`, `e-c` for `engrams-candidates`, `.y` for `.yaml`. -/

def stamp (d : Nat) : List Nat := [50, 48, 50, 54, 48, 57, 50, 51, 45, 49, 48, 49, 48, 49, 48 + d]
def name (stem : List Nat) (d : Nat) : List Nat := stem ++ [45] ++ stamp d ++ [46, 121]
def eStem : List Nat := [101]
def cStem : List Nat := [101, 45, 99]
/-- One fresh `engrams` backup beside seven `engrams-candidates` backups. -/
def dir : List (List Nat) := name eStem 9 :: (List.range 7).map (name cStem)

/-- Python `sorted()` on names: code-point lexicographic order. -/
def lexLe : List Nat → List Nat → Bool
  | [], _ => true
  | _ :: _, [] => false
  | a :: as, b :: bs => if a < b then true else if a = b then lexLe as bs else false

def insertBy (le : List Nat → List Nat → Bool) (x : List Nat) : List (List Nat) → List (List Nat)
  | [] => [x]
  | y :: ys => if le x y then x :: y :: ys else y :: insertBy le x ys
def isort (le : List Nat → List Nat → Bool) : List (List Nat) → List (List Nat)
  | [] => []
  | x :: xs => insertBy le x (isort le xs)

/-- Refutation of the old glob: the purge that follows writing the `engrams`
backup deletes exactly that backup (the candidates sort after it, 'c' > '2'). -/
theorem old_glob_deletes_the_new_backup :
    purge (globMatch eStem [46, 121]) (isort lexLe) 7 dir = [name eStem 9] := by decide

/-- The fixed matcher selects only the `engrams` backup, so nothing is deleted. -/
theorem new_shape_selects_own : dir.filter (ownBackup eStem [46, 121]) = [name eStem 9] := by
  decide

theorem new_purge_keeps_it (le : List Nat → List Nat → Bool) :
    purge (ownBackup eStem [46, 121]) (·.mergeSort le) 7 dir = [] := by
  simp [purge, new_shape_selects_own]

end Rotate

end DatacoreSpec.Knowledge
