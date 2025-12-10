# Tomorrow

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

## Sequence

### 1. Repository Sync Verification

**Verify all repos are synced (should be clean if /wrap-up was used):**

```
REPOSITORY STATUS
─────────────────
Verifying all repositories...

datacore (root).......... [SYNCED]
datafund-space........... [SYNCED]
datacore-space........... [SYNCED]
datacore-dips............ [SYNCED]
trading.................. [SYNCED]

All repos synced.
```

**If dirty repos found (rare - means /wrap-up was skipped):**

```
⚠ Uncommitted changes found:

datacore (root).......... [DIRTY - 5 uncommitted]
datacore-space........... [DIRTY - 1 uncommitted]

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
Datafund (1-datafund/0-inbox/)...... 1 item
Datacore (2-datacore/0-inbox/)...... 0 items

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

### 5. Tomorrow's Priorities

**Gather input for tomorrow:**

```
TOMORROW'S PRIORITIES
---------------------
What's most important for tomorrow? (1-3 items, or Enter to skip)
> [user input]

These will appear in tomorrow's /today briefing.
```

**Store in:**
- `0-personal/notes/journals/tomorrow-priorities.md` (temporary file)
- Or append to tomorrow's journal entry if it exists

### 6. AI Delegation Review (Nightshift Queue)

**Main AI delegation happens here via Nightshift module:**

```
NIGHTSHIFT QUEUE
────────────────
Reviewing tasks for overnight AI execution.

Current :AI: tagged tasks:
┌─────────────────────────────────────────────────────────────────┐
│ # │ Space    │ Task                      │ Type         │ Pri │
├───┼──────────┼───────────────────────────┼──────────────┼─────┤
│ 1 │ datafund │ Research competitor X     │ :AI:research:│  A  │
│ 2 │ datafund │ Draft blog post           │ :AI:content: │  B  │
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
- `:AI:research:` → `gtd-research-processor`
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

### 7. Tomorrow's Preview

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

### 8. Final Status

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
| `/diagnostic` | Full system check (this runs abbreviated version) |
| `/gtd-daily-end` | GTD-specific wrap-up (can be called from here) |

---

*"Bridge to all hands: Secure your stations. Tomorrow's watch begins at 0700."*
