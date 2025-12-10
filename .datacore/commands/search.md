# Search

Semantic search across the knowledge base using RAG retrieval.

**Query:** $ARGUMENTS

## Behavior

1. **Run semantic search**:
   ```bash
   datacortex search "$ARGUMENTS" --top 5
   ```

2. **Synthesize a concise answer** (2-3 sentences) from the retrieved documents. This should:
   - Directly answer the query
   - Be conversational and invite follow-up
   - Draw from the actual content found

3. **List sources** as condensed bullets:
   ```
   Sources:
   - [Title] (type) - one-line summary
   - [Title] (type) - one-line summary
   ...
   ```

## Output Format

```
[Concise 2-3 sentence answer synthesized from results]

Sources:
- **Title** (type, score) - brief summary of relevance
- **Title** (type, score) - brief summary of relevance
...

[Engaging question or provocative thought to continue conversation]
```

Then use `AskUserQuestion` to offer options:
- **Save as zettel** - Create a new zettel from this search
- **Tell me more** - Expand on the answer with more detail
- **Done** - End the search

## Examples

```
/search how does GTD weekly review work
/search stoicism and business leadership
/search sleep productivity connection
```

## If No Results

Suggest alternative search terms or note that embeddings may need computing (`datacortex embed`).

## Save as Zettel

After presenting results, offer: "Want me to save this as a zettel?"

If yes:
1. Run search again with `--top 15` to get full source list
2. Create a new zettel in `0-personal/notes/2-knowledge/zettel/` that includes:
   - Synthesized answer as the core content
   - All source zettels linked in Related Concepts
   - References section listing ALL sources (not just top 5) with relevance scores
3. Open the file after creation

**Note:** Console output shows top 5 for readability. Saved zettel includes all sources for completeness.
