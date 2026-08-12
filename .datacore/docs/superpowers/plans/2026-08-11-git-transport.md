# DIP-0046 Implementation Plan — Git as Ledger Transport (rev 2)

> Spec: `.datacore/dips/DIP-0046-git-transport.md` (branch `dip/0046-git-transport`)
> Revised after four evaluators (critic, cto 0.58, coo, dijkstra 0.74) — **none approved**.

**Global constraints**

- Python for `.datacore/lib/`. Never multi-line Bash; chain with `&&`.
- stdlib-only to import `ledger/`.
- Every detector emits `metric.attest` + a **dated artifact**; green requires the
  artifact to exist and be fresh (ENG-2026-0804-033).
- Every item is verified by **causing the failure it prevents**.
- `~/Data` is a shared tree — never `git checkout <other-branch>` in it.
- `.datacore/dips` and `.datacore/modules/*` are separate repos; never
  auto-commit a submodule pointer.

**Cross-track ordering** — the rev-1 claim "across tracks there is no dependency"
was false. Three real ones:

| Dependency | Why |
|---|---|
| **C3 before E4** | both modify nightshift's `run.py` |
| **D3 before C2** | `ledger_transport` refuses repos absent from the registry |
| **A before F2** | the 14-day gate is only meaningful if detectors can go red |

**The operator is the serialization point.** Parallelising tracks compresses
calendar time; it does not create more operator attention, which is the scarce
resource here. Anything requiring a human decision is scheduled, not parallel.

---

## Track A — detectors ✅ partially done

### A1. Seq-gap detector — **DONE** (`detectors/seq_gap.py`)
Verified: unpushed event → `GAP … 1 unpublished`, exit 1; pushed → exit 0.
**Outstanding:** its verify only proves the *local* case. A machine that never
fetches reports `gap=0` while a third machine holds work it has not seen. Add a
cross-machine verification: push from nightshift, assert mac goes red **before**
any local action. `--fetch` must be set in the scheduled invocation.

### A2. Actor-presence — **DONE** (`detectors/actor_presence.py`)
Absorbs actor-file **ownership** (dijkstra: one check, two assertions).
Verified: deleted log → `MISSING`, exit 1, sticky across re-runs.
Fault injection found two bugs pre-trust: baseline recorded expectations not
observations (5 MISSING instead of 1), and the alarm self-healed on re-run.

### A3. ~~State root as a standalone detector~~ → **folded into F1**
Dijkstra: once seq-gap is clean and the chain verifies, both machines hold
identical event sets and `fold()` is pure — divergence means `fold.py` version
skew, which a golden-fixture test catches more cheaply. The root becomes a
*field* on projection-drift, covering **all three** `LedgerState` fields
(`items`, `spend`, `orphans`) via `canonical_bytes`, and gated on seq-agreement
so convergence lag is not reported as divergence.

### A4. Contracts
One job per detector in `manifest.yaml`, `max_age_hours: 26`, `on_fail: telegram`.
**Verify:** backdate an artifact → that job red.

---

## Track B — provenance

### B1. Provenance record + local sink
`record(subject, actor, action, event, at)`; sinks never gate the write.
**Verify:** a sink that raises does not fail `emit`.

### B2. `commit-msg` hook
Refuse a Conventional-Commits prefix authored inside `subject`; append trailers.
**Verify:** `feat(api: change` refused; a human commit untouched.

### B3. Commit↔event cross-reference *(needs B1)*
**Both directions.** Rev 1 tested only the safe one.
**Verify:** (a) commit with bogus `Datacore-Event` → red; (b) **`item.complete`
naming an unresolvable `artifact_commit` → red** — the direction the spec calls
dangerous and the one ordering exists to make unreachable.

---

## Track C — transport (critical path, decomposed)

### C1. ~~Atomic append via temp+rename~~ → **DROPPED**
CTO: technically wrong and would regress working code. `log.py` already does
open + `flock(LOCK_EX)` + read-tail + truncate-torn-line + append in one critical
section. Temp+rename would make every append O(file) read *and* write — O(n²)
over the log's life — and without one critical section reintroduces a lost
update. **Nothing changes in `log.py`.**
Re-scoped: temp+rename is used for **snapshots (F1)** and **projections**, which
are replaced wholesale.

### C2. `ledger_transport.py` *(needs D3)*
`append` / `converge` / `gaps`; expected failures return `{ok, reason, context}`,
only unexpected ones raise; refuses unregistered repos.
`flock` serialises **same-machine** writers only — cross-machine races are
handled by an explicit bounded **fetch → merge → retry** loop on non-fast-forward.
**Verify:** two *machines* pushing concurrently both land (a two-local-process
test passes without exercising this); offline remote → `ok=False`, no exception.

### C3a. Migrate `lib/` git callers — **DONE, and `space_sync.py` is gone**
142 lines → an 80-line shim → deleted. A shim is still a file, still a name to
remember, and still somewhere the next fix can land on one side only, which is
the defect being removed rather than a smaller instance of it. `sync_repo` and
`sync_all` now live in `ledger_transport`, reached as
`ledger_transport.py sync [--repo NAME]`. Callers updated: `gitea_pull_webhook`
(import), `morning_journal` (subprocess), `/today` step 3.

`/today` step 3 was telling the operator to run `git pull --rebase --autostash`
on the root repo — two lines above its own warning never to do that. Replaced
with `converge --space .`; the root repo is in the registry, so it was always
eligible.

**C5 — measured, and one earlier claim here was wrong.**

`claim.py`, `01-droplet-setup.sh` and `02-mirror-sync.sh` each existed twice,
tracked, byte-identical, all three from one commit ("snapshot: server drift
2026-07-12"). Deleted — 1,137 lines. `claim.py` was the one that mattered: E3
added the commit gate to `lib/claim.py`, leaving the root copy 33 lines stale
and still carrying the ungated `git add -A`. A byte-identical duplicate is a
latent divergence; a DIVERGED duplicate of a safety gate is a trap.

**Correction:** this plan previously said `cos_sync.sh` "exists twice
BYTE-IDENTICAL" in version control. It does not. The canonical copy lives in the
PRIVATE chief-of-staff module (`server/lib/`), and `server/deploy.sh` rsyncs it
to the box; the copy under `.datacore/lib/` is **gitignored and untracked**
(`.gitignore:309`) because this repo is public and those files must never be
tracked here. Two files on disk, one under version control — so there is no
git-level drift risk, which is what "duplicated" was claiming. The DIP's
"`cos_sync.sh` ×2 paths" line overstates this the same way and should be
amended.

**`cos_sync.sh` — MIGRATED AND DEPLOYED.** The last writer syncing by `rebase` +
rescue-branch + `reset --hard` now delegates to `converge`. Deployed to winston
via the module's own rsync path and verified on a live run: 5 GitHub spaces
`synced clean`, 4 Gitea spaces `offline` (log only, no alert — the host's disk
had failed). Kept locally because the transport has no opinion on either: the
autosave commit runs before converge so Winston's `Co-Authored-By` trailer
survives, and alerts stay deduped per space per day.

**What the old path cost, measured on the box:** 76 `cos-rescue-*` branches, 3
still carrying unmerged commits. One is from today at 17:30 and holds **three
ledger events** — `item.claim`, `item.release` (error: "Failed to authenticate:
OAuth"), `item.claim` — that were `reset --hard` out of the tree. Both chains
then continued independently from seq 76, so an **append-only log forked**: the
surviving log has 83 events, the rescue branch 80, and none of the three hashes
appear in the survivor.

They are not recoverable by appending — their `prev` hashes point into the
abandoned fork — and their content is superseded failure records, so the branch
was pushed to origin for preservation rather than merged. Both chains verify OK
(winston 204 events, mac 206; the difference is ordinary convergence lag).

That a hard reset could fork an append-only log is the strongest single
argument for this track, and it is now unreachable: nothing discards to make a
sync succeed, so nothing needs rescuing.
### C3b. Migrate module hooks — **measured: near-empty**

Classified all 11 hooks that reference org files: **10 are readers only**
(`crm`, `github`, `health`, `mail`×2, `meetings`×2, `nightshift`, `ventures`,
`crm/weekly`). The one write reference is `research/nightshift-hook`, and it
writes a **journal entry**, not task state.

That is the Phase-1 scoping principle paying off — *breaks writers, not readers*
— and it means this sub-track is roughly one item rather than the "10+ module
hooks" both the DIP and rev 1 of this plan claimed. The 30+ writer figure for
Track C is correspondingly ~10 smaller and should not be quoted as-is.
**Verify:** `grep -rn "subprocess.*git"` across **`.datacore` AND `datacore-mcp`
AND `datacore-app`** returns only the transport module and the audit tools. Rev 1
scoped this grep to `.datacore` only, so the track could go green with the two
repos the spec calls the hard part untouched.

### C4a. The 5 slash commands — **scheduled, not parallel; last within C**
Daily driver. One command at a time, each verified against a live day, each with
a stated fallback to the inline path. This is the operator's workflow.

### C4b. `datacore-mcp` GTD write tools — separate repo, separate release
(MCP server rebuild + restart).

### C4c. `datacore-app` — **greenfield, not migration**
The spec states it has no ledger awareness at all. It has no existing behaviour
to preserve, so the "readers keep working" safety property does not apply.
Scoped and reviewed as its own effort.

### C5. Delete dead code *(needs C3, C4)*
**Verify additionally:** `tris` and `data` still function. They hold partial
checkouts, and `git_fleet_sync`/`space_sync`/`cos_sync` may be what currently
keeps them viable. "Tests pass, no dead imports" proves compilation, not that
two special-cased actors survive.

---

## Track D — membership and enforcement

### D1/D2. ~~`member.*` events + genesis backfill~~ → **DROPPED**
Replaced by a flat `<space>/.datacore/members.yaml`. Folding member events at
push time would require a version-locked `fold.py` **on the Gitea server** — a
deployment dependency that contradicts the very argument used to justify
membership-as-fact. `git log` answers "who admitted whom, when".

### D3. `registry/repositories.yaml` *(blocks C2)*
`category` (knowledge|code|agent-personal), `transport`, `host` per repo.

### D4. `core.hooksPath` on **all five** machines + config-drift detector
Named: mac, winston (rsync+cron), nightshift (git pull + systemctl), hermes and
plur-claw (manual ssh) — three different deploy mechanisms.
**Verify:** unset on each machine in turn → red naming that machine. Rev 1 said
"3 machines" and would have left two silently unguarded.

### D1/D2 replacement. `members.yaml` — **DONE**
Written for all 9 space repos from **verified** collaborator access, not from
observed writers (history is not intent) and not from `ledger_actors` (that says
which actors run on a MACHINE, a different question). The GitHub/Gitea split was
checked against `git remote get-url` per repo rather than generalised from one:
github = 1-datafund, 2-datacore, 3-fds, 5-plur, 8-firm; gitea = 0-personal,
4-forge, 6-meridian, 7-megaphone (ENG-2026-08-11-074 — an earlier session got
burned assuming uniform hosting). `genesis` is a member everywhere: it wrote each
space's initial import and would otherwise be unattributable.

### D5. Gitea `pre-receive` — **written + rehearsed, NOT deployed**
Reads `members.yaml`; no `fold.py` on the server. Enforces two invariants:
membership, and **single-writer log ownership** — the latter is load-bearing,
because disjoint per-writer files are the entire reason a merge is a union that
cannot conflict.

Rehearsed against a real bare repo (`tests/test_gitea_pre_receive.py`, 8 cases).
Two bugs found by rehearsing rather than asserting:

  **A global `core.hooksPath` silently disables per-repo server-side hooks.** The
  hook was installed, executable and correct, and never ran. Every "is the hook
  present" check reads green through this — the same check-strength lesson as E1,
  found again in a new place.

  **Enforce mode rejected a space with no `members.yaml`.** The comment said
  "report, never reject"; the code appended to `violations`. That is a lockout of
  `0-personal` — unbypassable from the client — shipped as a security control.

**Blocked:** `ssh blackpi` fails host-key verification from the Mac, and
accepting a host key is the operator's trust decision. Procedure, including
Gitea's `custom_hooks/` requirement (Gitea overwrites `hooks/pre-receive` on
upgrade), is in `.datacore/hooks/DEPLOY-gitea.md`. Report-only first; a week of
silence before `DATACORE_ENFORCE=1`.

### D6. GitHub Rulesets + `bypass_actors` — **NEW**
Rev 1 had no task for this at all, leaving **5 of 9 spaces** (1-datafund,
2-datacore, 3-fds, 5-plur, 8-firm) with detection-only enforcement by silent
omission. Merge queue on code repos.

---

## Track E — verification and the code gate

### E1. Check isolation
Checks run in a checkout the executing agent never had write access to.
**Verify:** the `touch proof.txt` attack fails.

### E2. ~~Effect verifiers~~ → **OUT OF SCOPE**, own proposal
Not a transport concern; not motivated by any of the five incidents. The **rule**
stays in the DIP: an effect with no registered verifier never auto-completes.

### E3. Commit-decision gate — **DONE**
`commit_gate.py` + `detectors/pending_decisions.py`, wired into
`claim.git_commit_push`, contracted as `mac-pending-decisions`.
Verified by causing it — same fixture, one run each:
`gate off -> stranger.txt stranger2.txt` / `gate on -> report.md` (strangers
still untracked). It never blocks: ~20 unattended tasks a night, and waiting
for an answer turns one unreviewed commit into a stalled queue. The backlog
alerts on AGE, not volume.

### E4. Worktree isolation — **RESOLVED: specified form is not implementable here**

Measured, not assumed. `execute_task` runs the agent with `cwd=data_dir` — the
Data ROOT — because tasks legitimately read across spaces (context, the
knowledge base, other spaces' org files). So:

  * a worktree of ONE space repo breaks every cross-space read;
  * a worktree of **Data** does not contain the spaces at all, because they are
    separate repos, not submodules.

"One worktree per task" therefore cannot be built against this topology without
first changing how spaces relate to Data. `agent_workspace.py` remains correct
and tested for the case it fits — a task working inside a single repo — and is
kept for that.

**The risk E4 targets is closed by three things that ARE in place:**

  1. converge-at-run-start (E4's reachable half): a dirty tree at batch start is
     committed and published as the OTHER writer's work, so `git checkout`
     cannot carry it onto the branch this run commits to.
  2. E3's declared outputs: a task commits what it produced and nothing else, so
     a concurrent writer's edits are never attributed to it.
  3. `ledger_transport`'s per-repo `flock`: same-machine writers serialise on the
     operations that mutate a repo.

**Residual, stated rather than hidden:** a task can still READ a file another
process is midway through writing. A coarse machine-level lock would close it
and is deliberately rejected — the batch runs ~2h, and blocking the Telegram
bot for two hours to prevent a rare torn read trades a real cost for a
hypothetical one. Verified 2026-08-12 that the three units share the tree with
no coordination whatsoever, so this is the honest description of where things
stand, not an assumption that it is fine.

### E4-old. Worktree isolation — library DONE, not wired
`agent_workspace.py`, 8 tests. Justified by measurement, not assumption: three
systemd units on the nightshift box run as one user in one WorkingDirectory, so
a Telegram session can start mid-batch. A collision raises; failure never
returns the source checkout. **Wiring into `run.py` is deliberately not done** —
that changes an unattended overnight batch on a remote box.

### E3-old. Commit-decision gate
Pause on a verdict with a dirty tree; persist the decision as an audit artifact.
**Verify:** an unattended run with a dirty tree does not commit.
**Additionally:** a pending-decision **backlog metric**, alerted. Nightshift runs
~20 tasks unattended; without this the operator wakes to a stalled queue with no
visibility into how bad it is.

### E4. Worktree isolation *(needs E3, and C3 — shared `run.py`)*
`agent/<task-id>` unique per run; a collision **fails loudly**.
**Verify:** two runs in one workspace → second gets its own branch or fails
visibly; never falls back to the source checkout.

---

## Track F — projection

### F1. Snapshots + projection-drift (absorbing the state root) *(needs A)*
Snapshots untracked, published by temp+rename. Drift detector regenerates and
diffs, emitting the root as a field.
**Verify:** delete a snapshot → fold still correct, only slower.

### F2. Phase 1 on one space *(needs A, F1, shadow streak)*
**Verify:** 14 consecutive days of positive counts.
**F2a. Revert drill — before F2.** Perform the documented reversal once, on a
scratch space: un-gitignore, commit the projection, resume authoring. A phase
billed as reversible that has never been reversed is a claim, not a property —
and this installation's history is 610 stranded commits and 110 wiped files.

---

## What is deliberately unresolved

- Append cost: `append()` re-reads its own log **and every sibling log** to
  compute the causal floor — O(own + siblings) per event. Snapshots fix *fold*
  cost, not *append* cost. Not addressed by any track; recorded, not hidden.
