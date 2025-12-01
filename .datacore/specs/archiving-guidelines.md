# Archiving Guidelines

**Version**: 1.1
**Created**: 2025-11-29
**Updated**: 2025-11-29
**Status**: Active

## Purpose

This document defines how content is archived in Datacore. Proper archiving preserves history while keeping active areas clean.

## Core Principles

1. **Single archive location per space**: `3-archive/`
2. **Mirror folder structure**: Archive maintains same path structure as source
3. **Version linking**: Active docs link to archived versions in footer
4. **No nested archives**: Never create `folder/archive/` subfolders in active areas

## When to Archive

Content should be archived when:
- **Superseded**: A newer version exists
- **Completed**: Project/initiative is finished
- **Deprecated**: Approach is no longer used
- **Historical**: Relevant for reference only

## Archive Structure

The archive mirrors the source folder structure:

```
3-archive/
├── 1-departments/
│   ├── dev/
│   │   └── architecture/
│   │       ├── Datacore-Specification-v1.md
│   │       └── Data-Architecture-v0.md
│   └── product/
│       └── Roadmap-Q3-2024.md
├── 2-knowledge/
│   └── literature/
│       └── old-research-note.md
└── README.md
```

**Rule**: If source is `1-departments/dev/architecture/Spec.md`, archive goes to `3-archive/1-departments/dev/architecture/Spec-v1.md`

### Naming Conventions

| Type | Pattern | Example |
|------|---------|---------|
| Versioned doc | `[name]-v[version].md` | `Datacore-Specification-v1.md` |
| Dated content | `[name]-[YYYY-MM].md` | `Roadmap-2024-Q3.md` |
| Simple archive | `[name]-archived.md` | `old-process-archived.md` |

## Version Linking

### In Active Documents

The **current/latest version** must include a footer linking to archived versions:

```markdown
---

## Version History

| Version | Date | Notes |
|---------|------|-------|
| v2 (current) | 2025-11-29 | Major rewrite |
| [[3-archive/1-departments/dev/architecture/Datacore-Specification-v1|v1]] | 2025-09-15 | Initial version |
```

Or simpler format:

```markdown
---

**Previous versions**: [[3-archive/.../Document-v1|v1]] | [[3-archive/.../Document-v0|v0]]
```

### In Archived Documents

Archived documents link forward to the current version:

```markdown
---

> **Archived**: This document has been superseded.
> **Current version**: [[1-departments/dev/architecture/Datacore Specification v2]]
```

## Frontmatter for Archived Files

```yaml
---
title: Original Title
archived: 2025-11-29
archived_from: 1-departments/dev/architecture/
archived_reason: Superseded by v2
superseded_by: [[1-departments/dev/architecture/Datacore Specification v2]]
original_created: 2025-09-15
version: 1
---
```

## Archive Process

### 1. Prepare the Active Document

Before archiving, update the **current version** with version history footer:

```markdown
---

## Version History

| Version | Date | Notes |
|---------|------|-------|
| v2 (current) | 2025-11-29 | [describe changes] |
| [[3-archive/.../Doc-v1|v1]] | 2025-09-15 | Initial version |
```

### 2. Add Archive Frontmatter

Add to the file being archived:

```yaml
---
archived: 2025-11-29
archived_from: [original path]
archived_reason: [reason]
superseded_by: [[path/to/current/version]]
version: [N]
---
```

### 3. Create Mirror Directory

```bash
# Mirror the source path in archive
mkdir -p "3-archive/[same/path/as/source]/"
```

### 4. Move with Version Suffix

```bash
mv "[source-path]/Document.md" "3-archive/[source-path]/Document-v[N].md"
```

### 5. Add Archive Notice

At top of archived file, add:

```markdown
> **Archived**: This document has been superseded.
> **Current version**: [[path/to/current/version]]
```

### 6. Update Archive README

Add entry to `3-archive/README.md`

### 7. Log to Journal

```markdown
## Archiving

Archived [item] v[N]:
- From: [original path]
- To: [archive path]
- Reason: [reason]
- Current version: [link to current]
```

## Examples

### Example: Archiving Spec v1 when v2 is created

**Step 1**: Update current document (`Datacore Specification v2.md`) footer:
```markdown
---

## Version History

| Version | Date | Notes |
|---------|------|-------|
| v2 (current) | 2025-11-29 | Consolidated architecture |
| [[3-archive/1-departments/dev/architecture/Datacore-Specification-v1|v1]] | 2025-09-15 | Initial specification |
```

**Step 2**: Add frontmatter to v1:
```yaml
---
archived: 2025-11-29
archived_from: 1-departments/dev/architecture/
archived_reason: Superseded by v2
superseded_by: [[1-departments/dev/architecture/Datacore Specification v2]]
version: 1
---
```

**Step 3**: Move:
```bash
mkdir -p "3-archive/1-departments/dev/architecture/"
mv "1-departments/dev/architecture/Datacore Specification v1.md" \
   "3-archive/1-departments/dev/architecture/Datacore-Specification-v1.md"
```

## What NOT to Archive

- **Zettel**: Atomic concepts evolve, don't archive
- **Journals**: Historical by nature, stay in place
- **Active docs**: If still being updated, don't archive
- **Literature notes**: Reference material stays in knowledge

## Anti-Patterns

❌ `department/archive/` - No nested archives
❌ Flat archive structure - Mirror source paths
❌ No version links - Always link old ↔ new
❌ Deleting without archiving - Always preserve history
❌ Archiving without reason - Document why

## Validation Checklist

Before completing archive:
- [ ] Mirror folder structure created in 3-archive/
- [ ] Archived file has proper frontmatter
- [ ] Archived file has "superseded" notice at top
- [ ] Current version has version history footer
- [ ] Bidirectional links work (current ↔ archived)
- [ ] Archive README updated
- [ ] Journal entry logged

---

*This spec is enforced by the archiver agent.*

## Version History

| Version | Date | Notes |
|---------|------|-------|
| v1.1 (current) | 2025-11-29 | Added mirror structure and version linking |
| v1.0 | 2025-11-29 | Initial version |
