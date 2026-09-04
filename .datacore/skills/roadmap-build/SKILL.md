---
name: roadmap-build
version: 2.0.0
description: |
  Build or repair a roadmap that agents can actually execute, not just read.
  Produces a single validated YAML source of truth, generated views, definitions
  of done with evidence kinds, a task pool linked on two axes, an idea intake
  queue, and three standing checks that keep it honest.
  Use when asked to "build a roadmap", "fix the roadmap", "consolidate roadmaps",
  "make the roadmap agent-ready", when several roadmap documents have drifted
  apart, or when agents cannot tell what to work on next.
triggers:
  - build a roadmap
  - fix the roadmap
  - consolidate roadmaps
  - roadmap is fragmented
  - make the roadmap agent-ready
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Grep
  - Glob
---

# Roadmap: build one agents can execute

Derived from rebuilding the PLUR roadmap, 2026-09-01 to 2026-09-03: five drifted
documents to one validated source of 93 epics, 750 tasks, and three checks that
catch regressions nobody would notice by reading.

## The one thing that matters

**A roadmap is good when it is CHECKABLE, not when it is well written.**

Every structural finding in that rebuild came from a script, never from reading
the file: five areas built and invisible, eleven epics whose repo state
contradicted them, fourteen agent-doable tasks parked behind a wrong flag. The
prose was fine throughout. The prose is always fine.

So: build the checks early and let them find the work. Do not write the whole
roadmap and validate at the end.

---

## Phase 1 — Find what is already true

Before writing anything, read the repos. Roadmaps decay by omission far more
than by error, and the omissions are invisible from inside the document.

```bash
gh issue list --repo <org>/<repo> --state open --limit 200
gh pr list --repo <org>/<repo> --state open
git -C <repo> log --oneline -40
ls <repo>/src/<subsystem>/          # whole subsystems with no roadmap presence
```

**Expect to find work that shipped and work that is invisible.** In the PLUR
rebuild: a provenance subsystem shipped in a repo the roadmap represented with
two gated lines; eleven modules and three shipping tools with zero items; a
49-route admin surface with no coverage at all.

Ask of every subsystem: *does the roadmap know this exists?* Where it does not,
that is an epic, and its status is whatever the code says — not what the
document assumed.

## Phase 2 — One writable source

Pick ONE file. Everything else becomes a generated view or a marked pointer.

**Why fragmentation happens, and it is not carelessness:** every document is
writable, so ideas land in whichever one is open, and none is wrong enough to
fix. The fix is not discipline. It is removing the ability to write.

Mark every other roadmap-shaped file with a canonical pointer naming what an
item added there would be invisible to. Then add a check that flags a *new*
unmarked one, because a sixth document is how the first five happened.

### Item schema

```yaml
- id: R-001
  track: core                 # which product line
  lane: prod                  # kind of work: prod | rnd | ops | geo | community
  milestone: M2               # the rung, or `continuous`
  title: <outcome, not a task>
  outcome: >-
    <what becomes true — one sentence, in the reader's language>
  done_when:
    condition: >-             # falsifiable, singular
      <what must be true>
    evidence: test            # test|metric|artifact|screenshot|url|merged-pr|decision|signed
    verify: >-                # the command or address that settles it
      <exact check>
  serves: [<intent-id>]       # must resolve against the intent graph
  horizon: now                # now | next | later | gated
  status: ready               # ready | in_progress | blocked | done
  blocked_on: null            # null | human | agent | external | dependency
  delegable: true             # can an agent finish this?
  owner: null
  gh: <org>/<repo>#123        # optional, enables drift detection
```

**`evidence` is the field that makes an epic actionable for an agent** — it says
what artifact to go and produce. A condition without an evidence kind is a wish.

### Milestones are rungs, not dates

Each milestone should widen **who benefits** — one person, one team, the
organisation, everyone building on you, everyone. That is the test for belonging
on the ladder. Gate on conditions, never dates, so nothing slips by being late.

`continuous` is a legitimate value with a rule attached: **an epic belongs there
only if it would still be running after the last milestone ships.** Anything that
ENDS belongs on a rung, even a distant one. Without that rule it becomes the
bucket with no gate and absorbs everything.

## Phase 3 — The task pool, on two axes

Tasks link to epics by a `ROADMAP:` property. That is necessary and not
sufficient.

**`ROADMAP` says which outcome a task serves. It does not say where the work
happens.** An agent needs both — different repos, different reviewers, different
context. Add a second axis:

- `SURFACE` — which repo or surface the work lands in
- `MILESTONE`, `EPIC` — propagated from the epic, never hand-set

Link conservatively. **A wrong parent is worse than none**, because it points an
agent at the wrong outcome. Leave a task unlinked rather than guess, and report
the unlinked count as a finding rather than hiding it.

## Phase 4 — Definitions of done, at BOTH levels

This is the step most likely to be got wrong, and the failure is subtle.

**An epic's `done_when` is a milestone test. A task needs its own.**

In the PLUR rebuild the readiness audit reported zero problems because it
checked whether the *epic* had a condition. But 66 tasks shared one epic's
*"fewer than five PRs are open"*. An agent that finishes one of them and checks
that condition finds it **false** — because thirteen other people's work is not
done. It cannot observe its own success.

**Rule:** if more than one task points at an epic, every one of those tasks needs
its own `DONE_WHEN`. Fixing the check turned 0 findings into 56.

Task-level conditions are cheap and specific:

> *"Rebase PR #919"* → **done when #919 shows no conflict and CI is green** —
> not when the merge queue is under five PRs.

## Phase 5 — Three checks

Build all three. Each catches a class the others cannot.

**1. Validate** — schema and internal contradictions. Runs on every commit that
touches the roadmap. Must refuse:
- a `now`/`next` epic with no `done_when` (nobody can finish what nothing defines)
- `blocked_on: human` together with `delegable: true` (tells an agent to start
  work it cannot finish — the most expensive contradiction on a board)
- an unknown milestone, evidence kind, or `serves` that resolves to nothing

**2. Drift** — the roadmap against reality. Manual or on a cadence, never on
commit: it reaches the network.
- an epic whose linked issue is **closed as COMPLETED** — may be done
- an epic whose issue is **closed as NOT_PLANNED** — points at a *rejected*
  issue. **These mean opposite things and checking only `closed` conflates
  them**, reading a retirement as progress
- a `now` epic no task points at — indistinguishable from finished
- a note asserting a state older than ~45 days, but **only when the date sits
  next to a state word.** Matching the whole note fires on citations, and a
  check that cries wolf teaches people to ignore it
- a roadmap-shaped file with no canonical pointer

**3. Agent readiness** — the five preconditions for unsupervised work:

| | asks |
|---|---|
| SELECT | can an agent find genuinely available work? |
| LOCATE | does the work say where it happens? |
| FINISH | does the **task** say what finishing means? |
| BRIEF | is there enough written to act on? |
| VERIFY | can the agent check the result itself? |
| AVOID | is undoable work clearly marked? |

For BRIEF: a heading naming a **resolvable reference** (issue, file, URL) *is* a
brief — the agent can fetch it. Only flag headings naming something unlocatable.
"Rebase plur#919" needs no prose.

For VERIFY: an epic marked `delegable` whose evidence is `signed`, `decision` or
`screenshot` is a contradiction. **No agent produces a signature.**

## Phase 6 — The intake queue

Create `feature-ideas.md`. Every candidate enters there; nothing goes straight
into the roadmap.

**Four verdicts, exactly one each, and nothing leaves a review without one:**

- **ADOPT** → a roadmap item with a `done_when`
- **TRIAL** → an experiment with a hypothesis and a date
- **WATCH** → a re-check date
- **DROP** → one line of reason

**DROP is a success.** The expensive outcome is not saying no; it is re-deriving
the same no three times because nobody wrote it down. An idea that stays
"interesting" for three reviews is a DROP nobody has been willing to write.

**Why a queue at all:** the roadmap refuses items with no definition of done, and
most ideas cannot state one yet. Forcing one produces fiction. The queue is
where an idea waits, costing nothing, until it can answer that question.

**Review bi-weekly, not weekly.** Most weeks produce nothing worth a decision,
and a review that usually has no input stops being attended.

---

## Phase 7 — The execution path, or the roadmap is decoration

A roadmap agents cannot REACH is not a roadmap, it is a document. This phase is
the half most likely to be skipped, because everything above it looks finished.

### Find the executor's real selection rule. Do not infer it.

Open the runner and read what it selects. In the PLUR system it turned out to be
three levels, and only the third runs anything:

| | |
|---|---|
| task exists in the pool file | exists |
| ...carries the agent tag | is a **CANDIDATE** |
| ...**referenced from the queue file** | **RUNS** |

The fleet had 261 tasks at level 2 and executed 2. The runner had been printing
`261 tagged but not queued — they will NOT run` on every single run, and it read
as noise. The space holding the roadmap had no queue file at all, so five
perfectly specified sprint items would have sat at level 2 forever.

**Ask "what does the runner select?" and answer it by calling the runner's own
selection function.** Not by reading it. Not by reasoning about the tag.

### The sprint must have a consumer

A sprint file nothing reads is a wish. Grep for a reader before trusting it —
`sprint_tag_filter` had **zero readers anywhere in the tree** while being the
field the sprint used to claim it routed work.

Write the projection instead: sprint file → task properties → queue entry. One
direction only, and it is the **sole writer** of the agent tag. In the sprint →
queued; not in the sprint → tag removed, entry removed, task untouched. Removing
the tag is not deprioritising; it means "not this sprint".

Then run it from inside the runner's own preflight, before it builds the queue.
Anything that depends on a human remembering to run it will drift the first busy
week.

Guards it needs, each bought with a real defect:

- **Refuse an underspecified item** — the same fields readiness checks. Caught
  while a human is still looking at the sprint, not at 02:00 by an agent.
- **Expire the in-flight exemption.** "Currently executing" sheltered two tasks
  stuck for 5 and 7 days, one with its COMPLETED stamp *before* its STARTED
  stamp. An unexpiring exemption protects a corpse.
- **Ignore an `active` sprint whose end date has passed.** Five sprints, oldest
  89 days, would all have projected at once. The status field is a claim; the
  date is a fact.
- **Deduplicate discovery** — worktrees make one sprint appear many times.
- **Never re-queue a closed task.** The queue keys off the sprint item, not the
  task's state, so a completed item gets handed back otherwise.

### Acceptance must stop at the agent's own control boundary

This is the highest-value rule in this phase.

Agents were "leaving work open" and looked unreliable. The cause was in the
acceptance criteria we wrote: they ended at **merged**, which needs an approving
review and a code owner. An agent cannot reach that state, so it never reports
done, so the task sits open — and the criterion, not the agent, was the defect.

Write agent acceptance as: **written, pushed, CI green, review requested.**
Merging is a human item and belongs in a human-gated list with a name against
it. If an item's acceptance needs another party, it is not agent work.

### Deploy it, then verify on the machine that runs it

Code on your laptop executes nothing. Check the runner host for the branch it is
actually on and whether your files exist there. The PLUR fleet ran the Data repo
on one unrelated feature branch and the runner module on another, with neither
new script present — so a night's work would have used none of it.


---

## Failure modes — every one of these actually happened

| symptom | cause | fix |
|---|---|---|
| Audit passes, agents still stall | checked the epic's `done_when`, not the task's | require task-level when an epic is shared |
| Agent starts work it cannot finish | `blocked_on: human` on an epic where only *some* steps need a person | `blocked_on` describes the WHOLE epic; put it on the steps |
| A check silently never fires | path matched the wrong form — the hook runs *inside* the repo, so the staged path is relative to it | stage something invalid and confirm rejection |
| Hook edits do nothing | `core.hooksPath` overrides `.git/hooks/`; the symlink is dead | find the live path before editing |
| A retired issue reads as progress | only checked `closed`, not `state_reason` | distinguish COMPLETED from NOT_PLANNED |
| Classifier looks right, output is wrong | verified aggregate counts, never sampled rows | print actual rows and read them |
| Stale-note check ignored | matched a state word anywhere in the note | require proximity to the date |
| Layout breaks after a "fix" | bumped font sizes inside a fixed-coordinate SVG | scale the whole SVG, never its fonts |
| Items appended to the wrong list | YAML appended at EOF joins the last top-level key | insert inside the target block explicitly |
| Fields lose their block scalar | replacement dropped the leading indent | preserve indentation on every line |
| Agent leaves everything open | acceptance ended at "merged", which needs a reviewer | stop acceptance at the agent's control boundary |
| A queued, well-specified task never runs | tag had a **hyphen** — org tag regex is `:[\w:]+:` and `\w` excludes `-`, so the whole group fails to match and the task parses with ZERO tags | use underscores; assert the parsed tags, never eyeball the heading |
| Check counts a set the executor ignores | scored every tagged task, but the runner selects only certain states | mirror the executor's filter exactly |
| Tasks look queued and are invisible | org inherits tags from ancestors; the runner reads only the heading line | use shallow tags wherever you mirror the runner |
| A test half of a check never fires | read a field the data source does not emit (`body` from a list API) | load the real object, not the summary |
| Section counts are not comparable | one check appended a row per task, another six rows plus "…and N more" | one finding per item; let the printer truncate |
| A later check dies mid-run | walrus in a comprehension binds in the ENCLOSING scope and clobbered the parsed roadmap | never reuse a live name for a comprehension walrus |
| Gate gets bypassed with --no-verify | the gate was STRICTER than the executor and rejected runnable work | mirror the executor's definition, never exceed it |
| Nothing runs after a "fix" | filtered at DISCOVERY, hiding work from the reports built to surface it | filter where work is CHOSEN, not where it is found |

## Verification discipline

**Verify the rendered output, not the source you just wrote.** Several defects
in the PLUR rebuild shipped because the source looked right:

```bash
# after generating a view, read what it actually produced
python3 -c "import re,html as H; h=open('out.html').read();
print(H.unescape(re.sub(r'<[^>]+>',' ',re.search(r'<div class=\"x\">(.*?)</div>',h,re.S).group(1))))"

# prove a check discriminates — revert the fix, confirm failure, restore
```

A test that passes before and after the fix is not a test.

**Call the real function rather than reasoning about it.** Every serious error in
the PLUR rebuild survived reading and died on execution: the queue that was three
levels deep, the tag regex that rejected hyphens, the executability filter that
broke nine tests the moment it ran. Reading tells you what the code appears to
do; calling it tells you what it does.

**When a suite fails after your change, the suite is usually right.** Nine tests
failed on a filter that looked obviously correct, and one of them
(`test_queue_visibility`) existed precisely because three earlier defects all had
the shape "the system knew something and said nothing". The change was the bug.

## Sequence

1. Read the repos. Find what is built and invisible.
2. Create the single source; mark every other file as a view.
3. Write the validator **before** the content, so bad items cannot land.
4. Fill items from repos + existing documents. Status from code, not prose.
5. Link tasks on both axes. Leave doubtful ones unlinked.
6. Write `done_when` at epic level, then at task level wherever shared.
7. Build drift and readiness. Fix what they find. Re-run.
8. Wire the validator into the commit hook. **Test it rejects.**
9. Create the intake queue. Seed it with live inputs so it is not aspirational.
10. **Find the executor's real selection rule by calling it.** Wire the sprint
    into it, and check the runner host has the code.
11. Execute one queued task end to end yourself. A loop you have not run once is
    a loop you are guessing about.
12. Report the residue honestly — what is unlinked, unproven, undecided.

## What to tell the user at the end

Give the number, and give what it does not cover. In the PLUR rebuild the last
finding was *"36 epics marked delegable with no agent work yet in review —
delegability is asserted, not demonstrated."* That is a fact, not a defect, and
editing it away would have been the only dishonest move available.

**Structural readiness is not demonstrated readiness.** One agent completing one
task end to end is worth more than any audit — say so.
