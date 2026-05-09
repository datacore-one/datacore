# Session Wrap-Up

## EXECUTION MODEL — INLINE WITH TRACKED CHECKLIST

**Execute /wrap-up inline** in the main conversation. The tracked checklist (Step 0b) prevents
step-skipping by making every step visible as a TaskCreate item that must be marked complete.

**Why not subagent:** Subagent dispatch (tried 2026-04-11) produces zero console output for
15-20 minutes — unacceptable UX. The user sees nothing while the agent runs 170+ tool calls
in the background. The tracked checklist is the actual compression guard, not process isolation.

**Anti-compression rule:** If you feel tempted to skip steps 6-9 ("no tasks to extract",
"nothing to verify"), STOP. The checklist forces you to mark each step in_progress and
completed. You cannot skip what is tracked. This is the fix for ENG-2026-0411-001.

---

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
| `learning-classifier` | Engram classification, dedup, promotion (DIP-0019) |

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

### 0a-bis. Detect Focus Mode

Check if running from a project folder inside a Datacore space:

```bash
python3 ~/Data/.datacore/lib/focus_mode.py detect
```

**If `mode: focus`:**
- Record space, project, and contributor from the output
- These values are passed to `journal-coordinator` in Step 4
- Journal entries will be written to the parent space's journal directory
- Continuation tasks will be written to the parent space's org files
- The session summary should note: `[Focus mode: {space_dir}/{project}]`

**If `mode: full` or `mode: none`:**
- Proceed as normal (current behavior)

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
10. "Kill orphaned dev servers" (activeForm: "Cleaning up dev servers")
11. "Push repos" (activeForm: "Pushing repos")
12. "Verify all checklist tasks completed" (activeForm: "Verifying checklist completion")
```

**The final task (#12) is a gate:** Before marking it complete, run `TaskList` and verify every prior task shows `completed`. If any task is still `pending` or `in_progress`, go back and finish it. Do NOT mark #12 complete until all others are done.

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

**If work is incomplete, delegate to `/continue --save` (inline mode).**

The continuation task format (Rich Task Standard + `:BOOTSTRAP:` field) is defined once in the `/continue` command spec. Do NOT reimplement it here — use `/continue`'s inline save logic directly.

```
CONTINUATION TASKS
──────────────────
This session's work appears incomplete. Let me capture what's needed to continue.

What remains to be done? (brief, or I'll infer from context)
> [user input or auto-inferred]

[Use /continue inline-save logic to create the continuation task.
 This creates a Rich Task Standard entry with :continuation: tag,
 BOOTSTRAP property, scheduled on next working day.]
```

**Why delegate:** The continuation task format (Rich Task Standard — DIP-0009 Part 3.5) with the `:BOOTSTRAP:` extension field is maintained in `/continue`. Duplicating it here creates drift — one spec gets updated, the other doesn't. `/continue` is the single source of truth for continuation task creation.

**What /wrap-up still owns:** Detecting that work is incomplete, asking the user what remains, and passing that context to the continuation task creation logic. The task format and scheduling logic belong to `/continue`.

### 4. Mark Completed Tasks (and Retroactive Task Creation)

**Tools to use:**
- Use `gtd.write_clock_entry` for tasks worked during the session (infer start/end times from conversation message timestamps -- first mention to last mention of each task)
- Use `gtd.duplicate_check` before creating any new tasks (continuation or GTD tasks) to avoid near-duplicates

```
TASK COMPLETION
───────────────
Checking for completed tasks from this session...

[Scan next_actions.org for tasks related to session work]
[Log CLOCK entries for tasks worked on using write_clock_entry]

Found X tasks that appear complete:
- [ ] Task 1 -> Mark DONE? [Y/n]
- [ ] Task 2 -> Mark DONE? [Y/n]

[Update org-mode states]
```

**Ad-hoc Task Gap Detection:**

Many sessions start with ad-hoc work (user dives into a task without a pre-existing
org entry). If Step 4 finds **no matching task** for the session's primary work:

1. **Create a retroactive task** in `next_actions.org` under the appropriate focus area:
   - Heading: session goal (from Step 1 summary)
   - State: DONE
   - Tags: inferred from session context
   - Properties: CREATED (session start time), EFFORT (estimated from session duration)
   - CLOSED: session end time

2. **Add a CLOCK entry** with actual session duration:
   ```
   :LOGBOOK:
   CLOCK: [start-timestamp]--[end-timestamp] => H:MM
   :END:
   ```
   Use `datacore.date` to get correct day names for timestamps. Never type from memory.

3. **Log it transparently:**
   ```
   No existing task found for this session's work.
   Created retroactive task: "Redesign /today daily briefing spec"
     State: DONE | Duration: 2:30 | Focus area: /Datacore
   ```

**Why this matters:** Without retroactive task creation, ad-hoc sessions are invisible
to productivity tracking. The daily score in `/tomorrow` needs completed task data.
Journal entries capture WHAT was done, but org tasks capture HOW MUCH and WHERE,
enabling trend analysis over time.

**Implementation:**
```python
from org_workspace import OrgWorkspace, Query
from org_workspace.log import add_clock_entry

ws = OrgWorkspace()
ws.load(next_actions_path)

# Create the retroactive task
node = ws.create_node(
    file=next_actions_path,
    heading=session_goal,
    state="DONE",
    tags=inferred_tags,
    EFFORT=estimated_effort,
)
ws.set_closed(node, session_end_time)
add_clock_entry(node.node, session_start_time, session_end_time)
ws.save()
```

### 5. Session Learning & Journal Update (Coordinator Pattern)

**Spawn two coordinators in parallel:**

1. **`journal-coordinator`** - Discovers spaces, spawns journal-entry-writer per space
2. **`session-learning-coordinator`** - Discovers spaces, spawns session-learning per space

> ⚠ **Always delegate to the coordinator agents. Never call `session-learning` or `journal-entry-writer` directly with a hardcoded space name.** Coordinators discover all relevant spaces automatically via `ls -d [0-9]-*/`. Bypassing them causes spaces with actual work (e.g., root system files in the Datacore space) to be silently skipped.

**Focus mode context:** If focus mode was detected in Step 0a-bis, pass the following additional context to `journal-coordinator`:

Focus mode active:
  space: [space_dir from detection]
  project: [project from detection]
  contributor: [contributor from detection]
  journal_path: [journal_path from detection]

This session was run from a project folder. Write the team journal entry
to the parent space's journal using the contributor and project info above.

The coordinator uses this to skip space discovery (the space is already known) and passes the project/contributor directly to journal-entry-writer.

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

> ⚠ **Spawning `learning-classifier` is mandatory — it is not optional and must not be deferred.** The agent always runs. What is optional is the *interactive review* of contradictions afterwards (the user can skip or defer that part). Never skip spawning the agent on the grounds of "deferring" — engrams will not be classified unless the agent runs.
>
> ⚠ **Sequential dependency:** `learning-classifier` MUST wait for step 5 (session-learning) to complete before starting. Session-learning writes to patterns.md/corrections.md AND calls plur_learn directly. The classifier then reads those files and deduplicates against PLUR.

1. **Classify new learnings**: Spawn `learning-classifier` agent. This reads new patterns.md/corrections.md entries since last cursor position, deduplicates via `plur_similarity_search`, creates engrams with proper type/polarity/tags, and detects recurrences and contradictions.

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

**Agents spawned:** `learning-classifier` (processes all spaces with new entries)
**Skills used:** `/daily-review` (if user chooses to review contradictions now)

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

### 9b. Postable Moment Detection (Build in Public)

**Purpose:** Flag session moments worth sharing on X (@jssr). Show don't tell - short demos, absorptions, before/afters, surprising results.

Scan the session for:
- **Framework absorptions** - read something, integrated it, shipped it (with timing)
- **Before/after moments** - friction that disappeared, workflow that clicked
- **Surprising metrics** - unexpected numbers, performance gains
- **Cool demos** - something that just works and looks impressive
- **Contrarian insights** - something you believe that others don't

```
POSTABLE MOMENTS
────────────────
[If any found:]
  Postable moment detected: [short description]
  Format: [screenshot / screen recording / text-only]
  Draft tweet? [suggest one, short and punchy, no hype words]

[If none:]
  No standout moments this session. That's fine.
```

**Guidelines:**
- Max 1-2 suggestions per session (don't spam)
- Tweet should stand alone without context
- Prefer visual proof (screenshot/recording) over text claims
- No em dashes, use hyphens
- Match voice profile: punchy, confident, no hype words
- If riding a wave (trending topic matches session work), note it

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

**Artifact Index (REQUIRED):**

In addition to listing artifacts in the journal, append each artifact to the monthly artifact index at `0-personal/notes/artifact-index-YYYY-MM.md`. This file is the cross-session lookup table for "when did I work on X and where is it?"

```markdown
# Artifact Index — YYYY-MM

| Date | Session | Type | Artifact | Path |
|------|---------|------|----------|------|
| 03-18 | Voice Terminal | module | Working voice prototype | .datacore/modules/voice-terminal/lib/voice_terminal.py |
| 03-18 | Voice Terminal | project-doc | Comprehensive product spec | 0-personal/notes/pages/datacore-voice-terminal.md |
| 03-18 | Voice Terminal | 3d-model | Blender model with components | 0-personal/notes/pages/datacore-voice-terminal.blend |
| 03-18 | Voice Terminal | render | 40+ product concept renders | 0-personal/notes/pages/datacore-voice-terminal-render-v*.png |
| 03-17 | FDS X Campaign | strategy | Campaign strategy v6 | 3-fds/1-tracks/comms/campaigns/.../campaign-strategy-v6.md |
```

**Artifact index rules:**
- One file per month: `artifact-index-YYYY-MM.md`
- Location: `0-personal/notes/`
- Append-only (never rewrite existing rows)
- Type column uses: `module`, `project-doc`, `report`, `zettel`, `render`, `3d-model`, `script`, `strategy`, `spec`, `style-guide`, `config`, `presentation`
- Path column is relative to `~/Data/`
- Use glob patterns for multiple files (e.g., `*-v*.png`)
- Session column matches the `## Session:` header in the journal

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

### 12. Kill Orphaned Dev Servers (Automatic)

**Automatically find and kill dev servers spawned by Claude sessions.**

Dev servers (Vite, Bun, Next.js, etc.) are often started by Claude for preview/testing but not cleaned up. Left running, they accumulate across sessions and burn CPU via file watchers. This step runs automatically without prompting.

**How to identify session-owned processes:**

1. Get the current Claude process PID (the `claude` process for this session)
2. Find all dev server processes system-wide
3. Check ancestry — if a dev server's PPID chain traces back to ANY `claude` process (current or previous sessions where parent is now PID 1 / orphaned), it's a Claude-spawned server
4. Orphaned dev servers (PPID=1) that match dev server patterns are always safe to kill — they lost their parent session

```bash
# Find dev servers: vite, bun run, next dev, webpack, npm run dev
ps -eo pid,ppid,etime,command | grep -E "vite|bun run (dev|server)|next dev|npm run dev|npm exec vite|webpack-dev-server" | grep -v grep

# Orphaned ones (PPID=1) are from dead sessions — kill automatically
# Current-session ones — kill automatically (session is ending)
```

```
PROCESS CLEANUP
───────────────
Scanning for dev servers...

[If found:]
  Killed 3 orphaned dev servers:
    PID 26939 (13h) — vite --host (megaphone-saas)
    PID 32781 (2d)  — bun run server
    PID 33001 (2d)  — vite (megaphone-websites)

[If none found:]
  No dev servers running. ✓
```

**What to kill:**
- `vite` / `next dev` / `webpack-dev-server`
- `bun run dev` / `bun run server`
- `npm run dev` / `npm exec vite`
- Any `node` process running from a project's `node_modules/.bin/` (e.g., esbuild child processes)

**What to preserve:**
- MCP server processes (datacore-mcp, exa-mcp-server) — these belong to active Claude sessions
- Non-dev-server node processes (MCP tools, etc.)

### 12.5. Archive Old Nightshift Reports (Automatic)

**Automatically archive nightshift reports older than 30 days.**

Nightshift execution reports accumulate in inbox directories. This step moves reports older than the retention period to structured monthly archives, keeping inboxes clean while preserving historical data.

```
NIGHTSHIFT ARCHIVAL
───────────────────
Archiving old nightshift reports...

[If reports found:]
  Archived 15 reports from 0-personal/0-inbox → 4-archive/nightshift/2025-12/
  Archived 8 reports from 1-datafund/0-inbox → 4-archive/nightshift/2025-12/
  Total: 23 reports archived (>30 days old)

[If none found:]
  No old reports to archive. ✓
```

**Archive structure:**
```
[space]/4-archive/nightshift/
├── 2025-11/  # November reports
├── 2025-12/  # December reports
└── 2026-01/  # January reports
```

**Retention policy:**
- Execution logs: 30 days in inbox
- Summary reports: 90 days in inbox (optional)
- Archive location: `[space]/4-archive/nightshift/YYYY-MM/`

**Configuration** (in `.datacore/settings.local.yaml`):
```yaml
nightshift:
  archival:
    enabled: true
    retention_days: 30
    summary_retention_days: 90
```

**Manual archival:**
```bash
# Archive all spaces
python .datacore/lib/nightshift_archival.py --all-spaces

# Archive specific space
python .datacore/lib/nightshift_archival.py --space 0-personal

# Preview without moving files
python .datacore/lib/nightshift_archival.py --dry-run
```

### 13. Push Changes to Repos

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

### 14. Context Sync (Automatic, Silent)

```
[Check if agents/commands changed during session]
[If changed: backup + update CLAUDE.md tables]
[Log to journal if updates made]
```

### 15. Quick AI Delegation Check (Optional)

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

### 16. Completion Checklist (REQUIRED)

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
[ ] 12. Orphaned dev servers killed
[ ] 12.5. Old nightshift reports archived (>30 days)
[ ] 13. All repos pushed:
       [ ] Root & spaces (./sync push)
       [ ] Subproject repos (project-alpha, etc.)
[ ] 14. Context sync completed
[ ] 15. AI delegation captured (if any)

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

### 17. Close — Consolidated Session Report (MANDATORY, NEVER SKIP)

**This step is the ENTIRE POINT of /wrap-up for the user.** Everything before this is processing. This is the output. If you skip this step, the user gets nothing usable — they have to scroll through hundreds of lines of tool calls and agent output to piece together what happened. That is unacceptable.

**HARD RULE: Step 17 must ALWAYS execute, regardless of session length, complexity, or context pressure.** If you are running low on context, compress other steps — never this one. If earlier steps were skipped or failed, still output this report with whatever information you have.

**How to build the consolidated report:**

1. **As you work through steps 1-16**, after each step completes, write a brief summary line to a running internal list (e.g., "Continuation: 1 task created for Verity cap table", "Tasks completed: 2 marked DONE", "Dev servers: killed 3"). This is lightweight — just notes, not full output.

2. **At step 17**, use those notes plus conversation context to compose the full consolidated report. Do NOT rely on being able to scroll back to earlier outputs — context compaction may have removed them.

3. **Output the report as a single unbroken text block** — no tool calls in between, no "let me check one more thing". The user reads this block and is done.

```
═══════════════════════════════════════════════════
SESSION COMPLETE — CONSOLIDATED REPORT
═══════════════════════════════════════════════════

Session: [HH:MM] — [HH:MM] ([duration])
Checklist: [X/17 items verified]

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

───────────────────────────────────────────────────
10. SOCIAL POSTS (draft for immediate posting)
───────────────────────────────────────────────────

Generate 3 social media post drafts from this session using the content engine:

```bash
python3 .datacore/modules/comms/lib/content_engine.py session-posts "SESSION_SUMMARY_HERE"
```

Or if the script is unavailable, generate manually:

**Personal X (@greaborisa)** — building in public, casual, what you worked on.
Max 280 chars. Specific, authentic.

**Project X (@FairDataSociety or @plur_ai)** — what shipped or what's interesting
to that community. Max 280 chars.

**LinkedIn** — 150-300 words. Professional but authentic. Strong hook.
Short paragraphs. End with a question. 2-3 hashtags.

Present all 3 drafts in the report. The user can:
- Post immediately (copy-paste)
- Schedule for later
- Skip

───────────────────────────────────────────────────
TOKEN COST
───────────────────────────────────────────────────

| Component              | Tokens  |
|------------------------|---------|
| journal-coordinator    | [N]     |
| learning-coordinator   | [N]     |
| [other subagents]      | [N]     |
| **Subagent total**     | **[N]** |
| Main conversation      | [N]     |
| **Session total**      | **[N]** |

Ready to close terminal.
═══════════════════════════════════════════════════
```

**HOW to fill the Main conversation row — DO NOT estimate.**

Run this command to read the current session's transcript and produce
exact counts (no estimation, no vibes):

```bash
python3 ~/Data/.datacore/lib/session_token_count.py --json
```

The output gives you `input_tokens` (uncached), `cache_creation_input_tokens`
(cached, billed at ~125%), `cache_read_input_tokens` (cache hits, billed
at ~10%), `output_tokens`, and a `total_billable_estimate` that applies
the cache multipliers.

Use the `total_billable_estimate` field in the Main conversation row.
Also report the breakdown beneath the table so the user can see how much
of the cost was cache amortization:

```
Main conversation breakdown:
  Turns:       N
  Input fresh: N
  Cache write: N
  Cache read:  N
  Output:      N
```

**Historical context (why this is now mandatory):** before this
instrument existed, /wrap-up estimated main-conversation tokens by
guessing — and was once off by ~5,000× (estimated 150K, actual 737M
total processed across 1,316 turns). The transcript file has the
exact API-returned usage per turn; there is no excuse to guess.

If the script ever fails, fall back to a Fermi estimate WITH the
arithmetic shown:
```
Cannot read transcript (reason: ...). Fermi-estimate floor:
  N turns × ~50K avg = ~XM tokens.
This is a lower bound, not a measurement.
```
Never report a single point estimate without instrument or arithmetic.

**Why this matters:** In long sessions, individual step outputs scroll past hundreds of lines of tool calls, agent output, and status updates. By the time the user reaches step 17, they've lost track of what earlier steps produced. The consolidated report gives them everything in one place — a single scannable receipt of the entire session.

**PERSIST TO JOURNAL (REQUIRED):**

After displaying the consolidated report to the user, **write a condensed version directly to the personal journal**. This replaces the coordinator-written entry as the authoritative session record. The main conversation has the best context — coordinator agents running in background have less.

1. **Write session entry to journal** (`0-personal/notes/journals/YYYY-MM-DD.md`):
   - Use the format from journal-entry-writer (TL;DR, Goal, Accomplished, Key Decisions, Files, Continuation, Learnings, Tags)
   - Include the artifact table
   - Include a `### Token Cost` section with the same table from the consolidated report (subagent tokens, main conversation estimate, session total)
   - This is the **authoritative record** — better than what any subagent produces

2. **Update Daily TL;DR** at the top of the journal file (after frontmatter):
   ```markdown
   ## Daily Summary
   - [Session 1 name]: [one line from TL;DR]
   - [Session 2 name]: [one line from TL;DR]
   - [Session 3 name]: [one line from TL;DR]
   ```
   If a `## Daily Summary` section already exists, update it (add/replace the current session's line). If it doesn't exist, create it right after the frontmatter.

3. **Append to artifact index** (`0-personal/notes/artifact-index-YYYY-MM.md`):
   - Create file if it doesn't exist (with header row)
   - Append one row per significant artifact created this session

**Why persist from main conversation:** The journal-coordinator spawns subagents that have limited context (only what was passed in the prompt). The main conversation has the FULL context — every decision, every file, every nuance. Writing from step 17 produces a much higher quality journal entry than delegating to a subagent. The coordinator-written entry is a fallback, not the primary.

**Failure modes to avoid:**
- "I'll just summarize briefly" — No. Output the full template with all sections.
- "The user already saw this" — No. They saw it interleaved with tool calls 500 lines ago.
- "Context is getting long, I'll skip the report" — No. Compress earlier steps instead.
- Outputting the report in pieces with tool calls in between — No. Single unbroken block.
- "The coordinator already wrote the journal" — No. Your version is better. Write it anyway (append, don't overwrite).

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

**Token Cost guidelines:**
- Report token usage from all background agents spawned during wrap-up (journal-coordinator, session-learning-coordinator, and any others). Each agent's `<usage>` block in the task notification contains `total_tokens`.
- Estimate main conversation tokens for the wrap-up portion (~tool calls from session_end through the consolidated report). This is approximate — state it as "~NK".
- Sum subagent tokens (precise) + main conversation estimate for a session total.
- This gives the user visibility into the cost of the wrap-up process itself.

## Key Concepts

### Bootstrap Prompts

When work is incomplete, the continuation task includes a **bootstrap prompt** — a self-contained context block that enables the next session to understand what was done, what remains, and what files are relevant. This eliminates the "where was I?" problem when resuming work.

The continuation task format (Rich Task Standard + `:BOOTSTRAP:` field) is defined in `/continue`. See `/continue` for the canonical format and scheduling logic. `/wrap-up` Step 3 delegates to `/continue`'s inline-save logic rather than reimplementing it.

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
| 12 | Kill orphaned dev servers | Automatic (scan + kill, no prompt) |
| 13 | Push to repos | Automatic (spaces via sync + subproject repos) |
| 14 | Context sync | Automatic (silent) |
| 15 | AI delegation | Optional (user-initiated) |
| 16 | Completion checklist | Required (verify all steps done) |
| 17 | Close (session narrative + token cost) | Automatic (inferred from conversation + agent usage) |

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
