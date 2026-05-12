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

- Nightshift scheduled execution (01:00 UTC nightly)
- Manual batch processing when inbox is full
- After email triage has added new items

## Mode Detection

**Nightshift mode** (no user present): Process all items autonomously. Make classification decisions without asking. Use conservative defaults — when uncertain, route to next_actions.org rather than deleting.

**Interactive mode** (user present): Ask for confirmation on ambiguous items.

## Workflow

### Step 1: Read inbox.org

Read `0-personal/org/inbox.org` and count entries under `* Inbox`.

If 0 entries: log "Inbox empty" and exit.

### Step 2: Process Each Entry

For each entry under `* Inbox`, spawn `gtd-inbox-processor` subagent with:
- The entry text
- Target files: `next_actions.org`, `research_learning.org`, `ideas.org`
- Mode: autonomous (no user confirmation needed)

Classification rules (from gtd-inbox-processor):
- **URL/link with "read/review/check out"** → `research_learning.org` under matching focus area
- **Actionable task** → `next_actions.org` under matching focus area
- **Idea/exploration** → `ideas.org` with scoring
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
1. Verify inbox.org entries have been removed (routed elsewhere)
2. Count items routed to each destination
3. Git commit and push

### Step 5: Report

Log summary:
```
Inbox processing complete:
- Total entries: X
- → next_actions.org: X
- → research_learning.org: X
- → ideas.org: X
- → knowledge notes: X
- Remaining in inbox: X (ambiguous, deferred)
```

## Error Handling

- If an entry can't be classified: keep in inbox.org, add `[NEEDS_REVIEW]` prefix
- If target file doesn't exist: create it with standard header
- If git push fails: log warning but don't fail the run

## Boundaries

**YOU CAN:**
- Read and modify inbox.org, next_actions.org, research_learning.org, ideas.org
- Create knowledge notes in the notes directory
- Spawn gtd-inbox-processor subagents

**YOU CANNOT:**
- Delete entries without routing them
- Modify entries already in next_actions.org (only add new ones)
- Skip entries silently — every entry must be accounted for in the report
