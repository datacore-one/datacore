# Datacore Install & Upgrade Process

**Version**: 1.0
**Created**: 2025-11-29
**Status**: Draft

## Overview

This document defines how Datacore installations are set up and how upgrades propagate from local changes to the shared repo.

## File Categories

### 1. Template Files (Tracked in Repo)

Files with `.template` suffix that provide starting points:

| Template | Purpose | Local Version |
|----------|---------|---------------|
| `CLAUDE.template.md` | Root instructions | `CLAUDE.md` |
| `0-personal/CLAUDE.template.md` | Personal space docs | `0-personal/CLAUDE.md` |
| `install.yaml.example` | Installation config | `install.yaml` |

### 2. Schema Files (Tracked in Repo)

Database schemas and code that can be upgraded:

| File | Purpose |
|------|---------|
| `.datacore/lib/zettel_db.py` | Database management code |
| `.datacore/lib/zettel_processor.py` | File processing code |

### 3. Local Files (Never Tracked)

Files specific to each installation:

- `CLAUDE.md` - Personalized instructions
- `install.yaml` - Personal configuration
- `**/*.db` - Generated databases
- All personal content (see privacy-policy.md)

---

## Fresh Installation Process

### Step 1: Clone Repo

```bash
git clone https://github.com/datacore-one/datacore.git ~/Data
cd ~/Data
```

### Step 2: Run Install Command

```bash
# Future: datacore install
# Current: Manual or Claude-assisted
```

### Step 3: Template Activation

The install process copies templates to active files:

```bash
# Copy templates to local versions
cp CLAUDE.template.md CLAUDE.md
cp 0-personal/CLAUDE.template.md 0-personal/CLAUDE.md
cp install.yaml.example install.yaml

# Initialize databases
python .datacore/lib/zettel_db.py init-all
```

### Step 4: Personalization Interview

Claude interviews user to customize:

1. **install.yaml**: Name, spaces, modules
2. **CLAUDE.md**: Add personal context
3. **0-personal/CLAUDE.md**: Define focus areas

---

## Upgrade Process

### Pulling Upstream Changes

```bash
git pull origin main
```

This updates:
- Template files (`.template.md`, `.example`)
- Library code (`.datacore/lib/`)
- Agents and commands
- Specs and documentation

### Applying Template Changes

When templates are updated upstream, user should review and merge changes:

```bash
# Compare local to updated template
diff CLAUDE.md CLAUDE.template.md

# Manually merge relevant changes
```

### Database Migrations

When `zettel_db.py` schema changes:

```bash
# Reinitialize databases (data regenerated from source files)
python .datacore/lib/zettel_db.py init-all
python .datacore/lib/zettel_processor.py --full-process
```

---

## Upgrade Hooks (Claude Code)

### Hook: CLAUDE.md Changed Locally

When user makes meaningful changes to local `CLAUDE.md`:

**Trigger**: Edit to `CLAUDE.md` that adds new methodology or system behavior

**Action**:
1. Identify if change is personal (space names, focus areas) or systemic (new workflows)
2. If systemic: Prompt user to update `CLAUDE.template.md`
3. Create todo to commit template changes

**Example Dialog**:
```
I noticed you added a new workflow section to CLAUDE.md.
This looks like a systemic improvement that could benefit all installations.

Should I update CLAUDE.template.md with a generic version of this workflow?
```

### Hook: Database Schema Changed

When `zettel_db.py` is modified:

**Trigger**: Edit to table definitions or schema

**Action**:
1. Note that schema is now ahead of any existing databases
2. Prompt user to reinitialize databases
3. Add to commit message that schema was updated

### Hook: New Agent/Command Added

When new agent or command is created:

**Trigger**: New file in `.datacore/agents/` or `.datacore/commands/`

**Action**:
1. Check if it's personal (trading) or generic (GTD)
2. If generic: Should be committed to repo
3. If personal: Should be in a module (`.datacore/modules/`)

---

## Commit Workflow

### Before Committing

1. **Review staged files** against privacy policy
2. **Check for personal data** in files being committed
3. **Update templates** if local files have systemic improvements

### Commit Checklist

```markdown
- [ ] No personal identifiers in committed files
- [ ] Templates updated if methodology changed
- [ ] Schema changes documented
- [ ] New agents/commands in correct location
```

### Post-Commit Hook (Future)

Automated check that could run pre-push:

```bash
#!/bin/bash
# .git/hooks/pre-push

# Check for personal data patterns
if grep -r "gregor\|datafund" --include="*.md" . | grep -v ".template"; then
    echo "ERROR: Personal data found in files"
    exit 1
fi

# Check CLAUDE.md is gitignored
if git ls-files | grep "^CLAUDE.md$"; then
    echo "ERROR: CLAUDE.md should not be tracked"
    exit 1
fi
```

---

## Sync Script Enhancement

The `./sync` script should support upgrade workflow:

```bash
./sync                  # Pull all repos
./sync push             # Commit and push all
./sync status           # Show status
./sync upgrade          # Pull + apply migrations + reinit DBs
./sync check-privacy    # Verify no personal data in staged files
```

---

## Template Synchronization Rules

### What Goes in Templates

| Include | Exclude |
|---------|---------|
| Generic workflows | Personal focus areas |
| System architecture | Space names |
| Agent/command docs | Project names |
| Best practices | Personal preferences |

### Keeping Templates in Sync

1. **After major CLAUDE.md edits**: Review template for updates
2. **Before releases**: Ensure templates reflect latest methodology
3. **During reviews**: Check if local improvements should propagate

---

## Implementation Status

- [x] Template files created
- [x] Privacy policy defined
- [x] Gitignore configured
- [ ] Install script (`datacore install`)
- [ ] Upgrade script (`./sync upgrade`)
- [ ] Pre-push hook
- [ ] Claude interview for personalization

---

*This process ensures installations stay current while protecting personal data.*
