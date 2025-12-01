# Datacore Documentation

System documentation and tutorials for new users.

## Reference

- [Commands Reference](commands.md) - All slash commands
- [Agents Reference](agents.md) - AI agents and how to use them
- [Modules](modules.md) - Installing and creating modules

## Getting Started

1. Follow the [Installation Guide](../INSTALL.md)
2. Read the [Commands Reference](commands.md) to understand available workflows
3. Review the [Agents Reference](agents.md) to learn about AI task delegation

## Quick Links

- [Main README](../README.md) - Project overview
- [CATALOG](../.datacore/CATALOG.md) - Available spaces and modules
- [CONTRIBUTING](../CONTRIBUTING.md) - How to contribute

## GTD Workflow Overview

The core Datacore workflow follows GTD (Getting Things Done) methodology:

| Time | Action | Command |
|------|--------|---------|
| Morning | Review AI work, set priorities | `/gtd-daily-start` |
| Day | Capture to inbox, work from next_actions | - |
| Evening | Process inbox, delegate to AI | `/gtd-daily-end` |
| Weekly | Full system review | `/gtd-weekly-review` |
| Monthly | Strategic planning | `/gtd-monthly-strategic` |

## AI Task Delegation

Tag tasks in `next_actions.org` for overnight AI processing:

| Tag | Agent | Output |
|-----|-------|--------|
| `:AI:research:` | gtd-research-processor | Literature notes, zettels |
| `:AI:content:` | gtd-content-writer | Blog posts, emails, docs |
| `:AI:data:` | gtd-data-analyzer | Reports, metrics |
| `:AI:pm:` | gtd-project-manager | Status updates |
