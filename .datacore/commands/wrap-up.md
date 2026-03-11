# Session Wrap-Up

## Command Context

### When to Reference DIP-0016

**Always reference when:**
- Capturing session learnings
- Creating continuation tasks
- Updating journals across spaces
- Syncing context and repos

**Key decisions this DIP informs:**
- Session memory extraction
- Bootstrap prompt format for continuations
- Per-space journal routing

### Quick Reference

| Question | Answer |
|----------|--------|
| When to run? | Before closing terminal |
| Duration? | ~2-5 minutes |
| Key output? | Continuation tasks, journal entries, patterns |
| What DIPs govern this? | DIP-0016 (Session Memory), DIP-0009 (GTD) |

### Agents This Command Invokes

| Agent | Purpose |
|-------|---------|
| `journal-coordinator` | Per-space journal entries |
| `session-learning-coordinator` | Pattern extraction |
| `context-maintainer` | Context sync if changes |
| `coach` | Quick emotional check (optional) |
| `learning-reviewer` | Engram candidate generation (DIP-0019) |

### Integration Points

- **DIP-0016** - Session memory embedding
- **DIP-0009** - Task completion marking
- **/tomorrow** - Day-end complement

---

Quick session wrap-up before closing a Claude Code conversation.

## Usage

```
/wrap-up
```

**Also triggered by natural language:**
- "wrap up"
- "let's wrap up"
- "it's done"
- "let's close"
- "I'm done"
- "that's it for now"
- "closing up"
- "end session"

**When**: Before closing terminal after a work session
**Duration**: ~2-5 minutes (mostly automated, light interaction)

## Context

You started this conversation with a goal. Work happened, insights emerged. Now you're ready to close this terminal window. This command ensures:
- Incomplete work becomes continuation tasks with context
- Learnings are captured
- Journal is updated
- Completed tasks are marked done

## Sequence

### 0a. Recover Full Session Context (BEFORE ANYTHING ELSE)

Long sessions get compacted — earlier conversation turns are summarized, losing detail. External work (e.g., repos in `/tmp/`, worktrees) may not be visible from `~/Data/` alone. Before generating any wrap-up output, reconstruct the full picture:

1. **Check for compaction**: If the conversation has been compacted (you see a summary of earlier work rather than the actual messages), read the full transcript file to recover details:
   ```
   # The transcript path is shown in the compaction summary
   # Read it to recover: file paths, decisions, errors, accomplishments
   ```

2. **Check user-specified arguments**: If the user passed arguments to `/wrap-up` (e.g., `/wrap-up check also tmp/ for full session`), scan those locations:
   ```bash
   # Example: scan /tmp for session repos
   ls -d /tmp/datacore-* /tmp/*-worktree* 2>/dev/null
   # Check git log in any found repos for today's commits
   git -C /tmp/found-repo log --oneline --since="today"
   ```

3. **Check additional working directories**: The environment may list additional working directories beyond `~/Data/`. Scan those for session work (git status, recent commits, modified files).

4. **Build session inventory**: Before proceeding, compile a complete list of:
   - All repos/directories where work happened (including `/tmp/`, worktrees)
   - All files created or modified (use `git diff --stat` in each repo)
   - Key decisions and errors from the full conversation history

**Store this inventory internally** — every subsequent step draws from it. Without this step, wrap-up misses work done before compaction or in external directories.

### 0b. Create Tracked Checklist (MANDATORY)

**After context recovery**, create a tracked task list for the wrap-up steps. This prevents silent step skipping — every step is visible and must be marked complete.

Use `TaskCreate` to create one task per non-automatic step:

```
Tasks to create (mark in_progress when starting, completed when done):

1. "Session summary" (activeForm: "Generating session summary")
2. "Continuation tasks" (activeForm: "Capturing continuation tasks")
3. "Mark completed tasks" (activeForm: "Marking completed tasks")
4. "Spawn coordinators (journal + learning)" (activeForm: "Spawning coordinators")
5. "Learning review" (activeForm: "Reviewing learning candidates")
6. "GTD task extraction" (activeForm: "Extracting GTD tasks")
7. "Insight verification" (activeForm: "Verifying insight capture")
8. "Session meta-analysis" (activeForm: "Writing meta-analysis")
9. "Artifact tracking" (activeForm: "Tracking knowledge artifacts")
10. "Push repos" (activeForm: "Pushing repos")
11. "Verify all checklist tasks completed" (activeForm: "Verifying checklist completion")
```

**The final task (#11) is a gate:** Before marking it complete, run `TaskList` and verify every prior task shows `completed`. If any task is still `pending` or `in_progress`, go back and finish it. Do NOT mark #11 complete until all others are done.

**Why this exists:** Without tracked tasks, steps 6-9 are routinely skipped or compressed in long sessions. The agent rationalizes "no tasks to extract" or "nothing to verify" without actually scanning. Tracked tasks make each step visible and non-skippable.

**CRITICAL:** Steps 4-5 must use `journal-coordinator` and `session-learning-coordinator` — NEVER spawn `journal-entry-writer` or `session-learning` directly with a hardcoded space name. Coordinators discover all relevant spaces automatically. Bypassing them silently skips spaces with actual work.

### 1. Session Summary (Automatic)

```
═══════════════════════════════════════════════════
SESSION WRAP-UP
═══════════════════════════════════════════════════

Session started: [HH:MM] (infer from first user message)
Goal: [Inferred from conversation start or ask user]

Work completed:
  - [List key accomplishments from session]
  - [Files created/modified]
  - [Decisions made]

───────────────────────────────────────────────────
```

**Note:** Record the session start time here (from first user message). It's needed at close for the duration calculation.

### 2. Quick Emotional Check (Optional)

**Brief coaching check-in at session end:**

```
QUICK CHECK
───────────
How are you feeling after this session? (1-10, or Enter to skip)
> [user input]

[If 1-4:]
  Something weighing on you? (brief, or skip for /tomorrow)
  > [user input or skip]

  [If input: note for evening reflection]

[If 5-10:]
  Great. Moving on.
```

**Behavior:**
- Ultra-brief - just a pulse check
- If low, offer to note for evening `/tomorrow` processing
- Don't do full ABC here - save for evening
- Skip if user is in a hurry

**Configuration** (in `.datacore/settings.local.yaml`):
```yaml
coach:
  wrap_up_check: true  # Include in /wrap-up
```

If `coach.wrap_up_check: false`, skip entirely.

### 3. Continuation Tasks

**If work is incomplete:**

```
CONTINUATION TASKS
──────────────────
This session's work appears incomplete. Let me capture what's needed to continue.

What remains to be done? (brief, or I'll infer from context)
> [user input or auto-inferred]

Creating continuation task with bootstrap context...
```

**Bootstrap prompt format** (Rich Task Standard — DIP-0009 Part 3.5):

```org
*** TODO Continue: [task description]                    :continuation:
SCHEDULED: <YYYY-MM-DD Day>
:PROPERTIES:
:CREATED: [YYYY-MM-DD Day HH:MM]
:SOURCE:  conversation
:EFFORT:  [Quick/Moderate/Significant — estimate remaining work]
:CONTEXT: |
  What was being worked on and why.
  What prompted this work originally.
:KEY_FILES: |
  - path/to/file1.md
  - path/to/file2.py
:CURRENT_STATUS: |
  What was accomplished this session.
  Journal YYYY-MM-DD ## Session N: "Key progress summary"
:ACCEPTANCE_CRITERIA: |
  - What "done" looks like for the remaining work
:TOOLS: |
  - Approach hints for resuming
:BOOTSTRAP: |
  [Full bootstrap prompt for next session — enough context
  to resume without re-reading the full conversation]
  Next steps:
  1. [Specific step 1]
  2. [Specific step 2]
  3. [Specific step 3]
  Blockers: [Any known blockers]
:END:
```

**The continuation task uses Rich Task Standard fields** (CONTEXT, KEY_FILES, CURRENT_STATUS, ACCEPTANCE_CRITERIA, TOOLS) plus the `:BOOTSTRAP:` extension field for session-specific resumption context. This means nightshift can execute continuation tasks with full context if they carry an `:AI:` tag.

### 4. Mark Completed Tasks

**Tools to use:**
- Use `gtd.write_clock_entry` for tasks worked during the session (infer start/end times from conversation message timestamps — first mention to last mention of each task)
- Use `gtd.duplicate_check` before creating any new tasks (continuation or GTD tasks) to avoid near-duplicates

```
TASK COMPLETION
───────────────
Checking for completed tasks from this session...

[Scan next_actions.org for tasks related to session work]
[Log CLOCK entries for tasks worked on using write_clock_entry]

Found X tasks that appear complete:
- [ ] Task 1 → Mark DONE? [Y/n]
- [ ] Task 2 → Mark DONE? [Y/n]

[Update org-mode states]
```

### 5. Session Learning & Journal Update (Coordinator Pattern)

**Spawn two coordinators in parallel:**

1. **`journal-coordinator`** - Discovers spaces, spawns journal-entry-writer per space
2. **`session-learning-coordinator`** - Discovers spaces, spawns session-learning per space

> ⚠ **Always delegate to the coordinator agents. Never call `session-learning` or `journal-entry-writer` directly with a hardcoded space name.** Coordinators discover all relevant spaces automatically via `ls -d [0-9]-*/`. Bypassing them causes spaces with actual work (e.g., root system files in the Datacore space) to be silently skipped.

```
SESSION LEARNING & JOURNALS
───────────────────────────
Discovering spaces and spawning per-space agents...

Spaces found: 0-personal, 1-teamspace, 2-projectspace

[Spawning in parallel:]
  - journal-coordinator → journal-entry-writer × N
  - session-learning-coordinator → session-learning × N

[Results aggregated:]

Journals updated:
  - 0-personal/journal/YYYY-MM-DD.md ✓
  - 1-teamspace/journal/YYYY-MM-DD.md ✓ (if work done there)
  - 2-projectspace/journal/YYYY-MM-DD.md ✓ (if work done there)

Learnings captured:
  - personal: X patterns
  - teamspace: X patterns (if relevant)
  - projectspace: X patterns (if relevant)
```

**How it works:**

1. Each coordinator discovers spaces via `ls -d [0-9]-*/`
2. Coordinator determines which spaces had relevant work
3. Spawns subagent for each relevant space (in parallel)
4. Subagents write to space-specific files
5. Coordinator aggregates and returns summary

**What gets captured per space:**
- Patterns → `[space]/.datacore/learning/patterns.md`
- Corrections → `[space]/.datacore/learning/corrections.md`
- Insights → `[space]/3-knowledge/insights.md`
- Journal entry → `[space]/journal/YYYY-MM-DD.md`

**Any additional insights to capture?** (brief, or Enter to skip)
> [user input - passed to coordinators]

> **Parallel execution:** While coordinators run in background, immediately proceed to steps 7-9 (GTD task extraction, insight verification, session meta-analysis). These steps work from conversation context and do NOT depend on coordinator output. Step 6 (learning review) is the only step that must wait for step 5 to complete.

### 6. Learning Review (DIP-0019 Engram Model)

**After step 5 coordinators complete, run learning review:**

> ⚠ **Spawning `learning-reviewer` is mandatory — it is not optional and must not be deferred.** The agent always runs. What is optional is the *interactive review* of candidates afterwards (the user can skip or defer that part). Never skip spawning the agent on the grounds of "deferring" — candidates will not exist to defer unless the agent runs.

1. **Generate engram candidates**: Spawn `learning-reviewer` agent for each space that had patterns captured. This reads new patterns.md entries, generates candidate engrams, detects contradictions, and applies decay.

2. **Present review to user** (interactive, skippable):

```
LEARNING REVIEW
───────────────
[If candidates exist:]
  Patterns evaluated: N
  Passed quality gates → candidates: N
  Failed → reference.md: N
  Failed → reinforced existing: N

  Candidates:
  1. [type] "Statement summary..."
     Value: {value_proposition from _review_metadata}
     Confidence: {quality_confidence}/10

  2. [type] "Statement summary..."
     Value: {value_proposition}
     Confidence: {quality_confidence}/10

  [If legacy audit flagged engrams:]
  Legacy audit (N re-evaluated):
  ⚠ ENG-XXXX-XXXX-XXX: "Statement..." — fails {gate}, consider retiring

  Review now? [Y/skip/defer]

  [If Y: invoke /daily-review skill for interactive review]
  [If skip: candidates persist for next session]
  [If defer: reviewed at next /today]

[If no candidates:]
  Patterns evaluated: N — none passed quality gates.
  (N routed to reference.md, M reinforced existing engrams)
```

**Configuration** (in `.datacore/settings.local.yaml`):
```yaml
learning:
  auto_defer_learning_review: false  # true = always defer to /today
  daily_review_max_items: 5
```

If `learning.auto_defer_learning_review: true`, skip the interactive prompt entirely. Candidates will surface in next `/today`.

**Agents spawned:** `learning-reviewer` (per space with new patterns)
**Skills used:** `/daily-review` (if user chooses to review now)

### 7. GTD Task Extraction from Session Insights

**Extract actionable tasks from conversation context (runs parallel to step 5 coordinators):**

Review the session's insights, decisions, and next steps. Identify items that should become tasks in `next_actions.org` — things that aren't continuation of current work (those go in step 3) but are *new* actionable items that emerged from the session.

```
GTD TASK EXTRACTION
───────────────────
Reviewing session for actionable items beyond continuation tasks...

New tasks identified:
  1. [#A] Task from insight X → Growth section
  2. [#B] Task from decision Y → Product section
  3. [#B] Task from discovery Z → Engineering section

Add these to next_actions.org? [Y/n/edit]
```

**What qualifies:**
- Strategic decisions that need follow-up work (but aren't the current task)
- New opportunities or ideas that emerged during the session
- Dependencies or prerequisites discovered for other work
- Research topics that surfaced and need dedicated attention

**What does NOT qualify (already captured elsewhere):**
- Current work that's incomplete → step 3 (continuation tasks)
- Completed items → step 4 (mark DONE)
- Patterns and insights → step 5 (session-learning)
- Engram candidates → step 6 (learning-reviewer)

**Task format** (Rich Task Standard — DIP-0009 Part 3.5):
```org
*** TODO [#B] Task description                        :tag1:tag2:
:PROPERTIES:
:CREATED: [YYYY-MM-DD Day]
:ASSIGNEE: {{USER}}
:CONTEXT: Why this task exists, what session insight prompted it.
:KEY_FILES: path/to/relevant/file.md | path/to/another.md
:END:
Brief description of what needs to be done.
```

**Routing:** Place tasks in the appropriate section of `next_actions.org` based on their nature (Operations, Product, Engineering, Growth, Research, Communications).

### 8. Insight Verification Checklist

**Verify all session insights are captured (runs parallel to step 5 coordinators):**

Compile a checklist of the session's key insights, decisions, and patterns. For each item, verify it was captured in at least one of three layers:

```
INSIGHT VERIFICATION
────────────────────
Checking all session insights are captured...

| # | Insight                          | Learning | Document | GTD | Journal |
|---|----------------------------------|----------|----------|-----|---------|
| 1 | Community Tap-In GTM pattern     | ENG-051  | plan.md  | --  | noted   |
| 2 | Anti-SaaS revenue model          | ENG-054  | plan.md  | --  | noted   |
| 3 | User base growth is #1           | --       | plan.md  | #A  | noted   |
| 4 | Obsidian is next community       | pattern  | --       | #B  | --      |
| ...                                                                        |

Coverage: X/X insights captured across 4 layers

[If gaps found:]
⚠ Uncaptured: "Insight description"
  → Capture as: [engram/pattern/task/zettel]?

[If all captured:]
All session insights accounted for.
```

**Four capture layers:**

| Layer | What it captures | Where |
|-------|------------------|-------|
| Learning | Patterns, engrams, corrections | `.datacore/learning/`, `3-knowledge/` |
| Documents | Plans, designs, reports, zettels | `content/`, `1-tracks/`, `3-knowledge/zettel/` |
| GTD | Actionable next steps | `org/next_actions.org` |
| Journal | Session narrative, decisions, context | `journal/YYYY-MM-DD.md` |

**Every significant insight should appear in at least one layer.** Many will appear in two or three (e.g., an engram AND a task AND a journal entry). The verification ensures nothing falls through the cracks.

### 9. Session Meta-Analysis

**Analyze the session itself, not just its content (runs parallel to step 5 coordinators).** This builds a longitudinal dataset for understanding how sessions work and improving over time.

```
SESSION META-ANALYSIS
─────────────────────

Session Arc: [category] → [category] → [category]
  (e.g., Research → Strategy → System Improvement)

Corrections: X total
  | # | Error                    | Category        |
  |---|--------------------------|-----------------|
  | 1 | [what was wrong]         | [routing/judgment/factual/context/prioritization] |
  | ...                                            |

Correction Categories:
  - Routing: X     (wrong space, wrong file location)
  - Judgment: X    (naive assumption, over-engineering)
  - Factual: X     (wrong name, wrong model, wrong number)
  - Context: X     (missing background, wrong assumption about user)
  - Prioritization: X (wrong emphasis, wrong ordering)

Insight Density:
  - Engrams: X
  - Patterns: X
  - Zettels: X
  - GTD tasks: X
  - Documents: X
  - Total artifacts: X

User Role: [editor/collaborator/director/author]
  (How did the user participate? Strategic corrections,
  detailed authorship, high-level direction, hands-on?)

Session Energy Pattern:
  (Creative connections, precision, fatigue indicators,
  time of day effects on output quality)

Key Observation:
  [One sentence — the most interesting meta-insight
  about how the session itself worked]
```

**Write the meta-analysis to the personal journal** under a `### Session Meta-Analysis` heading within the session's journal entry.

**Why this matters:**
- Correction patterns reveal systematic biases (e.g., routing errors)
- Insight density tracks session productivity over time
- User role patterns show how collaboration evolves
- Energy patterns correlate with time-of-day and session duration
- Over time, this data reveals what makes sessions effective

**Correction categories** (use consistently for tracking):

| Category | Meaning | Example |
|----------|---------|---------|
| Routing | Wrong location for content | File in wrong space |
| Judgment | Naive or over-engineered proposal | Twitter ads on zero budget |
| Factual | Incorrect information | Wrong model name |
| Context | Missing background about user/project | Assumed funding relationship |
| Prioritization | Wrong emphasis or ordering | Revenue focus before user base |

**DIP Gap Detection:** During meta-analysis, scan the session for architectural decisions, new patterns, or system changes that may warrant a DIP. Indicators:
- New agent or command was created
- Existing workflow was significantly modified
- A cross-cutting pattern emerged that affects multiple modules
- An architectural decision was made that constrains future choices

If found, add a TODO to inbox.org tagged `:datacore:dip:` with the gap description:
```org
** TODO [#B] Consider DIP for [gap description]              :datacore:dip:
:PROPERTIES:
:CREATED: [YYYY-MM-DD Day]
:CONTEXT: [What session insight prompted this]
:END:
```

### 10. Knowledge Artifact Tracking (CRITICAL)

**Purpose:** Ensure all knowledge artifacts created during the session are discoverable.

```
KNOWLEDGE ARTIFACTS
───────────────────
Scanning for artifacts created this session...

[Scan these locations for new/modified files:]
  - .datacore/specs/          (style guides, specifications)
  - .datacore/learning/       (patterns, corrections)
  - [space]/3-knowledge/      (zettels, insights, reference)
  - [space]/notes/            (topic notes, literature)
  - [space]/content/reports/  (analysis reports)

Artifacts found:
  ┌─────────────────────────────────────────────────────────────┐
  │ TYPE          │ PATH                        │ DESCRIPTION   │
  ├───────────────┼─────────────────────────────┼───────────────┤
  │ Style Guide   │ .datacore/specs/X.md        │ X writing...  │
  │ Zettel        │ 3-knowledge/zettel/Y.md     │ Concept Y     │
  │ Report        │ content/reports/Z.md        │ Analysis of Z │
  └─────────────────────────────────────────────────────────────┘

[If artifacts found, prompt:]
Are these descriptions accurate? (Enter to confirm, or correct)
>
```

**What gets tracked:**

| Artifact Type | Location | Discoverability |
|---------------|----------|-----------------|
| Style guides | `.datacore/specs/` | grep "style-guide" in type |
| Specifications | `.datacore/specs/` | grep by topic |
| Patterns | `.datacore/learning/` | patterns.md index |
| Zettels | `3-knowledge/zettel/` | datacortex search |
| Reports | `content/reports/` | date-prefixed, indexed |
| Topic notes | `notes/` | wiki-links |

**Artifact tracking actions:**

1. **List in journal** - Add "Artifacts Created" section with full paths
2. **Tag appropriately** - Ensure frontmatter has searchable tags
3. **Cross-reference** - Link from related files
4. **Index** - Add to datacortex if not auto-indexed

**Journal artifact section format:**

```markdown
## Artifacts Created

| File | Type | Purpose |
|------|------|---------|
| `0-personal/1-active/personal-dev/x-style-guide.md` | style-guide | X/Twitter voice for content generation |
| `3-knowledge/zettel/new-concept.md` | zettel | Atomic concept about X |
```

**Why this matters:**
- Prevents "I know I created this but can't find it" problem
- Enables future sessions to discover past work
- Makes knowledge artifacts part of the searchable corpus
- Creates audit trail of what was produced

### 11. Index Session to Database (DIP-0004)

```
INDEXING SESSION
────────────────
Updating knowledge database with session data...

Session indexed:
  - Goal: [session goal]
  - Accomplishments: X
  - Files modified: X
  - Decisions: X

[If index fails, warn and continue - data still in journal files]
```

**Run:**
```bash
python ~/.datacore/lib/journal_parser.py --sync --space personal
```

### 12. Push Changes to Repos

**Push ALL repos including subprojects within spaces.**

```
SAVING WORK
───────────
Checking for uncommitted changes...

1. Spaces & Root (via ./sync push):
   datacore (root).......... [3 files changed] → Pushed
   0-personal............... [2 files changed] → Pushed (to nightshift server)
   teamspace................ [No changes]
   projectspace............. [1 file changed] → Pushed

2. Subproject repos (manual check):
   [Check git status in common subproject locations]
   1-teamspace/2-projects/project-alpha... [1 commit ahead] → Pushing...
   [Any other repos with unpushed commits]

All work saved.
```

**Steps:**
1. Run `./sync push` for spaces and root
2. Check subproject repos for unpushed commits:
   - `git -C 1-teamspace/2-projects/project-alpha status`
   - Any other active project repos
3. Push any repos that are ahead of origin

**Commit message format:**
```
Session: [brief goal/topic]

- [Key change 1]
- [Key change 2]

🤖 Generated with Claude Code

Co-Authored-By: Claude <noreply@anthropic.com>
```

**If push fails:**
```
⚠ Push failed for [repo]. Will retry in /tomorrow.
  Error: [error message]
  Your changes are committed locally.
```

### 13. Context Sync (Automatic, Silent)

```
[Check if agents/commands changed during session]
[If changed: backup + update CLAUDE.md tables]
[Log to journal if updates made]
```

### 14. Quick AI Delegation Check (Optional)

```
AI DELEGATION
─────────────
Any quick tasks to delegate to AI? (brief, or Enter to skip)
> [user input]

[If input:]
Added to next_actions.org with :AI: tag.
Will be reviewed in /tomorrow for overnight execution.

[If skipped:]
No new AI tasks.
```

### 15. Completion Checklist (REQUIRED)

**Before closing, verify all steps are done:**

```
WRAP-UP CHECKLIST
─────────────────
[ ] 1. Session summary displayed
[ ] 2. Quick emotional check (optional - skipped or completed)
[ ] 3. Continuation tasks created (if work incomplete)
[ ] 4. Completed tasks marked DONE in next_actions.org
[ ] 5. Session learnings captured (coordinators spawned)
[ ] 6. Learning review completed (engram candidates reviewed/deferred)
[ ] 7. GTD tasks extracted from session insights
[ ] 8. Insight verification checklist — all insights in ≥1 layer
[ ] 9. Session meta-analysis written to personal journal
[ ] 10. Knowledge artifacts tracked:
       [ ] New files listed with paths and purposes
       [ ] Artifacts have proper frontmatter/tags
       [ ] Artifacts listed in journal entry
[ ] 11. Journals updated (only if relevant work was done):
       [ ] Personal (0-personal/journal/) - always
       [ ] Team (1-teamspace/journal/) - if team work
       [ ] Project (2-projectspace/journal/) - if project work
       [ ] Journal includes "Artifacts Created" section
[ ] 12. All repos pushed:
       [ ] Root & spaces (./sync push)
       [ ] Subproject repos (project-alpha, etc.)
[ ] 13. Context sync completed
[ ] 14. AI delegation captured (if any)

Note: Space journals only need updating if session involved that space.
      It's OK to skip a space journal if no relevant work was done.
```

**Verification commands:**
```bash
# Check all repos are pushed
git -C ~/Data status --short
git -C ~/Data/1-teamspace/2-projects/project-alpha log --oneline origin/main..HEAD

# Check journals exist (personal is required, spaces are optional)
ls -la ~/Data/0-personal/journal/$(date +%Y-%m-%d).md
ls -la ~/Data/1-teamspace/journal/$(date +%Y-%m-%d).md 2>/dev/null
ls -la ~/Data/2-projectspace/journal/$(date +%Y-%m-%d).md 2>/dev/null
```

### 16. Close — Consolidated Session Report

**CRITICAL: Re-present ALL section outputs together.** During wrap-up, individual step outputs scroll past and get lost. The close step must collect and re-display every section's output in one consolidated block. This is the user's single point of review.

**How to build the consolidated report:**
- As you work through steps 1-15, store each section's output (the formatted blocks you display to the user) internally
- At step 16, replay ALL of them in sequence inside a single consolidated report
- The user should be able to read this one block and verify the entire wrap-up without scrolling back

```
═══════════════════════════════════════════════════
SESSION COMPLETE — CONSOLIDATED REPORT
═══════════════════════════════════════════════════

Session: [HH:MM] — [HH:MM] ([duration])
Checklist: [X/16 items verified]

───────────────────────────────────────────────────
1. SESSION NARRATIVE
───────────────────────────────────────────────────

Goal: [One line — what the session set out to do]

Done:
  - [Key accomplishment 1]
  - [Key accomplishment 2]
  - [Key accomplishment 3]

Decisions:
  - [Key decision 1]
  - [Key decision 2]

[If applicable:]
Rejected: [Alternative explored but not taken, and why]

Next: [What follows — continuation task, or "complete"]

───────────────────────────────────────────────────
2. CONTINUATION TASKS
───────────────────────────────────────────────────

[Replay step 3 output — tasks created, or "None needed"]

───────────────────────────────────────────────────
3. TASKS COMPLETED
───────────────────────────────────────────────────

[Replay step 4 output — tasks marked DONE, or "None found"]

───────────────────────────────────────────────────
4. LEARNING & JOURNALS
───────────────────────────────────────────────────

[Replay coordinator results from step 5:]

Journals updated:
  - [space]/journal/YYYY-MM-DD.md ✓
  ...

Learnings captured:
  - [space]: X patterns, Y corrections
  ...

Engrams registered: [count] (ENG-IDs)

───────────────────────────────────────────────────
5. GTD TASKS EXTRACTED
───────────────────────────────────────────────────

[Replay step 7 output — new tasks added, or "None"]

───────────────────────────────────────────────────
6. INSIGHT VERIFICATION
───────────────────────────────────────────────────

[Replay step 8 output — the coverage table]

───────────────────────────────────────────────────
7. SESSION META-ANALYSIS
───────────────────────────────────────────────────

[Replay step 9 output — arc, corrections, insight density]

───────────────────────────────────────────────────
8. FILES CREATED/MODIFIED
───────────────────────────────────────────────────

[List ALL files from ALL working directories — ~/Data/,
/tmp/ repos, worktrees, etc. Group by location.]

  /tmp/project-repo/:
    Created:
      - lib/new-module.py (NEW)
    Modified:
      - tests/test_module.py

  ~/Data/:
    Modified:
      - 0-personal/journal/YYYY-MM-DD.md

───────────────────────────────────────────────────
9. KNOWLEDGE ARTIFACTS
───────────────────────────────────────────────────

[Replay step 10 output — artifact table]

───────────────────────────────────────────────────
STATS
───────────────────────────────────────────────────

- Tasks completed: X
- Continuation tasks: X (with bootstrap context)
- Knowledge artifacts: X (with paths in journal)
- Learnings captured: X patterns, Y corrections
- Engrams: X registered
- Journals updated: personal [+ teamspace] [+ projectspace]
- All repos pushed: Yes/No

[If continuation task created:]
Next session can run: /continue
Or search for :continuation: tagged tasks.

Ready to close terminal.
═══════════════════════════════════════════════════
```

**Why this matters:** In long sessions, individual step outputs scroll past hundreds of lines of tool calls, agent output, and status updates. By the time the user reaches step 16, they've lost track of what steps 1-8 produced. The consolidated report gives them everything in one place — a single scannable receipt of the entire session.

**Session timing:**
- Infer start time from the first user message timestamp in the conversation
- End time is now (when /wrap-up runs)
- Display both times and duration (e.g., "Session: 14:20 — 16:45 (2h 25m)")

**Session Narrative guidelines:**
- Bullet points, not prose — scannable at a glance
- Structure: Goal (1 line) → Done (bullets) → Decisions (bullets) → Rejected (if any) → Next
- Each bullet is a short phrase, not a full sentence
- The user should scan it and say "yes, that's right" in 5 seconds
- Include rejected alternatives only if they were significant
- "Next" is either a continuation task reference or "complete"

**Files Created/Modified guidelines:**
- List ALL files created or meaningfully modified by the session across ALL working directories (not just `~/Data/` — include `/tmp/` repos, worktrees, any external locations from step 0a)
- Mark new files with (NEW)
- Include org-mode files, spreadsheets, code, documents — anything the user's work produced
- Exclude temporary files, lock files (~$...), and auto-generated artifacts
- Group by working directory when files span multiple locations
- This is the user's "what did I produce today" receipt

## Key Concepts

### Bootstrap Prompts

When work is incomplete, the continuation task includes a **bootstrap prompt** - a self-contained context block that enables the next session to understand:
- What was the goal
- What progress was made
- What specifically needs to happen next
- What files/context are relevant

This eliminates the "where was I?" problem when resuming work.

### Session vs Day

| Command | Scope | Purpose |
|---------|-------|---------|
| `/wrap-up` | Session | Close current conversation, capture continuations |
| `/tomorrow` | Day | End of day, AI delegation, priorities for tomorrow |

You can run `/wrap-up` multiple times per day (after each session).
Run `/tomorrow` once at end of day.

### Light vs Full AI Delegation

- `/wrap-up`: Quick capture of obvious AI tasks
- `/tomorrow`: Full review, priority setting, overnight delegation

## Files Referenced

**Read:**
- Conversation context (including full transcript if compacted)
- `org/next_actions.org` (for completed tasks)
- Today's journal
- External working directories (`/tmp/`, worktrees, repos specified in arguments)
- Git status/log in all session-active repos

**Update:**
- `org/next_actions.org` (mark DONE, add continuations)
- `0-personal/journal/YYYY-MM-DD.md`
- Space journals if applicable
- `.datacore/learning/patterns.md`
- `CLAUDE.md` (if context sync needed)

**Create:**
- Continuation tasks with bootstrap prompts
- Backup in `.datacore/state/` (if context changed)

## Automation Level

| # | Step | Automation |
|---|------|------------|
| 1 | Session summary | Automatic (inferred from context) |
| 2 | Emotional check | Optional (user-initiated, skippable) |
| 3 | Continuation tasks | Semi-auto (user confirms/adds context) |
| 4 | Task completion | Semi-auto (user confirms) |
| 5 | Learning & journal coordinators | Mostly auto (background, optional user input) |
| 6 | Learning review | Semi-auto (candidates reviewed/deferred) |
| 7 | GTD task extraction | Semi-auto (AI proposes, user confirms) |
| 8 | Insight verification | Automatic (checklist generated, gaps flagged) |
| 9 | Session meta-analysis | Automatic (written to personal journal) |
| 10 | Artifact tracking | Semi-auto (scan + user confirms descriptions) |
| 11 | Index session | Automatic (journal parser) |
| 12 | Push to repos | Automatic (spaces via sync + subproject repos) |
| 13 | Context sync | Automatic (silent) |
| 14 | AI delegation | Optional (user-initiated) |
| 15 | Completion checklist | Required (verify all steps done) |
| 16 | Close (session narrative) | Automatic (inferred from conversation) |

## Related

- `/tomorrow` - End of day, full AI delegation
- `/today` - Start of day briefing
- `/coach` - REBT coaching (quick check included here)
- `/gtd-daily-start` - Morning planning
- `journal-coordinator` agent - Orchestrates per-space journal entries
- `journal-entry-writer` agent - Writes single space journal entry
- `session-learning-coordinator` agent - Orchestrates per-space learning extraction
- `session-learning` agent - Extracts learnings for single space
- `coach` agent - REBT coaching
- `context-maintainer` agent
