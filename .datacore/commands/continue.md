# Continue

## Command Context

### When to Reference DIP-0016

**Always reference when:**
- Resuming incomplete work from previous sessions
- Finding high-impact next actions
- Loading bootstrap context from continuation tasks
- Suggesting work based on system state

**Key decisions this DIP informs:**
- Bootstrap prompt retrieval from :continuation: tasks
- Session memory queries for context
- Task prioritization (impact vs effort)

### Quick Reference

| Question | Answer |
|----------|--------|
| When to run? | Start of session, or when looking for what to work on |
| Duration? | ~1-2 minutes |
| Key output? | Loaded context, suggested next action |
| What DIPs govern this? | DIP-0016 (Session Memory), DIP-0009 (GTD) |

### Agents This Command Invokes

### Integration Points

- **DIP-0016** - Session memory retrieval
- **DIP-0009** - Task scanning and prioritization
- **/wrap-up** - Creates continuation tasks this command consumes
- **/today** - Morning complement (broader scope)

---

Resume incomplete work or find the highest-impact next action.

## Usage

```
/continue [search_term]       # Resume: find and load continuation tasks
/continue                     # Resume: show all continuations or suggest next actions
/continue --save [topic]      # Save: create continuation task, schedule next working day, then /wrap-up
```

**Examples:**
- `/continue` - Show all continuation tasks
- `/continue fairdrop` - Find continuations related to fairdrop
- `/continue --save` - Save current work as continuation and wrap up
- `/continue --save "organization deal"` - Save with explicit topic

**Also triggered by natural language:**
- "continue" / "what should I work on" / "resume" / "what's next" → **Resume mode**
- "save and continue later" / "park this" / "continue this tomorrow" → **Save mode**

**When**: Start of session (resume), or end of session (save)
**Duration**: ~1-2 minutes (resume), ~3-5 minutes (save, includes wrap-up)

## Context

This command has two modes:

**Resume mode** (default): You're starting a new session. Either:
1. You have unfinished work from a previous session (continuation tasks)
2. You want guidance on the highest-impact thing to work on

**Save mode** (`--save`): You're ending a session with unfinished work. This:
1. Creates a continuation task from current session context
2. Schedules it on the next working day (skips weekends)
3. Calls `/wrap-up` to complete the session

## Tracked Checklist Note

`/continue` has only 3-4 steps and low skip risk. No mandatory TaskCreate checklist needed. However, if using `--save` mode (which triggers `/wrap-up`), the wrap-up checklist applies — see `/wrap-up` Step 0.

## Sequence

### 0. Save Mode (--save)

If invoked with `--save`:

```
═══════════════════════════════════════════════════
CONTINUE — SAVE MODE
═══════════════════════════════════════════════════

Analyzing current session...
```

**Steps:**

1. **Summarize session context** — review conversation to extract:
   - What was being worked on
   - What was accomplished
   - What remains to be done
   - Key files touched or referenced

2. **Determine next working day:**
   - If today is Friday → schedule Monday
   - If today is Saturday → schedule Monday
   - If today is Sunday → schedule Monday
   - Otherwise → schedule tomorrow
   - Format: `<YYYY-MM-DD Day>`

3. **Create continuation task** in `0-personal/org/inbox.org` (or appropriate space) using Rich Task Standard (DIP-0009 Part 3.5):

```org
*** TODO Continue: [topic or auto-generated summary]     :continuation:
SCHEDULED: <next-working-day>
:PROPERTIES:
:CREATED: [YYYY-MM-DD Day HH:MM]
:SOURCE:  conversation
:EFFORT:  [Quick/Moderate/Significant — estimate remaining work]
:CONTEXT: |
  What was being worked on and why.
:KEY_FILES: |
  - [relevant file paths from session]
:CURRENT_STATUS: |
  What was accomplished this session.
  Journal YYYY-MM-DD ## Session N: "Key progress summary"
:ACCEPTANCE_CRITERIA: |
  - What "done" looks like for remaining work
:TOOLS: |
  - Approach hints for resuming
:BOOTSTRAP: |
  [Full bootstrap prompt for next session]
  Next steps: [remaining work]
  Blockers: [any known blockers]
:END:
```

4. **Confirm to user:**

```
CONTINUATION SAVED
──────────────────
Task: Continue: [topic]
Scheduled: <next-working-day>
Location: 0-personal/org/inbox.org

Launching /wrap-up...
```

5. **Invoke `/wrap-up`** — this handles journals, learnings, sync.

---

### 1. Resume Mode — Scan for Continuation Tasks

```
═══════════════════════════════════════════════════
CONTINUE
═══════════════════════════════════════════════════

Scanning for continuation tasks...
[Search term: {search_term or "all"}]

[Grep org files for :continuation: tag]
[Filter by search term if provided]
```

**Search locations:**
- `0-personal/org/inbox.org`
- `0-personal/org/next_actions.org`
- `[space]/org/inbox.org`
- `[space]/org/next_actions.org`

### 2a. If Continuation Tasks Found

```
CONTINUATION TASKS FOUND
────────────────────────

[If multiple matches:]
┌───┬────────────────────────────────┬────────────┬──────────┐
│ # │ Task                           │ Created    │ Space    │
├───┼────────────────────────────────┼────────────┼──────────┤
│ 1 │ Continue: ProductX v3 testing   │ 2026-01-22 │ personal │
│ 2 │ Continue: DIP-0019 impl        │ 2026-01-23 │ projectspace│
│ 3 │ Continue: Landing page A/B     │ 2026-01-20 │ teamspace│
└───┴────────────────────────────────┴────────────┴──────────┘

Select task to continue (1-3), or Enter for most recent:
> [user input]

[If single match or user selects:]
Loading continuation context...

───────────────────────────────────────────────────
BOOTSTRAP CONTEXT
───────────────────────────────────────────────────

[Display BOOTSTRAP property content from task]

Context: [What was being worked on]
Progress: [What was accomplished]
Next steps: [Specific next actions]
Key files: [Relevant file paths]
Blockers: [Any known blockers]

───────────────────────────────────────────────────

Ready to continue. First step: [extracted from bootstrap]
```

### 2b. If No Continuation Tasks (or none match search)

**Tool usage for task discovery:**
- Use `gtd.agenda_view` with `states: ['NEXT', 'TODO']` to get the full task list
- Use `gtd.effort_aggregate` to show work distribution across focus areas
- Use `gtd.deadline_warnings` to surface time-sensitive items

```
NO CONTINUATION TASKS
─────────────────────
[No tasks found matching "{search_term}"]

Analyzing system for high-impact next actions...

[Scan these sources:]
  - gtd.agenda_view tool (pending tasks with priority and deadline)
  - Recent journals (last 7 days - incomplete work mentions)
  - Git repos (uncommitted changes, stale branches)
  - Inbox files (unprocessed items by age)

───────────────────────────────────────────────────
SUGGESTED NEXT ACTIONS
───────────────────────────────────────────────────

Based on system analysis:

HIGH IMPACT:
┌───┬────────────────────────────────┬────────────┬──────────────┐
│ # │ Action                         │ Source     │ Why          │
├───┼────────────────────────────────┼────────────┼──────────────┤
│ 1 │ Finalize DIP-0019 impl         │ inbox.org  │ Scheduled    │
│ 2 │ Process 90 nightshift reports  │ inbox.org  │ Inbox bloat  │
└───┴────────────────────────────────┴────────────┴──────────────┘

LOW HANGING FRUIT:
┌───┬────────────────────────────────┬────────────┬──────────────┐
│ # │ Action                         │ Source     │ Why          │
├───┼────────────────────────────────┼────────────┼──────────────┤
│ 3 │ Push stale branch: feature-x   │ git        │ 2 commits    │
│ 4 │ Mark 3 DONE tasks complete     │ journal    │ Quick wins   │
└───┴────────────────────────────────┴────────────┴──────────────┘

Select action (1-4), or describe what you want to work on:
> [user input]
```

### 3. Load Context and Begin

**If continuation task selected:**
```
LOADING CONTEXT
───────────────
Reading key files from bootstrap...
  - [file 1] ✓
  - [file 2] ✓

Context loaded. You can now:
1. Ask questions about the previous work
2. Continue from the next step
3. See full task details

[Mark task as in-progress in org file]

What would you like to do first?
```

**If suggested action selected:**
```
STARTING: [Action description]
─────────────────────────────────

[Load relevant context for the action]
[Display brief summary of what's involved]

Ready to begin. [First suggested step]
```

## Prioritization Logic

When no continuation tasks exist, rank potential actions by:

### High Impact Criteria
1. **Scheduled for today/overdue** - Time-sensitive
2. **Blocking other work** - Dependency chain
3. **Priority A tasks** - Explicitly marked important
4. **Large inbox accumulation** - System health
5. **Stale drafts** - Work started but abandoned

### Low Hanging Fruit Criteria
1. **Single-step completions** - Mark DONE, push commit
2. **Small file counts** - Quick processing
3. **Clear next action** - No ambiguity
4. **Recent context** - Fresh in memory (last 3 days)

### Scoring Formula
```
Score = (Impact × 0.6) + (Ease × 0.4)

Impact:
  - Scheduled today: +3
  - Overdue: +4
  - Priority A: +3
  - Blocking others: +2
  - Inbox > 50 items: +2

Ease:
  - Single step: +3
  - Recent (< 3 days): +2
  - Has bootstrap context: +2
  - < 5 files involved: +1
```

## Continuation Task Format

Tasks created by `/wrap-up` follow the Rich Task Standard (DIP-0009 Part 3.5):

```org
*** TODO Continue: [task description]                    :continuation:
SCHEDULED: <YYYY-MM-DD Day>
:PROPERTIES:
:CREATED: [YYYY-MM-DD Day HH:MM]
:SOURCE:  conversation
:EFFORT:  [Quick/Moderate/Significant]
:CONTEXT: |
  What was being worked on and why.
:KEY_FILES: |
  - [relevant file paths]
:CURRENT_STATUS: |
  What was accomplished. Journal references.
:ACCEPTANCE_CRITERIA: |
  - What "done" looks like
:BOOTSTRAP: |
  [Full bootstrap prompt for next session]
  Next steps: [remaining work]
  Blockers: [any known blockers]
:END:
```

The `:BOOTSTRAP:` property contains session-specific resumption context. Rich Task Standard fields (CONTEXT, KEY_FILES, CURRENT_STATUS, ACCEPTANCE_CRITERIA) enable nightshift to execute continuation tasks with full context if they carry an `:AI:` tag.

## Files Referenced

**Read:**
- `org/inbox.org` (all spaces)
- `org/next_actions.org` (all spaces)
- Recent journal entries (last 7 days)
- Git status across repos

**Update:**
- Selected task status (TODO → NEXT/in-progress)

## Automation Level

| Step | Automation |
|------|------------|
| Continuation scan | Automatic |
| Task selection | User choice (or auto if single match) |
| Bootstrap loading | Automatic |
| System analysis | Automatic (when no continuations) |
| Action suggestion | Automatic (ranked by score) |
| Context loading | Automatic |

## Examples

### Example 1: Single Continuation Match

```
> /continue productx

═══════════════════════════════════════════════════
CONTINUE
═══════════════════════════════════════════════════

Scanning for continuation tasks...
[Search term: "productx"]

Found 1 matching task:

*** TODO Continue: ProductX v3 escrow testing        :continuation:

Loading bootstrap context...

───────────────────────────────────────────────────
BOOTSTRAP CONTEXT
───────────────────────────────────────────────────

Context: Testing ProductX v3 escrow mechanism on Sepolia
Progress: Contract deployed, basic tests passing
Next steps:
  1. Test edge case: expired escrow claim
  2. Test edge case: partial release
  3. Run full integration test suite
Key files:
  - 3-partnerspace/2-projects/productx/contracts/Escrow.sol
  - 3-partnerspace/2-projects/productx/test/escrow.test.ts
Blockers: None

───────────────────────────────────────────────────

Ready to continue. First step: Test edge case for expired escrow claim.
```

### Example 2: No Continuations, System Suggests

```
> /continue

═══════════════════════════════════════════════════
CONTINUE
═══════════════════════════════════════════════════

Scanning for continuation tasks...
[Search term: all]

No continuation tasks found.

Analyzing system for high-impact next actions...

───────────────────────────────────────────────────
SUGGESTED NEXT ACTIONS
───────────────────────────────────────────────────

HIGH IMPACT:
1. Finalize all draft DIPs (scheduled Mon, Priority B)
2. Complete DIP-0017 migration - rename 4-archive (scheduled Fri, Priority A)

LOW HANGING FRUIT:
3. Push 2-projectspace branch (1 commit ahead)
4. Archive 90 nightshift reports in 1-teamspace (clear inbox)

Select action (1-4), or describe what you want to work on:
>
```

## Related

- `/wrap-up` - Session wrap-up (called automatically in save mode)
- `/today` - Morning briefing (broader scope)
- `/tomorrow` - End of day, AI delegation
