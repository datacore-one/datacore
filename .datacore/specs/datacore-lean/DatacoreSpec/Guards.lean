/-!
# Guards — the decision functions of Datacore's pre-execution guards

Each guard's decision is modelled as a pure function over an abstract command
or payload, branch for branch with the Python. Where the Python classifies
strings (is this token `git`? an option? an env assignment?), the model takes
the classification as an oracle (`Lex`), so a theorem proved for every oracle
holds whatever the strings are. Counterexamples instantiate the oracle at the
Python's actual classification of the handful of tokens involved.

| § | Python | Result |
|---|---|---|
| 1 | `hooks/restricted_hosts_guard.py` | old: refuted (4 bypasses); fixed: **candidate directories are sound** |
| 2 | `tool_policy.decide` / `call_text` / `classify` | never ⇒ deny for all grants: **proved**; S1+Q4: **every input field is matched but Bash's `description`** |
| 3 | `hooks/injection_integrity_guard.py` | old: refuted (any mention clears); fixed: **clears only on a full read**; S2: **armed Bash reads only the spill** |
| 4 | `hooks/log_ownership_guard.py` | old: refuted (evil merge, forged author); S3/S4: **every unpushed write is judged; an unlistable range refuses** |
| 5 | `egress_scan.py --enforce`, `egress_runtime_check.py` | old: refuted (unknowns pass); fixed: **exit 0 ⇒ nothing unknown**; S5: **runtime exit 0 ⇒ something verified** |
| 6 | `hooks.py` retry schedule / validate | old: refuted (one lifetime budget); fixed: **budget per task**; S6: **validation passes only known checks** |
| 7 | `pre_push_scan.py` exit contract | old: refuted (YAMLError ⇒ 1); fixed: **every failure ⇒ 2**; both always block |
| 8 | `commit_gate.decide` | **proved**: allowed ⊎ withheld = dirty, allowed ⊆ produced |

Not modelled: shell quoting and `sh -c` / `eval` / `$( )` unpacking in §1
(covered by the pytest replays), directories only known at run time (the
Python fails closed on them), URL/scp host extraction, regex semantics.
-/

namespace DatacoreSpec.Guards

/-! ## 1. restricted_hosts_guard — a network git subcommand with a restricted remote is blocked -/
namespace Hosts

/-- How the Python classifies one shell token. -/
structure Lex (T : Type) where
  isGit     : T → Bool   -- `Path(t).name == "git"`
  isDash    : T → Bool   -- `t.startswith("-")`
  isAssign  : T → Bool   -- `^[A-Za-z_][A-Za-z0-9_]*=`
  isKeyword : T → Bool   -- `KEYWORDS`
  isWrapper : T → Bool   -- `WRAPPERS`
  isKnown   : T → Bool   -- git, a remote tool, a shell, `eval`
  takesArg  : T → Bool   -- `GIT_OPTS_WITH_ARG`
  isNet     : T → Bool   -- `GIT_NETWORK_SUBCOMMANDS`
  isCd      : T → Bool   -- `cd`, `pushd`

/-- The facts about the token classes the lemmas use. -/
structure Lex.WF {T : Type} (L : Lex T) : Prop where
  git_known      : ∀ t, L.isGit t = true → L.isKnown t = true
  git_not_prefix : ∀ t, L.isGit t = true → L.isKeyword t = false ∧ L.isAssign t = false
  git_not_wrap   : ∀ t, L.isGit t = true → L.isWrapper t = false
  wrap_not_prefix : ∀ t, L.isWrapper t = true → L.isKeyword t = false ∧ L.isAssign t = false

variable {T D : Type} (L : Lex T)

/-- `_invocation`: strip leading keywords and `VAR=value`, then skip a
wrapper's own options to the first recognised command name. -/
def dropPrefix : List T → List T
  | [] => []
  | t :: r => if L.isKeyword t || L.isAssign t then dropPrefix r else t :: r

def skipToKnown : List T → List T
  | [] => []
  | t :: r => if L.isKnown t then t :: r else skipToKnown r

def invocation (seg : List T) : List T :=
  match dropPrefix L seg with
  | [] => []
  | w :: r => if L.isWrapper w then
      (match skipToKnown L r with
       | [] => w :: r
       | x :: xs => x :: xs)
    else w :: r

/-- `_git_subcommand`: the first non-option token, skipping option values. -/
def gitSub : List T → Option T
  | [] => none
  | [t] => if L.takesArg t then none else if L.isDash t then none else some t
  | t :: u :: r =>
    if L.takesArg t then gitSub r else if L.isDash t then gitSub (u :: r) else some t

def netGit (argv : List T) : Bool :=
  match argv with
  | [] => false
  | g :: r => L.isGit g && ((gitSub L r).map L.isNet).getD false

def cdTarget (argv : List T) : Option T :=
  match argv with
  | c :: a :: _ => if L.isCd c then some a else none
  | _ => none

/-- The FIXED guard (`_git_targets` + `offending`): track every directory the
shell could be in — each `cd` applied or not — and block when a network git
runs while any of them has a restricted remote (`bad`). -/
def guard (res : D → T → D) (bad : D → Bool) : List D → List (List T) → Bool
  | _, [] => false
  | S, seg :: rest =>
    match cdTarget L (invocation L seg) with
    | some a => guard res bad (S ++ S.map (res · a)) rest
    | none => (netGit L (invocation L seg) && S.any bad) || guard res bad S rest

/-- What the shell does: each `cd` persists or not (`(cd x); …`, pipelines),
chosen by `cs`; a network git in a directory with a restricted remote reaches
it. -/
def shell (res : D → T → D) (bad : D → Bool) : D → List Bool → List (List T) → Bool
  | _, _, [] => false
  | d, cs, seg :: rest =>
    match cdTarget L (invocation L seg), cs with
    | some a, c :: cs' => shell res bad (if c then res d a else d) cs' rest
    | some _, [] => shell res bad d [] rest
    | none, cs => (netGit L (invocation L seg) && bad d) || shell res bad d cs rest

/-- **Soundness of the fixed guard.** Whatever subset of the `cd`s takes
effect, if the command reaches a restricted remote, the guard blocks it. -/
theorem git_candidates_sound (res : D → T → D) (bad : D → Bool) :
    ∀ (cmd : List (List T)) (S : List D) (d : D) (cs : List Bool),
      d ∈ S → shell L res bad d cs cmd = true → guard L res bad S cmd = true := by
  intro cmd
  induction cmd with
  | nil => intro S d cs _ h; simp [shell] at h
  | cons seg rest ih =>
    intro S d cs hd h
    cases hc : cdTarget L (invocation L seg) with
    | some a =>
      cases cs with
      | nil =>
        simp only [shell, guard, hc] at h ⊢
        exact ih _ d [] (List.mem_append_left _ hd) h
      | cons c cs' =>
        simp only [shell, guard, hc] at h ⊢
        refine ih _ _ cs' ?_ h
        cases c
        · exact List.mem_append_left _ hd
        · exact List.mem_append_right _ (List.mem_map.mpr ⟨d, hd, rfl⟩)
    | none =>
      have hs : shell L res bad d cs (seg :: rest) =
          ((netGit L (invocation L seg) && bad d) || shell L res bad d cs rest) := by
        cases cs <;> simp [shell, hc]
      rw [hs] at h
      simp only [guard, hc]
      simp only [Bool.or_eq_true, Bool.and_eq_true] at h ⊢
      rcases h with ⟨hg, hb⟩ | h
      · exact Or.inl ⟨hg, List.any_eq_true.mpr ⟨d, hd, hb⟩⟩
      · exact Or.inr (ih S d cs hd h)

/-- Leading `VAR=value` assignments do not hide git (`FOO=1 git push`). -/
theorem invocation_strips_assignments (hL : L.WF) (as r : List T) (g : T)
    (has : ∀ a ∈ as, L.isAssign a = true) (hg : L.isGit g = true) :
    invocation L (as ++ g :: r) = g :: r := by
  have hdrop : dropPrefix L (as ++ g :: r) = g :: r := by
    induction as with
    | nil =>
      have := hL.git_not_prefix g hg
      simp [dropPrefix, this.1, this.2]
    | cons a as ih =>
      simp [dropPrefix, has a (by simp), ih (fun x hx => has x (by simp [hx]))]
  simp [invocation, hdrop, hL.git_not_wrap g hg]

/-- A wrapper (`env`, `sudo -u me`, `timeout 30`) does not hide git. -/
theorem invocation_unwraps (hL : L.WF) (w g : T) (xs r : List T)
    (hw : L.isWrapper w = true) (hxs : ∀ x ∈ xs, L.isKnown x = false)
    (hg : L.isGit g = true) :
    invocation L (w :: xs ++ g :: r) = g :: r := by
  have hskip : skipToKnown L (xs ++ g :: r) = g :: r := by
    induction xs with
    | nil => simp [skipToKnown, hL.git_known g hg]
    | cons x xs ih =>
      simp [skipToKnown, hxs x (by simp), ih (fun y hy => hxs y (by simp [hy]))]
  have hw' := hL.wrap_not_prefix w hw
  simp [invocation, dropPrefix, hw'.1, hw'.2, hw, hskip]

/-- An option's value is never read as the subcommand (`git -c k=v push`). -/
theorem gitSub_skips_option_value (o v : T) (r : List T) (ho : L.takesArg o = true) :
    gitSub L (o :: v :: r) = gitSub L r := by
  simp [gitSub, ho]

/-! ### The code before 2026-09-23, and its counterexamples -/

/-- The old `_git_targets`: git only as the FIRST token of the whole command
(operators included), and only `-C`'s value skipped; remotes read in the
payload's cwd only. -/
def oldSub (isC : T → Bool) : List T → Option T
  | [] => none
  | [t] => if L.isDash t then none else some t
  | t :: u :: r => if isC t then oldSub isC r else if L.isDash t then oldSub isC (u :: r) else some t

def oldGuard (isC : T → Bool) (bad : D → Bool) (cwd : D) (whole : List T) : Bool :=
  match whole with
  | [] => false
  | g :: r => L.isGit g && ((oldSub L isC r).map L.isNet).getD false && bad cwd

/-- The Python's classification of every token used below. -/
def py : Lex String where
  isGit     := (· == "git")
  isDash    := (· ∈ ["-c", "-C", "-u", "-i", "--git-dir"])
  isAssign  := (· ∈ ["FOO=1", "k=v", "PATH=/usr/bin"])
  isKeyword := (· ∈ ["!", "if", "then"])
  isWrapper := (· ∈ ["env", "sudo", "timeout", "nohup"])
  isKnown   := (· ∈ ["git", "ssh", "sh", "bash", "eval"])
  takesArg  := (· ∈ ["-C", "-c", "--git-dir"])
  isNet     := (· ∈ ["push", "pull", "fetch"])
  isCd      := (· ∈ ["cd", "pushd"])

def isC : String → Bool := (· == "-C")
def bad : String → Bool := (· == "/bad")
def absCd : String → String → String := fun _ a => a

/-- `cd /bad && git push` from a safe cwd: old allows, the shell reaches /bad,
the fixed guard blocks. -/
theorem old_misses_cd :
    oldGuard py isC bad "/ok" ["cd", "/bad", "&&", "git", "push"] = false ∧
    shell py absCd bad "/ok" [true] [["cd", "/bad"], ["git", "push"]] = true ∧
    guard py absCd bad ["/ok"] [["cd", "/bad"], ["git", "push"]] = true := by decide

/-- `FOO=1 git push`, `env git push`, `git -c k=v push`, each run in /bad. -/
theorem old_misses_prefixes :
    oldGuard py isC bad "/bad" ["FOO=1", "git", "push"] = false ∧
    oldGuard py isC bad "/bad" ["env", "git", "push"] = false ∧
    oldGuard py isC bad "/bad" ["git", "-c", "k=v", "push"] = false ∧
    guard py absCd bad ["/bad"] [["FOO=1", "git", "push"]] = true ∧
    guard py absCd bad ["/bad"] [["env", "git", "push"]] = true ∧
    guard py absCd bad ["/bad"] [["git", "-c", "k=v", "push"]] = true := by decide

/-- Failure behaviour. `Outcome.error unevaluable`: `true` for `Unevaluable`,
`false` for any other exception. `reach` is `_can_reach_network`. -/
inductive Outcome | target (blocked : Bool) | error (unevaluable : Bool)

def exitOld : Outcome → Bool → Nat
  | .target b, _ => if b then 2 else 0
  | .error true, reach => if reach then 2 else 0
  | .error false, _ => 0          -- escaped to `__main__`'s catch-all

def exitNew : Outcome → Bool → Nat
  | .target b, _ => if b then 2 else 0
  | .error _, reach => if reach then 2 else 0

/-- The docstring's promise, "fails closed when it cannot finish evaluating a
command that can reach a network", holds for every exception now … -/
theorem fails_closed (u : Bool) : exitNew (.error u) true = 2 := by cases u <;> rfl
/-- … and did not before (a private list written as a YAML list raised
AttributeError, which exited 0). -/
theorem old_fails_open : exitOld (.error false) true = 0 := rfl

end Hosts

/-! ## 2. tool_policy — never-effects cannot be granted away -/
namespace Policy

variable {E : Type} [DecidableEq E]

inductive Kind | allow | never | cosign | granted
  deriving DecidableEq

/-- `decide`, branch for branch (`granted` already stripped of blanks). -/
def decide' (never cosign granted hit : List E) : Bool × Kind :=
  if hit = [] then (true, .allow)
  else if hit.any (· ∈ never) then (false, .never)
  else if hit.any (fun e => e ∈ cosign && e ∉ granted) then (false, .cosign)
  else (true, .granted)

/-- **A never-effect hit is refused whatever was granted.** -/
theorem never_denies_for_all_grants (never cosign hit : List E)
    (h : ∃ e ∈ hit, e ∈ never) : ∀ granted, (decide' never cosign granted hit).1 = false := by
  intro granted
  obtain ⟨e, he, hn⟩ := h
  have hne : hit ≠ [] := by intro h0; simp [h0] at he
  have hany : hit.any (· ∈ never) = true := List.any_eq_true.mpr ⟨e, he, by simp [hn]⟩
  simp [decide', hne, hany]

/-- An allowed call carries no never-effect and every cosign effect it hits
was granted. -/
theorem allowed_means_permitted (never cosign granted hit : List E)
    (h : (decide' never cosign granted hit).1 = true) :
    (∀ e ∈ hit, e ∉ never) ∧ (∀ e ∈ hit, e ∈ cosign → e ∈ granted) := by
  unfold decide' at h
  split at h
  · rename_i h0; subst h0; simp
  · split at h
    · simp at h
    · rename_i hn
      split at h
      · simp at h
      · rename_i hc
        refine ⟨fun e he hne => hn (List.any_eq_true.mpr ⟨e, he, by simp [hne]⟩), fun e he hco => ?_⟩
        apply Classical.byContradiction
        intro hg
        exact hc (List.any_eq_true.mpr ⟨e, he, by simp [hco, hg]⟩)

/-- `evaluate_hook`: an exception (unreadable policy, unlisted principal) denies. -/
def hookAllows {ε : Type} : Except ε (Bool × Kind) → Bool
  | .ok d => d.1
  | .error _ => false

theorem policy_error_denies {ε : Type} (e : ε) :
    hookAllows (.error e : Except ε (Bool × Kind)) = false := rfl

/-- `classify` for one effect with a non-empty `tools` list: a tool outside it
is never classified, whatever its text. This is why MultiEdit's
`edits[].new_string` and NotebookEdit's `new_source` being unmatched is moot:
neither is an acting tool in `tool_effects.yaml`. -/
def classifies (toolListed toolPat textPat : Bool) : Bool := toolListed && (toolPat || textPat)

theorem unlisted_tool_never_classified (a b : Bool) : classifies false a b = false := rfl

/-- `call_text` until 2026-09-23: the text-key fields if any is present, else
every value (the JSON). -/
def callTextOld (textKeys : List String) (inp : List (String × String)) : List String :=
  let parts := (inp.filter (fun kv => kv.1 ∈ textKeys)).map (·.2)
  if parts = [] then inp.map (·.2) else parts

/-- Counterexample to the old code: a value outside the text keys was unmatched
as soon as one text key was present. -/
theorem old_call_text_field_gap :
    "api.stripe.com/v1/charges" ∉
      callTextOld ["url", "query"] [("url", "https://example.org"), ("body", "api.stripe.com/v1/charges")] := by
  decide

/-- `call_text` under decision S1 alone (2026-09-23, before Q4): the text-key
fields first, then every value (the JSON of the whole input), always. Kept as
the counterexample to Q4 (`s1_matches_bash_description`). -/
def callTextS1 (textKeys : List String) (inp : List (String × String)) : List String :=
  (inp.filter (fun kv => kv.1 ∈ textKeys)).map (·.2) ++ inp.map (·.2)

/-- `_PROSE_FIELDS`: the Bash `description` is prose about the call (decision Q4). -/
def prose (tool key : String) : Bool := tool == "Bash" && key == "description"

/-- `call_text(tool_input, tool_name)` (decisions S1 + Q4): the tool's prose
fields are dropped from the input (`matched`), then the text-key fields of
what is left come first, then every value of what is left (its JSON). -/
def callText (tool : String) (textKeys : List String) (inp : List (String × String)) :
    List String :=
  let matched := inp.filter (fun kv => !prose tool kv.1)
  (matched.filter (fun kv => kv.1 ∈ textKeys)).map (·.2) ++ matched.map (·.2)

/-- **Every field of the input is matched** except the Bash `description`,
whatever else is present (S1 with Q4's exclusion stated). -/
theorem call_text_covers_every_field (tool : String) (textKeys : List String)
    (inp : List (String × String)) (kv : String × String) (h : kv ∈ inp)
    (hp : prose tool kv.1 = false) : kv.2 ∈ callText tool textKeys inp :=
  List.mem_append_right _ (List.mem_map.mpr ⟨kv, List.mem_filter.mpr ⟨h, by simp [hp]⟩, rfl⟩)

/-- Any tool but Bash: every field, the `description` included. -/
theorem call_text_non_bash_covers_every_field (tool : String) (htool : tool ≠ "Bash")
    (textKeys : List String) (inp : List (String × String)) (kv : String × String)
    (h : kv ∈ inp) : kv.2 ∈ callText tool textKeys inp :=
  call_text_covers_every_field tool textKeys inp kv h (by simp [prose, htool])

/-- **Q4: the Bash `description` is not matched.** A value that only the
`description` field carries is absent from Bash's matched text, from its
text-key part and its JSON part alike (both are read from `matched`). -/
theorem call_text_bash_description_unmatched (textKeys : List String)
    (inp : List (String × String)) (d : String)
    (honly : ∀ kv ∈ inp, kv.2 = d → kv.1 = "description") :
    d ∉ callText "Bash" textKeys inp := by
  simp only [callText, List.mem_append, List.mem_map, List.mem_filter]
  rintro (⟨kv, ⟨⟨hin, hnp⟩, _⟩, hv⟩ | ⟨kv, ⟨hin, hnp⟩, hv⟩) <;>
    · have := honly kv hin hv
      simp [prose, this] at hnp

/-- The text-key fields (of what is matched) still come first (what
`detail[:200]` shows). -/
theorem call_text_text_keys_first (tool : String) (textKeys : List String)
    (inp : List (String × String)) :
    ((inp.filter (fun kv => !prose tool kv.1)).filter (fun kv => kv.1 ∈ textKeys)).map (·.2)
      <+: callText tool textKeys inp :=
  List.prefix_append _ _

/-- Counterexample to S1 alone (the behaviour Q4 changes): an innocent
`git status` whose description quotes an effect pattern had that pattern in
its matched text, so `classify` paused it. -/
theorem s1_matches_bash_description :
    "api.stripe.com/v1/charges" ∈
      callTextS1 ["command"] [("command", "git status"), ("description", "api.stripe.com/v1/charges")] := by
  decide

/-- ... and under Q4 it is not (replayed: tests/test_followups_sync.py). -/
theorem q4_drops_bash_description :
    "api.stripe.com/v1/charges" ∉
      callText "Bash" ["command"] [("command", "git status"), ("description", "api.stripe.com/v1/charges")] := by
  decide

end Policy

/-! ## 3. injection_integrity_guard — the gate clears only on a full read -/
namespace Gate

/-- `_covered` after sorting: sweep the ranges, failing at the first gap. -/
def sweep : Nat → List (Nat × Nat) → Nat → Bool
  | reach, [], total => decide (total ≤ reach)
  | reach, (s, e) :: rs, total => if s > reach + 1 then false else sweep (max reach e) rs total

def Covers (rs : List (Nat × Nat)) (n : Nat) : Prop := ∃ r ∈ rs, r.1 ≤ n ∧ n ≤ r.2

/-- The sweep is sound in ANY order (sorting only makes it complete). -/
theorem sweep_sound (total : Nat) :
    ∀ (rs seen : List (Nat × Nat)) (reach : Nat),
      (∀ n, 1 ≤ n → n ≤ reach → Covers seen n) →
      sweep reach rs total = true → ∀ n, 1 ≤ n → n ≤ total → Covers (seen ++ rs) n := by
  intro rs
  induction rs with
  | nil =>
    intro seen reach hinv h n h1 hn
    simp [sweep] at h
    simpa using hinv n h1 (by omega)
  | cons r rs ih =>
    obtain ⟨s, e⟩ := r
    intro seen reach hinv h n h1 hn
    simp only [sweep] at h
    split at h
    · simp at h
    · rename_i hs
      have hinv' : ∀ m, 1 ≤ m → m ≤ max reach e → Covers (seen ++ [(s, e)]) m := by
        intro m hm1 hm
        by_cases hmr : m ≤ reach
        · obtain ⟨q, hq, hq1⟩ := hinv m hm1 hmr
          exact ⟨q, List.mem_append_left _ hq, hq1⟩
        · exact ⟨(s, e), by simp, by simp only; omega, by simp only; omega⟩
      obtain ⟨q, hq, hq1⟩ := ih (seen ++ [(s, e)]) (max reach e) hinv' h n h1 hn
      exact ⟨q, by simpa using hq, hq1⟩

/-- Tool events the `clear` hook sees. -/
inductive Ev (P : Type)
  | read (path : P) (offset limit : Nat)   -- Read with its offset/limit
  | mention (path : P)                     -- the path appears in any input/response

variable {P : Type} [DecidableEq P]

/-- The fixed `clear`: only a Read of the target adds a range. -/
def absorb (target : P) (rs : List (Nat × Nat)) : Ev P → List (Nat × Nat)
  | .read p o l => if p = target ∧ 1 ≤ l then rs ++ [(max 1 o, max 1 o + l - 1)] else rs
  | .mention _ => rs

def FromRead (target : P) (es : List (Ev P)) (r : Nat × Nat) : Prop :=
  ∃ o l, Ev.read target o l ∈ es ∧ r = (max 1 o, max 1 o + l - 1)

/-- Every range the state holds came from a Read of the target. -/
theorem absorb_from_reads (target : P) :
    ∀ (es : List (Ev P)) (rs : List (Nat × Nat)) (seen : List (Ev P)),
      (∀ r ∈ rs, FromRead target seen r) →
      ∀ r ∈ es.foldl (absorb target) rs, FromRead target (seen ++ es) r := by
  intro es
  induction es with
  | nil =>
    intro rs seen h r hr
    obtain ⟨o, l, hm, he⟩ := h r (by simpa using hr)
    exact ⟨o, l, by simpa using hm, he⟩
  | cons e es ih =>
    intro rs seen h r hr
    have := ih (absorb target rs e) (seen ++ [e]) (by
      intro q hq
      cases e with
      | mention p =>
        obtain ⟨o, l, hm, he⟩ := h q hq; exact ⟨o, l, by simp [hm], he⟩
      | read p o l =>
        simp only [absorb] at hq
        split at hq
        · rename_i hc
          rcases List.mem_append.mp hq with hq | hq
          · obtain ⟨o', l', hm, he⟩ := h q hq; exact ⟨o', l', by simp [hm], he⟩
          · simp at hq; exact ⟨o, l, by simp [hc.1], hq⟩
        · obtain ⟨o', l', hm, he⟩ := h q hq; exact ⟨o', l', by simp [hm], he⟩) r hr
    simp at this; exact this

/-- **The gate clears only on a full read.** If the (sorted, by any
permutation `ord`) sweep over the ranges the Read calls produced says
"covered", every line 1..total lies inside one of those Read ranges. -/
theorem gate_clears_only_on_full_read (ord : List (Nat × Nat) → List (Nat × Nat))
    (hord : ∀ l x, x ∈ ord l ↔ x ∈ l) (rs : List (Nat × Nat)) (total : Nat)
    (h : sweep 0 (ord rs) total = true) : ∀ n, 1 ≤ n → n ≤ total → Covers rs n := by
  intro n h1 hn
  obtain ⟨q, hq, hq1⟩ := sweep_sound total (ord rs) [] 0 (fun m h1 h0 => by omega) h n h1 hn
  exact ⟨q, (hord rs q).mp (by simpa using hq), hq1⟩

/-- The end-to-end claim: once the gate clears after events `es`, every line
of the spill lies inside the range of an actual Read of that file in `es`. -/
theorem cleared_means_every_line_read (ord : List (Nat × Nat) → List (Nat × Nat))
    (hord : ∀ l x, x ∈ ord l ↔ x ∈ l) (target : P) (es : List (Ev P)) (total : Nat)
    (h : sweep 0 (ord (es.foldl (absorb target) [])) total = true) :
    ∀ n, 1 ≤ n → n ≤ total → ∃ r, FromRead target es r ∧ r.1 ≤ n ∧ n ≤ r.2 := by
  intro n h1 hn
  obtain ⟨r, hr, hr1⟩ := gate_clears_only_on_full_read ord hord _ total h n h1 hn
  exact ⟨r, by simpa using absorb_from_reads target es [] [] (by simp) r hr, hr1⟩

/-- Mentions never add coverage. -/
theorem mention_is_not_a_read (target p : P) (rs : List (Nat × Nat)) :
    absorb target rs (.mention p) = rs := rfl

/-- The old `clear`: any event whose text contains the path disarms. -/
def clearOld (target : P) : Ev P → Bool
  | .read p _ _ => decide (p = target)
  | .mention p => decide (p = target)

/-- Counterexample: a 10-line peek at a 5000-line spill disarmed the old gate,
and a bare mention (an `ls` listing) did too. The fixed sweep says no. -/
theorem old_clears_on_peek :
    clearOld "spill" (.read "spill" 1 10) = true ∧
    clearOld "spill" (.mention "spill") = true ∧
    sweep 0 (absorb "spill" [] (.read "spill" 1 10)) 5000 = false := by decide

/-- Completeness on the tested paging: three consecutive pages clear. -/
example : sweep 0 [(1, 2000), (2001, 4000), (4001, 5000)] 5000 = true := by decide

/-! ### Bash while the gate is armed (decision S2) -/

/-- What `bash_reads_only` sees of a command: `hasMeta` is any of
`| ; & < > $ \` ( ) { }` or a newline anywhere; `argv` is the `shlex` split,
`none` on unbalanced quotes. -/
structure BashCmd (T : Type) where
  hasMeta : Bool
  argv    : Option (List T)

variable {T : Type}

/-- `bash_reads_only`, branch for branch. Oracles: `readOnly` is membership in
cat/head/tail/sed/grep/wc/less; `operands prog args` is `_opts_ok`, `none` when
an option is not on that program's read-only list, else the file operands;
`same p` is `realpath p == realpath spill`. -/
def bashReadsOnly (readOnly : T → Bool) (operands : T → List T → Option (List T))
    (same : T → Bool) (c : BashCmd T) : Bool :=
  if c.hasMeta then false else
  match c.argv with
  | none => false
  | some [] => false
  | some (prog :: args) =>
    if readOnly prog then
      match operands prog args with
      | some [p] => same p
      | _ => false
    else false

inductive Tool | read | grep | glob | toolSearch | bash | other
  deriving DecidableEq

/-- READERS, which no longer contains Bash. -/
def isReader : Tool → Bool
  | .read | .grep | .glob | .toolSearch => true
  | _ => false

/-- `check`: does the tool run? -/
def checkAllows (readOnly : T → Bool) (operands : T → List T → Option (List T))
    (same : T → Bool) (armed : Bool) (t : Tool) (c : BashCmd T) : Bool :=
  if !armed then true
  else if isReader t then true
  else if t = .bash then bashReadsOnly readOnly operands same c
  else false

/-- **While armed, a Bash call runs only as one read-only program, with no
shell metacharacter, whose only file operand is the spill file.** -/
theorem armed_bash_reads_only_the_spill (readOnly : T → Bool)
    (operands : T → List T → Option (List T)) (same : T → Bool) (c : BashCmd T)
    (h : checkAllows readOnly operands same true .bash c = true) :
    c.hasMeta = false ∧ ∃ prog args p, c.argv = some (prog :: args) ∧
      readOnly prog = true ∧ operands prog args = some [p] ∧ same p = true := by
  simp only [checkAllows, isReader, Bool.not_true] at h
  simp only [bashReadsOnly] at h
  cases hm : c.hasMeta <;> simp [hm] at h
  refine ⟨rfl, ?_⟩
  cases ha : c.argv with
  | none => simp [ha] at h
  | some xs =>
    cases xs with
    | nil => simp [ha] at h
    | cons prog args =>
      simp only [ha] at h
      cases hr : readOnly prog <;> simp [hr] at h
      cases ho : operands prog args with
      | none => simp [ho] at h
      | some ps =>
        simp only [ho] at h
        match ps, h with
        | [p], h => exact ⟨prog, args, p, rfl, hr, ho, h⟩

/-- Every other non-reader tool is denied while armed. -/
theorem armed_denies_other_tools (readOnly : T → Bool) (operands : T → List T → Option (List T))
    (same : T → Bool) (t : Tool) (c : BashCmd T) (hr : isReader t = false) (hb : t ≠ .bash) :
    checkAllows readOnly operands same true t c = false := by
  simp [checkAllows, hr, hb]

/-- Unarmed, nothing is gated. -/
theorem unarmed_allows (readOnly : T → Bool) (operands : T → List T → Option (List T))
    (same : T → Bool) (t : Tool) (c : BashCmd T) :
    checkAllows readOnly operands same false t c = true := rfl

/-- The code before S2: Bash was a reader, so it ran whatever the command. -/
def checkAllowsOld (armed : Bool) (t : Tool) : Bool := !armed || isReader t || t == .bash

/-- Counterexample: `curl …` ran while armed; the fixed check denies it. -/
theorem old_allows_curl_while_armed :
    checkAllowsOld true .bash = true ∧
    checkAllows (· == "cat") (fun _ a => some a) (· == "/spill") true .bash
      ⟨false, some ["curl", "https://example.org"]⟩ = false := by decide

end Gate

/-! ## 4. log_ownership_guard — a write to another actor's log is refused -/
namespace Ownership

variable {A F : Type}

/-- A commit in the push range. `files` is what `git show --cc --name-only`
lists: for a merge, only paths whose result differs from EVERY parent.
`onRemote`: some remote-tracking ref contains it. `writtenBy` is ghost state
(who really made it); the guard never sees it. -/
structure Commit (A F : Type) where
  author    : A
  writtenBy : A
  isMerge   : Bool
  onRemote  : Bool
  files     : List F

/-- Before 2026-09-23: authored here, merges skipped. -/
def refusesOld [DecidableEq A] (me : A) (foreign : F → Bool) (cs : List (Commit A F)) : Bool :=
  cs.any (fun c => !c.isMerge && c.author = me && c.files.any foreign)

/-- The first fix (merges seen), still filtering by author email. -/
def refusesAuthor [DecidableEq A] (me : A) (foreign : F → Bool) (cs : List (Commit A F)) : Bool :=
  cs.any (fun c => c.author = me && c.files.any foreign)

/-- Decision S3: every commit no remote has is judged, whatever its author. -/
def refusesNew (foreign : F → Bool) (cs : List (Commit A F)) : Bool :=
  cs.any (fun c => !c.onRemote && c.files.any foreign)

/-- **Every write to a foreign log that no remote has yet is refused** —
merges and foreign author emails included. -/
theorem unpushed_writes_are_judged (foreign : F → Bool) (cs : List (Commit A F))
    (c : Commit A F) (hc : c ∈ cs) (hr : c.onRemote = false) (f : F) (hf : f ∈ c.files)
    (hfor : foreign f = true) : refusesNew foreign cs = true :=
  List.any_eq_true.mpr ⟨c, hc, by simp [hr]; exact ⟨f, hf, hfor⟩⟩

/-- Merges are judged like any commit (the evil-merge fix, kept). -/
theorem ownership_sees_merge_writes (foreign : F → Bool) (cs : List (Commit A F))
    (c : Commit A F) (hc : c ∈ cs) (_hm : c.isMerge = true) (hr : c.onRemote = false)
    (f : F) (hf : f ∈ c.files) (hfor : foreign f = true) : refusesNew foreign cs = true :=
  unpushed_writes_are_judged foreign cs c hc hr f hf hfor

/-- Honest sync still passes: commits a remote already has (fetched, then
merged) are carried, not written. -/
theorem carried_commits_pass (foreign : F → Bool) (cs : List (Commit A F))
    (h : ∀ c ∈ cs, c.files.any foreign = true → c.onRemote = true) :
    refusesNew foreign cs = false := by
  cases hcs : refusesNew foreign cs with
  | false => rfl
  | true =>
    obtain ⟨c, hc, hx⟩ := List.any_eq_true.mp hcs
    simp only [Bool.and_eq_true, Bool.not_eq_true'] at hx
    have := h c hc hx.2
    simp [this] at hx

/-- Counterexample to the old code: an evil merge. -/
theorem old_misses_evil_merge :
    refusesOld "miles" (· == "winston.jsonl")
      [{ author := "miles", writtenBy := "miles", isMerge := true, onRemote := false,
         files := ["winston.jsonl"] }] = false ∧
    refusesNew (A := String) (· == "winston.jsonl")
      [{ author := "miles", writtenBy := "miles", isMerge := true, onRemote := false,
         files := ["winston.jsonl"] }] = true := by
  decide

/-- Counterexample to the author filter: a commit written here under another
author's email passed; S3 refuses it. -/
theorem author_filter_is_forgeable :
    refusesAuthor "miles" (· == "winston.jsonl")
      [{ author := "winston", writtenBy := "miles", isMerge := false, onRemote := false,
         files := ["winston.jsonl"] }] = false ∧
    refusesNew (A := String) (· == "winston.jsonl")
      [{ author := "winston", writtenBy := "miles", isMerge := false, onRemote := false,
         files := ["winston.jsonl"] }] = true := by
  decide

/-- `main` over one range: `none` when `git rev-list` (or a `git show`)
cannot list it. Before S4 an unlistable range was allowed. -/
def verdictOld (foreign : F → Bool) : Option (List (Commit A F)) → Bool
  | none => false
  | some cs => refusesNew foreign cs

def verdict (foreign : F → Bool) : Option (List (Commit A F)) → Bool
  | none => true
  | some cs => refusesNew foreign cs

/-- **Decision S4: a range that cannot be listed refuses the push.** -/
theorem unlistable_range_refuses (foreign : F → Bool) :
    verdict (A := A) foreign none = true := rfl

theorem old_unlistable_range_allowed (foreign : F → Bool) :
    verdictOld (A := A) foreign none = false := rfl

end Ownership

/-! ## 5. egress_scan --enforce / egress_runtime_check -/
namespace Egress

/-- What `scan_module` returns, as far as the verdict sees it. -/
inductive Scan
  | error                                   -- manifest unreadable
  | ok (optedIn : Bool) (undeclared undecorated badKind unparsed : Nat)

def badOld : Scan → Nat
  | .error => 0                             -- printed, then `continue`
  | .ok o u d k _ => (if o then u else 0) + d + k

def badNew : Scan → Nat
  | .error => 1
  | .ok o u d k p => (if o then u + p else 0) + d + k

def total (bad : Scan → Nat) : List Scan → Nat
  | [] => 0
  | m :: ms => bad m + total bad ms

def enforce (bad : Scan → Nat) (ms : List Scan) : Nat :=
  if total bad ms = 0 then 0 else 1

theorem total_zero (bad : Scan → Nat) :
    ∀ ms, total bad ms = 0 → ∀ m ∈ ms, bad m = 0 := by
  intro ms
  induction ms with
  | nil => intro _ m hm; simp at hm
  | cons x xs ih =>
    intro h m hm
    simp only [total] at h
    rcases List.mem_cons.mp hm with rfl | hm
    · omega
    · exact ih (by omega) m hm

/-- **Fixed:** `--enforce` exiting 0 means no manifest error and nothing
unparseable or undeclared in an opted-in module. -/
theorem enforce_counts_unknowns (ms : List Scan) (h : enforce badNew ms = 0) :
    ∀ m ∈ ms, m ≠ .error ∧ ∀ u d k p, m = .ok true u d k p → p = 0 ∧ u = 0 := by
  unfold enforce at h
  split at h
  · rename_i hs
    intro m hm
    have hz := total_zero badNew ms hs m hm
    refine ⟨fun he => by simp [he, badNew] at hz, fun u d k p hm' => ?_⟩
    subst hm'; simp [badNew] at hz; omega
  · simp at h

theorem old_passes_broken_manifest : enforce badOld [.error] = 0 := by decide

/-- `egress_runtime_check` rows: `some true` ok, `some false` FAIL, `none` n-a.
Before S5 the exit code was 0 when every row was n-a. -/
def runtimeExitOld (rows : List (Option Bool)) : Nat :=
  if rows.any (· == some false) then 1 else 0

theorem old_all_na_exits_zero (n : Nat) : runtimeExitOld (List.replicate n none) = 0 := by
  induction n with
  | zero => rfl
  | succ n ih => simp_all [runtimeExitOld, List.replicate_succ]

/-- Decision S5: 1 on a broken row, else 3 when no row is verified, else 0.
(`--functional` failing returns 1 first; not modelled.) -/
def runtimeExit (rows : List (Option Bool)) : Nat :=
  if rows.any (· == some false) then 1
  else if rows.any (· == some true) then 0
  else 3

/-- **Exit 0 means something was verified and nothing is broken.** -/
theorem runtime_exit_zero_means_something_verified (rows : List (Option Bool))
    (h : runtimeExit rows = 0) : some true ∈ rows ∧ some false ∉ rows := by
  unfold runtimeExit at h
  split at h
  · simp at h
  · rename_i hb
    split at h
    · rename_i ht
      obtain ⟨x, hx, hx1⟩ := List.any_eq_true.mp ht
      refine ⟨by simpa using (show x = some true by simpa using hx1) ▸ hx, ?_⟩
      intro hf; exact hb (List.any_eq_true.mpr ⟨_, hf, by simp⟩)
    · simp at h

theorem runtime_all_na_exits_three (n : Nat) : runtimeExit (List.replicate n none) = 3 := by
  induction n with
  | zero => rfl
  | succ n ih => simp_all [runtimeExit, List.replicate_succ]

end Egress

/-! ## 6. hooks.py — the retry budget -/
namespace Retry

variable {K : Type} [DecidableEq K]

/-- `_hook_retry_schedule` on a counter map. -/
def step (max : Nat) (cnt : K → Nat) (k : K) : Bool × (K → Nat) :=
  if max ≤ cnt k then (false, cnt) else (true, fun j => if j = k then cnt k + 1 else cnt j)

def run (max : Nat) (key : K → K) : (K → Nat) → List K → (K → Nat)
  | cnt, [] => cnt
  | cnt, t :: ts => run max key (step max cnt (key t)).2 ts

/-- **Fixed (keyed by task):** errors of OTHER tasks never touch this task's
budget, so its first error retries. -/
theorem step_other (max : Nat) (cnt : K → Nat) (j k : K) (h : j ≠ k) :
    (step max cnt j).2 k = cnt k := by
  simp only [step]; split
  · rfl
  · simp [Ne.symm h]

theorem retry_budget_is_per_task (max : Nat) (hmax : 0 < max) (k : K) :
    ∀ (hist : List K) (cnt : K → Nat), cnt k = 0 → (∀ j ∈ hist, j ≠ k) →
      (step max (run max id cnt hist) k).1 = true := by
  intro hist
  induction hist with
  | nil =>
    intro cnt h0 _
    have hle : ¬ max ≤ cnt k := by omega
    simp [run, step, hle]
  | cons j hist ih =>
    intro cnt h0 hne
    apply ih
    · show (step max cnt (id j)).2 k = 0
      rw [step_other max cnt (id j) k (hne j (by simp))]; exact h0
    · intro x hx; exact hne x (by simp [hx])

/-- The old code: every task's key is `"default"`. After three transient
errors of task A, task B's first error is not retried. -/
theorem old_budget_is_global :
    (step 3 (run 3 (fun _ => "default") (fun _ => 0) ["A", "A", "A"]) "default").1 = false := by
  decide

/-- Before S6: `execute_validate_hooks` skipped unknown hook types, so a list
of only unknown (e.g. misspelt) validators passed. -/
def validateOld (known : String → Bool) (check : String → Bool) : List String → Bool
  | [] => true
  | h :: hs => if known h then (check h && validateOld known check hs) else validateOld known check hs

theorem old_unknown_validators_pass (known check : String → Bool) (hs : List String)
    (h : ∀ x ∈ hs, known x = false) : validateOld known check hs = true := by
  induction hs with
  | nil => rfl
  | cons x xs ih => simp [validateOld, h x (by simp), ih (fun y hy => h y (by simp [hy]))]

/-- Decision S6: an unknown hook type fails validation. -/
def validate (known : String → Bool) (check : String → Bool) : List String → Bool
  | [] => true
  | h :: hs => known h && check h && validate known check hs

/-- **Validation passes only when every hook is known and its check passed.** -/
theorem validate_passes_only_known (known check : String → Bool) :
    ∀ hs, validate known check hs = true → ∀ x ∈ hs, known x = true ∧ check x = true := by
  intro hs
  induction hs with
  | nil => intro _ x hx; simp at hx
  | cons y ys ih =>
    intro h x hx
    simp only [validate, Bool.and_eq_true] at h
    rcases List.mem_cons.mp hx with rfl | hx
    · exact ⟨h.1.1, h.1.2⟩
    · exact ih h.2 x hx

end Retry

/-! ## 7. pre_push_scan — exit contract 0 clean / 1 violation / 2 scanner error -/
namespace PrePush

inductive Failure | yamlError | notMapping | osError | importError | valueError | other

inductive Run | clean | violation | fail (f : Failure)

def exitOld : Run → Nat
  | .clean => 0
  | .violation => 1
  | .fail .osError | .fail .importError | .fail .valueError => 2
  | .fail _ => 1                 -- uncaught: Python's traceback exit status

def exitNew : Run → Nat
  | .clean => 0
  | .violation => 1
  | .fail _ => 2

theorem every_failure_is_exit_2 (f : Failure) : exitNew (.fail f) = 2 := rfl
theorem old_yaml_error_reads_as_violation : exitOld (.fail .yamlError) = 1 := rfl

/-- Safety was never lost: the hook blocks on any non-zero exit, old or new. -/
theorem failures_always_block (f : Failure) : exitOld (.fail f) ≠ 0 ∧ exitNew (.fail f) ≠ 0 := by
  cases f <;> decide

/-- Empty stdin is "clean", but the hook never calls the scanner then: it
exits on an empty outgoing range first (DOWNGRADED). -/
def hookScans (range : List String) : Bool := !range.isEmpty

theorem scanner_only_sees_nonempty (range : List String) (h : hookScans range = true) :
    range ≠ [] := by
  intro h0; simp [h0, hookScans] at h

end PrePush

/-! ## 8. commit_gate.decide — the partition -/
namespace CommitGate

variable {P : Type} [DecidableEq P]

def decide' (dirty : List P) : Option (List P) → List P × List P
  | none => ([], dirty)
  | some w => (dirty.filter (· ∈ w), dirty.filter (· ∉ w))

theorem partition (dirty : List P) (produced : Option (List P)) (p : P) :
    p ∈ dirty ↔ p ∈ (decide' dirty produced).1 ∨ p ∈ (decide' dirty produced).2 := by
  cases produced with
  | none => simp [decide']
  | some w => by_cases h : p ∈ w <;> simp [decide', h]

theorem disjoint (dirty : List P) (produced : Option (List P)) (p : P)
    (h : p ∈ (decide' dirty produced).1) : p ∉ (decide' dirty produced).2 := by
  cases produced with
  | none => simp [decide'] at h
  | some w => simp_all [decide']

theorem allowed_produced (dirty : List P) (produced : Option (List P)) (p : P)
    (h : p ∈ (decide' dirty produced).1) : ∃ w, produced = some w ∧ p ∈ w := by
  cases produced with
  | none => simp [decide'] at h
  | some w => exact ⟨w, rfl, by simp_all [decide']⟩

theorem sizes (dirty : List P) (produced : Option (List P)) :
    (decide' dirty produced).1.length + (decide' dirty produced).2.length = dirty.length := by
  cases produced with
  | none => simp [decide']
  | some w =>
    simp only [decide']
    induction dirty with
    | nil => rfl
    | cons x xs ih =>
      by_cases h : x ∈ w
      · simp only [List.filter_cons, h, decide_true, decide_not, Bool.not_true, List.length_cons] at ih ⊢
        simp only [ite_true, List.length_cons, Bool.false_eq_true, ite_false] at ih ⊢
        omega
      · simp only [List.filter_cons, h, decide_false, decide_not, Bool.not_false, List.length_cons] at ih ⊢
        simp only [ite_true, List.length_cons, Bool.false_eq_true, ite_false] at ih ⊢
        omega

end CommitGate

end DatacoreSpec.Guards
