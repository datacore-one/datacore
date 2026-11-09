/-!
# Credentials — the broker (`creds.py get/doctor`), precedence, routing floor

Models, branch for branch:

* `credential_access.verify_value` and `_entry_verifier` (verifier selection),
  `creds.py` `cmd_get` / `cmd_doctor` (which variable and which index row
  they hand the verifier, and the serve decision), and the broker lock key.
* `credential_access.resolve` + `get_value` versus `env_utils.load_env_files`
  (host `local.env` versus fleet `.env` precedence), duplicate-key parsing,
  and `config_plane.ConfigError`'s message.
* `model_routing.pick_model`'s privacy floor.

Owner decisions applied 2026-09-23: C1 (the n-a notice is unambiguous,
`na_notice_iff`), C2 (every env parser is one last-wins fold,
`all_parsers_agree`), C3 (dmcc's two `floor_exceptions`; the floor holds
for every other task, `pick_respects_floor`, `dmcc_floor_elsewhere`).

What is abstracted, and why it is safe:

* Variable names, ids and URLs are an arbitrary type with decidable equality.
  The verifier tables (`VERIFIERS`, `NO_PROBE`, `OAUTH1_SETS`, the claude CLI
  probe) are a `Tables` record of oracles, and the network is an oracle
  `net : Url → Resp`. Every positive theorem holds for EVERY table and EVERY
  network, so it does not depend on which providers are listed or how they
  answer. The counterexamples instantiate them concretely (with `Nat`).
* An index row is reduced to the fields the verifier reads: provider,
  https `api_base`, `disabled`, and the declared variables.
-/

namespace DatacoreSpec.Credentials

/-! ## 1. Verdicts and verifier selection -/

inductive Verdict | ok | fail | na
  deriving DecidableEq, Repr

inductive Provider | gitea | gitlab | other
  deriving DecidableEq, Repr

/-- A probe: where to send the value and what the body must contain
(`expect = none`: any 2xx passes). -/
structure Spec (U : Type) where
  url    : U
  expect : Option U

inductive Resp (U : Type)
  | ok2xx (body : U)   -- 2xx
  | httpErr            -- urllib HTTPError
  | fault              -- any other exception: n-a, not a failure

/-- The index row, as far as verification sees it. -/
structure Entry (U : Type) where
  id       : U
  primary  : U               -- `var_name or vars[0]`
  vars     : List U
  provider : Option Provider
  apiBase  : Option U        -- only an https base; `_entry_verifier` refuses others
  disabled : Bool

/-- The static tables in `credential_access.py`, as oracles. -/
structure Tables (U : Type) where
  cli       : U → Bool              -- CLAUDE_CODE_OAUTH_TOKEN
  cliProbe  : U → Verdict
  oauth1    : U → Bool              -- OAUTH1_SETS keys
  oauth1Probe : U → Verdict
  noProbe   : U → Bool              -- NO_PROBE
  builtin   : U → Option (Spec U)   -- VERIFIERS
  giteaSpec : U → Spec U            -- base ↦ base/api/v1/user, expect "login"
  gitlabSpec : U → Spec U           -- base ↦ base/api/v4/user, expect "username"

variable {U : Type} [DecidableEq U]

/-- The network, as an oracle: (url, value) ↦ response. -/
abbrev Net (U : Type) := U → U → Resp U

/-- `_entry_verifier`. -/
def entryVerifier (T : Tables U) (e : Entry U) : Option (Spec U) :=
  match e.apiBase with
  | none => none
  | some b =>
    if e.provider = some .gitea then some (T.giteaSpec b)
    else if e.provider = some .gitlab then some (T.gitlabSpec b)
    else some ⟨b, none⟩

/-- The HTTP leg of `verify_value`. `has body s` is "s in body". -/
def probe (net : Net U) (has : U → U → Bool) (empty : U → Bool)
    (s : Spec U) (v : U) : Verdict :=
  if empty v then .fail else
  match net s.url v with
  | .fault => .na
  | .httpErr => .fail
  | .ok2xx body =>
    match s.expect with
    | none => .ok
    | some x => if has body x then .ok else .fail

/-- `verify_value(var, value, entry=e)`, in its branch order. -/
def verify (T : Tables U) (net : Net U) (has : U → U → Bool)
    (empty : U → Bool) (var v : U) (e : Entry U) : Verdict :=
  if T.cli var then T.cliProbe v
  else if e.disabled then .na
  else if T.oauth1 var then T.oauth1Probe v
  else
    let es := entryVerifier T e
    if T.noProbe var ∧ es = none then .na
    else match (T.builtin var).orElse (fun _ => es) with
      | none => .na
      | some s => probe net has empty s v

/-- `entry=None` in `verify_value`. -/
def noEntry (e : Entry U) : Entry U :=
  { e with provider := none, apiBase := none, disabled := false }

/-- `Credential.extra`: every KNOWN_FIELD dropped, so `provider` is gone
while `api_base` and `disabled` survive. -/
def extraOnly (e : Entry U) : Entry U := { e with provider := none }

/-- `_var_for`: the variable the caller asked for, if the entry declares it. -/
def varFor (e : Entry U) (name : U) : U :=
  if name = e.primary ∨ name ∈ e.vars then name else e.primary

/-! ### The code before the fix -/

/-- Old `cmd_get`: verified the PRIMARY variable, with no entry. -/
def getOld (T : Tables U) (net : Net U) (has : U → U → Bool) (empty : U → Bool)
    (e : Entry U) (_name v : U) : Verdict :=
  verify T net has empty e.primary v (noEntry e)

/-- Old `cmd_doctor`: primary variable, entry = `c.extra`. -/
def doctorOld (T : Tables U) (net : Net U) (has : U → U → Bool) (empty : U → Bool)
    (e : Entry U) (v : U) : Verdict :=
  verify T net has empty e.primary v (extraOnly e)

/-! ### The code after the fix -/

/-- `cmd_get`: the variable actually served, and the full row. -/
def getNew (T : Tables U) (net : Net U) (has : U → U → Bool) (empty : U → Bool)
    (e : Entry U) (name v : U) : Verdict :=
  verify T net has empty (varFor e name) v e

/-- `cmd_doctor`: primary variable, full row (`_full_entry`). -/
def doctorNew (T : Tables U) (net : Net U) (has : U → U → Bool) (empty : U → Bool)
    (e : Entry U) (v : U) : Verdict :=
  verify T net has empty e.primary v e

/-- get and doctor now give the same verdict for the same credential:
asking by id (as doctor does) or by the primary variable. -/
theorem get_agrees_with_doctor (T : Tables U) (net : Net U) (has : U → U → Bool)
    (empty : U → Bool) (e : Entry U) (name v : U)
    (hname : name = e.primary ∨ (name = e.id ∧ e.id ∉ e.vars)) :
    getNew T net has empty e name v = doctorNew T net has empty e v := by
  have hv : varFor e name = e.primary := by
    unfold varFor
    rcases hname with h | ⟨h, hn⟩
    · simp [h]
    · subst h
      by_cases hp : e.id = e.primary
      · simp [hp]
      · simp [hp, hn]
  simp [getNew, doctorNew, hv]

/-- The variable get verifies is the variable it serves (for every name). -/
theorem get_verifies_served (T : Tables U) (net : Net U) (has : U → U → Bool)
    (empty : U → Bool) (e : Entry U) (name v : U) :
    getNew T net has empty e name v = verify T net has empty (varFor e name) v e := rfl

/-! ### Counterexamples to the old code (replayed in
`lib/tests/test_credentials_formal.py`) -/

/-- A concrete world. Nat names: 0 = GITEA_TOKEN (NO_PROBE), 1 = OPENAI
(built-in verifier at url 10), 2 = GH_TOKEN (built-in at url 20), 7 = an
https api_base. The network answers 2xx with body 99 ("{}") everywhere, and
99 contains nothing. -/
def T0 : Tables Nat where
  cli _ := false
  cliProbe _ := .na
  oauth1 _ := false
  oauth1Probe _ := .na
  noProbe n := n == 0
  builtin n := if n = 1 then some ⟨10, some 11⟩ else if n = 2 then some ⟨20, some 21⟩ else none
  giteaSpec b := ⟨b + 100, some 5⟩     -- base/api/v1/user, expect "login"
  gitlabSpec b := ⟨b + 200, some 6⟩

def net0 : Net Nat := fun _ _ => .ok2xx 99
def has0 : Nat → Nat → Bool := fun _ _ => false
def empty0 : Nat → Bool := fun _ => false

def gitea : Entry Nat :=
  { id := 50, primary := 0, vars := [0], provider := some .gitea, apiBase := some 7,
    disabled := false }

/-- doctor reported ok (generic probe of the api_base root, any 2xx) for a
value the gitea verifier proves dead. -/
theorem doctor_old_passes_any_2xx :
    doctorOld T0 net0 has0 empty0 gitea 42 = .ok ∧
    doctorNew T0 net0 has0 empty0 gitea 42 = .fail := by
  decide

/-- get served (n-a) a value the full-row verifier proves dead. -/
theorem get_old_serves_dead :
    getOld T0 net0 has0 empty0 gitea 0 42 = .na ∧
    getNew T0 net0 has0 empty0 gitea 0 42 = .fail := by
  decide

def multi : Entry Nat :=
  { id := 51, primary := 1, vars := [1, 2], provider := none, apiBase := none,
    disabled := false }

/-- `creds get GH_TOKEN` served GH_TOKEN's value but verified it as
OPENAI_API_KEY: the value went to url 10 (OpenAI's) instead of url 20. -/
theorem get_old_verifies_wrong_var :
    varFor multi 2 = 2 ∧ multi.primary = 1 ∧ (T0.builtin 1).map (·.url) = some 10 ∧
    (T0.builtin (varFor multi 2)).map (·.url) = some 20 := by
  decide

/-- `disabled` applied by doctor, skipped by get: a verifier-backed variable
(OPENAI, url 10) on a disabled row. Old get probes and says ok; doctor n-a. -/
theorem get_old_ignores_disabled :
    let off : Entry Nat := { multi with disabled := true, vars := [1] }
    let net1 : Net Nat := fun _ _ => .ok2xx 11
    let has1 : Nat → Nat → Bool := fun b s => b == s
    getOld T0 net1 has1 empty0 off 1 42 = .ok ∧
    doctorNew T0 net1 has1 empty0 off 42 = .na ∧
    getNew T0 net1 has1 empty0 off 1 42 = .na := by
  decide

/-! ## 2. The serve decision

Decision C1 (2026-09-23): n-a stays served with exit 0 by default;
`--strict` stays opt-in. The stderr line is the only signal, so it is
modelled as a tagged notice and proved unambiguous: the n-a prefix
(`NA_NOTICE_PREFIX`) is printed exactly when an unverified value is served.
-/

/-- Which stderr line `cmd_get` prints (the model of its fixed prefixes). -/
inductive Notice
  | silent     -- ok, or `--no-verify`: nothing on stderr
  | naServed   -- "creds get: n-a: <id>: served UNVERIFIED on stdout, exit 0 — …"
  | naRefused  -- "creds get: <id>: NOT served, exit 3 — … --strict refuses n-a"
  | dead       -- "<id>: value is DEAD (…). Not served."
  deriving DecidableEq, Repr

structure Outcome where
  served : Bool
  exit   : Nat
  notice : Notice
  deriving DecidableEq, Repr

/-- `cmd_get` after the verdict. -/
def serve (strict noVerify : Bool) (v : Verdict) : Outcome :=
  if noVerify then ⟨true, 0, .silent⟩ else
  match v with
  | .fail => ⟨false, 1, .dead⟩
  | .na   => if strict then ⟨false, 3, .naRefused⟩ else ⟨true, 0, .naServed⟩
  | .ok   => ⟨true, 0, .silent⟩

/-- A value proven dead is never served. -/
theorem serve_never_dead (strict : Bool) (v : Verdict) :
    (serve strict false v).served = true → v ≠ .fail := by
  cases v <;> cases strict <;> simp [serve]

/-- Served ⇒ verified ok and silent, or n-a AND flagged with the n-a notice.
"n-a is never a pass" holds on the stderr channel. -/
theorem serve_ok_or_warned (strict : Bool) (v : Verdict) :
    (serve strict false v).served = true →
      (v = .ok ∧ (serve strict false v).notice = .silent) ∨
      (v = .na ∧ (serve strict false v).notice = .naServed) := by
  cases v <;> cases strict <;> simp [serve]

/-- C1, UNAMBIGUOUS: the n-a notice appears iff the verdict was n-a, the
check was not skipped, and the value was served (no `--strict`). No other
outcome — ok, `--no-verify`, FAIL, strict refusal — prints it. -/
theorem na_notice_iff (strict nv : Bool) (v : Verdict) :
    (serve strict nv v).notice = .naServed ↔
      (nv = false ∧ v = .na ∧ strict = false) := by
  cases nv <;> cases v <;> cases strict <;> simp [serve]

/-- The notice alone determines served/exit: a reader of stderr knows
whether stdout carried a value and what the exit code was. -/
theorem notice_determines_outcome (s₁ s₂ n₁ n₂ : Bool) (v₁ v₂ : Verdict)
    (h : (serve s₁ n₁ v₁).notice = (serve s₂ n₂ v₂).notice) :
    serve s₁ n₁ v₁ = serve s₂ n₂ v₂ := by
  cases n₁ <;> cases n₂ <;> cases v₁ <;> cases v₂ <;> cases s₁ <;> cases s₂ <;>
    simp_all [serve]

/-- Under `--strict`, served ⇒ ok. -/
theorem strict_serves_only_ok (v : Verdict) :
    (serve true false v).served = true → v = .ok := by
  cases v <;> simp [serve]

/-- Exit 0 iff served. -/
theorem exit_zero_iff_served (strict nv : Bool) (v : Verdict) :
    (serve strict nv v).exit = 0 ↔ (serve strict nv v).served = true := by
  cases nv <;> cases v <;> cases strict <;> simp [serve]

/-- Decided residue (C1: serve, warn on stderr): without `--strict`, the exit
status does not distinguish n-a from ok; the notice does (`na_notice_iff`). -/
theorem default_exit_hides_na :
    (serve false false .na).exit = (serve false false .ok).exit := rfl

/-! ## 3. The broker lock key -/

/-- Old: the caller's raw argument. New: the resolved entry's id. -/
def lockOld (name : U) : U := name
def lockNew (entryOf : U → Option (Entry U)) (name : U) : Option U :=
  (entryOf name).map (·.id)

omit [DecidableEq U] in
theorem lock_key_canonical (entryOf : U → Option (Entry U)) (a b : U) (e : Entry U)
    (ha : entryOf a = some e) (hb : entryOf b = some e) :
    lockNew entryOf a = lockNew entryOf b := by
  simp [lockNew, ha, hb]

def gem : Entry Nat := { multi with id := 3, primary := 4, vars := [4] }
def gemOf (n : Nat) : Option (Entry Nat) := if n = 3 ∨ n = 4 then some gem else none

/-- `creds get gem` (id 3) and `creds get GEMINI_API_KEY` (var 4) resolve to
one credential but locked different files; fixed, they share the id's lock. -/
theorem lock_old_splits :
    gemOf 3 = gemOf 4 ∧ lockOld 3 ≠ lockOld 4 ∧ lockNew gemOf 3 = lockNew gemOf 4 :=
  ⟨by simp [gemOf], by decide, by decide⟩

/-! ## 4. Precedence: `resolve`/`get_value` vs `load_env_files` -/

/-- One env file, already parsed (per-key value). -/
abbrev File (K : Type) := K → Option String

/-- `get_value` for a credential with no declared store: local.env wins if it
defines the variable, else scope=instance reads local.env, else `.env`. -/
def getVal {K} (host fleet : File K) (instScope : Bool) (k : K) : Option String :=
  match host k with
  | some v => some v
  | none => if instScope then host k else fleet k

/-- One file of `load_env_files`' loop, on the pending map. -/
def loadStep {K} (override : Bool) (pre : File K) (pending : File K) (f : File K) : File K :=
  fun k => match f k with
    | none => pending k
    | some v =>
      if override then some v
      else if (pre k).isSome ∨ (pending k).isSome then pending k else some v

/-- `os.environ[k]` after the call. -/
def environAfter {K} (override : Bool) (pre : File K) (files : List (File K)) (k : K) :
    Option String :=
  match (files.foldl (loadStep override pre) (fun _ => none)) k with
  | some v => some v
  | none => pre k

def defaultOld {K} (_override : Bool) (host fleet : File K) : List (File K) := [fleet, host]
def defaultNew {K} (override : Bool) (host fleet : File K) : List (File K) :=
  if override then [fleet, host] else [host, fleet]

/-- After the fix, whenever `creds get` serves a value, `load_env_files()`
exports the same one (for a variable the process has not already set, or
under override). -/
theorem load_agrees_with_resolve {K} (override instScope : Bool) (pre host fleet : File K)
    (k : K) (v : String) (hpre : pre k = none ∨ override = true)
    (hget : getVal host fleet instScope k = some v) :
    environAfter override pre (defaultNew override host fleet) k = some v := by
  unfold getVal at hget
  cases override <;> simp at hpre <;>
    simp [environAfter, defaultNew, List.foldl, loadStep] <;>
    cases hh : host k <;> cases hf : fleet k <;> cases instScope <;>
    simp_all

/-- Before the fix: fleet beat host for every default caller. -/
theorem load_old_fleet_beats_host :
    let host : File Nat := fun _ => some "host"
    let fleet : File Nat := fun _ => some "fleet"
    getVal host fleet false 0 = some "host" ∧
    environAfter false (fun _ => none) (defaultOld false host fleet) 0 = some "fleet" := by
  simp [getVal, environAfter, defaultOld, List.foldl, loadStep]

/-! ### Duplicate keys inside one file — decision C2: last wins everywhere

A file is its list of assignments `(key, decoded value)` in file order
(comments, blanks and `export ` already dropped; each parser's own value
decoding is the `V` it stores, and is not what this section is about). -/

section Dup
variable {K V : Type} [DecidableEq K]

/-- The first assignment of `k` (the OLD `_read_var`). -/
def firstWins : List (K × V) → K → Option V
  | [], _ => none
  | p :: l, k => if p.1 = k then some p.2 else firstWins l k

/-- The last assignment of `k`: what shell `source` and systemd see. -/
def lastWins : List (K × V) → K → Option V
  | [], _ => none
  | p :: l, k => match lastWins l k with
    | some v => some v
    | none => if p.1 = k then some p.2 else none

/-- THE ONE FOLD: every parser inserts each line into a map, a later line
overwriting an earlier one (`vals[k] = v` / `result[key] = …`). -/
def assign (m : K → Option V) (p : K × V) : K → Option V :=
  fun k => if k = p.1 then some p.2 else m k
def parseFold (l : List (K × V)) : K → Option V := l.foldl assign (fun _ => none)

/-- `_read_var` after C2: scan every line, keep the last match of `k`. -/
def readVarNew (l : List (K × V)) (k : K) : Option V :=
  l.foldl (fun acc p => if p.1 = k then some p.2 else acc) none
/-- `_vars_in` and `config_plane.load`: the dict fold. -/
def varsIn (l : List (K × V)) : K → Option V := parseFold l
def configLoad (l : List (K × V)) : K → Option V := parseFold l
/-- `env_utils.parse_env_file` after C2: no longer refuses a duplicate. -/
def parseEnvFile (l : List (K × V)) : Option (K → Option V) := some (parseFold l)

theorem foldl_assign (k : K) : ∀ (l : List (K × V)) (m : K → Option V),
    (l.foldl assign m) k = match lastWins l k with | some v => some v | none => m k
  | [], m => by simp [lastWins]
  | p :: l, m => by
    simp only [List.foldl, lastWins]
    rw [foldl_assign k l (assign m p)]
    cases lastWins l k with
    | some v => rfl
    | none =>
      simp only [assign]
      by_cases h : p.1 = k
      · simp [h]
      · have : k ≠ p.1 := fun e => h e.symm
        simp [h, this]

theorem foldl_readVar (k : K) : ∀ (l : List (K × V)) (acc : Option V),
    l.foldl (fun acc p => if p.1 = k then some p.2 else acc) acc =
      match lastWins l k with | some v => some v | none => acc
  | [], acc => by simp [lastWins]
  | p :: l, acc => by
    simp only [List.foldl, lastWins]
    rw [foldl_readVar k l]
    cases lastWins l k with
    | some v => rfl
    | none => by_cases h : p.1 = k <;> simp [h]

theorem parseFold_last (l : List (K × V)) (k : K) : parseFold l k = lastWins l k := by
  unfold parseFold; rw [foldl_assign]; cases lastWins l k <;> rfl

/-- C2: ALL FOUR PARSERS AGREE — each is the one fold, which is last-wins. -/
theorem all_parsers_agree (l : List (K × V)) (k : K) :
    readVarNew l k = lastWins l k ∧ varsIn l k = lastWins l k ∧
    configLoad l k = lastWins l k ∧ (parseEnvFile l).map (· k) = some (lastWins l k) := by
  refine ⟨?_, parseFold_last l k, parseFold_last l k, by simp [parseEnvFile, parseFold_last]⟩
  unfold readVarNew; rw [foldl_readVar]; cases lastWins l k <;> rfl

/-- How often `k` is assigned; `creds doctor` lists `k` when this is ≥ 2
(`credential_access.duplicate_keys`). -/
def count (l : List (K × V)) (k : K) : Nat := (l.filter (fun p => decide (p.1 = k))).length

theorem lastWins_none_of_count_zero (k : K) : ∀ (l : List (K × V)), count l k = 0 → lastWins l k = none
  | [], _ => rfl
  | p :: l, h => by
    by_cases hp : p.1 = k
    · simp [count, List.filter, hp] at h
    · simp [count, List.filter, hp] at h
      simp [lastWins, lastWins_none_of_count_zero k l (by simpa [count] using h), hp]

/-- The change C2 makes is confined to what doctor lists: on a key assigned
at most once, the old first-wins broker and the new last-wins broker serve
the same value. -/
theorem unlisted_key_unchanged (k : K) : ∀ (l : List (K × V)), count l k ≤ 1 →
    firstWins l k = lastWins l k
  | [], _ => rfl
  | p :: l, h => by
    by_cases hp : p.1 = k
    · have h0 : count l k = 0 := by simp [count, List.filter, hp] at h ⊢; omega
      simp [firstWins, lastWins, lastWins_none_of_count_zero k l h0, hp]
    · have h1 : count l k ≤ 1 := by simpa [count, List.filter, hp] using h
      simp [firstWins, lastWins, hp, unlisted_key_unchanged k l h1]
      cases lastWins l k <;> rfl

end Dup

/-- Before C2: `_read_var` (first) and `_vars_in`/`config_plane.load`/shell
`source` (last) served different values for `K=a / K=b`, and
`env_utils.parse_env_file` raised. -/
theorem parsers_disagreed :
    firstWins [(0, 1), (0, 2)] 0 ≠ lastWins [(0, 1), (0, 2)] (0 : Nat) := by
  decide

/-! ### `ConfigError` redaction -/

inductive Reason | noEq | badKey
  deriving DecidableEq

/-- An error line: number, reason, and whatever text of the line it echoes. -/
structure ErrLine where
  lineno : Nat
  reason : Reason
  echo   : Option String
  deriving DecidableEq

def errOld (n : Nat) (r : Reason) (raw key : String) : ErrLine :=
  ⟨n, r, some (match r with | .noEq => raw | .badKey => key)⟩
def errNew (n : Nat) (r : Reason) (_raw _key : String) : ErrLine := ⟨n, r, none⟩

/-- The message is a function of (line number, reason) alone. -/
theorem config_error_redacted (n : Nat) (r : Reason) (raw₁ key₁ raw₂ key₂ : String) :
    errNew n r raw₁ key₁ = errNew n r raw₂ key₂ ∧ (errNew n r raw₁ key₁).echo = none :=
  ⟨rfl, rfl⟩

theorem config_error_old_leaks :
    (errOld 1 .noEq "sk_fake" "").echo = some "sk_fake" := rfl

/-! ## 5. `model_routing.pick_model`: the privacy floor -/

section Routing
variable {C : Type} [DecidableEq C]

/-- `_privacy_rank` for a task/result class: outside the levels ranks 0. -/
def rankT (L : List C) (c : C) : Nat := if c ∈ L then L.idxOf c else 0

structure Venture (C : Type) where
  esc     : C → Option C
  default : Option C       -- `default_class` (absent ⇒ the task itself)
  floor   : Option C
  /-- `floor_exceptions` (decision C3, 2026-09-23): task types exempt from
  THIS venture's floor. Shipped: dmcc's client-report and contract-review. -/
  exc     : C → Bool

/-- Old `pick_model`: floor applied BEFORE escalation, which then overwrites
it; constraints ranked with `rankT` (unknown ⇒ 0, i.e. no constraint).
`none` = ValueError. -/
def pickOld (L : List C) (isClass : C → Bool) (task : C) (ven : Option (Venture C))
    (pc : Option C) : Option C :=
  let e1 := match ven with
    | none => task
    | some v =>
      let ef := match v.floor with
        | some f => if rankT L task < rankT L f then f else task
        | none => task
      match v.esc task with
      | some c => c
      | none => if isClass task then ef else v.default.getD task
  if isClass e1 = false then none else
  match pc with
  | some p => some (if rankT L e1 < rankT L p then p else e1)
  | none => some e1

/-- One constraint: an unknown constraint is an error (fail closed); else
raise the class to the constraint if it ranks below it. -/
def bump (L : List C) (e f : C) : Option C :=
  if f ∈ L then some (if rankT L e < rankT L f then f else e) else none

/-- The venture floor that applies to `task`: none for a listed exception. -/
def ventureFloor (task : C) (ven : Option (Venture C)) : Option C :=
  ven.bind (fun v => if v.exc task then none else v.floor)

/-- The constraints that apply: venture floor (unless `task` is one of the
venture's exceptions), the task's own privacy level, the caller's
`privacy_class` — in that order, as in the Python. -/
def constraints (L : List C) (task : C) (ven : Option (Venture C)) (pc : Option C) : List C :=
  (ventureFloor task ven).toList ++ (if task ∈ L then [task] else []) ++ pc.toList

/-- Venture escalation / default, before any constraint. -/
def escalate (isClass : C → Bool) (task : C) (ven : Option (Venture C)) : C :=
  match ven with
  | none => task
  | some v =>
    match v.esc task with
    | some c => c
    | none => if isClass task then task else v.default.getD task

/-- New `pick_model`: escalate first, then apply every constraint. -/
def pickNew (L : List C) (isClass : C → Bool) (task : C) (ven : Option (Venture C))
    (pc : Option C) : Option C :=
  match (constraints L task ven pc).foldlM (bump L) (escalate isClass task ven) with
  | none => none
  | some e => if isClass e then some e else none

theorem bump_mono (L : List C) (e f r : C) (h : bump L e f = some r) :
    rankT L e ≤ rankT L r ∧ rankT L f ≤ rankT L r := by
  unfold bump at h
  split at h
  · cases h
    split <;> omega
  · cases h

theorem fold_bump (L : List C) : ∀ (cs : List C) (e r : C),
    cs.foldlM (bump L) e = some r → rankT L e ≤ rankT L r ∧ ∀ f ∈ cs, rankT L f ≤ rankT L r
  | [], e, r, h => by
    simp [List.foldlM] at h; subst h; simp
  | f :: cs, e, r, h => by
    simp only [List.foldlM] at h
    cases hb : bump L e f with
    | none => simp [hb] at h
    | some e' =>
      rw [hb] at h
      have ⟨h1, h2⟩ := bump_mono L e f e' hb
      have ⟨h3, h4⟩ := fold_bump L cs e' r h
      refine ⟨by omega, ?_⟩
      intro g hg
      rcases List.mem_cons.mp hg with rfl | hg
      · omega
      · exact h4 g hg

/-- THE GUARANTEE: the class picked ranks at least as private as every
constraint that applies. After C3 the venture floor is among them for every
task EXCEPT the venture's listed exceptions (`pick_respects_floor`); the
task's own level and the caller's `privacy_class` apply to every task. -/
theorem pick_respects_all (L : List C) (isClass : C → Bool) (task : C)
    (ven : Option (Venture C)) (pc : Option C) (r : C)
    (h : pickNew L isClass task ven pc = some r) :
    ∀ f ∈ constraints L task ven pc, rankT L f ≤ rankT L r := by
  unfold pickNew at h
  cases hf : (constraints L task ven pc).foldlM (bump L) (escalate isClass task ven) with
  | none => simp [hf] at h
  | some e =>
    simp [hf] at h
    obtain ⟨_, rfl⟩ := h
    exact (fold_bump L _ _ _ hf).2

/-- The floor holds for all tasks except the listed exceptions. -/
theorem pick_respects_floor (L : List C) isClass task (v : Venture C) pc r f
    (hf : v.floor = some f) (hx : v.exc task = false)
    (h : pickNew L isClass task (some v) pc = some r) :
    rankT L f ≤ rankT L r :=
  pick_respects_all L isClass task (some v) pc r h f
    (by simp [constraints, ventureFloor, hf, hx])

/-- An exception waives the venture floor and nothing else: the constraint
list differs from the no-exception one only by that floor. -/
theorem exception_only_drops_floor (L : List C) (task : C) (v : Venture C) (pc : Option C) :
    constraints L task (some { v with exc := fun _ => false }) pc =
      v.floor.toList ++ (if task ∈ L then [task] else []) ++ pc.toList ∧
    (v.exc task = true →
      constraints L task (some v) pc = (if task ∈ L then [task] else []) ++ pc.toList) := by
  constructor
  · simp [constraints, ventureFloor]
  · intro hx; simp [constraints, ventureFloor, hx]

/-- "Sensitive tasks NEVER escalate": a task that is a privacy level bounds
its own result, whatever the venture's escalation map says. -/
theorem pick_never_escalates_privacy (L : List C) isClass task ven pc r
    (ht : task ∈ L) (h : pickNew L isClass task ven pc = some r) :
    rankT L task ≤ rankT L r :=
  pick_respects_all L isClass task ven pc r h task (by simp [constraints, ht])

theorem pick_respects_privacy_class (L : List C) isClass task ven p r
    (h : pickNew L isClass task ven (some p) = some r) :
    rankT L p ≤ rankT L r :=
  pick_respects_all L isClass task ven (some p) r h p (by simp [constraints])

/-- An unknown constraint is refused, never waived. -/
theorem fold_unknown_none (L : List C) (f : C) (hf : f ∉ L) :
    ∀ (pre post : List C) (e : C), (pre ++ f :: post).foldlM (bump L) e = none
  | [], post, e => by simp [bump, hf]
  | g :: pre, post, e => by
    simp only [List.cons_append, List.foldlM]
    cases bump L e g with
    | none => rfl
    | some e' => exact fold_unknown_none L f hf pre post e'

theorem unknown_constraint_rejected (L : List C) isClass task ven (p : C) (hp : p ∉ L) :
    pickNew L isClass task ven (some p) = none := by
  have h := fold_unknown_none L p hp
    ((ventureFloor task ven).toList ++ (if task ∈ L then [task] else [])) []
    (escalate isClass task ven)
  have : (constraints L task ven (some p)).foldlM (bump L) (escalate isClass task ven) = none := by
    simpa [constraints] using h
  simp [pickNew, this]

end Routing

/-! ### Counterexamples to the old routing (replayed) -/

inductive Cls | pub | internal | sensitive | reasoning | high | contract | report | other
  | confidential
  deriving DecidableEq, Repr

def levels : List Cls := [.pub, .internal, .sensitive]
def isCls : Cls → Bool
  | .contract | .report | .other | .confidential => false
  | _ => true
def dmccEsc (c : Cls) : Option Cls :=
  if c = .contract then some .high else if c = .report then some .reasoning else none
/-- dmcc as shipped before C3: no exceptions. -/
def dmccOld : Venture Cls :=
  { esc := dmccEsc, default := some .internal, floor := some .internal,
    exc := fun _ => false }
/-- dmcc after C3 (routing.yaml `floor_exceptions`). -/
def dmcc : Venture Cls :=
  { dmccOld with exc := fun c => c = .contract || c = .report }

/-- `pick_model("contract-review", venture="dmcc")` → high-stakes (opus),
below dmcc's internal floor, BY ACCIDENT (floor applied before escalation).
The fix made it internal. -/
theorem pick_old_breaks_floor :
    pickOld levels isCls .contract (some dmccOld) none = some .high ∧
    rankT levels Cls.high < rankT levels Cls.internal ∧
    pickNew levels isCls .contract (some dmccOld) none = some .internal := by
  decide

/-- C3: the owner's two exceptions now escalate to frontier ON PURPOSE. -/
theorem dmcc_exceptions_escalate :
    pickNew levels isCls .contract (some dmcc) none = some .high ∧
    pickNew levels isCls .report (some dmcc) none = some .reasoning := by
  decide

/-- C3 is narrow: every other dmcc task keeps the internal floor. -/
theorem dmcc_floor_elsewhere (t : Cls) (ht : t ≠ .contract ∧ t ≠ .report) (pc : Option Cls)
    (r : Cls) (h : pickNew levels isCls t (some dmcc) pc = some r) :
    rankT levels Cls.internal ≤ rankT levels r :=
  pick_respects_floor levels isCls t dmcc pc r .internal rfl
    (by cases t <;> simp_all [dmcc]) h

/-- …and a caller's `privacy_class` still binds the two exceptions. -/
theorem dmcc_exceptions_keep_privacy_class :
    pickNew levels isCls .contract (some dmcc) (some .internal) = some .internal ∧
    pickNew levels isCls .report (some dmcc) (some .sensitive) = some .sensitive := by
  decide

/-- `privacy_class="confidential"` (not a level) constrained nothing. -/
theorem pick_old_unknown_constraint_open :
    pickOld levels isCls .high none (some .confidential) = some .high ∧
    pickNew levels isCls .high none (some .confidential) = none := by
  decide

end DatacoreSpec.Credentials
