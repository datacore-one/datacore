# Datacore

**Own Your Intelligence.**

An open-source framework for building AI-automated businesses. Datacore gives Claude (and other MCP-compatible agents) the context, structure, and autonomy to run day-to-day operations while you focus on strategy.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE) [![Python 3.8+](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org) [![Modules](https://img.shields.io/badge/Modules-21-green.svg)](.datacore/CATALOG.md) [![DIPs](https://img.shields.io/badge/DIPs-18-purple.svg)](.datacore/dips/README.md)

## Quick Start

**Option 1: Let your AI install it**

Tell Claude Code (or Cursor, Windsurf, OpenClaw):
> "Go to datacore.one and install Datacore."

**Option 2: CLI**

```bash
npx @datacore-one/cli init
```

Sets up `~/Data`, clones modules, and configures the MCP server automatically.

**Option 3: MCP server only**

```bash
npx @datacore-one/mcp init
```

Then add to `.claude/mcp.json` or `.cursor/mcp.json`:

```json
"datacore": {
  "command": "npx",
  "args": ["-y", "@datacore-one/mcp"]
}
```

Then open Claude Code and try `/today` or `/continue`. See [GETTING_STARTED.md](GETTING_STARTED.md) for a full walkthrough.

---

## What is Datacore?

It starts as an extended mind. It becomes an autonomous business.

```
Stage 1 — Extended mind        ← start here
  AI that knows your work, remembers your decisions, surfaces what matters.
  Persistent memory via PLUR. GTD task management. Zettelkasten knowledge base.

Stage 2 — Autonomous business  ← where most users end up
  Agents run day-to-day operations: content, research, outreach, coordination.
  Queued during the day. Executed overnight. Reviewed in your morning briefing.

Stage 3 — AI business network  ← the horizon
  Agents from different businesses collaborating and exchanging value.
```

Your data stays on your drive. You control the agents. You set the direction.

At its core, it provides:

- **Autonomous execution** -- Delegate tasks to AI agents overnight; wake up to a quality-evaluated briefing
- **GTD task management** -- Capture, organize, and delegate tasks using Getting Things Done methodology with org-mode
- **Knowledge management** -- Zettelkasten-style notes, wiki-links, and semantic search across your knowledge base
- **Modular architecture** -- Install only what you need; extend with community or custom modules
- **Persistent memory** -- Powered by [PLUR](https://plur.ai) (preinstalled): corrections, preferences, and decisions survive across sessions

### How It Works

```
You capture ideas and tasks
        |
Datacore organizes, links, and indexes them
        |
AI assistants access your knowledge and context via MCP
        |
Agents execute delegated work overnight
        |
You review results in your morning briefing
```

---

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) -- AI coding assistant
- [Git](https://git-scm.com/) and [GitHub CLI](https://cli.github.com/)
- Python 3.8+

---

## Architecture

```
~/Data/
|
+-- .datacore/                    # System core
|   +-- agents/                   # AI agent definitions
|   +-- commands/                 # Slash commands (workflows)
|   +-- modules/                  # Installed modules
|   +-- lib/                      # Python utilities
|   +-- specs/                    # System specifications
|   +-- dips/                     # Design proposals
|   +-- registry/                 # Agent, command, source registries
|   +-- state/                    # Runtime state (gitignored)
|   \-- env/                      # Secrets (gitignored)
|
+-- 0-personal/                   # Personal space
|   +-- org/                      # GTD system (org-mode)
|   +-- notes/                    # PKM (Obsidian)
|   +-- code/                     # Personal projects
|   \-- content/                  # Generated content
|
+-- [N]-[name]/                   # Team spaces (separate repos)
|
+-- CLAUDE.md                     # AI context (layered, auto-generated)
+-- install.yaml                  # Installation manifest
\-- sync                          # Multi-repo sync script
```

### Key Concepts

**Spaces** -- Isolated workspaces for different contexts (personal, teams, organizations). Each space has its own GTD system, knowledge base, and journal. Team spaces are separate git repos.

**Agents** -- AI agent definitions that handle specific types of work: inbox processing, content writing, data analysis, research orchestration, project management, and more.

**Commands** -- Slash commands that orchestrate multi-step workflows: `/today` (morning briefing), `/continue` (resume work), `/tomorrow` (end-of-day delegation), `/wrap-up` (session close).

**Modules** -- Optional extensions that add domain-specific functionality. Install only what you need.

**Layered Context** -- Configuration files use a four-layer privacy model (public, org, team, private) so you can contribute improvements upstream without exposing personal data.

**Memory** -- Persistent memory is handled by [PLUR](https://plur.ai), an open-source engram engine that comes preinstalled. Corrections, preferences, and decisions survive across sessions and are injected automatically — no setup needed.

---

## Modules

Public modules available for community use:

| Module | Description |
|--------|-------------|
| **gtd** | Getting Things Done -- task capture, inbox processing, org-mode management |
| **nightshift** | Autonomous overnight task execution with multi-persona quality evaluation |
| **research** | Automated research pipelines with knowledge extraction and podcast generation |
| **outbox** | Content routing out of active workspaces -- archive, delivery, publish |
| **datacortex** | Knowledge graph -- semantic search, graph statistics, link analysis |
| **crm** | Network intelligence -- track entities, relationships, interaction history |
| **meetings** | Meeting lifecycle -- standup generation, preparation, transcription processing |
| **mail** | Email integration -- Gmail adapter, classification, processing |

See the [Module Catalog](.datacore/CATALOG.md) for installation instructions and the full list of available modules.

---

## Documentation

| Resource | Description |
|----------|-------------|
| [Getting Started](GETTING_STARTED.md) | Quick walkthrough for new users |
| [Installation Guide](INSTALL.md) | Complete setup instructions |
| [Contributing](CONTRIBUTING.md) | How to contribute |
| [Module Catalog](.datacore/CATALOG.md) | Available modules and space templates |
| [DIP Specifications](.datacore/dips/README.md) | System design documents |
| [Agent Registry](.datacore/registry/agents.yaml) | All registered agents |
| [Command Registry](.datacore/registry/commands.yaml) | All registered commands |

---

## Contributing

Datacore uses a **fork-and-overlay** contribution model. Fork the repo, make improvements to public layer files (`.base.md`), and submit a PR upstream. Your private configuration stays local and is never shared.

See [CONTRIBUTING.md](CONTRIBUTING.md) for full guidelines.

---

## License

MIT License -- see [LICENSE](LICENSE) for details.

---

*Datacore is built by Datacore. The AI system that bootstraps itself into existence.*

**[datacore.one](https://datacore.one) · [github.com/datacore-one/datacore](https://github.com/datacore-one/datacore)**
