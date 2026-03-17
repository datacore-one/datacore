# CLAUDE.md

This file teaches you how to work effectively in this Datacore installation.

## Your Memory

You have persistent memory through Datacore MCP — **use it in every session**.

Your memory contains ~330 personal engrams + 780 pack engrams covering infrastructure, past decisions, API quirks, user preferences, project conventions, debugging insights, and more. **This file describes HOW the system works. Your engrams contain WHAT you've learned.**

### Session lifecycle

1. **Start**: Call `datacore.session.start` with a brief task description — this injects relevant engrams
2. **Recall**: Before answering factual questions (server IPs, past decisions, preferences), call `datacore.recall` — the answer is likely in memory, not in this file
3. **Learn**: When corrected or discovering something new, call `datacore.learn`
4. **Feedback**: Rate injected engrams with `datacore.feedback` — this trains relevance
5. **End**: Call `datacore.session.end` with summary before the conversation ends

### What's in your memory

| Domain | Examples | When to recall |
|--------|---------|----------------|
| Infrastructure | Server IPs, SSH configs, deployment targets | Asked about servers, deployment |
| Decisions | Design choices, architecture rationale | "What did we decide about X?" |
| Corrections | API quirks, bugs, wrong assumptions | Working with specific APIs/tools |
| Preferences | Formatting, tone, workflow, tool choices | Generating content, formatting |
| Conventions | DIPs, tag formats, file routing, org-mode rules | System conventions (also below) |

**Do not rely on this file for factual recall.** Call `datacore.recall` or `datacore.search` instead.

## Overview

**Datacore** is a modular AI second brain built on GTD methodology.

- **0-personal/**: Personal space (GTD, PKM, projects)
- **[N]-[name]/**: Team spaces (separate repos)

### Modules

Datacore is extensible via **modules** — self-contained packages that add agents, commands, tools, and context. Each module lives in `.datacore/modules/<name>/` with a `module.yaml` manifest. Modules can hook into commands (e.g., adding a section to `/today`), register agents, and provide MCP tools. Use `datacore.modules.list` to see installed modules, `datacore.modules.info` for details.

### Structure

```
~/Data/
├── .datacore/              # System: commands, agents, modules, specs, lib
├── 0-personal/             # Personal: org/ (GTD), notes/ (PKM), code/
├── [N]-[name]/             # Team spaces (separate repos)
└── sync                    # Repo sync script
```

> Detailed conventions are in your engram memory (DIP pack). Call `datacore.recall` for specifics.

## Finding Things

### Commands & Agents

Commands and agents are registered in `.datacore/registry/`. Don't memorize them — look them up:
- `datacore.recall` for agent/command details by name or purpose
- `.datacore/registry/commands.yaml` for all commands
- `.datacore/registry/agents.yaml` for all agents (100+)
- `datacore.modules.list` / `datacore.modules.info` for module capabilities

Slash commands (e.g., `/today`, `/research`, `/wrap-up`) are multi-phase workflows. Conversational commands work by saying what you need ("process inbox", "weekly review").

### Knowledge Base

When working on a task, check for relevant existing knowledge:
- `datacore.search` or `datacore.recall` — search across engrams and knowledge
- `[space]/3-knowledge/` — permanent knowledge (zettel, literature, reference, pages)
- `[space]/notes/` or `[space]/journal/` — journals and working notes
- `[space]/1-tracks/` or `[space]/1-active/` — active work by area

Don't start from scratch when context might already exist.

## Installed Modules

<!-- REGISTRY:modules -->

## MCP Sources & Services

<!-- REGISTRY:sources -->

## Infrastructure

<!-- REGISTRY:infrastructure -->

> Server IPs, SSH configs, deployment procedures, and service details are in your engram memory. Call `datacore.recall` with domain "infrastructure". Do NOT guess IPs — always verify via recall.

## Working with Spaces

### Personal (0-personal/)

- `org/inbox.org` — single capture point (sacred — always return to clean)
- `org/next_actions.org` — tasks with `:AI:` tags for overnight delegation
- `notes/` — Obsidian PKM (journals, pages, knowledge)

### Team Spaces ([N]-[name]/)

Separate git repos. GitHub Issues are source of truth. `org/` routes AI work only.

## org-mode Conventions

- Headings: `*` per level. States: TODO, NEXT, WAITING, DONE
- Properties: `:PROPERTIES:` ... `:END:`. Tags: `:tag1:tag2:`
- Timestamps: `<2026-03-17 Tue>`. **Always verify day-of-week** — LLMs get these wrong:
  `python3 -c "from datetime import date; print(date(YYYY,M,D).strftime('%a'))"`

**AI Task Tags**: `:AI:` (general), `:AI:research:`, `:AI:content:`, `:AI:data:`, `:AI:pm:`, `:AI:technical:` (human review)

> Full org-mode and GTD conventions are in engram memory (DIP pack, 747 engrams). Call `datacore.recall` for detailed rules.

## Notes & Tags

- Wiki-links: `[[Page Name]]`. Journal: `YYYY-MM-DD.md`
- Tags: inline `#tag` in PKM/CRM, `:tag:` in org-mode, single values in frontmatter (NOT arrays)
- Registries: `.datacore/tags.yaml` (system), `[space]/.datacore/tags.yaml` (space)

## Sync & Bash

```bash
./sync          # Pull all repos
./sync push     # Commit and push all
```

- **Never use multi-line Bash commands.** Chain with `&&`.
- Use dedicated tools: `Glob` not `find`, `Read` not `cat`, `Grep` not `grep`.

## Key Principles

- **Augment, don't replace** — agents assist, humans decide
- **Progressive processing** — inbox → triage → knowledge → archive
- **Single capture point** — inbox.org, then route and remove
- **org-mode for AI** — internal coordination and task routing
- **GitHub for teams** — external collaboration via Issues

## System Patterns (DIPs)

Datacore follows **Datacore Improvement Proposals** for system changes. 15 DIPs cover: contribution model, layered context, tag taxonomy, agent registry, knowledge management, GTD workflow, nightshift execution, and more. Located in `.datacore/dips/`.

To propose changes: branch → copy `DIP-0000-template.md` → fill in → PR.

> All DIP content is in your engram memory (dips-v1 pack, 747 engrams). Call `datacore.recall` with the relevant topic rather than reading DIP files for quick lookups.

### Layered Context (DIP-0002)

All context files use layered privacy: `.base.md` (public) → `.org.md` → `.team.md` → `.local.md` (private). Composed `.md` is gitignored. Rebuild: `python .datacore/lib/context_merge.py rebuild --path .`

## Privacy

See `.datacore/specs/privacy-policy.md` for data classification.

---

**This is CLAUDE.base.md** — the PUBLIC layer. Customize with `CLAUDE.org.md` or `CLAUDE.local.md` (gitignored).
