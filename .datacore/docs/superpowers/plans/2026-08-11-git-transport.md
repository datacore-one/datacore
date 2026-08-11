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

### C3a. Migrate `lib/` git callers *(then C3b)*
### C3b. Migrate module hooks — one sub-track per module, fully parallel
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

### D5. Gitea `pre-receive` *(needs D3)* — **log-only first, then enforce**
Reads `members.yaml`; no `fold.py` on the server.
Staged: run in report-only mode until it has been silent for a week against real
pushes, *then* enforce. `0-personal` is the operator's own daily space and the
rejection is unbypassable from the client — a wrong rule locks them out of their
own notes. Rehearse on a throwaway repo first.

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

### E3. Commit-decision gate
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
