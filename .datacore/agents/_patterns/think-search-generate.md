# Think-Search-Generate Pattern

**DIP-0016 Reference Pattern**

Use this pattern when an agent needs to dynamically retrieve knowledge during execution.

## Template Section

Add this section to agents that would benefit from dynamic knowledge retrieval:

```markdown
## Execution Pattern: Think-Search-Generate

### Phase 1: Think

Before taking action, analyze:

1. **What is the task asking?** - Core objective
2. **What do I already know?** - From required reads and context
3. **What am I missing?** - Knowledge gaps that need filling
4. **What could go wrong?** - Risks and edge cases

### Phase 2: Search

For each knowledge gap identified, query datacortex:

```bash
datacortex search "<specific query>" --top 5
```

**Query formulation tips:**
- Be specific: "GTD inbox processing workflow" not just "GTD"
- Include context: "project-alpha pricing strategy" not just "pricing"
- Use domain terms: "zettel atomic note pattern" not just "note taking"

### Phase 3: Generate

With retrieved context, proceed with the task:

1. Synthesize knowledge from search results
2. Apply to the specific task at hand
3. Document any new insights for future retrieval
```

## When to Use

Apply this pattern to agents that:

- Answer questions based on knowledge base
- Need up-to-date information
- Make decisions based on prior context
- Generate content referencing existing work

## When NOT to Use

Skip this pattern for agents that:

- Execute deterministic workflows
- Have all required context in reads.required
- Are time-critical (search adds latency)
- Don't benefit from dynamic knowledge

## Implementation Notes

1. **First hop is always vector search** - Fast, gets initial candidates
2. **Graph expansion for related concepts** - Use `--expand` flag
3. **Limit to top 5 results** - More overwhelms context window
4. **Cache common queries** - Datacortex caches embeddings

## Example: Research Processor with Pattern

```markdown
## Execution Pattern: Think-Search-Generate

### Phase 1: Think

Given task: "Research competitor X's pricing model"

- Objective: Understand pricing structure and positioning
- Known: Competitor name from task
- Gaps: Current pricing, our existing notes on this competitor
- Risks: Outdated info, missing context

### Phase 2: Search

```bash
# Check for existing notes
datacortex search "competitor X pricing notes" --top 3

# Get related strategy context
datacortex search "pricing strategy frameworks" --top 3
```

### Phase 3: Generate

Synthesize: Found existing competitive analysis note + pricing framework zettel.
Apply: Use framework to analyze new pricing page.
Document: Create literature note + zettel with insights.
```

## Related Patterns

- **Agent Context Section** - Static knowledge declaration
- **Multi-Hop Reasoning** - Follow links across documents
- **Session Memory** - Retrieve from past agent executions
