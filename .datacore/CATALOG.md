# Datacore Catalog

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

| Template | Description | Repo | Visibility |
|----------|-------------|------|------------|
| datacore-org | Framework for autonomous organization management | [datacore-one/datacore-org](https://github.com/datacore-one/datacore-org) | Public |

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
  - id: 1-myorg
    name: My Organization
    type: team
    repo: https://github.com/your-org/datacore-org.git
    upstream: https://github.com/datacore-one/datacore-org.git
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

Modules extend Datacore with specialized functionality. They can be installed in any space.

| Module | Description | Repo | Visibility |
|--------|-------------|------|------------|
| trading | Position management, performance tracking, trading workflows | [datacore-one/datacore-trading](https://github.com/datacore-one/datacore-trading) | Private |
| campaigns | Landing pages, deployment, analytics, A/B testing | [datacore-one/datacore-campaigns](https://github.com/datacore-one/datacore-campaigns) | Private |

### Installing Modules

```bash
# Clone module to modules folder
git clone https://github.com/datacore-one/datacore-trading .datacore/modules/trading

# Commands and agents are automatically available
```

```yaml
# install.yaml
modules:
  - trading
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

## Official Spaces

These are spaces managed by the Datacore organization. They demonstrate the system in action.

| Space | Description | Repo | Notes |
|-------|-------------|------|-------|
| datacore-space | Datacore development team workspace | datacore-one/datacore-space | Forked from datacore-org |
| datafund-space | Datafund team workspace | datacore-one/datafund-space | Forked from datacore-org |

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
4. **Register** in `install.yaml`
5. **Optional**: Publish as separate repo for community

---

## Roadmap

Planned modules and spaces:

| Name | Type | Description | Status |
|------|------|-------------|--------|
| research | Module | Academic research workflows | Planned |
| writing | Module | Long-form content creation | Planned |
| finance | Module | Personal finance tracking | Planned |

Want to propose a new module? See [DIPs](../dips/README.md).

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

*Last updated: 2025-12-01*
