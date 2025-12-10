# GTD Workflow in Datacore

Getting Things Done (GTD) is the methodology powering Datacore's task management. This guide explains how it works.

## The Five Stages

### 1. Capture

Everything goes to `org/inbox.org`. Don't think, just capture:

```org
* TODO Call mom about Sunday dinner
* TODO Fix bug in login page
* Research note: https://interesting-article.com
* Idea: Build a habit tracker
```

**Rule**: If it takes mental energy to remember, capture it.

### 2. Clarify

Process each inbox item by asking:

1. **Is it actionable?**
   - No → Reference (notes/) or Trash (delete)
   - Yes → Continue

2. **What's the next action?**
   - Single action → `next_actions.org`
   - Multiple steps → Create project

3. **Can AI do it?**
   - Research → Tag `:AI:research:`
   - Content → Tag `:AI:content:`
   - Analysis → Tag `:AI:data:`

### 3. Organize

Items flow to their proper homes:

| Destination | Content |
|-------------|---------|
| `next_actions.org` | Actionable tasks |
| `someday.org` | Future possibilities |
| `notes/pages/` | Reference information |
| `notes/2-knowledge/` | Permanent knowledge |

### 4. Review

**Daily** (`/gtd-daily-start`, `/gtd-daily-end`):
- Process inbox to zero
- Review today's priorities
- Delegate to AI

**Weekly** (`/gtd-weekly-review`):
- Review all focus areas
- Update project status
- Clear stale items

**Monthly** (`/gtd-monthly-strategic`):
- Strategic planning
- Goal assessment
- System health check

### 5. Engage

Work from `next_actions.org`, organized by focus area and context.

## File Structure

```
org/
├── inbox.org           # Capture point (process to zero)
├── next_actions.org    # Active tasks by focus area
├── someday.org         # Future possibilities
└── habits.org          # Recurring habits
```

## Task States

```org
* TODO Task not started
* NEXT Priority task to do next
* WAITING Blocked on someone/something
* DONE Completed
```

## Focus Areas

Tasks in `next_actions.org` are organized by focus area:

```org
* Work
** Project Alpha
*** TODO Write proposal :AI:content:
*** TODO Research competitors :AI:research:
** Daily Operations
*** TODO Review pull requests
*** TODO Team standup

* Personal
** Health
*** TODO Schedule checkup
** Learning
*** TODO Read chapter 5
```

## AI Task Delegation

### Tagging Convention

```org
* TODO [task description] :AI:[type]:
```

### Tag Types

| Tag | Agent | Output |
|-----|-------|--------|
| `:AI:research:` | gtd-research-processor | Literature notes, zettels |
| `:AI:content:` | gtd-content-writer | Drafts in `content/` |
| `:AI:data:` | gtd-data-analyzer | Reports, metrics |
| `:AI:pm:` | gtd-project-manager | Status updates |

### Example Delegations

```org
* TODO Research market trends for Q1 :AI:research:
  SCHEDULED: <2025-01-15 Wed>
  :PROPERTIES:
  :CREATED: [2025-01-10 Fri]
  :END:
  Focus on competitor pricing and market size.

* TODO Draft blog post about productivity :AI:content:
  :PROPERTIES:
  :TONE: Professional, actionable
  :LENGTH: 1500 words
  :END:
  Cover GTD methodology and AI augmentation.
```

## Daily Rhythm

| Time | Action | Command |
|------|--------|---------|
| Morning | Review AI work, set priorities | `/gtd-daily-start` |
| Day | Capture to inbox, work from next_actions | - |
| Evening | Process inbox, delegate to AI | `/gtd-daily-end` |

## Weekly Review Checklist

The `/gtd-weekly-review` command guides you through:

- [ ] Inbox at zero
- [ ] Review all focus areas
- [ ] Check waiting items
- [ ] Process someday/maybe
- [ ] Review projects
- [ ] Plan next week

## Tips

1. **Keep inbox empty** - Process daily, not weekly
2. **Two-minute rule** - If it takes <2 min, do it now
3. **Context matters** - Tag tasks with where/how you can do them
4. **Trust the system** - If it's captured, let it go mentally
5. **AI handles the mechanical** - You focus on judgment and creativity

## Related

- [[Welcome to Datacore]] - Getting started
- [[Commands Reference]] - Daily commands
- [[Agents Reference]] - AI delegation details

---

*"Your mind is for having ideas, not holding them." — David Allen*
