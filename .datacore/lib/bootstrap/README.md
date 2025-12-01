# Datacore Bootstrap Scripts

Complete installation scripts for setting up Datacore on new machines.

## setup-datacore.sh

**Purpose:** Full Datacore installation including dependencies, all repositories, and secrets.

**What it does:**
- Detects OS (macOS/Linux)
- Updates Homebrew (macOS) or apt (Linux)
- Installs/upgrades dependencies:
  - Python 3 + pip
  - Node.js (detects nvm) + npm
  - GitHub CLI (gh)
  - Claude Code CLI
- Clones all repositories (~25 repos):
  - 5 spaces (Data, 0-personal, 1-teamspace, 2-projectspace, 3-partnerspace)
  - 12 root modules
  - 5 team projects
  - 3 partner projects
- Restores secrets from dotfiles repo
- Automatic cleanup of temporary files

**Usage:**

```bash
# On new machine
bash setup-datacore.sh
```

**Estimated time:** 25-35 minutes (depends on network speed)

**Requirements:**
- Node.js 20+ (script will attempt to upgrade automatically, or install via nvm)
- SSH access to nightshift server (set via NIGHTSHIFT_HOST env var)
- GitHub SSH keys configured
- Internet connection

**Created:** 2026-01-14 during multi-machine setup session

**Next steps:**
- Convert to proper module with `/create-module`
- Add to module registry
- Create .command wrapper for double-click installation
- Add progress indicators/percentage
- Add option for minimal vs full install
