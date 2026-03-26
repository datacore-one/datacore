# CLAUDE.md

This file teaches you how to work effectively in this Datacore installation.

## Your Memory

You have persistent memory through Datacore MCP — **use it in every session**.

~330 personal engrams + 780 pack engrams covering infrastructure, past decisions, API quirks, user preferences, project conventions, and more. **This file describes HOW the system works. Your engrams contain WHAT you've learned.**

1. **Start**: Call `datacore.session.start` with task description — injects relevant engrams
2. **Recall**: Before answering factual questions, call `datacore.recall` — the answer is in memory, not here
3. **Learn**: When corrected or discovering something new, call `datacore.learn`
4. **Feedback**: Rate injected engrams with `datacore.feedback` — trains relevance
5. **End**: Call `datacore.session.end` with summary before conversation ends

| Domain | When to recall |
|--------|----------------|
| Infrastructure | Server IPs, SSH configs, deployment targets |
| Decisions | Past design choices, architecture rationale |
| Corrections | API quirks, bugs, wrong assumptions |
| Preferences | Formatting, tone, workflow, tool choices |
| Conventions | DIPs, tag formats, file routing, org-mode rules |

## Methodologies

Datacore combines established methodologies with AI augmentation:

- **GTD (Getting Things Done)** — task management. Single capture point (`inbox.org`), clarify/organize into `next_actions.org`, `:AI:` tags delegate to agents overnight. Weekly reviews maintain the system.
- **Zettelkasten** — knowledge management. Atomic notes (`zettel/`), literature summaries (`literature/`), reference entries (`reference/`), wiki pages (`pages/`). Cross-linked for emergent connections.
- **Engram memory** — AI learning. Corrections, preferences, and patterns persist across sessions via Datacore MCP. Three loops: capture → absorption → application.
- **Modular architecture** — extensibility. Self-contained modules add domain capabilities. Fork-and-overlay contribution model (DIP-0001).

## Spaces

| Space | Purpose | Key Projects |
|-------|---------|-------------|
| **0-personal** | GTD, PKM, personal projects | Trading, health tracking |
| **1-datafund** | Fair data economy, data tokenization | Verity (data marketplace), Santorio, Dubai pilot |
| **2-datacore** | AI second brain system development | datacore-mcp, org-workspace, datacore-bench |
| **3-fds** | Fair Data Society — data sovereignty building blocks | Fairdrop, ADE (agent data exchange), FDS-ID |
| **4-forge** | Autonomous digital product business | Etsy products, AI-generated goods |

Each space is a separate git repo with its own CLAUDE.md, org files, knowledge base, and journal. When working in a space, its CLAUDE.md loads automatically with space-specific context.

### Personal (0-personal/)

- `org/inbox.org` — single capture point (sacred — always return to clean)
- `org/next_actions.org` — tasks with `:AI:` tags for overnight delegation
- `notes/` — Obsidian PKM (journals, zettel, literature, pages)

### Team Spaces ([N]-[name]/)

GitHub Issues are source of truth. `org/` routes AI work only. `1-tracks/` organizes by department. `3-knowledge/` is the shared Zettelkasten.

## Finding Things

### Commands & Agents

100+ agents and 40+ commands registered in `.datacore/registry/`. Don't memorize — look up:
- `datacore.recall` — search by name or purpose
- `datacore.modules.info <name>` — module capabilities, agents, commands
- `.datacore/registry/agents.yaml` / `commands.yaml` — full registries

Slash commands (`/today`, `/research`, `/wrap-up`) are multi-phase workflows. Conversational commands work naturally — "process inbox", "weekly review", "sync repos".

### Knowledge Base

Before starting work, check for existing knowledge:
- `datacore.search` — semantic search across engrams AND knowledge files (uses Datacortex embeddings)
- `datacore.recall` — targeted engram retrieval by domain or keywords
- `[space]/3-knowledge/` — permanent knowledge: `zettel/` (concepts), `literature/` (sources), `reference/` (people, companies), `pages/` (wiki)
- `[space]/notes/` or `[space]/journal/` — working notes and daily journals

Don't start from scratch when context might already exist.

## Modules

Datacore is extensible via **modules** — self-contained packages that add agents, commands, tools, and context to specific domains. Each lives in `.datacore/modules/<name>/` with a `module.yaml` manifest.

Modules hook into workflows (e.g., adding sections to `/today`), register their own agents, and provide MCP tools. Their CLAUDE.md loads on-demand when the domain is relevant.

Use `datacore.modules.list` for installed modules, `datacore.modules.info <name>` for details.

<!-- REGISTRY:modules -->

## MCP Sources & Services

<!-- REGISTRY:sources -->

## Infrastructure

<!-- REGISTRY:infrastructure -->

> Server IPs, SSH configs, deployment procedures are in engram memory. Call `datacore.recall` with domain "infrastructure". Do NOT guess IPs — always verify via recall.

**Deployment Resources**:
- `.datacore/specs/module-deployment-checklist.md` — Universal server deployment & credential parity checklist
- Module-specific: Check `[module]/SERVER.md` or `[module]/docs/` for detailed setup guides

## Conventions

### org-mode

- Headings: `*` per level. States: TODO, NEXT, WAITING, DONE
- Properties: `:PROPERTIES:` ... `:END:`. Tags: `:tag1:tag2:`
- Timestamps: **Always verify day-of-week** — LLMs get these wrong:
  `python3 -c "from datetime import date; print(date(YYYY,M,D).strftime('%a'))"`
- AI Task Tags: `:AI:` (general), `:AI:research:`, `:AI:content:`, `:AI:data:`, `:AI:pm:`, `:AI:technical:` (human review)

### Notes & Tags

- Wiki-links: `[[Page Name]]`. Journal: `YYYY-MM-DD.md`
- Tags: `#tag` in PKM/CRM, `:tag:` in org-mode. NOT frontmatter arrays.
- Registries: `.datacore/tags.yaml` (system), `[space]/.datacore/tags.yaml` (space)

### Bash

- **Never multi-line Bash.** Chain with `&&`.
- Use dedicated tools: `Glob` not `find`, `Read` not `cat`, `Grep` not `grep`.

> Detailed conventions are in engram memory (DIP pack, 747 engrams). Call `datacore.recall` for specifics.

## System Patterns (DIPs)

Datacore Improvement Proposals define system patterns. 15+ DIPs cover: contribution model, layered context, tag taxonomy, agent registry, knowledge management, GTD workflow, nightshift execution. Located in `.datacore/dips/`.

All DIP content is in engram memory (dips-v1 pack). Call `datacore.recall` for quick lookups.

### Layered Context (DIP-0002)

All context files use layered privacy: `.base.md` (public) → `.space.md` → `.local.md` (private). Composed `.md` is gitignored. Rebuild: `python .datacore/lib/context_merge.py rebuild --path .`

## Key Principles

- **Augment, don't replace** — agents assist, humans decide
- **Progressive processing** — inbox → triage → knowledge → archive
- **Single capture point** — inbox.org, then route and remove
- **Memory over repetition** — learn once, recall always

---

**This is CLAUDE.base.md** — the PUBLIC layer. Customize with `CLAUDE.local.md` (gitignored).
