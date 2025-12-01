# Agent Context Pattern

**DIP-0016 Reference Pattern**

Use this pattern to declare an agent's knowledge dependencies and integration points.

## Template Section

Add after frontmatter, before main agent content:

```markdown
## Agent Context

### When to Reference [Primary DIP]

**Always reference when:**
- [Scenario 1 from agent's role]
- [Scenario 2 from agent's role]
- [Scenario 3 from agent's role]

**Key decisions this DIP informs:**
- [Decision 1 the DIP guides]
- [Decision 2 the DIP guides]
- [Decision 3 the DIP guides]

### Quick Reference

| Question | Answer |
|----------|--------|
| [Common question 1] | [Answer from agent knowledge] |
| [Common question 2] | [Answer from agent knowledge] |
| [Common question 3] | [Answer from agent knowledge] |
| [Common question 4] | [Answer from agent knowledge] |

### Related DIPs

- [DIP-XXXX](path) - Why it's related
- [DIP-YYYY](path) - Why it's related

### Related Agents

| Agent | Relationship |
|-------|--------------|
| `agent-name` | Spawns this agent / Called by this agent |
| `other-agent` | Shares knowledge domain |

### Integration Points

- **[DIP-XXXX]** - How this agent uses it
- **[DIP-YYYY]** - How this agent uses it
```

## Purpose

The Agent Context section serves multiple purposes:

1. **Self-Documentation** - Agent knows what knowledge it needs
2. **Pre-Fetch Guidance** - Registry uses this for context loading
3. **Human Understanding** - Maintainers know agent's scope
4. **AI Routing** - Other agents know when to invoke this one

## Example: GTD Research Processor

```markdown
## Agent Context

### When to Reference DIP-0009

**Always reference when:**
- Processing :AI:research: tagged tasks
- Creating literature notes from URLs
- Generating atomic zettels from content
- Updating research_learning.org

**Key decisions this DIP informs:**
- Task routing based on AI tags
- Output location for research artifacts
- Integration with GTD daily workflow

### Quick Reference

| Question | Answer |
|----------|--------|
| Where do literature notes go? | `0-personal/3-knowledge/clippings/` |
| Where do zettels go? | `0-personal/3-knowledge/zettel/` |
| What tag triggers me? | `:AI:research:` |
| Who routes tasks to me? | `ai-task-executor` |

### Related DIPs

- [DIP-0009](../dips/DIP-0009-gtd-specification.md) - GTD workflow and task states
- [DIP-0004](../dips/DIP-0004-knowledge-database.md) - Knowledge database structure

### Related Agents

| Agent | Relationship |
|-------|--------------|
| `ai-task-executor` | Routes :AI:research: tasks to me |
| `research-orchestrator` | Spawns me for individual URLs |

### Integration Points

- **DIP-0009** - Receives tasks from GTD next_actions.org
- **DIP-0004** - Writes to Obsidian knowledge database
```

## Implementation Notes

1. **Keep it concise** - This is reference, not documentation
2. **Focus on decisions** - What choices does this inform?
3. **List relationships** - Who spawns/is spawned by this agent?
4. **Reference paths** - Where does this agent read/write?

## Validation

The agent-registry-auditor checks for:

- [ ] Agent Context section exists
- [ ] Quick Reference table present
- [ ] At least one Related DIP
- [ ] Integration Points listed
- [ ] Registry entry matches declarations

## Related Patterns

- **Think-Search-Generate** - Dynamic knowledge retrieval
- **Registry Entry** - Machine-readable version of this
- **Reads Declaration** - Source of truth in registry
