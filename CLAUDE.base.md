# CLAUDE.md

This file teaches you how to work effectively in this Datacore installation.

## Your Memory

You have persistent memory through **two MCP servers** — use both in every session.

**PLUR** (`plur_*` tools) — engram memory engine. Corrections, preferences, and patterns persist across sessions.
**Datacore** (`datacore.*` tools) — GTD productivity, journal, knowledge files, modules.

### Session Workflow

1. **Start**: Call `plur_session_start` with task description — injects relevant engrams
2. **Recall**: Before answering factual questions, call `plur_recall_hybrid` — the answer is in memory
3. **Learn**: When corrected or discovering something new, call `plur_learn`
4. **Feedback**: Rate injected engrams with `plur_feedback` — trains relevance
5. **End**: Call `plur_session_end` with summary + engram suggestions, then `datacore.capture` for journal

### Datacore Tools (productivity)

- `datacore.capture` — write journal entries and knowledge notes
- `datacore.search` — search journal and knowledge files (NOT engrams — use `plur_recall_hybrid` for engram memory)
- `datacore.ingest` — import content into knowledge base
- `datacore.status` — system health
- `datacore.date` — canonical date operations (today, dow, validate, add, parse, org-stamp)
- `datacore.modules.*` — manage installed modules

### Dates — NEVER type from memory

LLMs hallucinate day-of-week names and anchor to training-era years. You will get dates wrong if you type them from memory. Rules:

1. **Today's date**: use the date injected into your system prompt (e.g. "Today's date is 2026-04-08") — copy it literally. If unsure, call `datacore.date` with `op: today`.
2. **Day-of-week for any date**: call `datacore.date` with `op: dow`. Never compute it in your head.
3. **Relative dates** ("next Monday", "in 3 days"): call `datacore.date` with `op: parse`.
4. **org-mode timestamps**: call `datacore.date` with `op: org-stamp` — returns `<YYYY-MM-DD Day>` correctly.
5. **Before writing** a date+dow into any `.org` or `.md` file, mentally verify or validate with `datacore.date op:validate`. A PreToolUse hook will reject writes containing wrong day names — fix them before the write, don't fight the hook.

The CLI equivalent is `python3 .datacore/lib/date_utils.py today|dow|validate|...` for shell scripts and subagents.

> **Recall split**: `plur_recall_hybrid` searches engram memory. `datacore.search` searches journal/knowledge files. For comprehensive results, call both.

| Domain | When to recall (plur_recall_hybrid) |
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
- **Engram memory** — AI learning via PLUR MCP. Corrections, preferences, and patterns persist across sessions. Engrams stored in `~/.plur/engrams.yaml`.
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
- `plur_recall_hybrid` — search by name or purpose
- `datacore.modules.info <name>` — module capabilities, agents, commands
- `.datacore/registry/agents.yaml` / `commands.yaml` — full registries

Slash commands (`/today`, `/research`, `/wrap-up`) are multi-phase workflows. Conversational commands work naturally — "process inbox", "weekly review", "sync repos".

### Knowledge Base

Before starting work, check for existing knowledge:
- `datacore.search` — semantic search across journal and knowledge files (uses Datacortex embeddings)
- `plur_recall_hybrid` — targeted engram retrieval by domain or keywords
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

> Server IPs, SSH configs, deployment procedures are in engram memory. Call `plur_recall_hybrid` with domain "infrastructure". Do NOT guess IPs — always verify via recall.

**Deployment Resources**:
- `.datacore/specs/module-deployment-checklist.md` — Universal server deployment & credential parity checklist
- Module-specific: Check `[module]/SERVER.md` or `[module]/docs/` for detailed setup guides

## Conventions

### Tasks — org-workspace is mandatory

**NEVER grep raw `.org` files for task queries.** Use org-workspace, which treats tasks as structured objects:

```bash
# CLI adapter (12 commands):
python3 .datacore/lib/org_workspace_adapter.py list --file [path] --tags continuation --states TODO
python3 .datacore/lib/org_workspace_adapter.py agenda --file [path] --days 7
python3 .datacore/lib/org_workspace_adapter.py ensure-ids --file [path]
```

```python
# Python (for complex queries):
from org_workspace import OrgWorkspace, Query
ws = OrgWorkspace()
ws.load('/path/to/org/inbox.org')
q = Query(ws)
q.by_tag('continuation')  # by_state, agenda, deadlines, overdue, stale, ai_tasks
```

Each task is a **NodeView** with: `heading`, `todo`, `tags`, `scheduled`, `deadline`, `priority`, `properties`, `body`, `parent`, `children`, `id()`. Use `get_property('BOOTSTRAP')` for rich task properties.

GTD MCP tools (`datacore.gtd.*`) are also available when the MCP server is running: `inbox_count`, `add_task`, `list_next_actions`, `complete_task`, `agenda_view`, `deadline_warnings`, `archive_tasks`, `project_health`, `effort_aggregate`, `duplicate_check`, `write_clock_entry`.

### org-mode format

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

> Detailed conventions are in engram memory (DIP pack, 747 engrams). Call `plur_recall_hybrid` for specifics.

## System Patterns (DIPs)

Datacore Improvement Proposals define system patterns. 15+ DIPs cover: contribution model, layered context, tag taxonomy, agent registry, knowledge management, GTD workflow, nightshift execution. Located in `.datacore/dips/`.

All DIP content is in engram memory (dips-v1 pack). Call `plur_recall_hybrid` for quick lookups.

### Layered Context (DIP-0002)

All context files use layered privacy: `.base.md` (public) → `.space.md` → `.local.md` (private). Composed `.md` is gitignored. Rebuild: `python .datacore/lib/context_merge.py rebuild --path .`

## Verification Protocol

When recalling facts that will drive actions (server IPs, file paths, API endpoints, credential locations):
1. State the recalled fact explicitly before acting on it
2. Include the engram ID or search that produced it
3. If no engram matches, say "No engram found — verifying from filesystem" and check directly
4. Never interpolate between two engrams to produce a "probably correct" composite

When the user corrects a recalled fact: call `plur_learn` immediately, then `plur_feedback` with negative signal on the wrong engram, before continuing the task.

## Guardrails

### Over-engineering check
Before proposing any new system, module, DIP, or architectural change:
1. What is the simplest version that solves the actual problem?
2. Is there an existing module/tool/pattern that already covers 80% of this?
3. Will this create maintenance burden disproportionate to its value?

If a task can be done in <20 lines of shell script, do that first. Propose the module/system version only if the user explicitly asks.

### Tool Selection Discipline
Before invoking any MCP tool, apply the locality test:
1. Is the answer already in engrams? → `plur_recall_hybrid`
2. Is the answer in the local filesystem? → Read/Grep/Glob
3. Is the answer derivable from context already loaded? → Just answer
4. Only if 1-3 fail → Use external MCP tool

Specific restrictions:
- **Gamma**: Only when user explicitly says "Gamma" or "presentation"
- **Web search**: Only when local knowledge is insufficient or user asks for current info
- **Gemini tools**: Only for tasks explicitly requiring a second model's perspective

## Key Principles

- **Augment, don't replace** — agents assist, humans decide
- **Progressive processing** — inbox → triage → knowledge → archive
- **Single capture point** — inbox.org, then route and remove
- **Memory over repetition** — learn once, recall always

---

<!-- TODO: Migrate engagement/XP tracking from legacy learning system to PLUR -->

**This is CLAUDE.base.md** — the PUBLIC layer. Customize with `CLAUDE.local.md` (gitignored).
