/-!
# Authorship — the fold ignores unattested events (ledger-upgrade T6, audit E-F1)

Audit A#10 and D9: `fold()` applied every event by its self-declared `actor`
string. Anything that could write a file could change state, under any name,
in any log -- the 2026-09-25 forgery folded exactly like a genuine event.

T6 states the rule the fold must obey: **an event that is not attested by a
declared principal has no effect on state.**

Owner decision 1 (2026-09-26): no signing yet. So "attested" here is the
authorship binding the write-side gate already enforces, without keys:

* the event's `actor` is a declared writer -- some principal's `writes_as`
  in `registry/principals.yaml` (the roster), and
* the event sits in that writer's own log file: the log's writer (its file
  stem with a `-run-<date>` or `.telemetry` suffix removed) equals `actor`.

The signature conjunct of E-F1 (`verify key e`) returns with FDS-ID; it only
strengthens `attested`, so every theorem below survives adding it.

The model is parametric in the per-event step: whatever the item handlers
do, the authorship filter sits in front of them. `fold_eq_filter` is the shape
the code takes (`ledger.fold.fold` drops unattested events, then runs the
unchanged handlers), so the Python replay of this file is a filter test.
-/

namespace LedgerSpec.Author

/-- A writer name, as it appears in an event's `actor` and in a log's stem. -/
abbrev Writer := String

/-- What authorship needs to know about one event: the `actor` it declares,
the writer of the log file it was read from, and everything else (`body`). -/
structure Ev (B : Type) where
  actor : Writer
  log   : Writer
  body  : B

/-- The declared roster: every name in some principal's `writes_as`. -/
abbrev Roster := List Writer

variable {B S : Type}

/-- Attested: a declared writer, writing in its own log. -/
def attested (r : Roster) (e : Ev B) : Prop := e.actor ∈ r ∧ e.log = e.actor

instance (r : Roster) (e : Ev B) : Decidable (attested r e) :=
  inferInstanceAs (Decidable (e.actor ∈ r ∧ e.log = e.actor))

/-- One fold step: an attested event goes to the handlers, any other event is
skipped. -/
def gate (r : Roster) (step : S → Ev B → S) (acc : S) (e : Ev B) : S :=
  if attested r e then step acc e else acc

/-- The fold, for any per-event step. -/
def fold (r : Roster) (step : S → Ev B → S) (s : S) (evs : List (Ev B)) : S :=
  evs.foldl (gate r step) s

/-- **T6.** Appending an unattested event changes nothing. -/
theorem fold_ignores_unattested (r : Roster) (step : S → Ev B → S) (s : S)
    (evs : List (Ev B)) (e : Ev B) (h : ¬ attested r e) :
    fold r step s (evs ++ [e]) = fold r step s evs := by
  sorry

/-- Wherever it sits in the history, an unattested event changes nothing. -/
theorem fold_ignores_unattested_anywhere (r : Roster) (step : S → Ev B → S) (s : S)
    (pre post : List (Ev B)) (e : Ev B) (h : ¬ attested r e) :
    fold r step s (pre ++ e :: post) = fold r step s (pre ++ post) := by
  sorry

/-- The code's shape: drop the unattested events, then fold the rest with the
unchanged step. -/
theorem fold_eq_filter (r : Roster) (step : S → Ev B → S) (s : S) (evs : List (Ev B)) :
    fold r step s evs = (evs.filter fun e => decide (attested r e)).foldl step s := by
  sorry

/-- An impostor line -- a declared name in someone else's log -- has no effect. -/
theorem impostor_ignored (r : Roster) (step : S → Ev B → S) (s : S)
    (evs : List (Ev B)) (e : Ev B) (h : e.log ≠ e.actor) :
    fold r step s (evs ++ [e]) = fold r step s evs := by
  sorry

/-- An undeclared writer, even in its own log, has no effect. -/
theorem undeclared_ignored (r : Roster) (step : S → Ev B → S) (s : S)
    (evs : List (Ev B)) (e : Ev B) (h : e.actor ∉ r) :
    fold r step s (evs ++ [e]) = fold r step s evs := by
  sorry

/-- Non-vacuity: an attested event is applied exactly as the step says. -/
theorem attested_takes_effect (r : Roster) (step : S → Ev B → S) (s : S)
    (evs : List (Ev B)) (e : Ev B) (h : attested r e) :
    fold r step s (evs ++ [e]) = step (fold r step s evs) e := by
  sorry

end LedgerSpec.Author
