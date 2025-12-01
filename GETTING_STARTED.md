# Getting Started

Get Datacore running in 5 minutes.

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed
- [Git](https://git-scm.com/) and [GitHub CLI](https://cli.github.com/)
- Python 3.8+

## Setup

```bash
# 1. Fork and clone
mkdir ~/Data && cd ~/Data
gh repo fork datacore-one/datacore --clone=false
git clone https://github.com/YOUR-USERNAME/datacore.git .
git remote add upstream https://github.com/datacore-one/datacore.git

# 2. Initialize
cp install.yaml.example install.yaml
python .datacore/lib/context_merge.py rebuild --path .
```

## First Commands

Open Claude Code in `~/Data` and try:

| Command | What it does |
|---------|-------------|
| `/today` | Morning briefing with priorities and calendar |
| `/continue` | Resume work or find highest-impact next action |
| `/wrap-up` | End session with learning capture |

## Capture Tasks

Say "add task: Review quarterly report" and it goes to your inbox. Process with "process inbox".

## AI Delegation

Tag tasks with `:AI:` in org-mode and they execute overnight:

```
* TODO Research competitor pricing :AI:research:
```

Run `/tomorrow` before bed to queue AI tasks.

## Next Steps

- [Full Installation Guide](INSTALL.md) -- modules, MCP servers, team spaces
- [Module Catalog](.datacore/CATALOG.md) -- available extensions
- [Contributing](CONTRIBUTING.md) -- how to contribute
