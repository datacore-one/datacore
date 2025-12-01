# Datacore Module Catalog

Available spaces, modules, and extensions for Datacore.

## Contribution Model

Datacore uses a **fork-and-overlay** model:

1. **Fork** the template repo (`datacore-org` or a module)
2. **Clone** your fork to `~/Data/`
3. **Work** - content is auto-gitignored
4. **Improve** system files (agents, commands)
5. **PR** improvements back to upstream

See [DIP-0001](../dips/DIP-0001-contribution-model.md) for full details.

---

## Space Templates

Space templates provide the **system** for team/organization workspaces. Fork these to create your own space.

| Template | Description | Repo |
|----------|-------------|------|
| datacore-org | Framework for autonomous organization management | [datacore-one/datacore-org](https://github.com/datacore-one/datacore-org) |

### Using Space Templates

```bash
# 1. Fork datacore-org on GitHub to your-org/datacore-org

# 2. Clone YOUR fork
git clone https://github.com/your-org/datacore-org.git ~/Data/1-myorg
cd ~/Data/1-myorg

# 3. Add upstream for syncing community improvements
git remote add upstream https://github.com/datacore-one/datacore-org.git

# 4. Register in install.yaml
```

```yaml
# install.yaml
spaces:
  myorg:
    repo: your-org/datacore-org
    path: 1-myorg
    modules: []
```

### What's in a Space Template

**Tracked (system - contribute improvements):**
- `.datacore/agents/*.md` - Agent definitions
- `.datacore/commands/*.md` - Slash commands
- `CLAUDE.md` - AI context template
- `*/_index.md`, `*/README.md` - Structure documentation

**Gitignored (content - stays local):**
- `org/*.org` - Tasks
- `journal/*.md` - Activity logs
- `2-knowledge/**/*.md` - Knowledge base
- `1-departments/**/*.md` - Work products

---

## Modules

Modules extend Datacore with specialized functionality. 21 modules are currently installed.

### Public Modules

Open-source modules available for community use.

| Module | Version | Description | Tools | Agents | Skills | Cmds | Context Layers |
|--------|---------|-------------|-------|--------|--------|------|----------------|
| gtd | 1.0.0 | Getting Things Done -- task capture, inbox processing, org-mode management | 13 | 4 | 1 | 4 | 1 base |
| nightshift | 0.2.0 | Autonomous task execution with quality gates and multi-persona evaluation | 7 | 26 | 1 | 1 | 1 base |
| research | 0.2.0 | Automated research processing with NotebookLM podcasts and knowledge extraction | 3 | 2 | 2 | 1 | 1 base |
| outbox | 1.1.0 | Content routing out of active workspaces -- archive, delivery, publish | 3 | 2 | 1 | 1 | 1 base |
| datacortex | 0.2.0 | Knowledge graph -- semantic search, graph statistics, link analysis, visualization | 7 | 0 | 1 | 1 | 1 base |
| crm | 0.3.0 | Network intelligence -- track entities, relationships, industry landscape | 3 | 4 | 0 | 1 | 1 base |
| meetings | 0.4.0 | Meeting lifecycle -- standup generation, preparation, transcription processing | 2 | 5 | 2 | 3 | 1 base |
| mail | 1.1.0 | Email integration -- Gmail adapter, classification, processing | 0 | 1 | 0 | 1 | 1 base |

#### Installation Quick Reference

To install a public module, clone it and register in `install.yaml`:

```bash
# Example: clone a public module
git clone https://github.com/datacore-one/datacore-crm .datacore/modules/crm
```

```yaml
# install.yaml — register under modules:
modules:
  - repo: datacore-one/datacore-crm
    path: .datacore/modules/crm
  - repo: datacore-one/datacore-nightshift
    path: .datacore/modules/nightshift
  - repo: datacore-one/module-meetings
    path: .datacore/modules/meetings
```

All public modules use a single `CLAUDE.base.md` context layer (PUBLIC). Fork a module and add `.org.md` or `.local.md` layers for your customizations.

### Private Modules

Domain-specific modules for personal or organization use.

| Module | Version | Description | Tools | Agents | Skills | Cmds |
|--------|---------|-------------|-------|--------|--------|------|
| comms | 2.0.0 | Communications infrastructure -- brand, content, scheduling, engagement, ads, analytics | 3 | 11 | 0 | 10 |
| health | 0.2.0 | Agent-driven health management -- biometrics, prevention, routine optimization | 6 | 6 | 0 | 2 |
| trading | 1.1.0 | Trading workflows -- position management, risk monitoring, performance tracking | 2 | 1 | 5 | 5 |
| forge | 0.1.0 | Autonomous digital product business -- discover, generate, list, track, iterate | 0 | 6 | 0 | 0 |
| grants | 1.0.0 | Grant proposal writing and management for EU/NGI funding programs | 0 | 2 | 0 | 0 |
| slides | 1.0.0 | Visual content generation -- presentations (Gamma.app) with AI-powered backgrounds | 0 | 3 | 3 | 0 |
| news | 0.2.0 | On-demand news aggregation with AI-scored relevance and tiered processing | 0 | 0 | 1 | 0 |
| telegram | 0.1.0 | Telegram bot interface for mobile access to Claude Code | 0 | 0 | 0 | 0 |
| dev | 1.0.0 | Development workflows -- deployment, CI/CD monitoring, production verification | 0 | 0 | 1 | 1 |
| image-generation | 0.2.0 | Unified image generation -- Midjourney and Gemini with prompt library and archive | 0 | 1 | 1 | 0 |
| personal-finance | 0.1.0 | Crypto finance tracking -- holdings, transactions, loan reconciliation | 0 | 1 | 0 | 1 |
| verity | 1.0.0 | MCP server config management with secure secret resolution | 0 | 0 | 0 | 1 |
| whatsapp | 0.1.0 | WhatsApp integration -- import exports, sync contacts, bidirectional messaging | 0 | 2 | 0 | 1 |

### Installing Modules

```bash
# Clone module to modules folder
git clone https://github.com/datacore-one/datacore-trading .datacore/modules/trading

# Commands and agents are automatically available
```

```yaml
# install.yaml
modules:
  - repo: datacore-one/datacore-trading
    path: .datacore/modules/trading
```

### Module Structure

```
.datacore/modules/[module-name]/
├── module.yaml           # Module metadata
├── agents/               # Specialized agents
├── commands/             # Slash commands
├── prompts/              # Prompt templates
├── templates/            # Output templates
├── workflows/            # n8n workflows (optional)
└── docs/                 # Module documentation
```

### Contributing to Modules

Same fork-and-PR model as space templates:

```bash
# Fork the module repo
# Clone your fork to .datacore/modules/[name]
# Improve agents/commands
# PR to upstream
```

---

## Creating Your Own

### Custom Space

1. **Fork** `datacore-org` to your GitHub org
2. **Clone** your fork to `~/Data/N-spacename/`
3. **Customize** `CLAUDE.md` with your org context
4. **Add upstream** remote for syncing improvements
5. **Register** in `install.yaml`
6. **Contribute** system improvements via PR

### Custom Module

1. **Create** module structure in `.datacore/modules/[name]/`
2. **Add** `module.yaml` with metadata:
   ```yaml
   name: my-module
   version: 1.0.0
   description: What it does
   author: Your Name
   ```
3. **Add** agents and commands
4. **Register** using the `module-registrar` agent:
   ```
   :AI:module:register: Register datacore-<name> module
   ```
   The agent creates the repo, updates CATALOG, and submits PR.
5. **Or manually**: Create repo, update CATALOG.md, submit PR

---

## Contributing

### Small Improvements

1. Fork the relevant repo
2. Make your change
3. Open PR to upstream

### Significant Changes

1. Submit a [DIP](../dips/README.md)
2. Community discussion
3. Maintainer review
4. Implementation

### What We Accept

- Agent improvements (better prompts, new capabilities)
- New commands (general-purpose workflows)
- Bug fixes
- Documentation improvements
- Structure improvements

### What Belongs in Modules

- Domain-specific agents (trading, research, etc.)
- Specialized workflows
- Integration with external tools

---

*Last updated: 2026-03-04*
