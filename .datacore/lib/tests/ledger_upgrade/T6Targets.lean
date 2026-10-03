import LedgerSpec.Author

/-!
Eval-owned statement of T6 (ledger-upgrade Phase 1). The model
`LedgerSpec/Author.lean` must prove these, sorry-free, with `attested`
meaning exactly "a declared writer, in its own log". An implementer cannot
weaken a statement here by editing the model: a weaker `attested` breaks
`attested_is_authorship`, a weaker theorem fails to typecheck below.
-/

open LedgerSpec.Author

variable {B S : Type}

theorem t6_attested_is_authorship (r : Roster) (e : Ev B) :
    attested r e ↔ (e.actor ∈ r ∧ e.log = e.actor) := Iff.rfl

theorem t6_fold_ignores_unattested (r : Roster) (step : S → Ev B → S) (s : S)
    (evs : List (Ev B)) (e : Ev B) (h : ¬ attested r e) :
    fold r step s (evs ++ [e]) = fold r step s evs :=
  fold_ignores_unattested r step s evs e h

theorem t6_anywhere (r : Roster) (step : S → Ev B → S) (s : S)
    (pre post : List (Ev B)) (e : Ev B) (h : ¬ attested r e) :
    fold r step s (pre ++ e :: post) = fold r step s (pre ++ post) :=
  fold_ignores_unattested_anywhere r step s pre post e h

theorem t6_filter (r : Roster) (step : S → Ev B → S) (s : S) (evs : List (Ev B)) :
    fold r step s evs = (evs.filter fun e => decide (attested r e)).foldl step s :=
  fold_eq_filter r step s evs

theorem t6_impostor (r : Roster) (step : S → Ev B → S) (s : S)
    (evs : List (Ev B)) (e : Ev B) (h : e.log ≠ e.actor) :
    fold r step s (evs ++ [e]) = fold r step s evs :=
  impostor_ignored r step s evs e h

theorem t6_undeclared (r : Roster) (step : S → Ev B → S) (s : S)
    (evs : List (Ev B)) (e : Ev B) (h : e.actor ∉ r) :
    fold r step s (evs ++ [e]) = fold r step s evs :=
  undeclared_ignored r step s evs e h

theorem t6_non_vacuous (r : Roster) (step : S → Ev B → S) (s : S)
    (evs : List (Ev B)) (e : Ev B) (h : attested r e) :
    fold r step s (evs ++ [e]) = step (fold r step s evs) e :=
  attested_takes_effect r step s evs e h

#print axioms t6_fold_ignores_unattested
#print axioms t6_anywhere
#print axioms t6_filter
#print axioms t6_impostor
#print axioms t6_undeclared
#print axioms t6_non_vacuous
