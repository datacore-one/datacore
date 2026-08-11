# DIP-0046 Implementation Plan — Git as Ledger Transport

> Spec: `.datacore/dips/DIP-0046-git-transport.md` (branch `dip/0046-git-transport`)
> Six tracks. Within a track order is a dependency; across tracks there is none.

**Goal:** git carries only conflict-free payloads; derived state is regenerated;
facts move through one writer with detectors watching.

**Global constraints**

- Python for all `.datacore/lib/` tooling. No TypeScript.
- Never multi-line Bash; chain with `&&`.
- No new dependency may be required to import `ledger/` — stdlib only.
- Every detector emits `metric.attest` and writes a **dated artifact**; a job is
  green only if its artifact exists and is fresh (ENG-2026-0804-033).
- Every item below is verified by **causing the failure it prevents**, not by
  asserting the code runs.
- `~/Data` is a shared working tree. Never `git checkout <other-branch>` in it.
- `.datacore/dips` and `.datacore/modules/*` are separate repos; never
  auto-commit a submodule pointer bump.

---

## Track A — detectors (no dependencies; ships first)

### A1. Seq-gap detector
**Files:** create `.datacore/lib/detectors/seq_gap.py`
- Per space, per actor: compare local head `seq` against `origin/<default>`'s.
- Report `{actor, local_seq, remote_seq, gap}`; exit non-zero on any gap.
- **Verify:** append an event, do not push, run → gap of 1, exit 1. Push → 0.

### A2. Actor-presence detector
**Files:** create `.datacore/lib/detectors/actor_presence.py`
- Read the roster; assert every rostered actor has a log, non-empty, whose
  `seq` has not gone backwards since the last run (state in `~/.datacore/state/`).
- **Verify:** move an actor's `.jsonl` aside → red, naming the actor. Restore → green.

### A3. State root
**Files:** modify `.datacore/lib/ledger/fold.py`; create `.datacore/lib/detectors/state_root.py`
- `fold()` gains `state_root()` — a stable hash over folded item states
  (sorted by id; no timestamps or dict order in the input).
- Detector compares roots across machines via the newest `projection.attest`.
- **Verify:** fold the same log twice → identical root. Fold with one extra
  event → different root. Fold on two machines with identical logs → equal.

### A4. Contracts
**Files:** modify `.datacore/lib/jobs/manifest.yaml`
- One job per detector, `max_age_hours: 26`, `on_fail: telegram`.
- **Verify:** `job_verify.py --machine mac` shows the new jobs green; backdate
  one artifact → that job red.

---

## Track B — provenance

### B1. Provenance record + local sink
**Files:** create `.datacore/lib/provenance.py`
- `record(subject, actor, action, event, at) -> dict`; `emit(record, sinks)`.
- Local sink: render trailers + append `metric.attest`.
- **Sinks never gate the write** — a failing sink logs and returns; the caller
  is not blocked. **Verify:** a sink that raises does not fail `emit`.

### B2. commit-msg hook
**Files:** create `.datacore/githooks/commit-msg`
- Reject a Conventional-Commits prefix authored inside `subject`.
- Append the trailer block when `DATACORE_ACTOR` is set.
- **Verify:** a commit whose subject starts `feat(api: change` is refused; a
  normal human commit is untouched.

### B3. Commit↔event cross-reference *(needs B1)*
**Files:** create `.datacore/lib/detectors/commit_event_xref.py`
- Every commit carrying `Datacore-Event` names an event that exists; every
  `item.complete` names a resolvable `artifact_commit`.
- **Verify:** hand-write a commit with a bogus `Datacore-Event` → red.

---

## Track C — transport (critical path)

### C1. Atomic publish
**Files:** modify `.datacore/lib/ledger/log.py`
- Append via temp file + `os.replace()` onto the log path.
- **Verify:** a concurrent reader in a loop never observes a partial line while
  1,000 events are appended.

### C2. `ledger_transport.py` *(needs C1)*
**Files:** create `.datacore/lib/ledger_transport.py`
- `append(space, actor, event)`, `converge(space)`, `gaps(space)`.
- Per-repo `flock`. **Merge only — never rebase.**
- Expected failures return `{ok, reason, context}`; only unexpected ones raise.
- Reads `registry/repositories.yaml`; an unregistered repo is **refused**.
- **Verify:** offline remote → `ok=False` with reason, no exception; two
  processes appending concurrently both succeed and both events survive.

### C3. Migrate git callers *(needs C2)*
**Files:** the 16 identified callers
- **Verify:** `grep -rn "subprocess.*git" .datacore/lib .datacore/modules`
  returns only `ledger_transport.py` and the recovery/audit tools.

### C4. Migrate org writers *(needs C2)*
**Files:** 5 slash commands, 15 library/module writers, 10+ module hooks
- Readers are untouched — only writes route through the ledger.
- **Verify:** per writer, the write produces an event; `/wrap-up` refuses to
  close with a non-zero gap count.

### C5. Delete dead code *(needs C3, C4)*
- `nightshift_recover_stranded.py`, `cos_sync.sh` ×2, `space_sync.py`,
  `cos_merge_runs.sh`, most of `git_fleet_sync.py`, shadowed duplicate copies.
- **Verify:** full test suite green; no import of a deleted module remains.

---

## Track D — membership and enforcement

### D1. `member.*` event types
**Files:** modify `.datacore/lib/ledger/events.py`, `fold.py`
- Add `member.add` / `member.remove`; fold maintains a member set.
- **Verify:** add then remove an actor → member set empty; a remove for a
  non-member is a recorded no-op, not an error.

### D2. Genesis backfill *(needs D1)*
**Files:** create `.datacore/lib/ledger/members_genesis.py`
- One-time, idempotent: emit `member.add` per actor per space from
  `ledger_actors`.
- **Verify:** run twice → second run emits nothing.

### D3. Repository registry
**Files:** create `.datacore/registry/repositories.yaml`
- Each repo: `category` (knowledge|code|agent-personal), `transport`, `host`.
- **Verify:** `ledger_transport.append` on an unregistered repo → refused.

### D4. `core.hooksPath` + config-drift detector
**Files:** create `.datacore/lib/detectors/config_drift.py`; set on 3 machines
- Assert `core.hooksPath` resolves and the hook dir has the expected files.
- **Verify:** unset it on one machine → red naming that machine.

### D5. Gitea `pre-receive` *(needs D2)*
**Files:** create `.datacore/githooks/server/pre-receive`
- Reject a push writing `events/<actor>.jsonl` for a non-member.
- **Verify:** push a foreign actor's log from a test clone → rejected server-side.

---

## Track E — verification and the code gate

### E1. Check isolation
**Files:** modify `.datacore/lib/ledger_dispatch.py`
- Run `check` in a fresh checkout of the committed result; the executing agent
  never has write access to it.
- **Verify:** the `touch proof.txt` attack fails — an agent that fabricates the
  artifact without committing it does not pass.

### E2. Effect verifiers
**Files:** create `.datacore/lib/effects/` (`registry.py`, per-effect verifiers)
- Each `effects` tag binds a verifier reading an external system of record.
- Unregistered effect → review, never auto-complete.
- **Verify:** an `email.send` item with no BCC evidence lands in review.

### E3. Commit-decision gate
**Files:** create `.datacore/lib/commit_gate.py`
- On verdict with a dirty tree: pause, write pending decision, await
  `approve|fix|apply|skip|halt`, persist the decision as an audit artifact.
- **Verify:** an unattended run with a dirty tree does **not** commit.

### E4. Worktree isolation *(needs E3)*
**Files:** modify the code-work executor path
- `agent/<task-id>` unique per run; a collision **fails loudly**.
- **Verify:** two runs in one workspace → second gets its own branch, or fails
  visibly. It never falls back to the source checkout.

---

## Track F — projection

### F1. Snapshots + state root *(needs A3)*
**Files:** create `.datacore/lib/ledger/snapshot.py`
- Untracked local cache; `.gitignore` entry; fold resumes from newest snapshot.
- **Verify:** delete the snapshot → fold still correct, only slower.

### F2. Phase 1 on one space *(needs A, F1, shadow streak)*
- Write `phase1-active`, gitignore the projection, add the weekly archive.
- **Verify:** 14 consecutive days of positive counts.

---

## Sequencing

Critical path **C**. A and D are widest and least coupled. F2 is gated on
elapsed time, not effort, and cannot be compressed.
