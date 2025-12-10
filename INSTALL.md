# Datacore Installation Guide

This guide walks you through setting up Datacore on your system.

## Prerequisites

- [Claude Code](https://claude.ai/code) - AI coding assistant
- [Git](https://git-scm.com/) - Version control
- [GitHub CLI](https://cli.github.com/) - For forking and PR workflow
- Python 3.8+ (for utilities)

Optional (for power users):
- [Emacs](https://www.gnu.org/software/emacs/) with org-mode (for GTD)
- [Obsidian](https://obsidian.md/) or [Logseq](https://logseq.com/) (for PKM)

## Quick Start (Interactive)

The recommended way to install Datacore is with Claude's guidance:

```bash
mkdir ~/Data && cd ~/Data
claude
```

Then tell Claude:

**"fork datacore-one/datacore, then clone it HERE (current directory, not a subdirectory) using `git clone <url> .`"**

Claude must run these exact commands:
```bash
gh repo fork datacore-one/datacore --clone=false
git clone https://github.com/YOUR-USERNAME/datacore.git .   # <-- NOTE THE DOT!
git remote add upstream https://github.com/datacore-one/datacore.git
```

**IMPORTANT**: The `.` at the end of `git clone` means "clone into current directory". Without it, git creates a `datacore/` subdirectory which is wrong.

## Manual Installation

If you prefer manual setup, follow the steps below.

### Step 1: Fork and Clone into ~/Data

**Important**: Fork first, then clone into ~/Data directory (not a subdirectory).

```bash
# Create and enter Data directory
mkdir ~/Data && cd ~/Data

# Fork without cloning
gh repo fork datacore-one/datacore --clone=false

# Clone YOUR fork into current directory (note the "." at the end)
git clone https://github.com/YOUR-USERNAME/datacore.git .

# Add upstream for syncing
git remote add upstream https://github.com/datacore-one/datacore.git
```

Verify remotes:
```bash
git remote -v
# origin    https://github.com/YOUR-USERNAME/datacore.git (fetch)
# upstream  https://github.com/datacore-one/datacore.git (fetch)
```

### Step 2: Activate Templates

Copy template files to create your local configuration:

```bash
# Installation manifest
cp install.yaml.example install.yaml

# GTD org files
cp 0-personal/org/inbox.org.example 0-personal/org/inbox.org
cp 0-personal/org/next_actions.org.example 0-personal/org/next_actions.org
cp 0-personal/org/someday.org.example 0-personal/org/someday.org
cp 0-personal/org/habits.org.example 0-personal/org/habits.org

# Build CLAUDE.md from layers (see Layered Context Pattern below)
python .datacore/lib/context_merge.py rebuild --path .
```

### Step 3: Configure install.yaml

Edit `install.yaml` with your details:

```yaml
# Your identity
user:
  name: Your Name
  email: you@example.com

# Spaces (personal is always 0)
spaces:
  - id: 0-personal
    name: Personal
    type: personal
  # Add team spaces as needed
  # - id: 1-company
  #   name: Company Name
  #   repo: https://github.com/org/datacore-company.git

# Installed modules
modules:
  # - trading  # Uncomment to enable trading module
```

### Step 4: Personalize Context (Layered Pattern)

Datacore uses a **layered context pattern** (DIP-0002) for all configuration files:

| Layer | Suffix | Purpose | Tracked |
|-------|--------|---------|---------|
| PUBLIC | `.base.md` | Generic methodology (PRable to upstream) | Yes |
| ORG | `.org.md` | Organization-wide customizations | Yes (in fork) |
| TEAM | `.team.md` | Team-specific additions | Optional |
| PRIVATE | `.local.md` | Personal notes (never shared) | No |

**To customize**, create a `.local.md` file with your personal additions:

```bash
# Create your private layer
cat > CLAUDE.local.md << 'EOF'
## My Focus Areas

- **Trading**: Crypto perpetuals, momentum strategies
- **Side projects**: AI tools, personal automation
- **Health**: Longevity research, biometrics

## Personal Preferences

- Prefer concise communication
- Morning focus time: 6-10am
EOF

# Rebuild composed CLAUDE.md
python .datacore/lib/context_merge.py rebuild --path .
```

**To contribute improvements**, edit `.base.md` and submit PR:

```bash
# Make a generic improvement
vim CLAUDE.base.md

# Commit and PR to upstream
git add CLAUDE.base.md
git commit -m "Improve GTD workflow documentation"
git push && gh pr create
```

Edit your context layers:

1. **Root CLAUDE.md**: Add any personal workflows or system modifications
2. **0-personal/CLAUDE.md**: Define your focus areas:

```markdown
## Focus Areas (1-active/)

- **work/**: Day job projects
- **side-projects/**: Personal ventures
- **learning/**: Skills development
- **health/**: Health tracking
```

### Step 5: Initialize Databases

```bash
# Initialize knowledge database
python .datacore/lib/zettel_db.py init-all

# Process existing notes (if any)
python .datacore/lib/zettel_processor.py --full-process
```

### Step 6: Verify Installation

```bash
# Check structure
ls -la ~/Data/
ls -la ~/Data/0-personal/org/
ls -la ~/Data/.datacore/

# Test sync
./sync status
```

## Adding Team Spaces

### Joining an Existing Team Space

If you've been granted access to a team space (e.g., datafund-space), simply clone it:

```bash
cd ~/Data
git clone https://github.com/datacore-one/datafund-space.git 1-datafund
```

Then register it in your `install.yaml`:

```yaml
spaces:
  datafund:
    repo: datacore-one/datafund-space
    path: 1-datafund
```

That's it. The space is now available at `~/Data/1-datafund/`.

### Creating a New Team Space

Team spaces use the **fork-and-overlay** model for maximum contribution potential.

### How It Works

```
datacore-org (template)     Your fork              Your content
        │                       │                       │
        │    fork               │                       │
        └──────────────────────>│                       │
                                │    clone              │
                                └──────────────────────>│ ~/Data/1-teamname/
                                                        │
                                                        ├── System (tracked)
                                                        │   - .datacore/agents/
                                                        │   - .datacore/commands/
                                                        │   - CLAUDE.md
                                                        │
                                                        └── Content (gitignored)
                                                            - org/*.org
                                                            - journal/
                                                            - 2-knowledge/
```

### Step 1: Fork the Template

1. Go to [github.com/datacore-one/datacore-org](https://github.com/datacore-one/datacore-org)
2. Click "Fork" to create `your-org/datacore-org`

### Step 2: Clone Your Fork

```bash
# Clone YOUR fork (not datacore-one)
git clone https://github.com/your-org/datacore-org.git ~/Data/1-teamname
cd ~/Data/1-teamname

# Add upstream for syncing improvements
git remote add upstream https://github.com/datacore-one/datacore-org.git
```

### Step 3: Register in install.yaml

```yaml
spaces:
  - id: 0-personal
    name: Personal
    type: personal
  - id: 1-teamname
    name: Team Name
    type: team
    repo: https://github.com/your-org/datacore-org.git
    upstream: https://github.com/datacore-one/datacore-org.git
```

### Step 4: Customize CLAUDE.md

Edit `CLAUDE.md` with your organization's context:
- Team description
- Key projects
- Important links
- Custom workflows

### Contributing Improvements

When you improve an agent, command, or structure:

```bash
# Your improvement is in a system file (tracked)
git add .datacore/agents/my-improved-agent.md
git commit -m "Improve agent X to handle Y"
git push origin main

# Open PR to upstream
# Go to github.com/datacore-one/datacore-org
# Click "New Pull Request" → "compare across forks"
# Select your fork and branch
```

### Syncing Upstream Improvements

Get improvements from the community:

```bash
cd ~/Data/1-teamname/
git fetch upstream
git merge upstream/main
# Resolve any conflicts in system files
git push origin main
```

### What's Tracked vs Local

| Tracked (contribute via PR) | Local (gitignored) |
|----------------------------|-------------------|
| `.datacore/agents/*.md` | `org/*.org` |
| `.datacore/commands/*.md` | `journal/*.md` |
| `CLAUDE.md` | `2-knowledge/**/*.md` |
| `*/_index.md`, `*/README.md` | `1-departments/**/*.md` |

## Installing Modules

Modules add specialized functionality (trading, research, etc.).

**See [.datacore/CATALOG.md](.datacore/CATALOG.md) for full list of available modules and space templates.**

### Install a Module

```bash
# Clone module to modules folder
git clone https://github.com/datacore-one/datacore-trading .datacore/modules/trading

# Module commands and agents are automatically available
```

### Enable in install.yaml

```yaml
modules:
  - trading
```

## Post-Installation

### Configure Emacs

Add to your Emacs config:

```elisp
;; Org-mode GTD setup
(setq org-directory "~/Data/0-personal/org")
(setq org-default-notes-file "~/Data/0-personal/org/inbox.org")

;; Org agenda files
(setq org-agenda-files '("~/Data/0-personal/org/"))
```

### Configure Obsidian

1. Open Obsidian
2. Open vault: `~/Data/0-personal/notes/`
3. Enable plugins: Daily notes, Templates

### Test Commands

With Claude Code, try:

```
/today              # Generate daily briefing
/gtd-daily-start    # Morning planning
```

## Upgrade Process

When Datacore updates are available:

```bash
# Pull latest changes
git pull origin main

# Review template changes
diff CLAUDE.md CLAUDE.template.md

# Reinitialize databases if schema changed
python .datacore/lib/zettel_db.py init-all
```

See `.datacore/specs/install-upgrade-process.md` for full upgrade documentation.

## Troubleshooting

### Database errors

```bash
# Reinitialize databases
rm .datacore/*.db
python .datacore/lib/zettel_db.py init-all
```

### Sync issues

```bash
# Check status of all repos
./sync status

# Force pull
./sync
```

### Permission issues

```bash
# Ensure sync script is executable
chmod +x sync
```

## File Structure After Installation

```
~/Data/
├── .datacore/              # System (from repo)
├── 0-personal/             # Your personal space
│   ├── org/                # GTD files (from templates)
│   │   ├── inbox.org
│   │   ├── next_actions.org
│   │   ├── someday.org
│   │   └── habits.org
│   ├── notes/              # PKM (Obsidian/Logseq)
│   ├── code/               # Projects
│   └── content/            # AI-generated
├── 1-teamname/             # Team space (optional)
├── CLAUDE.md               # Your config (from template)
├── install.yaml            # Your manifest (from example)
└── sync                    # Multi-repo sync
```

## Getting Help

- Issues: [github.com/datacore-one/datacore/issues](https://github.com/datacore-one/datacore/issues)
- Discussions: [github.com/datacore-one/datacore/discussions](https://github.com/datacore-one/datacore/discussions)

---

*For technical specifications, see `.datacore/specs/install-upgrade-process.md`*
