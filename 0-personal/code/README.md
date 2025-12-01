# Code Repository

**Purpose:** Development projects organized by GTD focus area and project status

**Reorganized:** 2025-11-23

---

## Structure

```
code/
├── active/          # Current development projects (by focus area)
│   ├── datafund/    # Datafund product development (9 projects)
│   ├── trading/     # Trading bots and analysis (5 projects)
│   ├── mr-data/     # AI second brain components (3 projects)
│   └── personal/    # Personal tools (2 projects)
│
├── dependencies/    # Forked/external repos (3 projects)
├── utilities/       # General-purpose tools (7 projects)
├── experiments/     # POCs and trials (7 projects)
└── archive/         # Completed/abandoned projects (12 projects + 4 artifacts)
```

---

## Quick Navigation

### Active Projects by Focus Area

**Datafund** (`active/datafund/`)
- datafund - Core Datafund project
- gordon - Gordon AI memecoin analyzer
- bzzaar-app - Swarm marketplace application
- bzz-send - Swarm file transfer tool
- fairos - FairOS integration
- nodes - Node monitoring
- solarbee - Swarm node manager
- swarm-finances - Financial analysis
- token-curve - Token economics calculator

**Trading** (`active/trading/`)
- gateio - Gate.io API integration
- sol-scripts - Solana trading scripts
- hummingbot_scripts - Trading bot configurations
- df-kraken - Kraken integration
- dashboard - Trading dashboard

**Mr.Data** (`active/mr-data/`)
- chatgpt-export - ChatGPT conversation processor
- my-data-for-ai - AI integration components
- LogSeqToObsidian - PKM migration tool

**Personal** (`active/personal/`)
- health-dashboard - Health tracking
- U-experience - UX project

---

## Alignment with GTD System

This code structure mirrors the organization in `~/Data/notes/1-active/`:

| Code Folder | Notes Folder | GTD Category |
|-------------|--------------|--------------|
| `active/datafund/` | `notes/1-active/datafund/` | Work - Datafund |
| `active/trading/` | `notes/1-active/trading/` | Work - Trading |
| `active/mr-data/` | `notes/1-active/mr-data/` | Personal - Productivity |
| `active/personal/` | `notes/1-active/personal-dev/` | Personal |

---

## Usage Guidelines

### For Active Development
1. All active projects live in `active/[focus-area]/`
2. Project TODOs tracked in `~/Data/org/next_actions.org`
3. Project documentation in `~/Data/content/docs/`
4. Project notes in `~/Data/notes/1-active/[focus-area]/`

### For Dependencies
- Keep upstream remotes configured
- Document customizations
- Check for updates periodically

### For Experiments
- No production quality expectations
- Document learnings
- Graduate to `active/` or archive when done

### For Archiving
- Move inactive projects (>6 months) to `archive/`
- Keep for reference but no active maintenance
- Review annually for deletion

---

## Migration History

**2025-11-23:** Reorganized from flat structure (48 repos) to hierarchical structure by focus area and status
- Backup: `~/Backups/code_backup_2025-11-23.tar.gz` (4.4GB)
- Plan: `~/Data/content/reports/2025-11-23-code-folder-organization-plan.md`

---

## Cross-References

- **GTD System:** `~/Data/org/`
- **Active Notes:** `~/Data/notes/1-active/`
- **Project Docs:** `~/Data/content/docs/`
- **Reports:** `~/Data/content/reports/`
- **Claude Agents:** `~/.claude/agents/`

---

*Part of the Data second brain system*
*Last updated: 2025-11-23*
