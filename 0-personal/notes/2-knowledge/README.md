# 2-knowledge

**Purpose**: Permanent knowledge base - stable, timeless notes organized by content type.

## Overview

This folder contains your **permanent knowledge** - notes that are relatively stable and represent what you know about various topics. Unlike `1-active/` which changes frequently, these notes are refined in place or split into new notes.

## Structure

### journals/
Daily journal entries in ISO date format (YYYY-MM-DD.md). Compatible with both Obsidian and Logseq.

**Format**: Logseq block-based with YAML frontmatter
**Purpose**: Daily capture, agenda tracking, reflection

### zettel/
Atomic knowledge notes following Zettelkasten principles.

**Subfolders**:
- `concepts/` - Pure ideas and concepts (e.g., AGI, Data Economy)
- `frameworks/` - Mental models and strategic frameworks (e.g., Blue Ocean Strategy)
- `tools/` - Tools and services (general, not project-specific)
- `methods/` - Methodologies and processes

**Characteristics**:
- One concept per note
- Self-contained and understandable alone
- Heavily cross-linked via wiki-links
- Minimum 5 tags for discoverability

### literature/
Source material and progressive summarizations.

**Subfolders**:
- `articles/` - Web clippings and article summaries
- `books/` - Book notes and summaries
- `papers/` - Academic papers and research
- `highlights/` - Readwise highlights and annotations

**Purpose**: Capture knowledge from external sources, create literature notes that link to zettels

### reference/
Quick reference material and catalogs.

**Subfolders**:
- `case-studies/` - Examples and real-world applications
- `people/` - People, companies, organizations
- `definitions/` - Terminology and glossary

## Navigation

Use index notes in `_indexes/` to navigate:
- [[INDEX - Master]] - Top-level entry point
- [[INDEX - Concepts]] - Organized by domain
- [[INDEX - Tools]] - Organized by category
- [[INDEX - Literature]] - Organized by source type
- [[INDEX - Frameworks]] - Strategic thinking tools

## Zettelkasten Principles

1. **Atomic**: One idea per note
2. **Connected**: Link liberally to related notes
3. **Own words**: Process and reformulate (don't just copy)
4. **Progressive**: Build on existing notes over time
5. **Discoverable**: Tag extensively (min 5 tags)

## vs. 1-active/

**2-knowledge/**: "What do I know about X?"
- General concepts and ideas
- Source material and summaries
- Reference information
- Relatively stable

**1-active/**: "What am I working on now?"
- Project-specific application notes
- Living strategic documents
- Frequently changing
- Context: specific focus area

## Cross-References

Application notes in `1-active/` frequently reference general concepts here:
- `1-active/datafund/research/Datafund Exa Integration.md` → `2-knowledge/zettel/tools/Exa AI Search Engine.md`

## Maintenance

- **Daily**: Add journal entry, process literature highlights
- **Weekly**: Create new zettels from literature notes
- **Monthly**: Review and refine existing zettels, update index notes
- **Quarterly**: Split overgrown notes, merge duplicates

---

*This folder is part of the Data second brain system*
*Last updated: 2025-11-05*
