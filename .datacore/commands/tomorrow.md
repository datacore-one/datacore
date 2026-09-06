---
name: tomorrow
description: End-of-day review and AI delegation — queue overnight nightshift work
recall:
  # Per DIP-0029
  scopes:
    - command:tomorrow
  tags:
    - tomorrow
    - nightshift
    - delegation
  query:
    - "/tomorrow priorities AI delegation"
    - "nightshift task tagging"
---

# Tomorrow

## Command Context

### When to Reference DIP-0011

**Always reference when:**
- Queuing tasks for nightshift execution
- Routing :AI: tagged tasks to nightshift.org
- Reviewing overnight execution pipeline
- Estimating execution time and cost

**Key decisions this DIP informs:**
- Task movement from next_actions.org → nightshift.org
- Queue optimization (impact/effort/urgency)
- Nightshift server triggering

### Quick Reference

| Question | Answer |
|----------|--------|
| AI queue? | `org/nightshift.org` |
| When to run? | End of work day |
| Server trigger? | Push triggers server execution |
| What DIPs govern this? | DIP-0011 (Nightshift), DIP-0009 (GTD) |

### Agents This Command Invokes

| Agent | Purpose |
|-------|---------|
| `session-learning-coordinator` | Pattern extraction |
| `coach` | Evening REBT reflection (optional) |

### Integration Points

- **DIP-0011** - Nightshift pipeline
- **DIP-0009** - GTD daily end workflow
- **/today** - Morning counterpart

---

**"End of watch. Securing all stations for the night."**

End-of-day command that closes out today, celebrates accomplishments, and builds excitement for tomorrow. The counterpart to `/today`.

## Purpose

- Celebrate what you accomplished today
- Process inbox to zero (or delegate)
- Delegate work to AI for overnight execution
- Ensure system is clean and synced
- Build excitement for tomorrow

## Duration

~10 minutes (mostly automated, optional user input)

## Behavior

Execute the evening shutdown sequence with a focus on accomplishment and anticipation.

## Step 0: Create Tracked Checklist (MANDATORY FIRST STEP)

**Before doing anything else**, create a tracked task list for the /tomorrow steps. This prevents step skipping in end-of-day processing.

Use `TaskCreate` to create one task per major step:

```
Tasks to create (mark in_progress when starting, completed when done):

1. "Sync and inbox status" (activeForm: "Checking sync and inbox")
2. "Quick diagnostics" (activeForm: "Running diagnostics")
3. "Day summary and goal achievement" (activeForm: "Computing daily score")
4. "Journal entry" (activeForm: "Writing journal entry")
5. "Evening coaching" (activeForm: "Running evening check-in")
6. "DIP gap detection" (activeForm: "Scanning for DIP gaps")
7. "Task housekeeping + priorities" (activeForm: "Processing tasks")
8. "AI delegation review" (activeForm: "Reviewing AI task queue")
9. "Tomorrow preview + final status" (activeForm: "Generating preview")
10. "Verify all checklist tasks completed" (activeForm: "Verifying checklist completion")
```

**The final task (#9) is a gate:** Before marking it complete, run `TaskList` and verify every prior task shows `completed`. If any task is still `pending` or `in_progress`, go back and finish it.

**Why this exists:** /tomorrow's middle steps (DIP gap detection, task housekeeping) get compressed when the user is tired at end-of-day. Tracked tasks ensure each step gets proper attention.

## Sequence

### 1. Repository Sync Verification

**Verify all repos are synced (should be clean if /wrap-up was used):**

```
REPOSITORY STATUS
─────────────────
Verifying all repositories...

datacore (root).......... [SYNCED]
0-personal............... [SYNCED]  ← syncs with nightshift server (private)
teamspace................ [SYNCED]
projectspace............. [SYNCED]

All repos synced.
```

**If dirty repos found (rare - means /wrap-up was skipped):**

```
⚠ Uncommitted changes found:

datacore (root).......... [DIRTY - 5 uncommitted]
projectspace............. [DIRTY - 1 uncommitted]

Running the transport to save work...
[converge every knowledge repo; fast-forward code repos, never commit them]

Done. 12 repo(s), 0 needing a human.
```

**Uses `python3 .datacore/lib/ledger_transport.py sync`.** The old `./sync push`
is retired: it autostashed and swallowed conflicts (datacore#31).

### 2. Inbox Status

**Check all inboxes for unprocessed items:**

```
INBOX STATUS
------------
Personal (0-personal/0-inbox/)...... 2 items
Team (1-teamspace/0-inbox/)......... 1 item
Project (2-projectspace/0-inbox/)... 0 items

Unprocessed items found. Process now? [Y/n]
```

**If items exist:**
- List each item briefly
- Offer to process or defer to morning

### 3. Quick Diagnostics

**Run abbreviated diagnostic (critical systems only):**

```
QUICK DIAGNOSTIC
----------------
Core Systems............ [OPERATIONAL]
Repository Health....... [5/6 SYNCED]
Space Integrity......... [ALL OPERATIONAL]
DIP Compliance.......... [OK]

Knowledge Database (DIP-0004):
  Last sync............. 2 hours ago
  Tasks indexed......... 234
  Sessions today........ 3
  Status: CURRENT

[If issues found:]
⚠ Minor issues detected. Auto-heal? [Y/n]
```

**Run database stats:**
```bash
python ~/.datacore/lib/datacore_sync.py stats --quiet
```

**Auto-heal actions:**
- Rebuild composed CLAUDE.md files if stale
- Fix obvious git issues (stale locks, etc.)
- Re-sync database if stale (>24 hours)
- Report what was fixed

### 4. Day Summary and Goal Achievement

**The core new section.** Compare the morning briefing priorities against actual accomplishments.

```
DAY SUMMARY
───────────
Comparing morning plan vs actual work...

Morning Must-do:
  [x] Datacore infrastructure -- central git origin (DONE, 2:30h)
  [ ] QVAC PoC -- overdue, not addressed today
  [x] Reply to Polona re: payroll (DONE, 0:05)

Morning Should-do:
  [x] Numina.rs website continuation (DONE, 1:45h)
  [x] Crt's PLUR hook-inject bug (DONE, 0:45)
  [ ] Stefan outreach -- not reviewed

Morning Could-do:
  [ ] Venture heartbeat -- deferred
  [ ] Reddit repost -- deferred

Ad-hoc (not planned):
  [x] /today briefing redesign (3:00h) -- emerged from morning review
  [x] Email inbox processing (0:30h) -- proactive
  [x] Trading dashboard P&L fix (0:15h) -- discovered bug

DAILY SCORE
───────────
  Must-do:  2/3 (67%)
  Should-do: 2/3 (67%)
  Could-do: 0/2 (0%)
  Ad-hoc:   3 tasks (valuable but unplanned)

  Planned completion: 4/8 (50%)
  Total tasks completed: 7
  Alignment: REACTIVE -- more ad-hoc work than planned work

  Trend: [sparkline or 7-day rolling average]
```

**How to compute:**

1. Read the morning briefing from today's journal (`## Daily Briefing` > `## Your Agenda`)
2. Parse Must-do / Should-do / Could-do items
3. Match against DONE tasks in `next_actions.org` (CLOSED timestamp = today)
4. Also count tasks completed today that were NOT in the morning plan (ad-hoc)
5. Calculate scores

**Alignment categories:**
- **FOCUSED** (>70% planned completion, ad-hoc < planned) -- doing what you set out to do
- **PRODUCTIVE** (many completions but <50% planned) -- busy but reactive
- **REACTIVE** (more ad-hoc than planned, <50% planned) -- day driven by events
- **LIGHT** (few completions overall) -- rest day or meetings-heavy

**Persist to state:**

Write daily score to `0-personal/.datacore/state/daily-scores.yaml`:

```yaml
scores:
  - date: "2026-04-17"
    must_do: [2, 3]      # completed, total
    should_do: [2, 3]
    could_do: [0, 2]
    adhoc: 3
    total_completed: 7
    planned_pct: 50
    alignment: reactive
    readiness: 82         # from Oura (links capacity to output)
```

This enables trend analysis in `/today` morning briefing and weekly review.

### 4b. Journal Entry

**Update today's journal with the day summary:**

Append to journal under `## End of Day`:

```markdown
## End of Day

**Daily Score:** 50% planned | 7 tasks completed | Alignment: REACTIVE

**Must-do:** 2/3 -- QVAC still overdue (3rd consecutive day)
**Ad-hoc:** /today redesign, email triage, trading P&L fix

**Open Items:**
- QVAC PoC -- still not addressed, now 4 days overdue
- Stefan outreach emails -- needs decision

**Reflection:** [user input if provided, or auto-generated]
```

The daily score appears in tomorrow's `/today` briefing as part of "Good Morning":
"Yesterday you hit 50% of planned goals. The QVAC PoC has been overdue for 3 days
now -- it keeps not making the cut. Either do it first thing tomorrow or formally
renegotiate."

### 5. Evening Coaching Reflection (Optional)

**Spawn the `coach` agent in evening mode for REBT practice:**

```
EVENING COACHING REFLECTION
───────────────────────────
Time for evening reflection on emotional patterns.

Did any situation trigger strong emotions today? (y/n/skip)
> [user input]

[If yes:]
  Briefly describe the situation (A):
  > [user input]

  What emotion(s) did you feel most strongly? (C)
  > [user input]

  What belief might have been driving this? (B)
  Look for:
  - MUST statements ("They MUST respect me")
  - Awfulizing ("This is UNBEARABLE")
  - Self-downing ("I'm INCOMPETENT")
  > [user input]

  [Brief disputation based on identified belief]

  Let's challenge that belief:
  - Where is it written that [their MUST]?
  - What evidence supports/contradicts this?
  - How is this belief helping you?
  > [coaching dialogue]

PRACTICE RECOMMENDATION
───────────────────────
Complete the disputation form (takes ~10 min):
→ https://docs.google.com/forms/d/e/1FAIpQLSf99Nbf8jFY91kzsgaqSZJ80Z7AHDC9wbPjGyDW76-LCgjIKA/viewform

Rational Emotive Imagery (2 min):
- Relive the triggering situation
- Feel the unhealthy emotion briefly
- Keep situation same, shift to healthy emotion
- Focus on rational belief until natural

Open the form now? [Y/n/skip]
> [If yes: open in browser]
```

**Behavior:**
- More depth than morning - this is reflection time
- Guide through ABC if user has a trigger to process
- Offer disputation coaching using Ellis's style
- Always offer (never force) the form
- Log to journal under `## Coaching` section

**Skip behavior:**
- User can always skip with "n", "skip", or Enter
- If skipped at first prompt, move to next section
- Skipping is judgment-free - no guilt

**Journal logging:**
```markdown
## Coaching

**Evening reflection:** completed/skipped
**Trigger processed:** [brief description or "none"]
**Key belief:** [the irrational belief if identified]
**Disputation insight:** [what clicked, if any]
**Form completed:** [yes/no/skipped]
```

**Configuration** (in `.datacore/settings.local.yaml`):
```yaml
coach:
  enabled: true
  evening_reflection: true
  auto_open_form: false
```

If `coach.evening_reflection: false`, skip this section entirely.

### 6. DIP Gap Detection

**Check for architectural decisions needing documentation:**

```
DIP GAP DETECTION
─────────────────
Scanning today's journal for architectural decisions...

[Analyze today's journal entry for patterns indicating architectural work:]
- Mentions of "DIP-XXXX Implementation"
- Keywords: "architecture", "design decision", "system pattern"
- Multi-session work (>3 sessions on same topic)
- Tag: #pending-dip
- Phrases: "architectural decision", "design choice", "system-wide change"

[If architectural work detected without corresponding DIP:]

⚠️ ARCHITECTURAL WORK DETECTED - DIP DOCUMENTATION GAP

Today's work included architectural decisions that may warrant DIP creation:

**Detected Patterns:**
1. [Pattern type] - "[Quote from journal]"
   Context: [Session title or work description]
   Gap: [Why this needs DIP documentation]

2. [Pattern type] - "[Quote from journal]"
   Context: [Session title or work description]
   Gap: [Why this needs DIP documentation]

**Recommended Actions:**
1. Tag decision notes with #pending-dip for weekly review tracking
2. Create DIP stub now (5 min): /create-dip [topic]
3. Schedule DIP writing session (30-60 min)

Create DIP stub now? [Y/n/defer to weekly]
> [user input]

[If Y: spawn dip-preparer agent to create stub]
[If defer: add #pending-dip tag to journal entry]
[If n: skip but log to weekly review queue]

[If no gaps detected:]
No architectural decisions requiring DIP documentation detected today.
```

**Detection Logic:**

Scan today's journal (`0-personal/journal/[today].md`) for:

1. **Explicit DIP work without DIP file:**
   - Mentions "DIP-XXXX Implementation" but no corresponding file in `.datacore/dips/`
   - Indicates implementation happened before specification

2. **Multi-session architectural work:**
   - Session titles with "architecture", "design", "system", "pattern"
   - Same topic across 3+ sessions (suggests significant design work)
   - No corresponding DIP reference in journal

3. **Tagged items:**
   - Search for `#pending-dip` tag in journal
   - These are user-flagged decisions awaiting formalization

4. **Decision keywords:**
   - "architectural decision", "design decision", "system-wide change"
   - "consolidation", "migration", "refactoring" (of system, not code)
   - "specification", "standard", "convention"

**Output File (if gaps found):**

Write detection results to: `0-personal/0-inbox/pending-dip-[date].md`

```markdown
# Pending DIP Documentation - [Date]

## Detected Architectural Work

### [Decision/Pattern Name]

**Source:** [Journal date], [Session title]

**What was decided:**
[Quote or summary from journal]

**Why this needs DIP:**
[Gap explanation - e.g., "System-wide pattern affecting all agents"]

**Scope:**
- Affected components: [List]
- Related DIPs: [Existing DIPs this relates to]
- Impact: [High/Medium/Low]

**Next Steps:**
- [ ] Create DIP stub: /create-dip [topic]
- [ ] Draft specification (30-60 min session)
- [ ] Review with stakeholders (if applicable)
- [ ] Submit PR to datacore repo

---

[Repeat for each detected gap]

## Summary

Total gaps detected: X
High priority: X
Medium priority: X
Low priority: X

**Weekly Review:** These items will appear in Friday's GTD weekly review.
```

### 7. Task Housekeeping

**Before setting priorities, clean up the task system:**

**Tools to use:**
- Use `gtd.archive_tasks` with `dry_run: false` to archive DONE tasks older than 30 days from `next_actions.org`
- Use `gtd.project_health` to identify stuck projects (no active tasks, all waiting, empty projects)

```
TASK HOUSEKEEPING
─────────────────
Archiving old completed tasks...
  → [N] tasks archived (DONE > 30 days)

Project health check:
  → [N] projects healthy
  → [N] projects stuck (flagged for tomorrow's attention)
```

### 7b. Tomorrow's Priorities

**Gather input for tomorrow:**

```
TOMORROW'S PRIORITIES
---------------------
What's most important for tomorrow? (1-3 items, or Enter to skip)
> [user input]

These will appear in tomorrow's /today briefing.
```

**Store in:**
- `0-personal/journal/tomorrow-priorities.md` (temporary file)
- Or append to tomorrow's journal entry if it exists

### 8. AI Delegation Review (Nightshift Queue)

> **There are two delegation paths, and they do not see each other. Say which one you used.**
>
> | Path | How work is created | Who executes it |
> |---|---|---|
> | **Nightshift (this step)** | `:AI:` tag → moved to `nightshift.org` | `nightshift-orchestrator`, via the org files |
> | **v2 ledger** | `materialize()` → an `item.create` event with no `org` block | `ledger_claim.py`, across the fleet |
>
> `ledger_ingest_org.py` mirrors every org task into the ledger on the 05:35 sweep, but a mirrored item carries an `org` block in its payload and `ledger_claim.py` filters exactly those out:
> `pending = [i for i in claimable if not (i.payload or {}).get("org")]`
>
> That filter is deliberate — without it the first live pull reported "343 claimable items" and agents would have started working through a personal backlog unattended across five machines. The consequence for this step: **queueing a task into `nightshift.org` does not make it available to the v2 fleet, and never will.** Nightshift picks it up; `ledger_claim` does not.
>
> Every space is still at DIP-0043 Phase 0 (no `phase1-active` marker anywhere), so this step's org writes remain correct. When a space flips to Phase 1 its `next_actions.org` becomes generated and read-only, and this step's task movement must become event emission for that space. Check the marker before assuming; the answer is per-space and never "the whole installation is on v2".

**Main AI delegation happens here via Nightshift module:**

**Step 1: Recurring Task Instance Creation (DIP-0009 Part 3.6)**

Before routing tasks, check for recurring AI task templates:

```
RECURRING TASKS
───────────────
Scanning for :recurring: templates with SCHEDULED <= today...

Found 2 due templates:
  • Daily research digest (.+1d, last: 2026-02-20) → Creating instance
  • Weekly trading review (.+7d, last: 2026-02-14) → Creating instance

Instances created in nightshift.org:
  [✓] Daily research digest (instance, no :recurring: tag)
  [✓] Weekly trading review (instance, no :recurring: tag)

Templates updated: SCHEDULED advanced by repeater interval.
```

**Recurring task logic:**
1. Scan `next_actions.org` for tasks with `:recurring:` tag where SCHEDULED <= today
2. For each due template:
   - Copy all properties to a new instance in `nightshift.org`
   - Remove `:recurring:` tag from instance (instance is executable)
   - Inject fresh `:CURRENT_STATUS:` from journals + last execution output
   - If template SCHEDULED is far in the past: create ONE instance for today only (no backfill)
   - Advance template SCHEDULED date by repeater interval
3. Nightshift MUST skip any task with `:recurring:` tag (only instances are executed)

**Step 2: Task Movement to nightshift.org**

Scan for `:AI:` tagged tasks (non-recurring) that need to be queued:

```
TASK ROUTING
────────────
Scanning for :AI: tagged tasks...

Found 3 tasks to queue:
  • Research competitor X (next_actions.org → nightshift.org/Project Alpha)
  • Draft blog post (next_actions.org → nightshift.org/Organization)
  • Privacy paper review (research_learning.org → nightshift.org/Research/Project Alpha)

Moving tasks to nightshift queue...
  [✓] Research competitor X → QUEUED
  [✓] Draft blog post → QUEUED
  [✓] Privacy paper review → QUEUED

Done. 3 tasks queued.
```

**Movement logic:**
1. Scan `next_actions.org` and `research_learning.org` for `:AI:` tagged tasks in TODO/NEXT state (excluding `:recurring:` templates)
2. For each task found:
   - Identify parent heading path (e.g., "TIER 1/Project Alpha")
   - Find or create matching heading in `nightshift.org`
   - Copy task to nightshift.org under that heading
   - Change state from TODO/NEXT to QUEUED
   - Remove task from source file
3. Commit the changes

**Step 3: Display Queue**

```
NIGHTSHIFT QUEUE
────────────────
Reviewing tasks for overnight AI execution.

Current QUEUED tasks:
┌─────────────────────────────────────────────────────────────────┐
│ # │ Space    │ Task                      │ Type         │ Pri │
├───┼──────────┼───────────────────────────┼──────────────┼─────┤
│ 1 │ team     │ Research competitor X     │ :AI:research:│  A  │
│ 2 │ team     │ Draft blog post           │ :AI:content: │  B  │
│ 3 │ personal │ Organize reading list     │ :AI:        │  C  │
└─────────────────────────────────────────────────────────────────┘

Queue Optimization Preview:
  1. Research competitor X - Impact: 9, Effort: 3, Score: 7.8
  2. Draft blog post       - Impact: 6, Effort: 5, Score: 5.9
  3. Organize reading list - Impact: 4, Effort: 2, Score: 3.8

Estimated: 3 tasks, ~45 min, $0.35

Add more tasks to delegate? (describe, or Enter to continue)
> [user input]

[If input provided:]
Creating task with :AI: tag...

Confirm queue for nightshift? [Y/n]
> y

Pushing changes to make available for server...
Done. Triggering nightshift server...
```

**Nightshift Pipeline (DIP-0011):**
1. **Queue Optimizer** - Prioritizes by impact/effort/urgency
2. **Context Enhancer** - Builds KB context (datacortex, patterns, journals)
3. **Execution** - Specialized agents process each task
4. **Evaluation Panel** - 6 core evaluators + domain experts review
5. **Learning Extractor** - Captures patterns for improvement

**Evaluators that will review outputs:**
- Core: User, Critic, CEO, CTO, COO, Archivist (always)
- Domain: Twain (content), Popper (research), etc. (by task type)

**What gets delegated:**
- `:AI:research:` → `knowledge-extractor`
- `:AI:content:` → `gtd-content-writer`
- `:AI:data:` → `gtd-data-analyzer`
- `:AI:pm:` → `gtd-project-manager`

**Server Configuration:**
If server is configured (`.datacore/settings.local.yaml`):
- Push triggers server execution
- Server pulls, processes, pushes results
- Results available in morning `/today`

If no server:
- Tasks queue for local execution
- Manual trigger or scheduled local run

### 9. Tomorrow's Preview

**Show what's already scheduled:**

```
TOMORROW'S PREVIEW
------------------
Date: [tomorrow's date]

Scheduled:
- [calendar items if available]
- [org-mode scheduled items for tomorrow]

Pending AI Tasks:
- [count] tasks tagged :AI: in queue

Inbox Items:
- [count] items to process

Your Priorities (just set):
1. [priority 1]
2. [priority 2]
```

### 10. Final Status

**Before the closing message, archive the session:**

```bash
python3 ~/Data/.datacore/lib/session_archive.py --json
python3 ~/Data/.datacore/lib/session_learning_sweep.py --status | tail -2
```

`/tomorrow` is a day-end command, so it is the last chance to guarantee the day's own session reaches the archive that the 05:20 sweep reads. Idempotent — if `/wrap-up` or the SessionEnd hook already did it, this costs nothing and preserves `learning_status`.

Report the queue depth in the closing message so a sweep that has silently stopped running becomes visible the same evening rather than weeks later.

**Closing message:**

```
===============================
TOMORROW READY
===============================

All repositories: SYNCED
Inboxes: [CLEAR/X items pending]
Diagnostics: PASSED
Journal: UPDATED
Priorities: SET
Sessions queued for learning: N (sweep runs 05:20)

"The ship is secured. Rest well, Captain.
Tomorrow's briefing will be ready at 0700."
```

**Or if issues remain:**

```
===============================
TOMORROW READY (with notes)
===============================

⚠ 2 items remain in inbox (deferred)
⚠ 1 repo has uncommitted changes (user skipped)

"Most systems secured. These items will appear
in tomorrow's briefing for attention."
```

## Options

| Flag | Effect |
|------|--------|
| `--quick` | Skip user prompts, auto-commit, no priorities |
| `--no-push` | Commit but don't push (offline mode) |
| `--heal` | Auto-fix all issues without prompting |

## Integration

- Reads from `/diagnostic` for system checks
- Writes to journal (same format as `/today`)
- Priorities file read by `/today` next morning
- Can trigger `gtd-daily-end` processing if requested
- Invokes `session-learning` agent for pattern extraction
- Invokes `coach` agent for evening REBT reflection (optional)
- Updates `.datacore/learning/patterns.md` and `insights.md`

## Timing

Best run:
- End of work day
- Before shutting down
- After major work sessions

## Related Commands

| Command | Relationship |
|---------|--------------|
| `/today` | Morning counterpart - reads priorities set here |
| `/coach` | REBT coaching (evening reflection included here) |
| `/diagnostic` | Full system check (this runs abbreviated version) |
| `/gtd-daily-end` | GTD-specific wrap-up (can be called from here) |

---

*"Bridge to all hands: Secure your stations. Tomorrow's watch begins at 0700."*
