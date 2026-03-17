# Space CLAUDE.base.md Template

This is the canonical template for `CLAUDE.base.md` in Datacore spaces. It replaces the previous 220-line version that duplicated system documentation already covered by root `CLAUDE.md` and module context files.

## Design Principles

1. **No duplication** -- root CLAUDE.md covers GTD, Zettelkasten, org-mode, tags, modules
2. **Action-oriented** -- tells the agent HOW to work here, not what the system is
3. **Compact** -- target 50-60 lines so composed CLAUDE.md stays within budget
4. **Bubble-up ready** -- frontmatter summary for root CLAUDE.md aggregation
5. **Defer to engrams** -- learned preferences live in memory, not static docs

## Template

```markdown
---
summary: "[One-line space description for root CLAUDE.md listing]"
---

# [Space Name] Space

> System docs: ~/Data/CLAUDE.md | Learned preferences: `datacore.recall`

## Purpose

[1-2 sentences: what this space is for and who uses it.]

## Structure

` ` `
[N]-[name]/
├── org/             # Tasks: inbox.org (capture), next_actions.org (action)
├── journal/         # Daily entries: YYYY-MM-DD.md
├── 0-inbox/         # Unprocessed notes — process to zero
├── 1-tracks/        # Active work by area (ops, product, dev, research, comms)
├── 2-projects/      # Code repositories (gitignored)
├── 3-knowledge/     # Knowledge base (pages, zettel, literature, reference)
└── 4-archive/       # Historical content
` ` `

## Working Here

- **Capture**: tasks to `org/inbox.org`, notes to `0-inbox/`
- **Act**: prioritized work in `org/next_actions.org`, organized by track
- **Record**: daily journal in `journal/YYYY-MM-DD.md`
- **Store**: permanent knowledge in `3-knowledge/` (zettel for concepts, pages for documents)
- **AI tasks**: tag with `:AI:` in next_actions.org — processed overnight by agents

## Key Locations

| What | Where |
|------|-------|
| [Important item 1] | `path/to/it` |
| [Important item 2] | `path/to/it` |

## Space Conventions

[Anything specific to THIS space that differs from root conventions.
Examples: GitHub workflow, team communication norms, naming patterns,
domain-specific terminology, voice/tone requirements.]

---

*This is CLAUDE.base.md — the PUBLIC layer. Space details are in CLAUDE.space.md.*
```

## Layer Composition

When composed, a space CLAUDE.md contains (in order):
1. `CLAUDE.base.md` -- this template (generic how-to-work-here)
2. `CLAUDE.space.md` -- space-specific content (team, projects, conventions)
3. `CLAUDE.local.md` -- private notes (gitignored)

The base layer should NOT duplicate content that belongs in space.md.

## Migration

To migrate an existing space from the old 220-line template:
1. Replace `CLAUDE.base.md` with the new template, filled in for the space
2. Verify `CLAUDE.space.md` already contains the space-specific details
3. Run `python .datacore/lib/context_merge.py rebuild --path [space]`
4. Confirm composed `CLAUDE.md` is complete
