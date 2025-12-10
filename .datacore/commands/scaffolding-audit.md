# Scaffolding Audit Command

Run a comprehensive scaffolding audit against DIP-0003 requirements.

## Usage

```
/scaffolding-audit [space] [--generate]
```

**Arguments**:
- `space`: Target space to audit (default: current space or `1-datafund`)
- `--generate`: Also generate draft documents for gaps (optional)

**Examples**:
```
/scaffolding-audit                    # Audit current space
/scaffolding-audit 1-datafund         # Audit Datafund space
/scaffolding-audit 1-datafund --generate  # Audit and generate drafts
```

## What This Command Does

### 1. Audit Phase

1. **Check Indexes**: Verify `_index.md` files exist in key folders
2. **Read Scaffolding Status**: Load SCAFFOLDING.base.md and SCAFFOLDING.space.md
3. **Inventory Documents**: Compare expected vs existing documents
4. **Calculate Coverage**: Generate coverage metrics by category

### 2. Discovery Phase

1. **Scan Target Space**: Search knowledge base for existing content
2. **Scan 0-personal**: Check personal notes for strategic content
3. **Scan ChatGPT Exports**: Look for relevant insights
4. **Follow Wiki-Links**: Traverse connections for topic discovery

### 3. Report Phase

Generate report in `[space]/0-inbox/report-YYYY-MM-DD-scaffolding-audit.md`:
- Coverage by category
- Gaps identified (prioritized)
- Sources discovered for each gap
- Recommendations

### 4. Generation Phase (if --generate)

For each gap:
1. Synthesize from discovered sources
2. Use Title Case naming
3. Add proper frontmatter (`status: draft`)
4. Include source attribution
5. Update SCAFFOLDING.space.md

## Output

### Audit Report Format

```markdown
# Scaffolding Audit Report

**Space**: [space-name]
**Date**: YYYY-MM-DD
**Coverage**: X% (before) → Y% (after)

## Summary

| Category | Before | After | Change |
|----------|--------|-------|--------|
| Identity | 40% | 100% | +60% |
...

## Gaps Identified

### High Priority
1. Mission.md - Identity - Sources: [Credo.md, Core-Purpose zettel]
...

## Documents Created (if --generate)

| Document | Category | Path |
|----------|----------|------|
| Mission.md | Identity | 3-knowledge/pages/_core/ |
...

## Next Steps

1. Review draft documents
2. Move to `status: review` when ready
3. Get team approval
4. Update to `status: active`
```

## DIP-0003 Categories Checked

| Category | Key Documents |
|----------|---------------|
| **Identity** | Mission, Vision, Values, Principles |
| **Strategy** | Positioning, North Star Metric |
| **Brand** | Brand Guidelines, Voice & Tone, Glossary |
| **Sensing** | Competitor Map, Market Landscape |
| **Memory** | Zettel index, Literature, Reference |
| **Reasoning** | ADR template, Analysis frameworks |
| **Action** | Playbooks, Processes |
| **Learning** | Patterns, Corrections, Preferences |
| **Coordination** | Communication Guide, Handoffs |
| **Metrics** | OKRs, Health Metrics, North Star tracking |

## Project Modules Checked

For each project in `2-projects/`:
- README.md (required)
- CLAUDE.md (required)
- CANVAS.md (required)
- ROADMAP.md (recommended)
- IMPLEMENTATION.md (recommended)
- docs/setup-guide.md (required)

## Conventions Applied

### Naming
- **Scaffolding docs**: Title Case (`Mission.md`)
- **Regular notes**: lowercase (`meeting-notes.md`)

### Frontmatter
```yaml
---
scaffolding: required|recommended
category: identity|strategy|...
status: draft
created: YYYY-MM-DD
reviewer: null
reviewed_date: null
---
```

## When to Run

- **Weekly**: During GTD weekly review
- **Monthly**: During strategic planning
- **On-demand**: After major content changes
- **New space**: During space setup

## Related

- `/gtd-weekly-review` - Includes scaffolding check
- `scaffolding-auditor` agent - Underlying agent
- DIP-0003 - Full specification

## Agent

This command invokes the `scaffolding-auditor` agent with the specified parameters.

```
Agent: scaffolding-auditor
Input: space=[space], generate=[true/false]
Output: Audit report in 0-inbox, updated SCAFFOLDING.space.md
```
