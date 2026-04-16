#!/bin/bash
set -e

echo "=== Datacore Setup ==="
echo ""

# Configuration (override via environment variables)
NIGHTSHIFT="${NIGHTSHIFT_HOST:-}"
DOTFILES_TEMP="$HOME/dotfiles-temp"
DATACORE_PATH="$HOME/Data"
GITHUB_ORG_DATACORE="${GITHUB_ORG_DATACORE:-datacore-one}"
GITHUB_ORG_PARTNER="${GITHUB_ORG_PARTNER:-partnerorg}"
MIN_NODE_VERSION=20

# --------------------------------------------------------------------------
# Utility functions
# --------------------------------------------------------------------------

node_version_ok() {
    if ! command -v node &> /dev/null; then
        return 1
    fi
    local current
    current=$(node --version | sed 's/v//' | cut -d. -f1)
    [ "$current" -ge "$MIN_NODE_VERSION" ] 2>/dev/null
}

# Check if npm global install needs sudo (nvm doesn't, system node does)
needs_sudo_for_npm() {
    if [ -n "${NVM_DIR:-}" ] || [[ "$(command -v node 2>/dev/null)" == *".nvm"* ]]; then
        return 1
    fi
    local prefix
    prefix=$(npm config get prefix 2>/dev/null) || return 0
    [ ! -w "$prefix/lib" ] 2>/dev/null
}

npm_global_install() {
    if needs_sudo_for_npm; then
        sudo npm install -g "$@"
    else
        npm install -g "$@"
    fi
}

# Safe prompt - falls back to default when stdin is a pipe (curl | bash)
safe_read() {
    local prompt="$1" default="$2"
    if [ -t 0 ]; then
        read -p "$prompt" -n 1 -r
        echo
    else
        REPLY="$default"
    fi
}

# Clone a git repo: skip if exists, shallow clone, don't abort on failure
safe_clone() {
    local url="$1" target="$2"
    local name
    name=$(basename "$target")
    if [ -d "$target" ]; then
        echo "  ✓ $name (already exists)"
    else
        echo "  Cloning $name..."
        git clone --depth 1 "$url" "$target" || {
            echo "  WARNING: Failed to clone $name (skipping)"
            return 0
        }
    fi
}

# --------------------------------------------------------------------------
# Pre-flight checks
# --------------------------------------------------------------------------

if [[ -z "$NIGHTSHIFT" ]]; then
    echo "ERROR: NIGHTSHIFT_HOST is not set."
    echo "Set it to your nightshift server address, e.g.:"
    echo "  export NIGHTSHIFT_HOST=user@your-server-ip"
    exit 1
fi

cleanup() {
    if [ -d "$DOTFILES_TEMP" ]; then
        echo ""
        echo "Cleaning up temporary files..."
        rm -rf "$DOTFILES_TEMP"
    fi
}
trap cleanup EXIT

# Detect OS
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
elif [[ "$OSTYPE" == "linux"* ]]; then
    OS="linux"
else
    echo "Unsupported OS: $OSTYPE"
    exit 1
fi

echo "Detected OS: $OS"
echo ""

# --------------------------------------------------------------------------
# Install dependencies
# --------------------------------------------------------------------------

echo "=== Installing Dependencies ==="
echo ""

if [ "$OS" = "macos" ]; then
    # Check if Homebrew is installed
    if ! command -v brew &> /dev/null; then
        echo "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    else
        echo "✓ Homebrew already installed"
    fi

    # Update Homebrew
    echo "Updating Homebrew..."
    brew update

    # Optionally upgrade existing packages
    echo ""
    safe_read "Upgrade existing Homebrew packages? (recommended but may take time) [y/N] " "n"
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Upgrading Homebrew packages..."
        brew upgrade || true
    else
        echo "Skipping package upgrades"
    fi
    echo ""

    # Install GitHub CLI
    if ! command -v gh &> /dev/null; then
        echo "Installing GitHub CLI..."
        brew install gh
    else
        echo "✓ GitHub CLI already installed"
    fi

    # Install/upgrade Python 3
    if ! command -v python3 &> /dev/null; then
        echo "Installing Python 3..."
        brew install python3
    else
        echo "✓ Python 3 already installed ($(python3 --version))"
        brew upgrade python3 2>/dev/null || true
    fi

    # Upgrade pip (skip on PEP 668)
    if python3 -m pip install --upgrade pip 2>/dev/null; then
        echo "✓ pip upgraded"
    else
        echo "✓ pip managed by system package manager, skipping upgrade"
    fi

    # Install/upgrade Node.js
    if ! command -v node &> /dev/null; then
        echo "Installing Node.js..."
        brew install node
    else
        echo "Node.js already installed ($(node --version))"
        if command -v nvm &> /dev/null || [ -f "$HOME/.nvm/nvm.sh" ]; then
            echo "Detected nvm. Upgrading via nvm..."
            [ -s "$HOME/.nvm/nvm.sh" ] && . "$HOME/.nvm/nvm.sh"
            nvm install --lts
            nvm use --lts
            nvm alias default node
        else
            brew upgrade node 2>/dev/null || true
        fi
    fi

    # Verify Node version
    if ! node_version_ok; then
        echo ""
        echo "WARNING: Node.js $(node --version) below minimum (v${MIN_NODE_VERSION}.x)"
        echo "Installing nvm and Node.js LTS..."
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
        export NVM_DIR="$HOME/.nvm"
        [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
        nvm install --lts
        nvm use --lts
        nvm alias default node
    fi

    # Upgrade npm
    npm_global_install npm@latest

    # Install Claude Code CLI
    if ! command -v claude &> /dev/null; then
        echo "Installing Claude Code CLI..."
        brew install --cask claude-code
    else
        echo "✓ Claude Code CLI already installed"
    fi

elif [ "$OS" = "linux" ]; then
    # Update package list
    echo "Updating package list..."
    sudo apt-get update

    # Install GitHub CLI
    if ! command -v gh &> /dev/null; then
        echo "Installing GitHub CLI..."
        type -p curl >/dev/null || sudo apt-get install -y curl
        curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg 2>/dev/null
        sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
        sudo apt-get update
        sudo apt-get install -y gh
    else
        echo "✓ GitHub CLI already installed"
    fi

    # Install Python 3
    if ! command -v python3 &> /dev/null; then
        echo "Installing Python 3..."
        sudo apt-get install -y python3 python3-pip python3-venv
    else
        echo "✓ Python 3 already installed ($(python3 --version))"
        sudo apt-get install -y python3-pip python3-venv 2>/dev/null || true
    fi

    # Upgrade pip (skip on PEP 668)
    if python3 -m pip install --upgrade pip 2>/dev/null; then
        echo "✓ pip upgraded"
    else
        echo "✓ pip managed by system package manager (PEP 668), skipping upgrade"
    fi

    # Install/upgrade Node.js
    if ! command -v node &> /dev/null; then
        echo "Installing Node.js LTS..."
        curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
        sudo apt-get install -y nodejs
    else
        echo "Node.js already installed ($(node --version))"
        if ! node_version_ok; then
            echo "Upgrading to meet minimum requirement (v${MIN_NODE_VERSION}+)..."
            curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
            sudo apt-get install -y nodejs
        fi
    fi

    # Verify Node version, fall back to nvm if still too old
    if ! node_version_ok; then
        echo ""
        echo "WARNING: Node.js $(node --version 2>/dev/null || echo 'not found') below minimum (v${MIN_NODE_VERSION}.x)"
        echo "Installing nvm and Node.js LTS..."
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
        export NVM_DIR="$HOME/.nvm"
        [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
        nvm install --lts
        nvm use --lts
        nvm alias default node
    fi

    # Upgrade npm
    npm_global_install npm@latest

    # Install Claude Code CLI
    if ! command -v claude &> /dev/null; then
        echo "Installing Claude Code CLI..."
        npm_global_install @anthropic-ai/claude-code
    else
        echo "✓ Claude Code CLI already installed"
    fi
fi

echo ""
echo "=== Dependency Installation Complete ==="
echo ""

# Final Node version check
if ! node_version_ok; then
    echo ""
    echo "ERROR: Node.js $(node --version 2>/dev/null || echo 'not found') does not meet minimum requirement (v${MIN_NODE_VERSION}.x)"
    echo ""
    echo "Please install Node.js ${MIN_NODE_VERSION}+ manually:"
    echo "  Option 1 (recommended): Install nvm, then 'nvm install --lts'"
    echo "    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash"
    echo "  Option 2: Download from https://nodejs.org/"
    echo ""
    echo "Then re-run this script."
    exit 1
fi

echo "✓ Node.js $(node --version) meets minimum requirement (v${MIN_NODE_VERSION}+)"
echo ""

# --------------------------------------------------------------------------
# GitHub authentication
# --------------------------------------------------------------------------

if ! gh auth status &> /dev/null; then
    echo "GitHub CLI needs authentication. Running 'gh auth login'..."
    gh auth login
else
    echo "✓ GitHub CLI already authenticated"
fi

echo ""

# --------------------------------------------------------------------------
# Clone repos
# --------------------------------------------------------------------------

echo "Testing nightshift SSH access..."
ssh "$NIGHTSHIFT" "echo 'SSH access confirmed'" || { echo "ERROR: Cannot reach nightshift"; exit 1; }

echo ""
echo "Cloning dotfiles..."
git clone "$NIGHTSHIFT:~/dotfiles.git" "$DOTFILES_TEMP"

# Clone main Data repo
if [ -d "$DATACORE_PATH" ]; then
    echo ""
    echo "WARNING: ~/Data already exists. Remove it first or abort."
    safe_read "Remove existing ~/Data and continue? (y/N) " "n"
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborting."
        exit 1
    fi
    rm -rf "$DATACORE_PATH"
fi

echo ""
echo "Cloning main Datacore repo from nightshift..."
git clone "$NIGHTSHIFT:Data" "$DATACORE_PATH"

cd "$DATACORE_PATH"

echo ""
echo "✓ 0-personal space (tracked in main repo)"

# Clone team spaces from install.yaml
SPACES_CONFIG="${DATACORE_PATH}/install.yaml"
if [ -f "$SPACES_CONFIG" ]; then
    echo ""
    echo "Cloning team spaces defined in install.yaml..."
    echo "(Edit install.yaml to configure your spaces)"
else
    echo ""
    echo "No install.yaml found. Skipping team space cloning."
fi

cd "$DATACORE_PATH"

# Clone modules (all HTTPS, shallow, skip-on-failure)
echo ""
echo "Cloning modules..."
mkdir -p .datacore/modules

safe_clone "https://github.com/datacore-one/datacore-crm.git" ".datacore/modules/crm"
safe_clone "https://github.com/datacore-one/datacore-campaigns.git" ".datacore/modules/datacore-campaigns"
safe_clone "https://github.com/datacore-one/datacortex.git" ".datacore/modules/datacortex"
safe_clone "https://github.com/datacore-one/module-grants.git" ".datacore/modules/grants"
safe_clone "https://github.com/datacore-one/datacore-mail.git" ".datacore/modules/mail"
safe_clone "https://github.com/datacore-one/module-meetings.git" ".datacore/modules/meetings"
safe_clone "https://github.com/datacore-one/datacore-news.git" ".datacore/modules/news"
safe_clone "https://github.com/datacore-one/datacore-nightshift.git" ".datacore/modules/nightshift"
safe_clone "https://github.com/datacore-one/datacore-slides.git" ".datacore/modules/slides"
safe_clone "https://github.com/datacore-one/datacore-telegram.git" ".datacore/modules/telegram"
safe_clone "https://github.com/datacore-one/datacore-trading.git" ".datacore/modules/trading"
safe_clone "https://github.com/datacore-one/datacore-project-alpha.git" ".datacore/modules/project-alpha"

# --------------------------------------------------------------------------
# Restore secrets
# --------------------------------------------------------------------------

if [ -f "$DOTFILES_TEMP/datacore/main.env" ]; then
    echo ""
    echo "Restoring datacore secrets..."
    mkdir -p .datacore/env
    cp "$DOTFILES_TEMP/datacore/main.env" .datacore/env/.env
else
    echo ""
    echo "WARNING: No secrets found in dotfiles. You'll need to configure .datacore/env/.env manually."
fi

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Configure Claude Code hooks
# --------------------------------------------------------------------------

echo ""
echo "=== Configuring Claude Code Hooks ==="
echo ""

# Install PLUR CLI (needed for session hooks)
echo "Installing PLUR CLI..."
npm_global_install @plur-ai/cli@latest

# Initialize PLUR (registers MCP server + base hooks)
echo "Initializing PLUR..."
npx @plur-ai/cli init 2>/dev/null || true

# Configure session enforcement hooks (guard, sentinel, reminder)
echo "Configuring session enforcement hooks..."
python3 "$DATACORE_PATH/.datacore/lib/bootstrap/configure-hooks.py" --datacore-root "$DATACORE_PATH"

echo ""
echo "=== Datacore setup complete! ==="
echo ""
echo "Installed dependencies:"
echo "  ✓ GitHub CLI (gh)"
echo "  ✓ Claude Code CLI (claude)"
echo "  ✓ Python 3"
echo "  ✓ Node.js"
echo ""
echo "Cloned repositories:"
echo "  Spaces:"
echo "    ✓ ~/Data/                  (main repo)"
echo "    ✓ ~/Data/0-personal/       (tracked in main repo)"
for space_dir in ~/Data/[1-9]-*/; do
    [ -d "$space_dir" ] && echo "    ✓ $space_dir"
done
echo "  Modules:"
for mod_dir in ~/Data/.datacore/modules/*/; do
    [ -d "$mod_dir" ] && echo "    ✓ $(basename "$mod_dir")"
done
echo ""
echo "Usage:"
echo "  cd ~/Data"
echo "  claude                # Start Claude Code CLI"
echo "  ./sync                # Pull all repos"
echo "  ./sync push           # Push all repos"
echo "  ./sync status         # Check status"
echo ""
