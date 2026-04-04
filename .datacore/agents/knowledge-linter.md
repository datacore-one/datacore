# Knowledge Linter

Semantic health checks for the knowledge base. Combines deterministic
script checks (orphans, completeness, staleness) with LLM-powered
contradiction detection.

## When to Use

- Weekly review (scheduled lint pass)
- Manual `/knowledge-lint` command
- After large ingestion batches

## Workflow

### Phase 1: Script Checks (deterministic)

Run `knowledge_lint.py` on `[space]/3-knowledge/`:
- Orphan zettels (no inbound links)
- Incomplete literature notes (missing required sections)
- Stale seedlings (unchanged 180+ days)

Present findings with severity and suggestions.

### Phase 2: Contradiction Detection (LLM-powered, optional)

Only runs if user requests `--deep` or during monthly review.

1. Load all zettels for the space
2. Group by tag/topic (use frontmatter tags)
3. For each group, read all zettels and check for:
   - Direct contradictions (A claims X, B claims not-X)
   - Superseded claims (newer source overrides older)
   - Definitional drift (same term defined differently)
4. Present contradictions with source citations
5. User decides: update zettel, archive one, or mark as "contested"

### Phase 3: Suggestions

Based on findings, suggest:
- Sources to re-ingest for incomplete literature notes
- Zettels to cross-link for orphans
- Stale seedlings to review or archive
- Missing zettels for concepts mentioned but not yet created

## Integration

- **Weekly review**: Runs Phase 1 automatically, Phase 2 on request
- **structural-integrity**: Complements (structure vs semantics)
- **Datacortex**: Uses backlink data for orphan detection when available
