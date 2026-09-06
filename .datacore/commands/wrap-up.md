---
name: wrap-up
description: Session wrap-up before closing a Claude Code conversation
recall:
  # Failure-mode engrams that MUST be in context before /wrap-up runs.
  # Per DIP-0029, the harness injects matching engrams as "## Relevant memory"
  # before this command body. PreToolUse hook command_recall_inject.py also
  # injects via plur_recall_hybrid on Skill invocation for non-Datacore harnesses.
  ids:
    - ENG-2026-0411-001   # Subagent dispatch produces zero output for 15-20min — execute inline
    - ENG-2026-0505-029   # Token cost in /wrap-up was estimated 5000x too low
    - ENG-2026-0512-038   # Token cost MUST be read from the transcript; never Fermi-estimate
    - ENG-2026-08-16-011  # Measured /wrap-up cost: 405k output tok, 338 tool calls, 3.6 engrams
    - ENG-2026-08-16-012  # Learning-classifier cursor stalled; 37 candidates never reviewed
    - ENG-2026-08-16-013  # v2: an org-mirrored task carries an `org` payload and is undelegatable
  scopes:
    - command:wrap-up
  tags:
    - wrap-up
    - session-close
---

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
| `context-maintainer` | Context sync, only if `preflight` reports registry changes |
| `coach` | Quick emotional check (optional) |

**No longer invoked here:** `session-learning-coordinator`, `session-learning`, `learning-classifier`. Learning moved to the nightly batch sweep — see §5. Spawning any of them from this command re-creates the cost this revision removed.

### Scripts This Command Calls

| Script | Replaces |
|--------|----------|
| `wrap_up_mechanics.py preflight` | §10, §12, §12.5, §14 + the session archive |
| `wrap_up_mechanics.py meta` | §9 counters, `session_token_count.py` |
| `wrap_up_mechanics.py finalize` | §13 |
| `wrap_up_mechanics.py audit` | §16, and the machine-checkable half of §12 |
| `session_archive.py` | (called by preflight; also runs on the SessionEnd hook) |
| `focus_mode.py detect` | §0a-bis |

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
- **Personal journal (0-personal/journal/) is ALWAYS written — focus mode does NOT skip it**
- A team journal entry will ALSO be written to the focused space's journal directory IN ADDITION
- Continuation tasks will be written to the parent space's org files
- The session summary should note: `[Focus mode: {space_dir}/{project}]`

**If `mode: full` or `mode: none`:**
- Proceed as normal (current behavior)

### 0b. Create Tracked Checklist (MANDATORY)

**After context recovery**, create a tracked task list — **one TaskCreate per spec step. NO CLUSTERING.**

**HARD RULE — one-to-one mapping:** The spec has 12 tracked steps. Create EXACTLY 12 tasks, in order. Each spec step gets its own task. Clustering is where skipping hides: once "X + Y" is one task you can do X, mark it done, and silently drop Y. (§6 is deliberately one task — its two halves are a single decision about one item, not two steps. See §6.)

```
Tasks to create (one per spec step, mark in_progress when starting, completed when done):

 1. "Step 1  — Pulse + notes (ask, DO NOT WAIT)"
 2. "Step 2  — Preflight (wrap_up_mechanics.py preflight)"
 3. "Step 3  — Continuation tasks (inferred — no prompt)"
 4. "Step 4  — Mark completed tasks (auto-mark high-conf, surface med-conf)"
 5. "Step 5  — Journals (spawn journal-coordinator)"
 6. "Step 6  — Tasks & delegation (proposals, paired — no auto-add)"
 7. "Step 7  — Session meta-analysis (wrap_up_mechanics.py meta)"
 8. "Step 8  — Finalize (wrap_up_mechanics.py finalize + journal index)"
 9. "Step 9  — Audit (wrap_up_mechanics.py audit)"
10. "Step 10 — Consolidated report (opens with the session summary)"
11. "Step 11 — Postable moments"
12. "Step 12 — Self-audit section to journal (HOOK-ENFORCED)"
```

> **What changed, and where each old step went.**
>
> | Old step | Now |
> |---|---|
> | §1 session summary | Opens §10 as §10a. At the top of the run it scrolled away before anyone read it. |
> | §2 pulse | §1 — asked first and **non-blocking**. It used to strand the entire run when left unanswered. |
> | §6 learning review | The nightly sweep. Gone from this command. |
> | §8 insight verification | **Dropped**, survives as a one-line coverage note in §10c. Its `Learning` column is unknowable at wrap-up time now, and its other three re-verified §3, §5 and §6 one step after doing them. |
> | §10 artifacts, §12 orphans, §12.5 archival, §14 context | `preflight` JSON fields (§2a). |
> | §11 index-to-DB | §8 — and its command path was broken; see there. |
> | §13 push | §8. |
> | §6b AI delegation | §6b, paired with task extraction. |
> | §16 checklist | §9 `audit`. |
> | §1 pulse | §1, merged into the pulse. |
>
> Not a relaxation. Each removed step is now either a JSON field §9 asserts against the filesystem, or a merge that removes a seam. A model marking its own task done was never the stronger check.

**Step 12 is the gate.** Before marking it complete, run `TaskList` and verify every prior task is `completed`. Then write the `## Wrap-up Checklist Audit` section to today's personal journal listing each step's actual status using the §12 allowed statuses: `run ✓`, `skipped-by-user`, `skipped-by-mode-fast`, `not-answered`, `not-applicable (REASON)`, `inferred-and-reported (DESCRIPTION)`, or `applied-from-feedback (N CORRECTIONS)`. The PreToolUse hook `wrap_up_checklist_check.py` blocks `plur_session_end` until that section exists in the journal.

**Why this exists:** Spec step counts in past sessions: 17 spec steps, 9 tasks created, 6 silently skipped (observed 2026-05-29 SMK wrap-up; previously documented as ENG-2026-0512-044 on 2026-05-12 but recurred 17 days later). Memory engrams alone are insufficient — execution-time discipline failure. The hook is the structural defense; one-task-per-step is the readability defense.

**CRITICAL:** Step 5 must use `journal-coordinator` — NEVER spawn `journal-entry-writer` directly with a hardcoded space name. The coordinator discovers all relevant spaces automatically. Bypassing it silently skips spaces with actual work, including root system files.

### 0c. Inference-First Model (MANDATORY READ — supersedes the old "always prompt" rule)

The previous version of this spec required surfacing a prompt at every decision point (~8 separate interruptions). That was strictly worse than the current model. The new rule:

**Run every step. Infer every answer you can. Ask ONE non-blocking question at the start (§1). Let the user veto whenever they get to it.**

For each step that previously asked the user a question:

| Wording in spec | Means under inference-first |
|---|---|
| "ask user" / "prompt user" | **Infer.** Apply the inferred answer. List in §10 report. User vetoes via §0e if wrong. |
| "Optional (user-initiated)" | **Infer + apply.** The default is "do the cheap reversible thing." |
| "or Enter to skip" | **Apply silently.** Surface only as a line in the report. |
| "Automatic (silent)" | **Run silently — still RUN.** Unchanged. Not "skip without telling user." |

Only **three** unplanned prompts remain (§0e safety boundaries — force-push, external comms, credentials). Everything else is inferred.

If the agent reads "the talk shipped, user said it went well" and concludes "they don't need a coaching check / AI delegation / social posts" — that's still wrong. The new fix is not "ask harder" but "do the thing silently, then surface what you did so the user can veto." Skipping an entire step requires an explicit reason in the §12 audit table.

### 0d. Flags

`/wrap-up` accepts these forms:

```
/wrap-up                  # normal mode: one non-blocking pulse at §1, then silent to the §10 report
/wrap-up fast             # zero prompts: skip the §1 pulse entirely. Pure silent run.
/wrap-up --fast           # alias for `fast`
/wrap-up check also tmp/  # pass-through args for §0a context recovery
```

**Mode behaviors:**

| Mode | §1 pulse prompt | §0e feedback gate | Inference defaults |
|------|---|---|---|
| `normal` | 1 prompt (1-10, instant) | Single bulk prompt at end | Apply all (user vetoes in bulk) |
| `fast`   | none | none (Enter-to-accept implied) | Apply all |

In both modes, §0e safety prompts can still fire if the agent detects a destructive/external/credential action.

**Settings overrides** (in `.datacore/settings.local.yaml`):

```yaml
wrap_up:
  default_mode: normal              # or 'fast' — applies when no flag passed
  inference_mode: auto              # or 'off' (restores legacy per-step prompts)
  pulse: true                       # set false to skip the §1 question entirely
  tag_extracted_tasks_with: wrap_up_extracted   # so they're trivial to grep/review
```

When `inference_mode: off`, the spec falls back to the legacy per-step prompts (kept for users who want the old behavior).

### 0e. Safety Rule — the only unplanned prompt, applies throughout

These three categories of action are NOT auto-inferred. If the agent's inference logic during any prior step would trigger one of these, the agent MUST surface a prompt before proceeding. This is the only sanctioned mid-flow interruption in inference-first mode.

| Category | Examples | Action |
|---|---|---|
| **Destructive git** | force-push, `--force-with-lease` to protected branch, `git reset --hard` to discard committed work | AskUserQuestion: explicit Y/N before executing |
| **External communication** | `:AI:send:` task that fires real email/post tonight, posting to social media before review, sending Telegram/WhatsApp | AskUserQuestion: confirm send target + content |
| **Credential decisions** | rotating a token, changing OAuth scopes, prompting for new auth on a service mid-wrap-up | AskUserQuestion: explicit confirmation |

These are the *only* prompts allowed after §1 (pulse). If the agent finds itself about to add a fourth prompt category here, it's wrong — the right answer is "infer, apply, surface in §10, let user veto in §1 pulse" instead.

### 1. Pulse + Notes — FIRST, and it never blocks

**Ask once, in plain text, then keep going. Do NOT use AskUserQuestion here.**

```
Pulse 1-10? Anything to correct or add? (answer any time — I'm continuing)
```

Proceed immediately to §2. Do not wait. Do not re-ask. Never make a later step
conditional on having an answer.

**Why it moved to first, and why it stopped blocking.** It used to sit at §2 as
a blocking prompt: start /wrap-up, switch to another session, forget to answer,
and the whole run stalls indefinitely with work half-done. A prompt that can
strand the run is worse than no prompt. Asking first puts the question on screen
before the user walks away; not blocking makes their return optional.

**This absorbs the old §1 pulse.** That gate asked for bulk
corrections *after* a 270-line report — by which point the user had scrolled
past everything and pressed Enter. One interaction, asked when attention is
highest, beats two asked when it is lowest.

**Handling the answer, whenever it lands:**

| When it arrives | What to do |
|---|---|
| Before §10 | Apply corrections to whatever step they touch; carry the pulse score into the journal. |
| After §10 | Apply as a follow-up edit and state what changed. The report was not wrong, it was early. |
| Never | Finish the wrap-up. Record `Pulse: not answered` in §10 and `not-answered` in the §12 audit. A normal outcome, not a failure. |

**Correction vocabulary** (free text — parse intent, never demand syntax):
`drop task 2` · `task 3 to priority A` · `that one isn't done, undo it` ·
`also capture: <insight>` (call `plur_learn` directly) · `delegate 1` ·
`add task: <text>` · `skip the social posts`

`wrap_up.pulse: false` disables it; `/wrap-up fast` skips it. Neither changes
anything else — nothing downstream depends on it.

### 2. Preflight — one call, not two hundred

```bash
python3 ~/Data/.datacore/lib/wrap_up_mechanics.py preflight
```

Returns one JSON object. **Read it once and carry it through the whole run** — every field below feeds a later step, and re-deriving any of it with ad-hoc `ps`/`git`/`ls` calls is the thing this replaces.

| JSON field | Feeds | What it already did |
|---|---|---|
| `session_archive` | §5, §12 | Copied this session's transcript + subagents to `.datacore/state/sessions/archive/<date>/<id>/` and queued it for the nightly learning sweep |
| `processes.killed` | §10 report | Killed orphaned dev servers and hook workers (old §12) |
| `processes.flagged` | §10 report | ppid=1 node processes >1h — reported, never killed |
| `nightshift_archival` | §10 report | Ran `nightshift_archival.py --all-spaces` (old §12.5) |
| `context_sync` | §10 report | Checked whether agents/commands/registry changed (old §14) |
| `artifacts` | §8, §10 | Files created/modified this session, from git (old §10) |
| `repos` | §B, §10 | Per-repo dirty count and unpushed commits across all ~60 repos |

**Why this exists.** A measured wrap-up ran 338 tool calls, 218 of them ad-hoc Bash. None of that needed judgement. If `preflight` fails, report the failure in §10 and continue — do not fall back to hand-rolling the same scans, which is how the 218 accumulated in the first place.

**`--dry-run`** previews the process kills without executing them. Use it if the user asks what would be killed.

> **Always call the mechanics with an absolute path.** The Bash working directory
> persists across tool calls, so any earlier `cd <space> && git …` leaves the
> shell there and a later `python3 .datacore/lib/wrap_up_mechanics.py` resolves
> against `<space>/.datacore/lib/` and dies with `No such file or directory`.
> This happened twice inside a single run on 2026-08-17. Use
> `python3 ~/Data/.datacore/lib/…` in every step, and prefer `git -C <path>`
> over `cd <path> && git`.

#### 2a. What preflight already did (old §10, §12, §12.5, §14)

These three ran in Step A. **Do not re-run them by hand.** Report what preflight
returned, in §10:

| Preflight field | Report as |
|---|---|
| `processes.killed[]` | `Killed N orphaned process(es)` with pid, age and reason. Empty list -> `No stray dev servers or hook workers. OK` |
| `processes.flagged[]` | `Flagged (not killed): pid N, <cmd>` — ppid=1 node processes older than an hour that are not known MCP servers. Never kill these; they cannot be proven to be leaks from here. |
| `nightshift_archival.ok` | `Archived nightshift reports >30d` or the failure text. If `note` says the script is missing, say that explicitly rather than dropping the step. |
| `context_sync.registry_changed` | If true, run the rebuild in `context_sync.action` and say so. If false, `no agent/command registry changes — context already in sync` |

What preflight preserves, deliberately: MCP servers (`datacore-mcp`, `exa-mcp`,
`plur-mcp`) and any process with a live parent. The kill list is dev servers
(vite / next dev / bun run / webpack / `node_modules/.bin/`) plus orphaned
one-shot hook workers — 24 immortal `plur hook-inject` orphans once drove a
16GB machine to 17.4GB swap (2026-07-06, plur-ai/plur#504), which is why the
old dev-server-only pattern was not enough.

### 3. Continuation Tasks (inferred — no prompt)

**Infer-and-create.** Do NOT ask "What remains to be done?" — derive it from the session.

**Inference signals** (any one of these is sufficient to trigger continuation creation):

1. **Git state** — `git status --short` in any session-active repo shows uncommitted/unstaged hunks that aren't intentional WIP
2. **TODO comments** added during the session (grep diffs for `TODO|FIXME|XXX`)
3. **Conversation phrases** like "let me finish X tomorrow", "we'll do Y next", "this needs a follow-up", "leaving Z for another session"
4. **Files in mid-state** — e.g., a function with `pass`/`raise NotImplementedError`, a test file with `# placeholder`, a spec section with `[TBD]`
5. **Explicit user instruction** in the conversation: "remember to come back to this"
6. **/continue inline save was invoked** during the session

**If ANY signal exists:** call `/continue --save` inline-mode to create the continuation task. Compose the bootstrap context from the conversation + signals above. Tag `:continuation:`, schedule next working day.

**If NO signal exists:** no continuation task. §10 report shows `Continuation: none (work appears complete)`. §12 audit row reads `not-applicable (no incomplete-work signal)`.

**Surface in §10, not before:**
- The created continuation task heading + ID + scheduled date
- The signals that triggered it (so user can veto in §0e: "actually that's done, drop it")

**Delegation reference:** The continuation task format (Rich Task Standard — DIP-0009 Part 3.5) with the `:BOOTSTRAP:` extension field is maintained in `/continue`. Use `/continue --save` inline; do not reimplement.

**Legacy prompt** (only when `wrap_up.inference_mode: off`):
```
This session's work appears incomplete. What remains? (brief, or I'll infer)
> [user input or auto-inferred]
```

### 4. Mark Completed Tasks (and Retroactive Task Creation)

**Tools to use:**
- Use `gtd.write_clock_entry` for tasks worked during the session (infer start/end times from conversation message timestamps -- first mention to last mention of each task)
- Use `gtd.duplicate_check` before creating any new tasks (continuation or GTD tasks) to avoid near-duplicates

**Inference-first: auto-mark high-confidence, defer low-confidence to §0e bulk review.**

```
TASK COMPLETION (silent — surfaced in §10 report)
─────────────────────────────────────────────────
1. Scan next_actions.org for tasks related to session work
2. For each candidate compute a match score (heuristic):
     +3 if task heading matches session goal (semantic overlap)
     +2 if KEY_FILES property overlaps with files modified this session
     +1 if any CLOCK timestamp from this session falls inside session window
     +1 if conversation explicitly references the task ID or near-verbatim heading
3. Auto-mark DONE for tasks with score ≥ 4 (high confidence)
4. Add CLOCK entries via gtd.write_clock_entry using inferred start/end times
5. Tasks with score 2-3 → "Suggested DONE" list in §10 report — user vetoes/confirms in §0e
6. Tasks with score < 2 → ignored (not surfaced; too noisy)
```

**Report rendering** (§10):
```
Auto-marked DONE (high confidence):
  - org-XXX | "Task heading" — matched files: [...]
  - org-YYY | "Task heading" — matched goal verbatim

Suggested DONE (medium confidence — confirm in §0e):
  - org-ZZZ | "Task heading" — score 3 (file overlap only)
```

**§0e corrections** can include "actually task org-ZZZ isn't done, undo" or "yes confirm all suggested" — applied in one pass.

**Ad-hoc Task Gap Detection:**

Many sessions start with ad-hoc work (user dives into a task without a pre-existing
org entry). If Step 4 finds **no matching task** for the session's primary work:

1. **Create a retroactive task in `inbox.org`** — NOT `next_actions.org`. See
   "Everything this command writes goes to inbox.org" below.
   - Heading: session goal (from §10a summary), state `DONE`
   - Tags: inferred from session context
   - Properties: the full positioning block (below), plus `CREATED` (session
     start), `EFFORT` (from session duration), `CLOSED` (session end)

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
   Created retroactive task in inbox.org: "Redesign /today daily briefing spec"
     State: DONE | Duration: 2:30 | Routes to: next_actions.org / Datacore
   ```

**Why this matters:** Without retroactive task creation, ad-hoc sessions are invisible
to productivity tracking. The daily score in `/tomorrow` needs completed task data.
Journal entries capture WHAT was done, but org tasks capture HOW MUCH and WHERE,
enabling trend analysis over time. Landing them in inbox.org does not weaken that —
the CLOCK and CLOSED data is on the entry from the moment it is written, and
`/process-inbox` routes it without re-deriving anything.

**Implementation:** write to `inbox.org` via org-workspace, same as any capture.
Do NOT `ws.load(next_actions_path)` — that file is on its way to being generated.

---

### Everything this command writes goes to inbox.org

**Applies to §3 continuations, §4 retroactive tasks, §6 extracted tasks, §6b
delegations, and the §7 DIP-gap TODO. There are no exceptions.**

`inbox.org` is the single capture point (DIP-0009), and it is the **only org file
DIP-0043 exempts from projection in every phase**. `next_actions.org` becomes
generated and `chmod 444` the moment a space flips to Phase 1, and the projector's
guard will refuse writes to it. A command that writes there today is a command
that breaks on flip day, silently, per space, at different times.

Writing only to inbox.org means this command is already correct after the switch.
That is the point: **prepare for ledger-first now, while it costs one edit.**

**Positioning is mandatory.** Capture without routing intent just moves the work to
whoever processes the inbox. Every entry this command creates carries:

```org
** TODO [#B] Task description                              :tag1:tag2:
:PROPERTIES:
:CREATED:      [YYYY-MM-DD Day]
:ID:           org-YYYYMMDD-<slug>
:SOURCE:       wrap-up
:TARGET_FILE:  0-personal/org/next_actions.org
:TARGET_SPACE: 0-personal
:TARGET_SECTION: Engineering
:ASSIGNEE:     {{USER}}
:EFFORT:       1:00
:KEY_FILES:    path/to/file.md | path/to/another.py
:END:
:CONTEXT: Why this task exists, what session insight prompted it.
```

| Property | Why it is not optional |
|---|---|
| `TARGET_FILE` | Which file `/process-inbox` should route to. Under Phase 1 this becomes "which ledger stream", and the mapping already exists. |
| `TARGET_SPACE` | Which space. The agent knows it now; the inbox processor would have to guess. |
| `TARGET_SECTION` | Operations / Product / Engineering / Growth / Research / Communications. Preserves the routing decision §6 already made. |
| `SOURCE: wrap-up` | Makes the whole cohort greppable, the way `:wrap_up_extracted:` did — and survives re-tagging. |

> **Org tags cannot contain hyphens.** `:wrap-up:` does not parse as a tag — the
> whole `:a:b:` string stays inside the heading and org-workspace reports the
> file's inherited FILETAGS instead, so the entry looks tagged and is not. Use
> `wrap_up`, `ledger_first`, `session_close`. Verify after writing with
> `org_workspace_adapter.py`, never by eye: a broken tag string is invisible in
> the raw file and only shows up when a query fails to find the task months later.

**Retroactive DONE entries carry the same block**, plus `CLOSED` and the `LOGBOOK`
CLOCK. A DONE item in the inbox looks unusual for GTD; it is deliberate. The
alternative is writing to a file that is about to become read-only, and a DONE
entry costs the inbox processor one archive move.

### 5. Journals (Coordinator Pattern)

**Spawn ONE coordinator: `journal-coordinator`** — it discovers spaces and spawns `journal-entry-writer` per space.

> ⚠ **Always delegate to the coordinator. Never call `journal-entry-writer` directly with a hardcoded space name.** It discovers all relevant spaces automatically via `ls -d [0-9]-*/`. Bypassing it causes spaces with actual work (e.g., root system files) to be silently skipped.

#### Learning does NOT happen here any more

**Do not spawn `session-learning-coordinator`. Do not spawn `learning-classifier`. There is no §6.**

Step A already copied this session's full transcript and every subagent transcript to `.datacore/state/sessions/archive/<date>/<session-id>/` with `learning_status: pending`. The nightly sweep (`io.datacore.session-learning`, 05:20) reads the whole day's sessions in one batch and writes the engrams.

**Why it moved.** Measured over 54 runs: learning inside wrap-up cost a median 405k output tokens and ~14 subagent transcripts per session, and returned 3.6 engrams — about 112k output tokens each. It also never terminated: 5-plur's classifier cursor sat at 2026-07-30 while four consecutive passes re-read the same files and appended another run note explaining why they weren't advancing it, and all 37 candidates it queued are *still* unreviewed because the `/today` step they were deferred to did not exist. Batching the day removes the per-session fixed cost, sees cross-session repetition that a single-session pass structurally cannot, and replaces the date cursor — the thing that stalled — with a per-session claim that cannot skip work.

**What still belongs in the session:** calling `plur_learn` the moment the user corrects you. That path produced essentially all of the engrams that actually landed. It is not affected by any of this.

**Verify, don't assume.** Report the archive result from Step A's `session_archive` field in §10. If its status is not `archived`, say so plainly — an unarchived session is one the sweep will never see, and that is a silent loss, not a minor blemish.

**Focus mode context:** If focus mode was detected in Step 0a-bis, pass the following additional context to `journal-coordinator`:

Focus mode active:
  space: [space_dir from detection]
  project: [project from detection]
  contributor: [contributor from detection]
  journal_path: [journal_path from detection]

This session was run from a project folder. Write BOTH journals:
1. Personal journal (0-personal/journal/) — ALWAYS required, regardless of focus mode
2. Team journal entry in the focused space's journal — using contributor and project info above

The coordinator uses this to avoid full space discovery for the team entry (the space is already known) and passes the project/contributor directly to journal-entry-writer. It still spawns the personal journal-entry-writer as normal.

```
JOURNALS
────────
Discovering spaces and spawning per-space writers...

Spaces found: 0-personal, 1-teamspace, 2-projectspace

[Spawning: journal-coordinator → journal-entry-writer × N]

Journals updated:
  - 0-personal/journal/YYYY-MM-DD.md ✓
  - 1-teamspace/journal/YYYY-MM-DD.md ✓ (if work done there)
  - 2-projectspace/journal/YYYY-MM-DD.md ✓ (if work done there)

Session archived: .datacore/state/sessions/archive/YYYY-MM-DD/<id>/ ✓
  → queued for the 05:20 learning sweep (learning_status: pending)
```

**How it works:**

1. The coordinator discovers spaces via `ls -d [0-9]-*/`
2. It determines which spaces had relevant work
3. It spawns a `journal-entry-writer` for each relevant space (in parallel)
4. Those write to space-specific journals
5. The coordinator aggregates and returns a summary

**What gets written here:**
- Journal entry → `[space]/journal/YYYY-MM-DD.md`

**What gets written by the nightly sweep instead:**
- Patterns → `[space]/.datacore/learning/patterns.md`
- Corrections → `[space]/.datacore/learning/corrections.md`
- Preferences → `[space]/.datacore/learning/preferences.md`
- Engrams → PLUR, directly, no candidate queue

**No "additional insights" prompt.** If the user wants to force a specific insight into memory now rather than waiting for the sweep, they say so via §0e bulk feedback: *"also capture: the rebase-on-fork pattern for stale dependency PRs"* — call `plur_learn` on it directly in the same pass.

> **Parallel execution:** While the coordinator runs in background, immediately proceed to steps 7-9. Those work from conversation context and do not depend on its output. Nothing in this command now blocks on a learning agent.

### 6. Tasks & Delegation (paired — one decision, two destinations)

**Extract actionable tasks from conversation context (runs parallel to step 5):**

Review the session's insights, decisions, and next steps. Identify items that should become tasks — things that aren't continuation of current work (those go in step 3) but are *new* actionable items that emerged from the session.

**They are written to `inbox.org`, with positioning** (see §4). Not `next_actions.org`.

**Propose, do not auto-add.** Surface them in the §10 report as numbered proposals and create only what survives the §1 gate.

> **Why this flipped.** Auto-add was measured: 379 `:wrap_up_extracted:` tasks exist, they complete at **25.1% against a 36.8% baseline**, and the 276 still open are **a quarter of the entire open backlog**. The old rationale — "adding a task is cheap and reversible" — is true per task and false in aggregate; the cost is not the org edit, it is the backlog nobody can face. A proposal the user accepts costs one line of typing. A task the user never does costs attention every week forever.

```
GTD TASK EXTRACTION (proposals — confirmed in §1)
────────────────────────────────────────────────────
Reviewing session for actionable items beyond continuation tasks...

Proposed (say "add 1,3" or "add all" or ignore):
  1. [#A] Task from insight X → Growth section
  2. [#B] Task from decision Y → Product section
  3. [#B] Task from discovery Z → Engineering section
```

Tasks actually created still carry the `:wrap_up_extracted:` tag (configurable via `wrap_up.tag_extracted_tasks_with`) so the cohort stays measurable.

**Why auto-add with a tag instead of asking:**
- Adding an org task is cheap and reversible (1-line edit)
- The `:wrap_up_extracted:` tag (configurable via `wrap_up.tag_extracted_tasks_with`) makes them trivially greppable for batch review later
- §0e lets the user say "drop task 2, change task 3 to priority A, add another: research X" in one shot
- Not adding loses time-sensitive items the agent correctly identified

**§0e corrections** for this step:
- `drop task <n>` → delete the task by ID
- `change task <n> priority to <A/B/C>` → update priority cookie
- `change task <n> tag <add/remove> <tag>` → tag mutations
- `move task <n> to <section>` → update the entry's `TARGET_SECTION` property
- `add task: <free text>` → create new task in the same wrap_up_extracted batch

**What qualifies:**
- Strategic decisions that need follow-up work (but aren't the current task)
- New opportunities or ideas that emerged during the session
- Dependencies or prerequisites discovered for other work
- Research topics that surfaced and need dedicated attention

**What does NOT qualify (already captured elsewhere):**
- Current work that's incomplete → step 3 (continuation tasks)
- Completed items → step 4 (mark DONE)
- Patterns, insights and engram candidates → the nightly learning sweep (not this session)

**Task format:** the positioning block in §4. `TARGET_SECTION` carries the routing
decision (Operations, Product, Engineering, Growth, Research, Communications) that
this step used to express by *placing* the heading — the decision is the same, it is
just recorded as data instead of as a file offset.

> **v2: an org task is not a delegation.** `ledger_ingest_org.py` mirrors org tasks into the ledger on the 05:35 sweep, but a mirrored item carries an `org` block in its payload and `ledger_claim.py` skips exactly those: `pending = [i for i in claimable if not (i.payload or {}).get("org")]`. That filter is deliberate — it stopped agents working through a 342-item personal backlog unattended. The consequence: **a task written here can never be picked up by the fleet.** If the intent is "an agent should do this tonight", that is §6b delegation, not a task heading. Say which one you mean in the §10 report.
>
> **This is also why inbox-only matters for the switch.** Under Phase 1, `next_actions.org` is a projection of the ledger and refuses direct writes; `inbox.org` stays the capture surface it is today. Routing every write through inbox.org means the flip changes where entries *land*, never whether this command works — and the `TARGET_*` properties are exactly what a ledger-first `/process-inbox` needs to emit an `item.create` without re-deriving intent.


> **Why these are one step.** "This should happen" splits into *I will do it*
> (a task) and *an agent should do it tonight* (a delegation). Asking them in
> separate steps meant task-filing always ran first and delegation inherited
> whatever was left, which is how 379 auto-filed tasks accumulated at a 25%
> completion rate while the nightshift queue stayed thin. Decide the
> destination once, per item.

---

#### 6b. Delegation half


**Inference-first: scan the session for delegation candidates, surface them in §10 as suggestions. The user opts IN via §0e ("delegate task 1 and 3 tonight"). No tasks are auto-tagged `:AI:` here** — that's a heavier commitment because nightshift will actually execute. Surface, don't auto-execute.

**What to look for** (delegation candidates):

- **Research questions** the user voiced but didn't pursue: "I wonder if X..."
- **Time-sensitive opportunities** mentioned but parked: just-shipped feature → social posts; market signal → research deep-dive; competitor news → competitive brief
- **Repetitive maintenance** that's overdue per other GTD signals: "those 5 stale PRs need triage"
- **Content drafts** mentioned: "I should write about Y"
- **Outreach** that's been sitting: "I need to follow up with Z"

**Report rendering** (§10):
```
DELEGATION OPPORTUNITIES (suggestions — opt in via §0e):
  1. Research: "x402 vs ERC-8004 — which agentic-payment standard wins?"
     Why: session mentioned twice, no action taken
     If approved: would be added with :AI:research: tag
  2. Content: short post about today's [client]-space verification flow
     Why: time-sensitive, build-in-public momentum
     If approved: would be added with :AI:content: tag
  3. ...
```

**§0e corrections:**
- `delegate 1 and 3` → add those with appropriate `:AI:*:` tags
- `delegate all` → add all suggestions
- `delegate none` (default) → no AI tasks created
- `delegate: <free text>` → add a user-composed AI task

**Legacy prompt** (only when `wrap_up.inference_mode: off`):
```
Any quick tasks to delegate to AI? (brief, or Enter to skip)
> [user input]
```

### 7. Session Meta-Analysis

**Get the counters from the archive, not from memory:**

```bash
python3 ~/Data/.datacore/lib/wrap_up_mechanics.py meta
```

Returns `turns`, `user_turns`, `tool_calls`, `tool_breakdown`, `agents_spawned`, `output_tokens`, `subagent_output_tokens`, `billable_tokens`, `files_modified`, `spaces_touched` — computed from this session's archived transcript.

**This supersedes the old "Insight Density" tally and the `session_token_count.py` call.** Both asked the model to count things from a conversation it had partly compacted away, and the token figure in particular was once wrong by a factor of 5000 (ENG-2026-0505-029). Numbers that come from the transcript are right even when the session was long.

**What still needs judgement** — write these from the conversation, they are not in the JSON: session arc, correction categories, user role, energy pattern, key observation.

**Analyze the session itself, not just its content (runs parallel to step 5).** This builds a longitudinal dataset for understanding how sessions work and improving over time.

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

Session Shape (from `wrap_up_mechanics.py meta` — do NOT estimate these):
  - Turns: X (user: X)   Tool calls: X   Agents spawned: X
  - Output tokens: X     Subagent output: X     Billable: X
  - Spaces touched: [...]

Insight Density:
  - Engrams written in-session: X   (the sweep adds more at 05:20)
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

### 8. Finalize — push, and index the journal

```bash
python3 ~/Data/.datacore/lib/wrap_up_mechanics.py finalize
```

**Session-scoped by default.** It commits and pushes ONLY the files this session
touched, read from the archive's `files_modified` (main thread **and**
subagents). Everything else dirty in the repo is left alone.

> **Why.** The retired `./sync push` staged everything with `git add --ignore-removal .` and
> commits it as `Sync: <date>`. Correct for a single-session day; wrong the
> moment two sessions are open, because one session's wrap-up sweeps up the
> other's half-finished work and pushes it. That was the reported complaint, and
> it is a data-integrity problem, not a tidiness one — the other session never
> chose to publish that state.

Every session file lands in exactly one bucket, and **all four are reported**:

| Bucket | Meaning |
|---|---|
| `pushes[].session_files_staged` | Committed and pushed |
| `pushes[].left_for_other_sessions` | Dirty in the repo but not this session's — deliberately untouched |
| `skipped_project_repos` | Under `*/2-projects/*` — code repos are **never** auto-committed (a 2026-08-09 auto-commit to `main` triggered an unasked-for production deploy) |
| `unversioned` | Outside any repo, or invisible to git — `.claude/` is behind a symlink, launchd plists live in `~/Library`. **No push will ever carry these.** Say so in §10; this is how a hook config silently survives on one machine only. |

Report the counts for all four in §10. "Nothing to push" and "I left 4 files
behind" must never read the same.

**Guards it inherits from the retired `./sync`, each bought with an incident:**
- **Refuses to push from a non-default branch** unless `DATACORE_SYNC_ALLOW_BRANCH=1`. A warning alone let 610 commits pile up on a feature branch in 5-plur for two months — 52 zettels, every weekly content calendar since mid-June — pushed, but where nobody reads.
- **A rejected commit never falls through to a push.** It reports `COMMIT REJECTED` instead of a run that looks clean.
- **Names the push failure** rather than collapsing four causes into one message.
- **Reports `preexisting_unpushed_commits`** — a push carries every earlier unpushed commit on the branch. That is git, not a choice, and pretending the push was fully scoped would be false.

**`--scope all`** sweeps everything through the transport (`ledger_transport.py sync`:
converge every registered knowledge repo, fast-forward code repos, never commit
code). Use it only when the user explicitly wants everything swept up.

**`--dry-run`** reports what would be committed without doing it.

**It will not commit a project repo for you**, in either scope.

**Commit message format** (when you do commit, for this repo's own changes):
```
Session: [brief goal/topic]

- [Key change 1]
- [Key change 2]
```

**If push fails:** report it in §10 with the error. Changes are committed
locally; `/tomorrow` retries.


**Then index the session to the knowledge database (DIP-0004):**

```bash
python3 ~/Data/.datacore/lib/journal_parser.py --sync --space personal
```

> This was §11. Its command read `~/.datacore/lib/journal_parser.py` — a path
> that does not exist; the file lives under `~/Data/.datacore/lib/`. Paired
> with its own "if index fails, warn and continue", the step could not have
> worked and was built not to say so. Corrected above. If it fails now, report
> the error in §10 rather than swallowing it — the journal files remain the
> source of truth, but a silently stale index is one you will still trust.

### 9. Audit — asserted, not eyeballed

```bash
python3 ~/Data/.datacore/lib/wrap_up_mechanics.py audit
```

Returns `checks[]`, `passed`, `total`, `failed[]`. It asserts, against the filesystem and git, that:

| Check | Passes when |
|---|---|
| personal journal written | `0-personal/journal/<today>.md` (or `notes/journals/`) exists |
| space journals | reports which spaces got one — informational, never a failure |
| session archived | this session has a `meta.json` under the archive — i.e. the learning sweep will actually see it |
| all repos pushed | no repo has unpushed commits |
| no uncommitted work | no repo is dirty |
| context in sync | agents/commands/registry unchanged, or the rebuild was run |

**This replaces the old tick-box list.** A checklist the model fills in about its own behaviour tests nothing: 17 spec steps, 9 tasks created, 6 silently skipped (2026-05-29) happened *with* the checklist present. These checks read the disk.

**A failing check is reported, not hidden.** Put every entry of `failed[]` in the §10 report verbatim. If the personal journal is missing, that is the headline of the report, not a footnote.

### 10. Consolidated Report — the point of the command

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

**Personal X (@jssr)** — building in public, casual, what you worked on.
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
python3 ~/Data/.datacore/lib/wrap_up_mechanics.py meta
```

The output gives you `output_tokens`, `subagent_output_tokens` and
`billable_tokens` (uncached input + cache writes + output), all read from this
session's archived transcript. `session_token_count.py` still exists and reads
the live transcript directly if you need the raw cache-read breakdown, but
`meta` is the one to use here — it reads the same archive the sweep does, so
the two can never disagree about what the session cost.

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
- Take every figure from `wrap_up_mechanics.py meta`. Do not estimate any of them — the last spec that said "state it as ~NK" produced a number wrong by 5000x (ENG-2026-0505-029), and `meta` counts the transcript.
- `output_tokens` is the main thread; `subagent_output_tokens` covers every agent spawned this session, wrap-up included. No summing by hand.
- This gives the user visibility into the cost of the wrap-up process itself. For the cost across many sessions, `analyze_wrapup_cost.py` reports medians and the wrap-up's share of each session.


#### 10a. Open with the session summary

The summary is this report's first section, not a step at the top of the run.
Delivered first it scrolls away under a hundred tool calls before anyone reads
it; delivered here it is the first thing seen once the work is done.


> Run silently — no user prompt. Output the summary block below. Do not skip on the grounds that it is "automatic."

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


#### 10b. Artifacts

Describe what `preflight.artifacts.knowledge_docs` found — type, path, one line
of purpose — then append them to the journal's "Artifacts Created" section and
the monthly index. Preflight enumerated them already; only the descriptions
need judgement.


**Purpose:** Ensure all knowledge artifacts created during the session are discoverable.

**Do not re-scan the filesystem.** Step A's `artifacts` field already holds `created`, `modified` and `knowledge_docs` for this session, read from git rather than mtime (mtime catches every cache write; the point is knowledge artifacts, not churn). Your job here is to *describe* what it found, which needs judgement — enumerating it does not.

```
KNOWLEDGE ARTIFACTS
───────────────────
From preflight `artifacts.knowledge_docs`:

Artifacts found (descriptions inferred from file frontmatter + first heading + session context):
  ┌─────────────────────────────────────────────────────────────┐
  │ TYPE          │ PATH                        │ DESCRIPTION   │
  ├───────────────┼─────────────────────────────┼───────────────┤
  │ Style Guide   │ .datacore/specs/X.md        │ X writing...  │
  │ Zettel        │ 3-knowledge/zettel/Y.md     │ Concept Y     │
  │ Report        │ content/reports/Z.md        │ Analysis of Z │
  └─────────────────────────────────────────────────────────────┘
```

**Inference-first: no per-artifact prompt.** Descriptions are auto-applied to the journal artifact table and the monthly artifact index. The full table appears in §10 — if a description is wrong the user fixes it in §0e with `artifact <n> description: <new text>`.

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


#### 10c. Coverage note (replaces the old §8 insight-verification table)

One line, not a table: `Coverage: N insights — all in journal, M as tasks, K as
documents.` The old four-column checklist had a `Learning` column that is
unknowable at wrap-up time now (engrams do not exist until the 05:20 sweep),
and its other three columns re-verified §3, §5 and §6 one step after doing
them. Flag only genuine gaps.

### 11. Postable Moment Detection (Build in Public)

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

### 12. Wrap-up Self-Audit (MANDATORY, HOOK-ENFORCED)

**This step is the structural defense against silent skipping.** Before plur_session_end can succeed, today's personal journal MUST contain a `## Wrap-up Checklist Audit` section listing every spec step and its actual outcome. The PreToolUse hook `wrap_up_checklist_check.py` enforces this — it reads the journal and refuses to allow session_end if the section is missing.

**Format — exactly one row per spec step, no clustering:**

```markdown
## Wrap-up Checklist Audit

| Step | Title                          | Status |
|------|--------------------------------|--------|
|  1   | Pulse + notes                   | not-answered (asked 21:40, run continued) |
|  2   | Preflight                       | run ✓ — killed 1, flagged 1, session archived, 13 artifacts |
|  3   | Continuation tasks              | inferred-and-reported (none — work complete) |
|  4   | Mark completed tasks            | inferred-and-reported (1 auto-marked DONE, 2 surfaced) |
|  5   | Journals                        | run ✓ — 3 journals; session queued for 05:20 sweep |
|  6   | Tasks & delegation              | inferred-and-reported (4 tasks + 3 delegations proposed, 0 accepted — §1 unanswered) |
|  7   | Session meta-analysis           | run ✓ — counters from `mechanics meta`, appended to journal |
|  8   | Finalize (push + journal index) | run ✓ — ./sync push + 1 subproject; 1 dirty repo reported |
|  9   | Audit                           | run ✓ — `mechanics audit` 6/6 passed |
|  10  | Consolidated report             | run ✓ — report output, journal written |
|  11  | Postable moments                | inferred-and-reported (1 draft surfaced) |
|  12  | This audit                      | run ✓ |

Notes (use only when status alone isn't self-explanatory):
- Step 1: asked at 21:40, never answered — run completed as designed, nothing blocked
- Step 3: no continuation tasks needed (work complete) — see §10 report
- Step 6: proposals stand; nothing created because §1 went unanswered

Old steps 8, 10, 11, 12, 12.5, 13, 14, 16, 17.5 no longer have rows: §8 became a
coverage line, §6-learning moved to the nightly sweep, and the rest are
assertions inside `mechanics audit`, whose `passed/total` is reported on the
Step 9 row. A step that is machine-checked does not also need the model to
vouch for it.

(Be explicit. "I felt the user was done" is NOT a valid reason — that's a self-serving compression, see /wrap-up §0c. Likewise "I figured we could infer it" is not a §6b reason — that's the *point*; the reason must be specific.)
```

**Statuses allowed:**
- `run ✓` — step executed
- `skipped-by-user` — user explicitly declined (e.g. via AskUserQuestion answer)
- `skipped-by-mode-fast` — step was suppressed by `/wrap-up fast` (e.g. pulse, feedback gate)
- `not-applicable (REASON)` — concrete factual reason (e.g. "no continuation tasks because work is complete")
- `inferred-and-reported (DESCRIPTION)` — inference-first model: agent made decisions, applied them, surfaced in §10 report. Example: `inferred-and-reported (1 task auto-marked DONE, 2 surfaced for review)`
- `applied-from-feedback (N CORRECTIONS)` — §1 pulse received corrections and applied them. Example: `applied-from-feedback (dropped 1 task, bumped 1 priority)`

**Statuses NOT allowed:**
- "skipped" without reason
- "skipped because user is tired" / "in a hurry" / "shipped already" — these are agent inferences, not user statements
- "automatic so I didn't run it" — Automatic means run silently, not skip
- Missing rows entirely

**Why this exists:** Past wrap-up sessions documented in engram ENG-2026-0512-044 (2026-05-12) and ENG-2026-0529 (SMK 2026 wrap-up): steps 11, 12.5, 14, 15, plus parts of 17 were silently skipped. The agent rationalized that the user was "done" and dropped time-sensitive items (e.g. social posts about a just-shipped product). The skipped social posts cost user a viral LinkedIn moment about the SMK 2026 agent-claim demo. Memory engrams were insufficient — same failure repeated 17 days later. Step 18 + hook = structural enforcement.

**Hook behavior:**
- File: `~/Data/.datacore/lib/hooks/wrap_up_checklist_check.py`
- Trigger: PreToolUse on `mcp__plur__plur_session_end`
- Required journal sections: `Wrap-up Checklist Audit`, `Token Cost`, `Session Meta-Analysis`
- If any missing → `{"decision": "block", "reason": "..."}` returned on stdout
- Tool call refused; model must fix and retry

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
- `org/next_actions.org` (READ ONLY — to find tasks to mark DONE; never written by this command)
- Today's journal
- External working directories (`/tmp/`, worktrees, repos specified in arguments)
- Git status/log in all session-active repos

**Update:**
- `org/inbox.org` (ALL new entries: continuations, retroactive, extracted, delegations, DIP gaps — with `TARGET_*` positioning)
- `org/next_actions.org` (state changes only — mark DONE. No new headings; see §4.)
- `0-personal/journal/YYYY-MM-DD.md`
- Space journals if applicable
- `.datacore/learning/patterns.md`
- `CLAUDE.md` (if context sync needed)

**Create:**
- Continuation tasks with bootstrap prompts
- Backup in `.datacore/state/` (if context changed)

## Step Status Reference

**All 12 steps are REQUIRED to run.** This table says whether a step prompts or runs silently — both categories execute.

| # | Step | Normal mode | Fast mode |
|---|------|------|------|
| 1  | Pulse + notes | **asks once, never waits** | skipped-by-mode-fast |
| 2  | Preflight (`mechanics preflight`) | silent — one script call | same |
| 3  | Continuation tasks | inferred-and-reported | same |
| 4  | Mark completed tasks | inferred (high-conf auto-mark, med-conf surfaced) | same |
| 5  | Journals (`journal-coordinator`) | silent (background subagent) | silent |
| 6  | Tasks & delegation | proposed in §10; created only if §1 accepts | proposed only — nothing created |
| 7  | Session meta-analysis (`mechanics meta`) | silent (written to personal journal) | silent |
| 8  | Finalize: push + journal index (`mechanics finalize`) | silent | silent |
| 9  | Audit (`mechanics audit`) | silent | silent |
| 10 | Consolidated report (opens with session summary) | **the output** | same |
| 11 | Postable moments | inferred (drafts surfaced) | same |
| 12 | Self-audit to journal | silent (HOOK-ENFORCED) | silent |

**Prompt count totals:**
- Normal mode: **1** — §1, non-blocking. Plus §0e safety prompts only if a destructive / external / credential action is triggered.
- Fast mode: **0** + §0e.
- Legacy mode (`inference_mode: off`): 8 prompts (the original spec).

**"silent" ≠ "skip."** Every silent step still executes. **"inferred" ≠ "skip" either** — the agent does the work, applies the decision, lists it in §10. If the agent reads "inferred" as permission not to run, see §0c.

**An unanswered §1 is a normal outcome, and nothing may block on it.** If you find yourself waiting for the pulse, or holding a step until it arrives, that is precisely the failure §1 was reordered to remove: a wrap-up that stalls half-done because the user stepped away.

## Related

- `/tomorrow` - End of day, full AI delegation
- `/today` - Start of day briefing
- `/coach` - REBT coaching (quick check included here)
- `/gtd-daily-start` - Morning planning
- `journal-coordinator` agent - Orchestrates per-space journal entries
- `journal-entry-writer` agent - Writes single space journal entry
- `session_learning_sweep.py` - Nightly batch learning (replaces the per-session `session-learning-coordinator` / `learning-classifier` pair)
- `coach` agent - REBT coaching
- `context-maintainer` agent
