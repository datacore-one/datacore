# 1-active

**Purpose**: Living documents organized by focus area - your current active work and projects.

## Overview

This folder contains all notes that are **actively changing** and related to your current focus areas. These are "living documents" that evolve as projects progress, strategies are refined, and work is executed.

## Focus Areas

Each subfolder represents an active focus area aligned with your org-mode GTD system:

### Work Focus Areas

- **datafund/** - Datafund core operations, product development, strategy
- **trading/** - Trading strategies, market analysis, crypto investments

### Personal Focus Areas

- **health-longevity/** - Health optimization, nutrition, exercise, supplements
- **personal-dev/** - Personal development, stoicism, productivity, learning
- **family/** - Family life, parenting, Teo-related notes

### Project Focus Areas

- **mr-data/** - This AI second brain system (prompts, architecture, workflows)

## Folder Structure Pattern

Each focus area follows this pattern:

```
[focus-area]/
├── strategy/          # Strategic planning and direction
├── operations/        # Day-to-day execution and processes
├── research/          # Application notes (how to apply X to this area)
├── competitive/       # Competitive intelligence (business areas)
└── _versions/         # Previous versions of living documents
```

## Versioning

Living documents in this folder are versioned using the `_versions/` subfolder:

**Before major revision**:
```bash
cp current-doc.md _versions/current-doc-v1-2025-11-05.md
```

See [[Versioning Workflow]] for details.

## Cross-References

Notes here frequently reference:
- **2-knowledge/zettel/** - General concepts and tools used in your work
- **2-knowledge/literature/** - Source material and research
- **content/** - Generated reports and summaries

## Index Notes

Each focus area has an index note in `_indexes/`:
- [[INDEX - Datafund]]
- [[INDEX - Trading]]
- [[INDEX - Health & Longevity]]
- [[INDEX - Personal Development]]
- [[INDEX - Mr.Data]]
- [[INDEX - Family]]

## Maintenance

- **Daily**: Add new notes as work progresses
- **Weekly**: Review and update living documents, create versions if major changes
- **Monthly**: Review index notes, archive old versions to `3-archive/dated/`

---

*This folder is part of the Data second brain system*
*Last updated: 2025-11-05*
