# Task lifecycle, and what the ledger actually records

Written 2026-09-04, from reading the code rather than the design docs — the two
had drifted apart in three places, each noted below.

Companion diagram: `task-lifecycle-and-ledger-coverage.html` (open locally).

---

## The finding, in one paragraph

**For org-routed work — which is all roadmap and sprint work — the ledger is a
birth-and-death register, not a work record.** It sees `item.create` when the
hourly sweep mirrors an org task, `item.update` when a date or state changes,
and `item.dismiss` when org says DONE. It never sees the task claimed,
executed, evaluated or completed. Those facts live in two other places, neither
of which is hash-chained or sealed.

So the question "who did this task, when, and at what cost" cannot be answered
from the attested record. The evidence exists; it is just not in the chain.

---

## Three records, one task

| | where | attested? | what it holds |
|---|---|---|---|
| **org-mode** | `<space>/org/*.org` | no | the working state — TODO/NEXT/REVIEW/DONE, `NIGHTSHIFT_*` telemetry, CLOCK entries |
| **ledger** | `<space>/.datacore/events/<actor>.jsonl` | **yes** — sha256 hash chain, signed, sealed with watermarks | `item.create`, `item.update`, `item.dismiss` |
| **agent stream** | `~/.datacore/cos/agent-stream/events-<date>.jsonl` | no | `started`, `completed`, `failed`, `skipped` + exec id, duration, tokens |

The ledger is the only one of the three that can prove anything, and it is the
one missing the middle of the story.

---

## The lifecycle, stage by stage

Creation has four doors:

1. **Human capture** → `inbox.org`, processed to `next_actions.org`
2. **`/wrap-up`** → continuation and extracted tasks, always to `inbox.org`
3. **Cadences** → `venture_heartbeat.py` every 30 min, role duties per `venture.yaml`
4. **`sprint_sync.py`** → projects an active sprint into both the pool and the queue

Then eligibility, which is a ladder and not a switch:

| stage | what makes it true | who decides |
|---|---|---|
| exists | a heading in an org file | any writer |
| **candidate** | carries `:AI:` on its own heading line | `sprint_sync`, or a hand edit the pre-commit gate checks |
| **queued** | referenced from `<space>/org/nightshift.org` | `sprint_sync`, or a human |
| **selected** | survives `build_queue` — priority sort, dedup, live-claim filter, `max_tasks_per_run: 20` | `task_queue.py` |
| **claimed** | `NIGHTSHIFT_STARTED` newer than `NIGHTSHIFT_COMPLETED`, committed and pushed (git is the lock) | `claim.py` |
| executed | agent runs; output to `<space>/0-inbox/` | `run.py` |
| evaluated | six evaluators score the output | `run.py` |
| review / done | `REVIEW` awaiting a human, or `DONE` | human |

**Queued is priority, not eligibility.** `run.py` calls
`build_queue(include_pending=True)`, so a merely-tagged task is eligible too —
`nightshift.org` decides what sorts first. The module docstring said the
opposite until 2026-09-04.

---

## Where the ledger stops, and why

Three deliberate decisions, each defensible on its own, which together produce
the gap:

**1. `item.complete` is never emitted for an org task.** `ledger_ingest_org.py`
says why: the fold requires `status == claimed` before completing, so
completing an unclaimed item is a *silent no-op* — two full passes over
org-DONE tasks did nothing and reported success. Fabricating a claim to satisfy
the state machine "would put a lie in the audit trail". So a closed org task
becomes `item.dismiss`, which means "a human closed this", not "an agent
finished this".

**2. `ledger_claim.py` skips org-mirrored items outright:**

```python
pending = [i for i in claimable if not (i.payload or {}).get("org")]
```

Deliberate — it stopped agents working unattended through a 342-item personal
backlog. The consequence is that an org task can never be claimed *through the
ledger*, so no `item.claim` is ever written for one.

**3. Nightshift emits no ledger events at all.** `claim.py` imports from
`ledger_transport` only for `converge` (a git sync helper); `run.py` mentions
the ledger once, in a comment. Claiming writes org properties and a git commit.
Execution writes to the agent stream via `_emit_lifecycle`.

So the chain records that the task was born and that someone closed it. Between
those two points it is silent, and the events that would fill the gap —
`item.claim`, `item.complete`, `item.clock.start` / `item.clock.stop` — all
exist in `EVENT_TYPES` and are simply never written for this class of work.

---

## What would close it

The vocabulary is already there; only the emission is missing.

- **`claim.py` emits `item.claim`** when it takes a task, with the same actor
  string it writes to `NIGHTSHIFT_EXECUTOR`. That alone makes `item.complete`
  legal later, because the fold's precondition (`status == claimed`) becomes
  true honestly rather than by fabrication.
- **`run.py` emits `item.complete` or `item.release`** at the point it already
  calls `_emit_lifecycle('completed' | 'failed')`. Same call site, second sink.
- **`item.clock.start` / `item.clock.stop`** around execution, which the schema
  already models as events precisely because two machines can clock the same
  task.

That turns the agent stream from the only record of agent labour into a *live
view* of it, with the durable copy in the chain — which is the relationship the
two were presumably meant to have.

Until then, state the limit plainly: **the audit chain covers what a task *is*,
not what was *done to it*.**

---

## Two smaller drifts found on the way

- `find_unqueued_ai_tasks` documented itself as describing tasks that "do not
  run — by design", while the runner ran them. Fixed 2026-09-04: execution now
  falls back only to executable tasks, reporting still shows everything.
- `nightshift_parser.find_ai_tasks` reads **only the heading line** for tags, so
  org tag inheritance does not apply to selection — a live child under a tagged
  parent is invisible to the runner while `org_workspace` reports it as tagged.
  Any tool mirroring the runner must use shallow tags.
