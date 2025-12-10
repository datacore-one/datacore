# Org-mode Conventions for Datacore

This document defines org-mode syntax and conventions used across Datacore. All agents and humans editing `.org` files must follow these conventions to ensure consistent parsing.

## File Structure

### Standard org File Header

```org
#+TITLE: File Title
#+STARTUP: overview
#+FILETAGS: :tag1:tag2:

* Section Heading
```

### Heading Levels

| Level | Syntax | Usage |
|-------|--------|-------|
| 1 | `*` | Top-level sections (Tier, Category) |
| 2 | `**` | Focus areas, major groupings |
| 3 | `***` | Individual tasks or projects |
| 4 | `****` | Sub-tasks within a project |

**Critical**: Always use asterisks (`*`) at the beginning of the line with a space after.

```org
* Good heading
** Good subheading
*** Good task

*Bad - no space after asterisk
 * Bad - space before asterisk
```

## Task States

### Standard States

| State | Meaning | When to Use |
|-------|---------|-------------|
| `TODO` | Task ready to work on | Default state for new actionable items |
| `NEXT` | High priority, work soon | Today's focus or immediate action |
| `WAITING` | Blocked on external | Waiting for someone/something |
| `DONE` | Completed | Task finished successfully |
| `CANCELLED` | No longer needed | Task abandoned or obsolete |

### Syntax

```org
*** TODO Task description
*** NEXT Urgent task
*** WAITING Response from client
*** DONE Completed task
*** CANCELLED No longer relevant
```

**Critical**: State keyword must be immediately after heading asterisks, followed by space.

## Priority Levels

| Priority | Syntax | Meaning |
|----------|--------|---------|
| A | `[#A]` | Critical/urgent |
| B | `[#B]` | Important, default |
| C | `[#C]` | Nice to have, low priority |

### Syntax

```org
*** TODO [#A] Critical task
*** TODO [#B] Normal priority task
*** TODO [#C] Someday task
```

Priority comes after the state keyword, before the task description.

## Timestamps

### Active Timestamps (Scheduling)

```org
<2025-12-01 Mon>           # Date only
<2025-12-01 Mon 14:00>     # Date and time
<2025-12-01 Mon 14:00-15:00>  # Time range
```

### Inactive Timestamps (Logging)

```org
[2025-12-01 Mon]           # For CREATED, notes
[2025-12-01 Mon 14:00]     # With time
```

### Scheduling Keywords

```org
SCHEDULED: <2025-12-01 Mon>   # When to start working
DEADLINE: <2025-12-05 Fri>    # Must be done by
CLOSED: [2025-12-03 Wed]      # When completed (auto-added)
```

**Critical**: These keywords go on their own line, directly under the heading.

```org
*** TODO [#A] Important task
SCHEDULED: <2025-12-01 Mon>
DEADLINE: <2025-12-05 Fri>
```

## Property Drawers

Properties provide metadata for tasks. They must be immediately after the heading (and any scheduling).

### Syntax

```org
*** TODO [#A] Task heading
SCHEDULED: <2025-12-01 Mon>
:PROPERTIES:
:CREATED: [2025-11-28 Fri]
:SOURCE: Email from client
:EFFORT: 2h
:END:

Task description and notes go here.
```

### Common Properties

| Property | Purpose | Example |
|----------|---------|---------|
| `:CREATED:` | When task was created | `[2025-11-28 Fri]` |
| `:SOURCE:` | Origin of the task | `Email`, `Meeting`, `Idea` |
| `:EFFORT:` | Estimated time | `2h`, `30m`, `1d` |
| `:ID:` | Unique identifier | `uuid-string` |
| `:CUSTOM_ID:` | Custom reference | `project-task-001` |

**Critical**:
- `:PROPERTIES:` and `:END:` must be on their own lines
- Property names are case-sensitive
- No blank lines inside the drawer

## Tags

Tags categorize and filter tasks.

### Syntax

```org
*** TODO Task with tags                           :tag1:tag2:
*** TODO Another task                             :AI:research:
```

Tags go at the end of the heading line, surrounded by colons.

### AI Processing Tags

| Tag | Agent | Purpose |
|-----|-------|---------|
| `:AI:` | ai-task-executor | General AI processing |
| `:AI:research:` | gtd-research-processor | Research tasks |
| `:AI:content:` | gtd-content-writer | Content creation |
| `:AI:data:` | gtd-data-analyzer | Data analysis |
| `:AI:pm:` | gtd-project-manager | Project management |

### System Tags

| Tag | Purpose |
|-----|---------|
| `:standing:` | Recurring/permanent item (never delete) |
| `:ARCHIVE:` | Ready for archival |
| `:noexport:` | Exclude from exports |

## Checkboxes

For sub-items within a task:

```org
*** TODO Main task
- [ ] Unchecked item
- [X] Checked/completed item
- [-] Partially complete
```

**Critical**: Use `- [ ]` format, not `* [ ]` (asterisks are for headings).

## Links

### Wiki Links

```org
[[Page Name]]
[[Page Name][Display Text]]
```

### File Links

```org
[[file:path/to/file.md]]
[[file:path/to/file.md][Description]]
```

### External Links

```org
[[https://example.com][Example Site]]
```

## Body Text

### Paragraphs

Plain text under headings. Separate paragraphs with blank lines.

```org
*** TODO Task

First paragraph explaining context.

Second paragraph with more details.
```

### Lists

```org
Unordered:
- Item one
- Item two
  - Nested item

Ordered:
1. First step
2. Second step
   1. Sub-step
```

## Complete Task Example

```org
*** TODO [#A] Review quarterly metrics report                :AI:data:
SCHEDULED: <2025-12-01 Mon>
DEADLINE: <2025-12-05 Fri>
:PROPERTIES:
:CREATED: [2025-11-28 Fri]
:SOURCE: Weekly review
:EFFORT: 2h
:END:

Need to analyze Q4 metrics and prepare summary for board meeting.

Focus areas:
- [ ] Revenue trends
- [ ] User growth
- [ ] Churn analysis

Related: [[Q4 Metrics]], [[Board Meeting Prep]]
```

## Common Mistakes to Avoid

### Heading Errors

```org
# WRONG - using markdown heading
*WRONG - no space after asterisk
 * WRONG - space before asterisk
*  WRONG - two spaces after asterisk
```

### Property Drawer Errors

```org
*** TODO Task
Some text here
:PROPERTIES:        # WRONG - drawer must be immediately after heading
:CREATED: [...]
:END:

*** TODO Task
:PROPERTIES:
:CREATED: [...]
                    # WRONG - blank line inside drawer
:END:

*** TODO Task
:PROPERTIES:
CREATED: [...]      # WRONG - missing colon prefix
:END:
```

### Tag Errors

```org
*** TODO Task :tag:          # WRONG - tags at end should touch
*** TODO Task:tag1:tag2:     # WRONG - no space before tags
*** TODO Task :tag1 :tag2:   # WRONG - space between tags
```

## Parsing Safety

When editing org files programmatically:

1. **Never delete the first heading** (usually `* Inbox` or similar)
2. **Preserve standing items** marked with `:standing:` tag
3. **Keep property drawers intact** when moving tasks
4. **Maintain blank lines** between sections
5. **Escape special characters** in task descriptions

## File-Specific Conventions

### inbox.org

- First line: `* TODO Do more. With less.` (standing item)
- Main capture section: `* Inbox`
- Items processed to zero daily

### next_actions.org

- Organized by Tier and Focus Area
- Tasks at level 3 (`***`)
- Focus areas at level 2 (`**`)

### someday.org

- Future possibilities not yet committed
- No schedules or deadlines
- Review during weekly review

## References

- [Org Mode Manual](https://orgmode.org/manual/)
- [DIP-0009: GTD System Specification](../dips/DIP-0009-gtd-specification.md)
- [DIP-0002: Layered Context Pattern](../dips/DIP-0002-layered-context-pattern.md)
