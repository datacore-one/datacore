# Page References and Tagging Guidelines

**Version**: 2.0
**Created**: 2025-11-29
**Updated**: 2025-11-29
**Status**: Active

## Core Principle: Roam-Style References

All connections in the knowledge graph are **page references**. Following Roam Research's model:

| Syntax | Example | Creates/Links To |
|--------|---------|------------------|
| `[[Page]]` | `[[AI Safety]]` | Page "AI Safety" |
| `#tag` | `#strategy` | Page "strategy" |
| `#[[Multi Word]]` | `#[[Data Ownership]]` | Page "Data Ownership" |

**Key insight**: `#tag`, `#[[tag]]`, and `[[page]]` are ALL equivalent - they create or reference pages. The graph emerges from these connections.

## Current Stats (2025-11-29)

```
Total references: 254,677
  [[]] wiki-links: 249,237 (97.9%)
  #tag hashtags:     5,000 (2.0%)
  #[[]] hashtag-bracket: 440 (0.2%)
```

## When to Use Each Syntax

### `[[Page Reference]]` - Primary
Use for most references, especially:
- Concepts and topics: `[[Token Economy]]`
- People: `[[Vitalik Buterin]]`
- Projects: `[[Datafund]]`
- Cross-references: `See [[Related Topic]]`

### `#tag` - Inline Categorization
Use for single-word inline tags:
- `#strategy` - categorize content
- `#draft` - status indicators
- `#question` - content type markers

### `#[[Multi Word Tag]]` - Inline Multi-Word
Use for multi-word inline categories:
- `#[[Data Ownership]]`
- `#[[Risk Management]]`
- `#[[Open Questions]]`

## Frontmatter Tags vs Content References

### Frontmatter Tags (YAML)
```yaml
---
tags: [blockchain, web3, strategy]
---
```
- Metadata about the file
- Used for filtering/search
- Normalized automatically

### Content References
```markdown
This relates to [[Blockchain]] and #web3 concepts.
```
- Creates graph connections
- Builds the knowledge network
- Each becomes a potential page

## Tag Format Rules

### 1. Case
- **Rule**: Any case works (normalized for search)
- **Best practice**: Sentence case for readability
- **Examples**: `[[AI Safety]]`, `#strategy`, `#[[Data Market]]`

### 2. Multi-Word
- **`[[Page]]`**: Use spaces: `[[Smart Contract]]`
- **`#tag`**: Single words only: `#blockchain`
- **`#[[tag]]`**: Use for multi-word: `#[[Risk Management]]`

### 3. Consistency
- Pick one form and use it consistently
- System deduplicates case variants
- `[[AI Safety]]` and `[[ai safety]]` link to same page

## Normalization (Automatic)

The system normalizes for search/matching:

```python
# All these reference the same concept:
[[AI Agent]]    # -> "ai-agent"
[[ai agent]]    # -> "ai-agent"
[[AI-Agent]]    # -> "ai-agent"
#ai-agent       # -> "ai-agent"
```

### Rules Applied
1. Lowercase conversion
2. Spaces/underscores -> hyphens
3. Remove special characters
4. Basic singularization (strategies -> strategy)

## Stub Creation

When a reference has no corresponding page:
1. System creates a stub with backlinks
2. Stub marked as `status: stub`
3. Shows "Referenced By" section

```markdown
---
id: ai-safety
title: AI Safety
status: stub
---

# AI Safety

> This is a stub. Referenced but not yet documented.

## Referenced By
- [[AI Ethics]]
- [[Model Alignment]]
```

## Database Schema

References stored in `links` table:

```sql
CREATE TABLE links (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,
    target_id TEXT,
    target_title TEXT NOT NULL,
    syntax TEXT DEFAULT 'wiki-link',  -- wiki-link, hashtag, hashtag-bracket
    resolved BOOLEAN DEFAULT 0,
    FOREIGN KEY (source_id) REFERENCES files(id),
    FOREIGN KEY (target_id) REFERENCES files(id)
);
```

## Querying References

### All references to a topic
```sql
SELECT f.title, l.syntax FROM links l
JOIN files f ON l.source_id = f.id
WHERE l.target_title = 'AI Safety';
```

### Reference syntax distribution
```sql
SELECT syntax, COUNT(*) FROM links
GROUP BY syntax ORDER BY COUNT(*) DESC;
```

### Unresolved references (potential stubs)
```sql
SELECT target_title, COUNT(*) as refs
FROM links WHERE resolved = 0
GROUP BY target_title
ORDER BY refs DESC;
```

## Best Practices

1. **Use `[[]]` for concepts** - Creates clear, clickable links
2. **Use `#tag` for categories** - Quick inline classification
3. **Be consistent** - Same concept = same reference format
4. **Let stubs emerge** - Reference freely, document later
5. **Check existing pages** - Before creating, search for similar

## Migration Notes

- Previous system only captured `[[wiki-links]]`
- Now captures `#tag` and `#[[tag]]` as well
- All three are unified as page references
- Existing content benefits immediately

---

*Implemented in `zettel_processor.py` - Roam-style reference extraction.*
