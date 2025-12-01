---
title: Agents Reference
type: reference
created: 2025-01-01
---

# Agents Reference

Datacore agents are autonomous AI workers that execute tasks while you're away. They're defined in `.datacore/agents/` and invoked by the `ai-task-executor`.

## How Agents Work

1. **Tagging:** You tag tasks in `next_actions.org` with `:AI:` tags
2. **Scanning:** `ai-task-executor` scans for tagged tasks
3. **Routing:** Tasks route to specialized agents based on tag
4. **Execution:** Agent performs the work, saves output
5. **Reporting:** Results appear in your morning briefing

## Task Tags

| Tag | Routes To | Purpose |
|-----|-----------|---------|
| `:AI:` | ai-task-executor | Generic AI task |
| `:AI:research:` | gtd-research-processor | URL analysis, summaries |
| `:AI:content:` | gtd-content-writer | Writing drafts |
| `:AI:data:` | gtd-data-analyzer | Reports, metrics |
| `:AI:pm:` | gtd-project-manager | Project tracking |

## Built-in Agents

### ai-task-executor

**The routing hub.** Scans for `:AI:` tagged tasks and dispatches to specialized agents.

**Location:** `.datacore/agents/ai-task-executor.md`

**Behavior:**
- Scans `next_actions.org` for `:AI:` tags
- Parses task details and context
- Routes to appropriate specialized agent
- Logs execution results to journal
- Updates task status (DONE, WAITING, etc.)

**Does NOT:**
- Execute tasks itself (only routes)
- Make decisions about task priority
- Delete or modify tasks without logging

---

### gtd-research-processor

**Research and knowledge extraction.**

**Tag:** `:AI:research:`

**Input:** URLs, topics, questions

**Output:**
- Literature notes with progressive summarization
- Atomic zettels for key concepts
- Links to related existing notes

**Example task:**
```org
* TODO Research competitor pricing strategies :AI:research:
  https://competitor1.com/pricing
  https://competitor2.com/plans
  Focus on: enterprise tiers, usage-based pricing
```

**Output location:** `0-personal/notes/2-knowledge/literature/`

---

### gtd-content-writer

**Content generation.**

**Tag:** `:AI:content:`

**Input:** Topic, outline, key points, tone

**Output:**
- Draft blog posts
- Email drafts
- Documentation
- Social media content
- Marketing copy

**Example task:**
```org
* TODO Write blog post about data sovereignty :AI:content:
  Key points:
  - Why individuals should own their data
  - Current problems with data silos
  - Our solution approach
  Tone: Professional but accessible
  Length: ~1000 words
```

**Output location:** `0-personal/content/blog/` (or appropriate subfolder)

**Important:** All outputs are DRAFTS. Review before publishing.

---

### gtd-data-analyzer

**Data processing and reporting.**

**Tag:** `:AI:data:`

**Input:** Data sources, metrics to calculate, report format

**Output:**
- Metrics reports
- Trend analysis
- Pattern identification
- Actionable insights

**Example task:**
```org
* TODO Generate weekly productivity metrics :AI:data:
  Source: org files, journal entries
  Metrics: tasks completed, inbox processing time, AI delegation rate
  Period: Last 7 days
  Format: Summary with key insights
```

**Output location:** `0-personal/content/reports/`

---

### gtd-project-manager

**Project coordination and tracking.**

**Tag:** `:AI:pm:`

**Input:** Project name, tracking criteria

**Output:**
- Project status summary
- Blocker identification
- Completion percentage
- Dependency mapping
- Escalation flags (blockers >7 days)

**Example task:**
```org
* TODO Check Verity project status :AI:pm:
  Review: open issues, recent commits, blockers
  Flag: anything stuck >1 week
```

**Output:** Updates project note in `0-personal/notes/1-active/`

---

### gtd-inbox-processor

**Inbox entry processing.**

Used by `/gtd-daily-end` to process individual inbox items.

**Behavior:**
- Classifies item type (action, reference, someday, etc.)
- Enhances with metadata (dates, tags, context)
- Routes to appropriate destination
- Logs processing decision

**Not typically invoked directly** - used by GTD commands.

---

### conversation-processor

**ChatGPT export processing.**

Processes exported ChatGPT conversations into knowledge artifacts.

**Input:** ChatGPT export JSON file

**Output:**
- Zettels for key concepts
- Topic notes for major themes
- TODOs for action items mentioned
- Insights for strategic observations

**Example usage:**
```
Process the ChatGPT conversation about pricing strategy from last week.
```

---

### research-link-processor

**Batch URL processing.**

Processes multiple URLs for research purposes.

**Input:** List of URLs

**Output:**
- Literature notes for each URL
- Summary of key themes across sources
- Extracted quotes and citations

---

## Agent Output Standards

All agents follow these output conventions:

### File Naming
- Include date: `2025-01-28-topic-name.md`
- Lowercase with hyphens
- Descriptive but concise

### Frontmatter
```yaml
---
type: draft | report | literature | zettel
created: 2025-01-28
agent: gtd-content-writer
task: "Original task description"
status: draft | review | final
---
```

### Status Markers
- **DRAFT** - Needs human review
- **REVIEW** - Ready for approval
- **FINAL** - Approved and complete

## Creating Custom Agents

Add markdown files to `.datacore/agents/`:

```markdown
# my-agent

Description of what this agent does.

## Trigger

How this agent is invoked (tag, command, etc.)

## Input

What information the agent needs.

## Process

Step-by-step instructions for the agent.

## Output

What the agent produces and where it saves it.
```

## Best Practices

1. **Be specific in tasks.** The more context you provide, the better the output.

2. **Review all outputs.** Agents create drafts, not final products.

3. **Use appropriate tags.** Wrong tag = wrong agent = wrong output.

4. **Check output locations.** Know where agents save their work.

5. **Iterate.** If output isn't right, refine the task description.

## See Also

- [Commands Reference](commands.md)
- GTD Workflow
- [Modules](modules.md)
