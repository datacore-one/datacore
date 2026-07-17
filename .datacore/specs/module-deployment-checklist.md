# Module Deployment Checklist

Universal credential parity and deployment verification checklist for Datacore modules with server components.

## Purpose

This checklist ensures credentials, configuration, and environment are synchronized between local development and server production environments. Use when:

- Setting up a new server for any module
- Debugging sync/execution issues
- Onboarding team members to module operations
- Auditing production security

## Quick Reference

| Category | Critical Items | Documentation |
|----------|----------------|---------------|
| Environment | API keys, tokens | [Environment Variables](#environment-variables) |
| SSH/Deploy Keys | Git access, SSH auth | [SSH Keys](#ssh-keys-and-deploy-keys) |
| Git Config | User, repos, origins | [Git Configuration](#git-configuration) |
| Services | Module-specific APIs | [Service Credentials](#service-specific-credentials) |
| Permissions | File security | [File Permissions](#file-permissions) |

## Module-Specific Implementations

Refer to module documentation for detailed checklists:

- **Nightshift**: `.datacore/modules/nightshift/SERVER.md` (lines 190-457) - Most comprehensive
- **Telegram**: `.datacore/modules/telegram/DEPLOYMENT-STATUS.md`
- **Campaigns**: `.datacore/modules/datacore-campaigns/docs/setup-guide.md`

## Universal Checklist

### Environment Variables

Core environment variables required by most modules:

```bash
# Compare local and server environment files
cat ~/.datacore/env/.env                      # Local
ssh user@server 'cat ~/config/module.env'     # Server
```

#### Critical Variables

- [ ] **ANTHROPIC_API_KEY** - Required for AI execution
  - Local: `~/.datacore/env/.env`
  - Server: `/home/deploy/config/module.env` (or `/home/deploy/config/nightshift.env`)
  - Verify: `ssh user@server 'grep ANTHROPIC_API_KEY ~/config/*.env | head -c 50'`
  - **Impact if missing**: All AI tasks will fail

- [ ] **Module-specific API keys** - Check module documentation
  - Examples: `POSTHOG_API_KEY`, `TELEGRAM_BOT_TOKEN`, `X_ADS_BEARER_TOKEN`
  - Location: Module `.env` or secrets file
  - **Impact if missing**: Module-specific features will fail

#### Optional Variables

- [ ] `DATA_DIR` - Data directory path (default: `/home/deploy/Data`)
- [ ] `LOG_LEVEL` - Logging verbosity (default: `INFO`)
- [ ] `DO_API_TOKEN` - Digital Ocean API (for automated provisioning)

### SSH Keys and Deploy Keys

Verify SSH access and git authentication:

#### Personal SSH Keys

- [ ] **Local public key added to server**
  - Server path: `/home/deploy/.ssh/authorized_keys`
  - Test: `ssh user@server 'echo "SSH OK"'`
  - **Impact if missing**: Cannot SSH to server

#### Deploy Keys (Git Access)

- [ ] **Deploy private key on server**
  - Local: `~/.datacore/env/credentials/deploy_key`
  - Server: `/home/deploy/.ssh/deploy_key`
  - Permissions: `600` (critical!)
  - Verify: `ssh user@server 'ls -la ~/.ssh/deploy_key'`
  - **Impact if missing**: Cannot pull/push repos

- [ ] **Deploy public key in Git provider**
  - GitHub: Repository Settings → Deploy Keys
  - GitLab: Repository Settings → Repository → Deploy Keys
  - Key name: `datacore-deploy` or `module-deploy`
  - Should match: `cat ~/.datacore/env/credentials/deploy_key.pub`
  - **Impact if missing**: Git operations will fail with "Permission denied"

- [ ] **SSH config references deploy key**
  - Server path: `/home/deploy/.ssh/config`
  - Should contain: `IdentityFile ~/.ssh/deploy_key`
  - Test: `ssh user@server 'ssh -T git@github.com'` (should see "successfully authenticated")
  - **Impact if missing**: Git will use wrong key, fail auth

### Git Configuration

Ensure git behavior is consistent:

- [ ] **Git user email**
  - Local: `git config --global user.email`
  - Server: `ssh user@server 'git config --global user.email'`
  - Recommendation: `module-bot@datacore.one` or personal email
  - **Impact if mismatch**: Commit attribution confusion

- [ ] **Git user name**
  - Local: `git config --global user.name`
  - Server: `ssh user@server 'git config --global user.name'`
  - Recommendation: `Module Bot` or personal name
  - **Impact if mismatch**: Commit attribution confusion

- [ ] **Git pull/push behavior**
  - Both: `git config --global pull.rebase true`
  - Both: `git config --global push.autoSetupRemote true`
  - **Impact if missing**: Merge conflicts, push failures

- [ ] **Git hooks installed + core.hooksPath set** (EVERY host that can push)
  - Both: `git config --global core.hooksPath` must print `<Data root>/.datacore/githooks`
  - Set with: `git config --global core.hooksPath "$HOME/Data/.datacore/githooks"` (adjust root on servers, e.g. `/root/Data/...`)
  - Hook scripts + public denylist ship with the datacore repo (`.datacore/githooks/`, `.datacore/hooks/`, `.datacore/config/public-repo-denylist.yaml`) — a current clone has them; verify `python3 -c 'import yaml'` works (pre-push fails CLOSED on protected repos without it)
  - Optional: provision `~/.datacore/private/customer-denylist.yaml` (NEVER via git) — without it the pre-push content scan skips customer-name patterns and logs a loud notice
  - Verify with a dry run: `cd ~/Data && echo "refs/heads/main $(git rev-parse HEAD) refs/heads/main $(git rev-parse origin/main)" | .datacore/githooks/pre-push origin "$(git remote get-url origin)"`
  - **Impact if missing**: pushes from that host bypass the entire public-repo leak guard (this is how the 2026-07-16 cos/priorities.yaml leak shipped)

### Repository Architecture

Verify repo structure matches:

- [ ] **Root datacore repo**
  - Local: `~/Data/.git`
  - Server: `/home/deploy/Data/.git`
  - Origin: `ssh user@server 'cd ~/Data && git remote -v'`
  - **Impact if missing**: Core system updates won't sync

- [ ] **Space-specific repos** (if module uses spaces)
  - Local: `~/Data/[N]-space/.git` (separate repo)
  - Server: `/home/deploy/Data/[N]-space/.git` (separate repo)
  - Both should point to same origin (usually GitHub)
  - Verify: `ssh user@server 'cd ~/Data/[N]-space && git remote -v'`
  - **Impact if mismatch**: Outputs won't sync between environments

- [ ] **Personal space architecture** (if applicable)
  - **Special case**: 0-personal may use server as origin (self-hosted)
  - Local origin: Points to `user@server:~/Data/0-personal`
  - Server: May have no remote (is origin itself)
  - **Impact if mismatch**: Personal data won't sync

### Service-Specific Credentials

Module-dependent integrations:

#### PostHog (Analytics)

- [ ] **Project API Key** (`POSTHOG_PROJECT_KEY`)
  - Starts with `phc_`
  - Used in frontend: `posthog.init('phc_...')`
  - Get from: PostHog Project Settings → Project API Key

- [ ] **Personal API Key** (`POSTHOG_API_KEY`)
  - Starts with `phx_`
  - Used in backend: API queries, server tracking
  - Get from: PostHog Settings → Personal API Keys

- [ ] **Project ID** (`POSTHOG_PROJECT_ID`)
  - Numeric ID from PostHog URL: `posthog.com/project/12345`
  - Used for: API endpoint construction

- [ ] **API endpoint** (region)
  - Both should use same region
  - EU: `https://eu.i.posthog.com`
  - US: `https://us.i.posthog.com`
  - **Impact if mismatch**: Events won't reach correct project

#### Telegram

- [ ] **Bot Token** (`TELEGRAM_BOT_TOKEN`)
  - Get from: @BotFather on Telegram
  - Format: `123456789:ABCdefGHIjklMNOpqrSTUvwxYZ`
  - Used for: All bot operations

- [ ] **Webhook URL** (if using webhooks)
  - Requires HTTPS domain (no self-signed certs)
  - Format: `https://your-domain.com/webhook/telegram`
  - Set via: `bot.set_webhook(url)`

#### Digital Ocean (Infrastructure)

- [ ] **API Token** (`DO_API_TOKEN`)
  - Get from: DigitalOcean → API → Tokens
  - Used for: Automated droplet provisioning, DNS management
  - **Only needed for**: Infrastructure automation

### GitHub CLI (if applicable)

For modules creating issues/PRs:

- [ ] **GitHub CLI installed**
  - Test: `ssh user@server 'gh --version'`
  - Install: `sudo apt install gh` or download from GitHub

- [ ] **GitHub CLI authenticated**
  - Test: `ssh user@server 'gh auth status'`
  - Should show: "Logged in via SSH protocol"
  - Setup: `ssh user@server 'gh auth login'`

- [ ] **Same GitHub account**
  - Local: `gh api user | jq -r .login`
  - Server: `ssh user@server 'gh api user | jq -r .login'`
  - **Impact if mismatch**: Issues/PRs attributed to wrong account

### File Permissions

Security verification:

```bash
# Check on server
ssh user@server '
  ls -la ~/config/*.env
  ls -la ~/.ssh/deploy_key
  ls -la ~/.ssh/authorized_keys
'
```

**Expected permissions:**

- Environment files (`.env`): `600` (rw-------)
- Deploy key: `600` (rw-------)
- Authorized keys: `600` (rw-------)
- `.ssh/` directory: `700` (rwx------)
- **Impact if wrong**: Security risk, SSH may refuse to use keys

## Verification Commands

### Quick Health Check

Run from local machine:

```bash
# 1. SSH connectivity
ssh user@server 'echo "SSH: OK"'

# 2. API key present (first 20 chars only)
ssh user@server 'grep ANTHROPIC_API_KEY ~/config/*.env | cut -c 1-40'

# 3. Git authentication
ssh user@server 'ssh -T git@github.com 2>&1 | grep successfully'

# 4. Repo architecture
ssh user@server 'cd ~/Data && git remote -v'

# 5. File permissions
ssh user@server 'stat -c "%a %n" ~/.ssh/deploy_key ~/config/*.env'
```

### Test Execution

Test module with credentials:

```bash
# Example: Nightshift status check
ssh user@server 'cd ~/Data && ./.datacore/modules/nightshift/nightshift status'

# Example: Telegram bot health
ssh user@server 'cd ~/Data/.datacore/modules/telegram && python3 -c "import bot; print(\"OK\")"'

# Example: API health check
curl https://your-domain.com/api/health
```

## Common Issues and Fixes

### Issue: Authentication Failed

**Symptoms**: Tasks fail with "Authentication failed" or "401 Unauthorized"

**Diagnosis**:
```bash
# Check if API key is set
ssh user@server 'grep ANTHROPIC_API_KEY ~/config/*.env'

# Verify key is valid
claude --api-key $(grep ANTHROPIC_API_KEY ~/.datacore/env/.env | cut -d= -f2) auth status
```

**Fix**:
1. Copy valid key from local: `grep ANTHROPIC_API_KEY ~/.datacore/env/.env`
2. SSH to server and update: `nano ~/config/module.env`
3. Restart service: `sudo systemctl restart module-name`

### Issue: Git Push Fails - Permission Denied

**Symptoms**: `Permission denied (publickey)` when pushing/pulling

**Diagnosis**:
```bash
# Check deploy key exists
ssh user@server 'ls -la ~/.ssh/deploy_key'

# Check permissions
ssh user@server 'stat -c "%a" ~/.ssh/deploy_key'

# Test GitHub auth
ssh user@server 'ssh -T git@github.com'
```

**Fix**:
1. Ensure deploy key is on server: `scp ~/.datacore/env/credentials/deploy_key user@server:~/.ssh/`
2. Fix permissions: `ssh user@server 'chmod 600 ~/.ssh/deploy_key'`
3. Verify public key in GitHub: Settings → SSH Keys
4. Update SSH config: `ssh user@server 'nano ~/.ssh/config'`
   ```
   Host github.com
     IdentityFile ~/.ssh/deploy_key
     IdentitiesOnly yes
   ```

### Issue: Outputs Don't Sync to Local

**Symptoms**: Server executes tasks but local doesn't see results

**Diagnosis**:
```bash
# Check server committed
ssh user@server 'cd ~/Data/space && git log --oneline -5'

# Check server pushed
ssh user@server 'cd ~/Data/space && git status'

# Try pulling locally
cd ~/Data/space && git pull
```

**Fix**:
1. Verify repos are separate (not nested): `ls -la ~/Data/space/.git`
2. Check both point to same origin: `git remote -v` (local and server)
3. Check network connectivity: `ping github.com`
4. Manual pull: `cd ~/Data/space && git pull`

### Issue: Environment Variables Not Loading

**Symptoms**: Service starts but can't find config

**Diagnosis**:
```bash
# Check systemd service references env file
ssh user@server 'systemctl cat module-name.service | grep EnvironmentFile'

# Check env file exists
ssh user@server 'cat ~/config/module.env'
```

**Fix**:
1. Ensure `EnvironmentFile` directive in service file
2. Reload systemd: `ssh user@server 'sudo systemctl daemon-reload'`
3. Restart service: `ssh user@server 'sudo systemctl restart module-name'`

## Setup Scripts

### Credential Sync Script

```bash
#!/bin/bash
# sync-credentials.sh - Sync credentials to server

SERVER=$1
if [ -z "$SERVER" ]; then
  echo "Usage: $0 user@server"
  exit 1
fi

echo "Syncing credentials to $SERVER..."

# Copy deploy key
scp ~/.datacore/env/credentials/deploy_key $SERVER:~/.ssh/deploy_key
ssh $SERVER 'chmod 600 ~/.ssh/deploy_key'

# Copy environment file
scp ~/.datacore/env/.env $SERVER:~/config/module.env
ssh $SERVER 'chmod 600 ~/config/module.env'

# Verify
ssh $SERVER '
  echo "Deploy key:" && ls -la ~/.ssh/deploy_key
  echo "Env file:" && ls -la ~/config/module.env
  echo "Git auth test:" && ssh -T git@github.com 2>&1 | grep successfully
'

echo "Done! Verify with: ssh $SERVER 'systemctl restart module-name'"
```

### Audit Script

```bash
#!/bin/bash
# audit-server.sh - Audit server credential parity

SERVER=$1
LOCAL_ENV=~/.datacore/env/.env

echo "=== Credential Audit: $SERVER ==="
echo

echo "1. SSH Connectivity"
ssh $SERVER 'echo "✓ SSH OK"' || echo "✗ SSH FAILED"
echo

echo "2. Deploy Key"
ssh $SERVER 'test -f ~/.ssh/deploy_key && echo "✓ Deploy key exists" || echo "✗ Deploy key missing"'
ssh $SERVER 'stat -c "%a" ~/.ssh/deploy_key 2>/dev/null | grep -q "600" && echo "✓ Permissions OK (600)" || echo "✗ Permissions wrong"'
echo

echo "3. Git Authentication"
ssh $SERVER 'ssh -T git@github.com 2>&1 | grep -q successfully && echo "✓ GitHub auth OK" || echo "✗ GitHub auth failed"'
echo

echo "4. Environment Variables"
for var in ANTHROPIC_API_KEY POSTHOG_API_KEY; do
  local_val=$(grep "^$var=" $LOCAL_ENV 2>/dev/null | cut -d= -f2 | head -c 20)
  server_val=$(ssh $SERVER "grep '^$var=' ~/config/*.env 2>/dev/null | cut -d= -f2 | head -c 20")

  if [ "$local_val" = "$server_val" ]; then
    echo "✓ $var matches"
  else
    echo "✗ $var mismatch"
  fi
done
echo

echo "5. Repository Structure"
ssh $SERVER 'test -d ~/Data/.git && echo "✓ Root repo exists" || echo "✗ Root repo missing"'
ssh $SERVER 'test -d ~/Data/0-personal/.git && echo "✓ Personal space exists" || echo "⚠ Personal space missing (may be intentional)"'
echo

echo "=== Audit Complete ==="
```

## Module-Specific Sections

Modules should extend this checklist with module-specific items. Example:

```markdown
### Module Name: Nightshift

Additional items beyond universal checklist:

- [ ] Systemd timers installed (`nightshift-overnight.timer`, `nightshift-today.timer`)
- [ ] Scheduler CLI functional (`nightshift scheduler status`)
- [ ] Queue database initialized (`~/Data/.datacore/state/nightshift/queue.db`)
- [ ] Execution logs directory exists (`~/Data/0-personal/0-inbox/`)

See: `.datacore/modules/nightshift/SERVER.md` for full checklist
```

## Automation

### Pre-Flight Checklist (Before Deploy)

```bash
# Run before deploying any module
python .datacore/lib/deployment_preflight.py --module nightshift --server user@server
```

### Continuous Monitoring

```bash
# Add to cron for weekly audit
0 9 * * 1 /path/to/audit-server.sh user@server | mail -s "Weekly Credential Audit" admin@example.com
```

## Related Documentation

- [DIP-0018](../dips/DIP-0018-credential-management.md) - Credential management specification (if exists)
- [Privacy Policy](privacy-policy.md) - Data classification levels
- [Nightshift Server Ops](../modules/nightshift/SERVER.md) - Most comprehensive example
- Module-specific setup guides in each module's `docs/` directory

## Maintenance

This checklist should be updated when:

- New credential types are introduced
- New modules add deployment requirements
- Security best practices change
- Common issues are discovered

**Last Updated**: 2026-03-26
**Version**: 1.0.0
