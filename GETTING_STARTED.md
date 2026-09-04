# Getting Started

Get Datacore running in 2 minutes.

## Recommended: CLI Setup

The CLI handles everything automatically — forking, cloning, org file creation, Python
dependencies, git hooks, and MCP configuration:

```bash
npx @datacore-one/cli init
```

That's it. Open Claude Code in `~/Data` and jump to [First Commands](#first-commands).

**Prefer a global install?**

```bash
npm install -g @datacore-one/cli
datacore init
```

## Alternative: MCP Server Only

If you want Claude Code integration without the full GTD system (no `~/Data` directory,
no org files):

```bash
npx @datacore-one/mcp init
```

Then add to `.claude/mcp.json` (or `.cursor/mcp.json`):

```json
"datacore": {
  "command": "npx",
  "args": ["-y", "@datacore-one/mcp"]
}
```

---

## First Commands

Open Claude Code in `~/Data` and try:

| Command | What it does |
|---------|-------------|
| `/today` | Morning briefing with priorities and calendar |
| `/continue` | Resume work or find highest-impact next action |
| `/wrap-up` | End session with learning capture |

## Capture Tasks

Say "add task: Review quarterly report" and it goes to your inbox. Process with
"process inbox".

## AI Delegation

Tag tasks with `:AI:` in org-mode and they execute overnight:

```
* TODO Research competitor pricing :AI:research:
```

Run `/tomorrow` before bed to queue AI tasks.

## Next Steps

- [Full Installation Guide](INSTALL.md) — modules, MCP servers, team spaces
- [Module Catalog](.datacore/CATALOG.md) — available extensions
- [Contributing](CONTRIBUTING.md) — how to contribute

---

## Advanced: Manual Setup

For contributors, forks that need custom CI, or cases where the CLI cannot run.
The CLI above is faster and less error-prone — only use this path if you have a
specific reason to.

### Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed
- [Git](https://git-scm.com/) and [GitHub CLI](https://cli.github.com/)
- Python 3.8+

### Setup

```bash
# 1. Fork and clone into ~/Data (note the dot — clones into current directory)
mkdir ~/Data && cd ~/Data
gh repo fork datacore-one/datacore --clone=false
git clone https://github.com/YOUR-USERNAME/datacore.git .
git remote add upstream https://github.com/datacore-one/datacore.git

# 2. Install Python dependencies
pip install -r .datacore/lib/requirements.txt

# 3. Activate configuration template
cp install.yaml.example install.yaml

# 4. Build CLAUDE.md from layers
python .datacore/lib/context_merge.py rebuild --path .
```

See [INSTALL.md](INSTALL.md) for the complete manual walkthrough — org file templates,
MCP server configuration, team spaces, and module installation.
