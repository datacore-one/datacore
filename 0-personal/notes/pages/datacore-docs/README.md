# Datacore Documentation

System documentation and tutorials for Datacore users.

## Getting Started

- [[Welcome to Datacore]] - Your first day with Datacore
- [[GTD Workflow]] - How the GTD system works

## Reference

- [[Commands Reference]] - All slash commands
- [[Agents Reference]] - AI agents and how to use them
- [[Modules]] - Installing and creating modules

## Quick Start

1. Run `/gtd-daily-start` - Claude sets up your focus areas
2. Capture everything to `org/inbox.org`
3. Run `/gtd-daily-end` - Process inbox, delegate to AI
4. Next morning: AI work completed

## GTD Workflow Overview

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

## External Links

- [Main README](../../../../README.md) - Project overview
- [INSTALL.md](../../../../INSTALL.md) - Installation guide
- [CONTRIBUTING.md](../../../../CONTRIBUTING.md) - How to contribute
- [datacore.one](https://datacore.one) - Official website
