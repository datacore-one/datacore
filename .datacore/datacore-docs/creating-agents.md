# Creating Agents

Agents are autonomous task processors that run without user interaction.

## Agent File Structure

```markdown
---
name: my-agent
description: One-line description for the Task tool
model: sonnet
---

# Agent Title

Instructions for the agent...
```

### Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique identifier (kebab-case) |
| `description` | Yes | Shows in Claude Code's Task tool |
| `model` | No | `sonnet`, `opus`, or `haiku` |

## Agent Anatomy

### 1. Role Definition

Tell the agent what it is:

```markdown
# My Agent

You are the **My Agent** for [specific purpose].

## Your Role

[What this agent does and why it exists]
```

### 2. Trigger Conditions

When should this agent run?

```markdown
## When You're Called

- Task tagged with `:AI:mytag:`
- [Other trigger conditions]

## Input Format

Receives from ai-task-executor:
```json
{
  "task_headline": "...",
  "task_details": "...",
  "priority": "A/B/C",
  "category": "..."
}
```
```

### 3. Workflow Steps

Break down the process:

```markdown
## Your Workflow

### Step 1: Parse Input
Extract relevant information from the task...

### Step 2: Process
Do the main work...

### Step 3: Generate Output
Create the deliverable...
```

### 4. Output Specification

Define what the agent produces:

```markdown
## Output Format

**Location:** Where files are saved
**Filename:** Naming convention

**SUCCESS:**
```json
{
  "status": "completed",
  "output_path": "...",
  "summary": "..."
}
```

**FAILED:**
```json
{
  "status": "failed",
  "failure_reason": "...",
  "recommended_actions": [...]
}
```
```

### 5. Boundaries

Be explicit about capabilities:

```markdown
## Your Boundaries

**YOU CAN:**
- [Capability 1]
- [Capability 2]

**YOU CANNOT:**
- [Limitation 1]
- [Limitation 2]

**YOU MUST:**
- [Requirement 1]
- [Requirement 2]
```

## Example: Simple Agent

```markdown
---
name: meeting-summarizer
description: Summarizes meeting notes and extracts action items
model: haiku
---

# Meeting Summarizer Agent

You summarize meeting notes and extract action items.

## When You're Called

Tasks tagged with `:AI:meeting:`

## Input

Task details contain:
- Meeting notes (raw text or file path)
- Attendees (optional)
- Meeting type (optional)

## Workflow

### Step 1: Read Notes
Parse the meeting notes from task details or file.

### Step 2: Extract Information
Identify:
- Key decisions made
- Action items with owners
- Open questions
- Follow-up meetings needed

### Step 3: Generate Summary
Create structured summary with sections.

## Output

Save to: `0-personal/notes/pages/meetings/`
Filename: `[Date] - [Meeting Topic].md`

Format:
```markdown
---
type: meeting-summary
date: YYYY-MM-DD
attendees: [list]
---

# [Meeting Topic]

## Summary
[2-3 paragraph overview]

## Decisions
- [Decision 1]
- [Decision 2]

## Action Items
- [ ] [Action] - @owner - due [date]
- [ ] [Action] - @owner - due [date]

## Open Questions
- [Question 1]
- [Question 2]
```

## Boundaries

**YOU CAN:**
- Summarize any meeting notes
- Extract action items
- Identify decisions

**YOU CANNOT:**
- Assign owners not mentioned
- Make commitments
- Guess missing information

**YOU MUST:**
- Flag unclear action items
- Note when information seems incomplete
```

## Example: Complex Agent

```markdown
---
name: competitor-analyzer
description: Analyzes competitor products and generates strategic insights
model: sonnet
---

# Competitor Analyzer Agent

You analyze competitor products and generate strategic insights.

## When You're Called

Tasks tagged with `:AI:competitor:`

## Input Format

```json
{
  "task_headline": "Analyze [Competitor] [Product]",
  "task_details": "URL: https://...\nFocus: [pricing/features/positioning]",
  "category": "Product"
}
```

## Workflow

### Step 1: Fetch Competitor Data
Use WebFetch to retrieve:
- Product pages
- Pricing information
- Feature lists
- Documentation

### Step 2: Analyze
Evaluate against framework:

| Dimension | Analysis |
|-----------|----------|
| Features | What do they offer? |
| Pricing | How do they charge? |
| Positioning | Who do they target? |
| Strengths | What are they good at? |
| Weaknesses | Where do they fall short? |

### Step 3: Compare
Compare to our product:
- Feature gaps (theirs vs ours)
- Pricing comparison
- Target market overlap
- Differentiation opportunities

### Step 4: Generate Insights
Create actionable recommendations:
- What should we copy?
- What should we avoid?
- Where can we differentiate?

## Output

**Location:** `1-[space]/research/competitors/`
**Filename:** `[Competitor] Analysis - [Date].md`

**SUCCESS:**
```json
{
  "status": "completed",
  "output_path": "...",
  "summary": "Analyzed [Competitor]. Key finding: [insight]",
  "actionable_insights": [
    "Consider adding [feature]",
    "Opportunity in [market segment]"
  ]
}
```

**NEEDS_REVIEW:**
```json
{
  "status": "needs_review",
  "output_path": "...",
  "review_notes": "Found conflicting pricing information...",
  "review_questions": [
    "Which pricing tier should we compare against?"
  ]
}
```

## Quality Standards

Mark as "completed" when:
- [ ] All requested URLs fetched
- [ ] Analysis covers all dimensions
- [ ] Comparison to our product included
- [ ] At least 3 actionable insights
- [ ] Sources cited

Mark as "needs_review" when:
- Information is contradictory
- Strategic decision required
- Pricing is complex/unclear

## Boundaries

**YOU CAN:**
- Fetch public competitor information
- Analyze features and pricing
- Generate strategic recommendations

**YOU CANNOT:**
- Access paywalled content
- Make strategic decisions
- Commit to roadmap changes

**YOU MUST:**
- Cite all sources
- Note information gaps
- Flag when human judgment needed
```

## Agent Registration

Agents are automatically available when placed in:

| Location | Scope |
|----------|-------|
| `.datacore/agents/` | All spaces |
| `0-personal/.datacore/agents/` | Personal only |
| `1-[space]/.datacore/agents/` | That space only |

## Task Integration

Users trigger agents via org-mode tags:

```org
* TODO Analyze competitor pricing :AI:competitor:
  URL: https://competitor.com/pricing
  Focus: Enterprise tier comparison
```

The `ai-task-executor` routes to your agent based on the tag.

## Tips

### Be Specific
Vague instructions produce vague results. Specify exact formats, locations, and criteria.

### Handle Failures
Always define what happens when things go wrong. Users need actionable error messages.

### Set Boundaries
Explicit boundaries prevent scope creep and hallucination.

### Use Examples
Show input/output examples. They're worth more than paragraphs of explanation.

### Think in Steps
Break workflows into discrete steps. Easier to debug and maintain.

## Testing Agents

1. Create a test task in `next_actions.org`
2. Tag with your agent's tag
3. Run `ai-task-executor` manually
4. Check output location
5. Iterate on instructions

## Coordinator-Subagent Pattern

For multi-space operations, use the coordinator pattern:

```
command → coordinator → [discover spaces via [0-9]-*/]
                     → subagent (space: 0-personal)
                     → subagent (space: 1-[name])
                     → subagent (space: 2-[name])
```

**Key principles:**
- Coordinators discover spaces dynamically (never hardcode space names)
- Spawn ALL subagents in parallel (single message with multiple Task calls)
- Each subagent accepts `space` parameter and writes to space-specific files
- Coordinator aggregates results and returns summary

**Examples:**
- `journal-coordinator` → `journal-entry-writer` × N
- `session-learning-coordinator` → `session-learning` × N
- `gtd-inbox-coordinator` → `gtd-inbox-processor` × N

See [Agents Reference](agents.md) for full list.

## Next Steps

- [Creating Commands](creating-commands.md) - User-triggered workflows
- [Modules](modules.md) - Packaging agents for distribution
- [Agents Reference](agents.md) - All built-in agents
