# Finish making the roadmap agent-ready — handoff prompt

Written 2026-09-04. Everything below was verified by running it, not by reading
docs. Numbers are from that evening; re-measure before trusting them.

Paste the section under **PROMPT** into the parallel session.

---

## PROMPT

You are finishing a piece of work that is ~80% done. The PLUR roadmap is now
agent-executable in principle — `5-plur/roadmap.yaml` is the single source, a
pre-commit hook validates it, `sprint_sync.py` projects an active sprint into
the agent queue, and one queued task was executed end-to-end and closed. What
remains is the part that makes it true on the machines that actually run.

Read these two first. They are short and they contain the model you need:
- `.datacore/specs/task-lifecycle-and-ledger-coverage.md`
- `~/.claude/skills/roadmap-build/SKILL.md` (Phase 7 especially)

**The one thing to internalise:** there are three levels and only the third
runs. A task in `next_actions.org` *exists*; tagged `:AI:` it is a *candidate*;
referenced from `<space>/org/nightshift.org` it *runs*. `:AI:` is a **projection
of an active sprint**, never a hand-applied label — `sprint_sync --apply` strips
any tag it did not put there. If you want a task to run, put it in a sprint.

Work the items below in order; 1 blocks 2, and 2 blocks everything being real.

### 1. Land the tooling on main — nothing works until this happens

`.datacore/lib/sprint_sync.py` and `.datacore/lib/ai_task_gate.py` **do not
exist on `origin/main`**. They are only on `fix/creds-multivar-resolution`,
which is 12 commits ahead of main with no open PR. The nightshift host pulls
main, so today it can never run the projection.

Open a PR from that branch, or merge it. Verify with:

```bash
git cat-file -e origin/main:.datacore/lib/sprint_sync.py && echo on-main
```

Do not cherry-pick just those two files — the branch also carries the
`agent_readiness.py` corrections and the pre-commit gate wiring, and half of
that landing is worse than none.

### 2. Deploy to the nightshift host, and verify ON the host

The fleet is running code from before all of this:

| repo | server branch | needs |
|---|---|---|
| `~/Data` | `nightshift/<date>` during a run, else `main` | main to carry item 1 |
| `~/Data/.datacore/modules/nightshift` | `feat/tier1-haiku-batch` | switch to `main` |

The module checkout is 9 ahead / 15 behind `main` and its server-side work is
**already committed** as `7033050` ("park: server-side work before branch
realignment"). The push was rejected as behind — reconcile and push that branch
first so nothing is lost, *then* move to main.

**Never do this while a run is active.** `run.py` is being executed; checking
out another branch rewrites it under a live process. Check first:

```bash
ssh nightshift 'systemctl show nightshift-overnight.service -p ActiveState --value'
```

Only `inactive`/`failed` is safe. Then, on the host:

```bash
cd ~/Data/.datacore/modules/nightshift
git push origin feat/tier1-haiku-batch   # after reconciling
git checkout main && git pull
cd ~/Data && git checkout main && git pull
sudo systemctl restart nightshift-overnight.timer
```

Verify afterwards that `lib/run.py` contains `Syncing queue to active sprint`
and that `.datacore/lib/sprint_sync.py` exists **on the host**. Code on a laptop
executes nothing.

### 3. Merge the three sprint branches

The sprints were cut onto their own branches rather than onto whatever feature
branch each checkout happened to be sitting on:

- `plur-ai/plur` → `sprints/2026-W37-core-sprint1` (11 agent items)
- `plur-ai/enterprise` → `sprints/2026-W37-enterprise-sprint10` (8 agent items)
- the continuous sprint is already on `plur-space` main — nothing to do

### 4. Activate Monday's sprints

Both W37 sprints are `status: planning`. On Monday:

```bash
# set `status: active` in each sprint.yaml, then
python3 .datacore/lib/sprint_sync.py --active --apply
```

`2026-W37-continuous-sprint1` is already active and its 11 tasks are queued.
Expect the sync to be idempotent (0/0/0/0) if nothing changed.

### 5. Drain the unexecutable `:AI:` pool

`agent_readiness.py` reports **683 findings** fleet-wide. They do not run — the
overflow path filters on executability — but they are noise on every report and
they *would* run if that filter were ever turned off.

Measured 2026-09-04, `:AI:` in TODO/NEXT:

| space | executable | unexecutable |
|---|---|---|
| 0-personal | 4 | **186** |
| 6-meridian | 1 | 27 |
| 3-fds | 0 | 2 |
| 9-practice | 0 | 1 |
| 2-datacore | 51 | 0 |

For each: either give it `SURFACE` + `DONE_WHEN` (`ROADMAP` only where a
`roadmap.yaml` exists) or drop the `:AI:` tag. **Dropping the tag is not
demotion** — the task stays in the pool with its links intact; it is simply not
queued. Most of the 186 should lose the tag.

Watch for `SURFACE` holding a publication URL (`plur.ai/blog`) rather than a
repo. It passes an emptiness test, names no checkout, and several GEO tasks have
it. The vocabulary is in `.datacore/lib/task_surface.py`.

### 6. Close the ledger's blind span

For org-routed work the ledger sees `item.create`, `item.update`, `item.dismiss`
and nothing else — not claim, not execution, not completion. So "who did this,
when, at what cost" cannot be answered from the attested record.

The vocabulary already exists in `EVENT_TYPES` and is simply never written:

- `claim.py` emits **`item.claim`** where it already writes
  `NIGHTSHIFT_EXECUTOR`. This is the keystone: it makes `item.complete` legal
  later, because the fold's `status == claimed` precondition becomes true
  honestly instead of by fabrication.
- `run.py` emits **`item.complete`** / **`item.release`** at the existing
  `_emit_lifecycle('completed' | 'failed')` call site — same place, second sink.
- **`item.clock.start` / `item.clock.stop`** around execution.

Do **not** fabricate a claim to satisfy the state machine; that is exactly what
`ledger_ingest_org.py` refuses to do, and its reasoning is worth reading before
you touch this.

### Constraints that are not negotiable

- **Cap table and equity work is human-only** (ENG-2026-0709-013). Never `:AI:`,
  never nightshift, never in a sprint.
- **R-094's bizdev agent QUALIFIES and never CONTACTS** — no outreach in any
  channel at any autonomy level. Enable the `crm` module and reuse the
  `research-prospect` skill; do not write a new agent.
- **R-063: an agent drafts the DPA, it does not publish it.** Ask first. The
  trust page does not wait on that, and must not describe `plur.datafund.io` —
  internal infrastructure, not a service.
- **R-093 has two halves** — the reconciler going forward *and* a one-time sweep
  of the 167 finished `Review:` tasks. Half of it leaves the backlog wrong.
- **Commit org edits immediately.** Org files are rewritten wholesale from an
  in-memory model, so an uncommitted edit is destroyed silently and
  `git status` looks clean afterwards (ENG-GPL-2026-09-04-022). Verify with
  grep, not with a clean status.

### How to know you are done

```bash
python3 .datacore/lib/agent_readiness.py          # findings, fleet-wide
python3 .datacore/lib/ai_task_gate.py <org files> # exit 0
python3 .datacore/lib/sprint_sync.py --active     # idempotent: 0/0/0/0
cd .datacore/modules/nightshift && python3 -m pytest tests/ -q   # 180 pass
```

And the only test that actually matters: **let one overnight run execute a
sprint task and close it unattended**, then compare against the baseline of 66
tasks at `NIGHTSHIFT_STATUS: needs_review` against 14 done. If that ratio does
not move, the acceptance-criteria theory is wrong and the retro should say so
plainly rather than explaining it away.

### The habit that found every real bug here

Call the function; do not reason about it. Every serious error in this rebuild
survived careful reading and died the moment something ran: the queue that was
three levels deep, the tag regex that silently rejects hyphens, the check that
scored a set the executor never selects, the filter that broke nine tests. And
when a suite fails after your change, assume the suite is right.
