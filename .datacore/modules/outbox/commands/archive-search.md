---
name: archive-search
description: Search archived content using datacortex snapshots
user_invocable: true
recall:
  # DIP-0029 default — engrams scoped to this command + tag-matched.
  scopes:
    - command:archive-search
  tags:
    - archive-search
---

# Command: /archive-search

Search archived content using semantic similarity via datacortex embeddings.

## Command Context

### When to Reference DIP-0017

**Always reference when:**
- Searching historical content
- Finding archived documents
- Retrieving content from server archives

**Key decisions this DIP informs:**
- Archive snapshots use datacortex embeddings
- Snapshots sync from server to local for search
- Full content fetched on-demand via SSH

### Quick Reference

| Question | Answer |
|----------|--------|
| What gets searched? | Datacortex embedding snapshots |
| Where are snapshots? | `[space]/.archive-snapshot/` (synced from server) |
| What's the embedding model? | sentence-transformers/all-mpnet-base-v2 |
| Can I get full content? | Yes, via `--fetch` option |

### Integration Points

- **DIP-0017** - Archive snapshot specification
- **DIP-0004** - Datacortex embedding patterns

---

## Usage

```
/archive-search <query>                    # Search all archives
/archive-search --space <name> <query>     # Search specific space
/archive-search --reindex                  # Rebuild index (runs archive-indexer)
```

## Examples

```
/archive-search "investor agreement terms"
/archive-search --space 1-teamspace "2019 financial statements"
/archive-search --fetch "1-tracks/legal/contracts/acme-2018.pdf"
/archive-search --sync                     # Sync snapshots from server first
```

## Workflow

### Phase 1: Snapshot Check

1. **Check local snapshots** in `[space]/.archive-snapshot/`
2. **If missing or stale**, offer to sync from server:
   ```
   Archive snapshots not found locally.
   Sync from server? [Y/n]
   ```

### Phase 2: Semantic Search

1. **Embed query** using datacortex model
2. **Search each space's snapshot**:
   - Load embeddings from `archive.db`
   - Compute cosine similarity
   - Rank by score
3. **Return top results** with excerpts

### Phase 3: Content Retrieval (Optional)

If `--fetch` specified:
1. SSH to server
2. Cat file content
3. Return to user

## Options

| Option | Description |
|--------|-------------|
| `--space <name>` | Search only specified space's archive |
| `--limit <n>` | Maximum results (default: 10) |
| `--threshold <n>` | Minimum similarity score (default: 0.5) |
| `--fetch <path>` | Fetch full content of specific file |
| `--sync` | Sync snapshots from server before search |
| `--reindex` | Trigger archive-indexer to rebuild index |
| `--json` | Output as JSON |

## Output

```
Archive Search: "investor agreement"
====================================
Searched 856 chunks in ['0-personal', '1-teamspace']

1. 1-teamspace-archive/1-tracks/legal/contracts/investors/acme-2018.pdf
   Score: 0.89
   Investment agreement with ACME Corp, Series A, $500K commitment
   with standard preferred terms and anti-dilution provisions...

2. 1-teamspace-archive/1-tracks/legal/term-sheets/2017-series-seed.pdf
   Score: 0.75
   Term sheet for seed round with standard investor terms including
   board seat and information rights...

3. 0-personal-archive/3-knowledge/literature/vc-term-guide.md
   Score: 0.68
   Guide to understanding venture capital term sheets and common
   investor agreement clauses...

Use /archive-search --fetch <path> to retrieve full content.
```

## JSON Output

```json
{
  "query": "investor agreement",
  "spaces_searched": ["0-personal", "1-teamspace"],
  "total_searched": 856,
  "results": [
    {
      "file_path": "1-tracks/legal/contracts/investors/acme-2018.pdf",
      "space": "1-teamspace",
      "score": 0.89,
      "excerpt": "Investment agreement with ACME Corp..."
    }
  ]
}
```

## Technical Architecture

### Snapshot Structure

```
[space]/.archive-snapshot/           # Synced from server
├── archive.db                       # SQLite with embeddings
│   └── embeddings (
│         file_path TEXT,
│         chunk_index INTEGER,
│         chunk_text TEXT,
│         embedding BLOB,
│         created_at TIMESTAMP
│       )
└── manifest.yaml                    # Index metadata
```

### Embedding Process

1. **Chunking**: 1000 chars with 200 overlap
2. **Model**: sentence-transformers/all-mpnet-base-v2
3. **Storage**: Float32 vectors as BLOB
4. **Search**: Cosine similarity

### Library

`lib/archive_search.py` provides:

```python
from archive_search import search_archives, SearchResults

results = search_archives(
    query="contract terms",
    spaces=["1-teamspace"],
    limit=10
)

for r in results.results:
    print(f"{r.space}/{r.file_path}: {r.score:.2f}")
```

**CLI Usage:**
```bash
python lib/archive_search.py search "contract terms" --limit 5
python lib/archive_search.py sync --space 0-personal
python lib/archive_search.py index 0-personal ~/Data/0-personal-archive
```

## Error Handling

| Error | Recovery |
|-------|----------|
| Snapshot not found | Offer to sync from server |
| Server unreachable | Search available local snapshots |
| No results | Suggest broader query or different space |
| Datacortex unavailable | Report error, suggest installing deps |

## Agent

Invokes `archive-indexer` when `--reindex` specified.

## Reference

See [DIP-0017: Outbox & Archive Pattern](../../../dips/DIP-0017-outbox-archive-pattern.md) for full specification.

## Related

- [/outbox](./outbox.md) - Process outbox queue
- [archive-indexer](../agents/archive-indexer.md) - Index maintenance
- [outbox-processor](../agents/outbox-processor.md) - Archive routing
