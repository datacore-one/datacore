# Project CLAUDE.md Template

Template for project repositories inside Datacore spaces (`[space]/2-projects/[project]/`).

## Usage

Copy the Datacore section below into your project's CLAUDE.md (at the end, after project-specific content). Replace the placeholder values.

If the project has no CLAUDE.md yet, use the full template.

---

## Full Template (new projects)

```markdown
# CLAUDE.md

## [Project Name]

[One-paragraph description of what this project does.]

## Development

[How to build, test, run. Key commands.]

## Key Files

[Most important files/directories for orientation.]

## Datacore Space Context

This project lives inside a Datacore space. Session lifecycle commands are available:

- `/wrap-up` — write session entry to team journal, commit and push
- `/continue` — resume from yesterday's continuation notes; `--save` persists current work
- `/standup` — generate/post standup from recent team journals
- `/today` — daily briefing (incremental if already generated)

| Key | Value |
|-----|-------|
| Space | [N]-[name] (e.g., `1-datafund`) |
| Journal | `~/Data/[space]/journal/YYYY-MM-DD.md` |
| Org | `~/Data/[space]/org/next_actions.org` |
| Schema | `~/Data/.datacore/templates/journal-schema.md` |

When writing journal entries during `/wrap-up`, use the team journal schema:
- `## @contributor` sections with narrative (what, why, decisions, continuation)
- `## Session Metadata` YAML block (artifacts, tokens, git refs)

Contributor name comes from `git config user.name`.
```

## Append-only Section (existing projects)

For projects that already have a CLAUDE.md, append this section:

```markdown
## Datacore Space Context

This project lives inside a Datacore space. Session lifecycle commands are available:

- `/wrap-up` — write session entry to team journal, commit and push
- `/continue` — resume from yesterday's continuation notes; `--save` persists current work
- `/standup` — generate/post standup from recent team journals
- `/today` — daily briefing (incremental if already generated)

| Key | Value |
|-----|-------|
| Space | [SPACE_DIR] |
| Journal | `~/Data/[SPACE_DIR]/journal/YYYY-MM-DD.md` |
| Org | `~/Data/[SPACE_DIR]/org/next_actions.org` |

When `/wrap-up` runs, use the team journal schema: `## @contributor` narrative sections + `## Session Metadata` YAML block.
```
