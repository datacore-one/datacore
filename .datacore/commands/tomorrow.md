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
3. "Journal entry" (activeForm: "Writing journal entry")
4. "Evening coaching" (activeForm: "Running evening check-in")
5. "DIP gap detection" (activeForm: "Scanning for DIP gaps")
6. "Task housekeeping + priorities" (activeForm: "Processing tasks")
7. "AI delegation review" (activeForm: "Reviewing AI task queue")
8. "Tomorrow preview + final status" (activeForm: "Generating preview")
9. "Verify all checklist tasks completed" (activeForm: "Verifying checklist completion")
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

Running ./sync push to save work...
[Commit and push all dirty repos]

Done. All repos synced.
```

**Uses `./sync push` with retry logic if needed.**

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

### 4. Journal Entry

**Update today's journal with wrap-up:**

```
JOURNAL UPDATE
--------------
Adding end-of-day entry to journal...

What did you accomplish today? (brief, or press Enter to skip)
> [user input]

Any blockers or open items? (brief, or press Enter to skip)
> [user input]
```

**Append to journal:**
```markdown
## End of Day

**Accomplished:**
- [user input or auto-generated from commits]

**Open Items:**
- [user input]

**System Status:** All repos synced, diagnostics passed
```

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
