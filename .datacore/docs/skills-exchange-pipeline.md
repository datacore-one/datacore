# Skills Exchange Pipeline

Manual path for converting high-fitness engrams into reusable Claude Skills.

## Overview

Engrams that prove valuable across multiple contexts can be promoted to Claude Skills
for broader distribution. This is currently a manual pipeline; automated orchestration
is deferred pending coordinator agent design.

## Pipeline Steps

### 1. Identify High-Fitness Engrams

Scan `engrams.yaml` for candidates meeting these thresholds:

- `status: active` (already approved through daily review)
- `derivation_count >= 3` (validated across multiple sessions)
- `activation.retrieval_strength > 0.5` (actively useful, not decaying)
- `abstract` is not null (has been generalized beyond the original context)

Use the learning-publisher agent to list candidates:
```
Invoke learning-publisher agent — it scans all engrams.yaml files
and calculates fitness scores automatically.
```

### 2. Export as Exchange Pack

The learning-publisher agent packages eligible engrams as exchange packets:

- Strips concrete domain details (privacy-safe)
- Calculates fitness score from adoption, diversity, strength, age
- Writes packets to `.datacore/learning/exchange/outbox/`

Alternatively, use the Datacore MCP `datacore_packs_export` tool to create
a shareable engram pack filtered by domain or tags.

### 3. Convert Behavioral Engrams to SKILL.md

For engrams with fitness_score > 0.8, manually create a Claude Skill:

```
.claude/skills/{skill-name}/
  SKILL.md       # Instructions derived from the engram statement
  examples/      # Concrete instances as usage examples
```

**SKILL.md template:**
```markdown
# {Skill Name}

## When to Use
{Derived from engram's scope and contraindications}

## Instructions
{Derived from engram statement — the behavioral guidance}

## Examples
{Concrete instances from the engram's source_patterns}
```

**Mapping engram fields to skill sections:**
- `statement` -> Instructions (core behavioral guidance)
- `scope` -> When to Use (which agents/commands/contexts)
- `contraindications` -> When to Use (negative conditions)
- `source_patterns` -> Examples (real instances)
- `rationale` -> Instructions (the "why" context)

### 4. Register Skill via Module System

If the skill is module-specific, register it in the module's `manifest.yaml`:
```yaml
skills:
  - name: {skill-name}
    description: {from engram statement}
    path: skills/{skill-name}/SKILL.md
```

For global skills, place in `.claude/skills/` at the Datacore root.

## Current Limitations

- No automated orchestration — each step requires manual invocation
- No feedback loop from skill usage back to engram fitness scoring
- Exchange packets are write-only (no import/subscribe automation yet)
- Coordinator agent for end-to-end pipeline is not yet designed

## Related

- `learning-publisher` agent — fitness scoring and exchange packet creation
- `learning-reviewer` agent — quality gates for engram candidates
- DIP-0019 — Learning Architecture specification
- `datacore_packs_export` MCP tool — engram pack export
