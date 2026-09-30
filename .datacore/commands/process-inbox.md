---
name: process-inbox
description: process-inbox command
recall:
  # DIP-0029 default — engrams scoped to this command + tag-matched.
  scopes:
    - command:process-inbox
  tags:
    - process-inbox
---

# /process-inbox

Automated inbox processing — classify and route all entries from inbox.org.

## When to Use

- The nightly inbox job (chief-of-staff `cos_inbox.sh`), once per space with an inbox
- Manual batch processing when inbox is full
- After email triage has added new items

## Mode Detection

**Nightshift mode** (no user present — the caller says so, e.g. "nobody is present", "unattended", "nightshift mode"): Process all items autonomously. Make classification decisions without asking. Never delete an entry. An entry you cannot decide is NOT guessed into a list: it stays in the inbox, marked `[NEEDS_REVIEW]` (see "Entries you cannot decide" below), for the owner to review.

**Interactive mode** (user present): Ask for confirmation on ambiguous items. If the owner does not decide one, it stays in the inbox marked `[NEEDS_REVIEW]`, the same as at night.

## The three outcomes — every entry ends in exactly one

1. **Clarified and moved** to where it belongs (the routing rules below). It leaves the inbox.
2. **Finished** — its state is DONE or CANCELLED. It leaves the inbox and is kept: move the whole entry, unchanged (state, `CLOSED:` stamp, properties, body), to the end of `[space]/org/inbox_archive.org` (create it with `#+TITLE: Inbox archive` if missing). Never reopen it (no TODO/NEXT), never route it into next_actions.org as an open task, never delete it.
3. **Cannot decide** — you cannot tell what it is or what the next action would be (a fragment such as "blue thing w/ Marko??"). It STAYS in the inbox, where it is, with the literal text `[NEEDS_REVIEW]` put at the front of its title, after any state keyword: `** TODO [NEEDS_REVIEW] blue thing w/ Marko??` — a prefix in the title, not a `:NEEDS_REVIEW:` tag and not a state. Not someday.org, not next_actions.org, not "Operations with a note". An entry already marked `[NEEDS_REVIEW]` is left as it is.

Nothing else remains in the inbox after a run.

## Workflow

### Step 1: Read inbox.org

Read the inbox you were given (`[space]/org/inbox.org`). When none is named, use the personal space's inbox: the space `install.yaml` names as `roles.personal` (never assume a folder name or number).

An entry is each direct child of the `* Inbox` section, each top-level heading with a state, and each top-level heading with nothing under it (a bare capture — process it like any other) — open or finished. A stateless top-level heading WITH children is a section the owner parked work under: its open children wait for the owner, but its finished children leave like any finished entry (outcome 2). The nightly job moves finished entries to `inbox_archive.org` in code before and after you run.

If 0 entries: log "Inbox empty" and exit.

### Step 2: Process Each Entry

For each entry, spawn `gtd-inbox-processor` subagent (or apply its rules yourself) with:
- The entry text
- Target files in the SAME space: `next_actions.org`, `research_learning.org`, `someday.org`, `inbox_archive.org`
- Mode: autonomous (no user confirmation needed)

Classification rules (from gtd-inbox-processor):
- **Bare URL/link** (the URL alone, or only "read/review/check out") → `research_learning.org` under matching focus area
- **URL/link with the owner's comment** → `next_actions.org` as a verb-first task: the comment says it is already read, so it is an action. Keep the URL as `:SOURCE:` and the comment as `:CONTEXT:` — never research, never someday
- **Actionable task** → `next_actions.org` under matching focus area
- **Idea/exploration** → `someday.org` (`ideas.org` is retired)
- **Finished (DONE/CANCELLED)** → `inbox_archive.org`, unchanged (outcome 2)
- **Cannot decide** → stays in the inbox, prefixed `[NEEDS_REVIEW]` (outcome 3)
- **Reference** → knowledge note or next_actions with `:reference:` tag
- **Bookmarks with notes/instructions** → preserve the notes as CONTEXT property

### Step 3: Route Research Items

For items routed to `research_learning.org`:
- Place under the correct focus area heading (Verity, Trading, Health, Technology, etc.)
- Format as:
  ```org
  *** TODO [#B] Title or description
      :PROPERTIES:
      :CREATED: [YYYY-MM-DD Day]
      :SOURCE: URL or reference
      :EFFORT: 0:20
      :END:
      Link: https://...
      Why: [extracted relevance from bookmark notes]
  ```
- Preserve any user notes or instructions from the original bookmark

### Step 4: Commit Results

After all entries processed:
1. Verify the inbox holds only entries marked `[NEEDS_REVIEW]` (every other entry was routed or archived, and can be found at its destination)
2. Count items routed to each destination
3. Git commit the files you changed, by explicit path: `git commit -m "..." -- <your files>`, then push. When the caller says it commits for you (the nightly inbox job does), do not commit, pull or push at all.

**Uncommitted files belong to other writers.** The working tree of a space holds other agents' uncommitted work, including the ledger's event logs (`.datacore/events/*.jsonl`). Never run `git stash` (any form), `git reset` (any form), `git checkout -- <file>` / `git checkout <path>`, `git restore` or `git clean` — they hide or discard that work. On 2026-09-30 an unattended run stashed to get a pull through and swallowed five uncommitted ledger events. The unattended tool policy refuses these commands (`worktree.discard`); a refusal is not an obstacle to route around. **A git problem is reported, not tidied:** if a commit, pull or push fails, stop, change nothing more in git, and say in one line what failed.

### Step 5: Report

Log summary:
```
Inbox processing complete:
- Total entries: X
- → next_actions.org: X
- → research_learning.org: X
- → someday.org: X
- → knowledge notes: X
- → inbox_archive.org (finished): X
- Remaining in inbox, marked [NEEDS_REVIEW]: X
```

## Error Handling

- If an entry can't be classified: keep it in inbox.org with the `[NEEDS_REVIEW]` prefix (outcome 3)
- If target file doesn't exist: create it with standard header
- If git commit, pull or push fails: report it in one line and stop there — never stash, reset, checkout, restore or clean to get past it; the inbox work already done stays as it is for the owner or the calling job

## Boundaries

**YOU CAN:**
- Read and modify the space's inbox.org, inbox_archive.org, next_actions.org, research_learning.org, someday.org
- Create knowledge notes in the notes directory
- Spawn gtd-inbox-processor subagents

**YOU CANNOT:**
- Delete entries without routing or archiving them
- Reopen a finished entry, or leave one in the inbox
- Guess an undecidable entry into a list instead of marking it `[NEEDS_REVIEW]`
- Modify entries already in next_actions.org (only add new ones)
- Skip entries silently — every entry must be accounted for in the report
- Run `git stash`, `git reset`, `git checkout <path>`, `git restore` or `git clean` — uncommitted files belong to other writers
