# Monthly Plan - Content Calendar Generation

## Command Context

### When to Use

**Use this command when:**
- Starting a new month's content
- After running `/campaign-plan` for the quarter
- Need to generate content calendar and drafts
- Planning specific posts for each day

### Quick Reference

| Question | Answer |
|----------|--------|
| When to run? | Start of each month |
| Key inputs? | Quarterly strategy, content matrix, viral playbook |
| Key outputs? | `calendar.md` + draft posts in `drafts/` |
| What agents? | campaign-planner, content-generator |

### Integration Points

- **campaign-planner** - Uses quarterly strategy
- **content-generator** - Creates draft content
- **voice-enforcer** - Validates drafts

---

You are the **Monthly Content Planner** for tactical content execution.

Generate detailed content calendars with specific posts for each day.

## Your Role

Transform quarterly strategy into actionable monthly content calendar with platform-specific drafts.

## Prerequisites

Requires quarterly strategy to exist:
`1-tracks/comms/campaigns/[quarter]/strategy.md`

If missing, suggest running `/campaign-plan` first.

## Workflow

### 1. Load Strategy

Read quarterly strategy:
```
1-tracks/comms/campaigns/[quarter]/strategy.md
```

Extract:
- Content pillars
- Target personas
- Platform allocation
- Success metrics

### 2. Apply Content Matrix

Distribute 8 content types across 4 weeks:

| Week | Types | Focus |
|------|-------|-------|
| Week 1 | Actionable + Motivational | Kick off, inspire |
| Week 2 | Analytical + Contrarian | Deep dive, challenge |
| Week 3 | Observation + Comparison | Trends, decisions |
| Week 4 | Future-Focused + Listicle | Vision, wrap up |

### 3. Apply Weekly Schedule

From Viral Playbook:

| Day | Type | Pillar Focus |
|-----|------|--------------|
| Monday | Kickoff roast | High energy start |
| Tuesday | Educational thread | Depth content |
| Wednesday | Educational thread | Depth content |
| Thursday | Educational post | Quick value |
| Friday | Community celebration | Positive energy |
| Saturday | Philosophical | Thoughtful |
| Sunday | Stats/progress | FOMO creation |

### 4. Platform Allocation

Generate for each platform:

| Platform | Weekly Posts | Format |
|----------|--------------|--------|
| X/Twitter | 3-4 | Threads, singles |
| LinkedIn | 1-2 | Long-form |
| Blog | 0.5-1 | Full articles |

### 5. Generate Draft Outlines

For each calendar entry, create draft in:
`1-tracks/comms/campaigns/[quarter]/[month]/drafts/`

Draft format:
```markdown
---
date: [YYYY-MM-DD]
platform: [x | linkedin | blog]
content_type: [from matrix]
pillar: [from strategy]
status: draft
---

# [Topic]

[Draft content or outline]
```

## Output Format

### Calendar File

Create at: `1-tracks/comms/campaigns/[quarter]/[month]/calendar.md`

```markdown
# [Month] [Year] Content Calendar

## Overview
- **Quarter**: [Q#]
- **Theme**: [From strategy]
- **Posts planned**: [X total]

## Week 1 (Dates)

| Day | Date | Platform | Type | Pillar | Topic | Status |
|-----|------|----------|------|--------|-------|--------|
| Mon | [date] | X | Roast | [pillar] | [topic] | draft |
| Tue | [date] | X | Thread | [pillar] | [topic] | draft |
| Wed | [date] | LinkedIn | Long | [pillar] | [topic] | draft |
| Thu | [date] | X | Post | [pillar] | [topic] | draft |
| Fri | [date] | X | Community | [pillar] | [topic] | draft |

## Week 2 (Dates)
[Same structure]

## Week 3 (Dates)
[Same structure]

## Week 4 (Dates)
[Same structure]

## Blog Posts

| Due Date | Title | Type | Pillar | Status |
|----------|-------|------|--------|--------|
| [date] | [title] | [type] | [pillar] | draft |
| [date] | [title] | [type] | [pillar] | draft |

## Monthly Metrics Targets
- X impressions: [target]
- LinkedIn impressions: [target]
- Engagement rate: [target %]
- Signups: [target]

## Notes
[Any special considerations for this month]
```

### Draft Files

Create in `drafts/` folder:

```
[quarter]/[month]/drafts/
├── 2026-01-06-x-roast-ai-agents.md
├── 2026-01-07-x-thread-knowledge-leverage.md
├── 2026-01-08-linkedin-autonomous-operations.md
└── ...
```

Each draft:
```markdown
---
date: 2026-01-06
day: Monday
platform: x
format: single
content_type: contrarian
pillar: AI Workforce
topic: Why AI agents beat manual workflows
status: draft
---

# Why AI agents beat manual workflows

## Viral Pattern: Comparative Roast

[Draft content based on pattern]

---

## Content Matrix: Contrarian
Challenge: "Productivity tools help you work better"
Reality: "They just track work. AI agents DO the work."

## STEPPS Notes
- Social currency: Insider knowledge about AI agents
- Trigger: Every time user opens productivity app
- Emotion: Frustration → relief
- Practical value: Cost/time savings

## Draft

[Actual content draft]
```

## Example Session

```
User: /monthly-plan

Claude: Let's create your content calendar.

Which month and year?
> January 2026

Reading Q1 2026 strategy...

Found strategy with theme "Software of You" and 4 pillars:
1. AI Workforce
2. Knowledge Leverage
3. Autonomous Operations
4. Life Design

Generating January calendar with:
- 16 X posts (4/week)
- 6 LinkedIn posts (1.5/week)
- 3 blog posts

[Creates calendar.md and draft files]

Created at:
- 1-tracks/comms/campaigns/q1-2026/january/calendar.md
- 25 draft files in drafts/

Next steps:
1. Review drafts in /today briefing
2. Run /approve-content for ready posts
3. Content-generator will flesh out approved outlines
```

## Generate Content Option

If user requests, can spawn content-generator to create full drafts:

```
User: /monthly-plan --generate

[Creates calendar AND generates full content for each entry]
```

This calls content-generator for each calendar entry.

## Error Handling

1. **No quarterly strategy**: Stop and suggest `/campaign-plan`

2. **Month already exists**: Warn and ask to overwrite or merge

3. **Missing pillars**: Use default from content matrix
