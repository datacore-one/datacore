---
name: search
description: Multi-source semantic search across local knowledge (Datacortex) and web intelligence (Perplexity)
user_invocable: true
recall:
  # DIP-0029 default — engrams scoped to this command + tag-matched.
  scopes:
    - command:search
  tags:
    - search
---

# Search

## Command Context

### When to Reference DIP-0021

**Always reference when:**
- Running multi-source search queries
- Combining internal + external results
- Enforcing latency contracts
- Offering /research for deeper exploration

**Key decisions this DIP informs:**
- Source registry determines which sources to query
- Timeout enforcement (5 second max)
- Graceful degradation when external sources unavailable
- Synthesis format: internal-first, then external enrichment

### When to Reference DIP-0004

**Always reference when:**
- Performing Datacortex semantic search
- Synthesizing answers from documents
- Offering zettel creation
- Checking embedding status

### Quick Reference

| Question | Answer |
|----------|--------|
| Search engine (internal)? | `datacortex search` |
| Search engine (external)? | Perplexity via MCP (`perplexity_search`) |
| Source registry? | `.datacore/registry/sources.yaml` |
| Settings? | `.datacore/settings.yaml` (`search.timeout_ms`) |
| Timeout? | 5000ms (configurable) |
| Default internal results? | Top 5 |
| What DIPs govern this? | DIP-0021, DIP-0004 |

### Agents This Command Invokes

| Agent | Purpose |
|-------|---------|
| (none) | Direct datacortex + MCP tool calls |

### Integration Points

- **DIP-0021** - Multi-source search architecture
- **DIP-0004** - Datacortex retrieval
- **Source Registry** - `.datacore/registry/sources.yaml`

---

Multi-source semantic search: local knowledge (Datacortex) + web intelligence (Perplexity).

**Query:** $ARGUMENTS

## Reads (at startup)

1. `.datacore/registry/sources.yaml` — identify sources with `layers` containing `search` and valid API keys
2. `.datacore/settings.yaml` — read `search.timeout_ms` (default 5000)

## Behavior

### Step 1: Run Searches in Parallel

**Internal (always):**
```bash
datacortex search "$ARGUMENTS" --top 5
```

**External (if available):**
Check sources.yaml for sources with `layers: [search]` and valid API keys. For each available source within the latency budget (`max_latency_ms` < `timeout_ms`):

- **Perplexity** (`perplexity_search`): AI-synthesized web results with citations

Run internal and external searches **in parallel**. If an external source times out or fails, skip it with a note — never block on external failures.

### Step 2: Synthesize Combined Answer

Combine results following this structure:

**Lead with internal knowledge** (when relevant results found):
- "You have notes on this..." / "Your knowledge base contains..."
- Reference specific literature notes or zettels found

**Enrich with external intelligence** (when external results available):
- "Recent developments include..." / "Current web sources indicate..."
- Include citations from Perplexity

**Flag contradictions** (when internal and external disagree):
- "Note: Your zettel [[X]] says [A], but current sources indicate [B]"

### Step 3: List Sources

```
Sources:
- [local] **Title** (type, score) - brief summary
- [local] **Title** (type, score) - brief summary
- [web] **Title** - brief summary [citation URL]
- [web] **Title** - brief summary [citation URL]
```

### Step 4: Offer Actions

Use `AskUserQuestion` to offer options:
- **Save as zettel** - Create a new zettel from this search
- **Go deeper (/research)** - Launch full research pipeline on this topic
- **Done** - End the search

## Output Format

```
[Synthesized answer combining internal knowledge and web intelligence.
2-4 sentences. Internal knowledge first, external enrichment second.]

Sources:
- [local] **Title** (type, score) - brief summary
- [web] **Title** - brief summary [URL]
...

[Engaging follow-up question or note about contradictions/gaps]
```

## Graceful Degradation

**No Perplexity API key or MCP tool unavailable:**
- Fall back to Datacortex-only (current behavior)
- Note: "Showing local results only. Configure Perplexity for web-enriched search."

**Perplexity times out (>5s):**
- Show Datacortex results immediately
- Note: "External search timed out; showing local results only."

**Perplexity returns error:**
- Show Datacortex results
- Note: "Web search unavailable; showing local results."

**No Datacortex results:**
- Show Perplexity results only
- Note: "No matching notes found locally."
- Suggest: "Run `datacortex embed` to update your knowledge index."

**Both fail:**
- Report the issue
- Suggest alternative search terms or `/research` for deeper exploration

## Latency Contract

**Must respond within 5 seconds.** Only sources with `max_latency_ms` under the configured `search.timeout_ms` are eligible. Slow sources are skipped with a note.

## Examples

```
/search how does GTD weekly review work
/search stoicism and business leadership
/search rapamycin dosing protocols
/search SOL market sentiment
```

## Save as Zettel

If user selects "Save as zettel":
1. Run datacortex search again with `--top 15` for full source list
2. Create a new zettel in `0-personal/3-knowledge/zettel/` that includes:
   - Synthesized answer as the core content
   - All source zettels linked in Related Concepts
   - Web sources in References section
   - Tags applied via tag-suggester
3. Open the file after creation

## Go Deeper (/research)

If user selects "Go deeper":
- Launch `/research $ARGUMENTS` which invokes the research-orchestrator
- This transitions from the search layer to the research layer (DIP-0021)
