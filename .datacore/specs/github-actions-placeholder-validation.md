# GitHub Actions Placeholder Validation Step

**Purpose**: Prevent production deployments with placeholder values in configuration files that cause silent failures (401s, connection refused, etc.)

**Target**: Fairdrop MCP Server deployment workflow

**Patterns Detected**: `YOUR_`, `TODO`, `CHANGEME`, `PLACEHOLDER`, `REPLACE_ME`, `FIXME`, `XXX`

---

## Quick Integration

Add this step to `.github/workflows/deploy.yml` **BEFORE** the "Deploy to Production" step:

```yaml
    - name: Validate Configuration - No Placeholders
      run: |
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "🔍 Scanning for placeholder values..."
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

        # Patterns to detect
        PATTERNS="YOUR_|TODO|CHANGEME|PLACEHOLDER|REPLACE_ME|FIXME|XXX"

        # Find config files (adjust extensions as needed for your project)
        CONFIG_FILES=$(find . -type f \( \
          -name "*.env*" \
          -o -name ".env" \
          -o -name "*.config.js" \
          -o -name "*.config.ts" \
          -o -name "*.config.json" \
          -o -name "config.json" \
          -o -name "config.yaml" \
          -o -name "config.yml" \
          -o -name "production.json" \
          -o -name "production.yaml" \
          -o -name "secrets.json" \
        \) \
        -not -path "*/node_modules/*" \
        -not -path "*/.git/*" \
        -not -path "*/dist/*" \
        -not -path "*/build/*" \
        -not -path "*/.next/*" \
        -not -path "*/coverage/*" \
        2>/dev/null)

        if [ -z "$CONFIG_FILES" ]; then
          echo "⚠️  Warning: No configuration files found"
          echo "Files searched for:"
          echo "  - *.env*, .env"
          echo "  - *.config.{js,ts,json}, config.{json,yaml,yml}"
          echo "  - production.{json,yaml}, secrets.json"
          echo ""
          echo "If your config files use different naming, update the find command."
          exit 0
        fi

        echo "📁 Configuration files to validate:"
        echo "$CONFIG_FILES" | sed 's/^/  /'
        echo ""

        # Search for placeholder patterns
        VIOLATIONS=$(echo "$CONFIG_FILES" | xargs grep -E "$PATTERNS" 2>/dev/null || true)

        if [ -n "$VIOLATIONS" ]; then
          echo "❌ VALIDATION FAILED"
          echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
          echo ""
          echo "Found placeholder values in configuration files:"
          echo ""
          echo "$VIOLATIONS" | while IFS=: read -r file line content; do
            echo "  📄 File: $file"
            echo "  📍 Line: $line"
            echo "  ⚠️  Content: $content"
            echo ""
          done
          echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
          echo "🚫 DEPLOYMENT BLOCKED"
          echo ""
          echo "Action required:"
          echo "  1. Replace all placeholder values with actual configuration"
          echo "  2. Ensure secrets are stored in GitHub Secrets"
          echo "  3. Update config files to use environment variables"
          echo "  4. Re-run the deployment workflow"
          echo ""
          echo "Patterns blocked: YOUR_, TODO, CHANGEME, PLACEHOLDER, REPLACE_ME, FIXME, XXX"
          echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
          exit 1
        fi

        echo "✅ VALIDATION PASSED"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "No placeholder values detected in configuration files."
        echo "Safe to proceed with deployment."
        echo ""
```

---

## Full Workflow Example

Here's how the complete deployment workflow should look with the validation step properly positioned:

```yaml
name: Deploy Fairdrop MCP Server

on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  deploy:
    runs-on: ubuntu-latest

    steps:
      # ===== SETUP =====
      - name: Checkout code
        uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'

      # ===== BUILD =====
      - name: Install dependencies
        run: npm ci

      - name: Run linter
        run: npm run lint
        continue-on-error: false

      - name: Run tests
        run: npm test
        continue-on-error: false

      - name: Build application
        run: npm run build

      # ===== STAGING DEPLOYMENT =====
      - name: Deploy to Staging
        run: |
          # Your staging deployment commands
          echo "Deploying to staging environment..."

      - name: Run Smoke Tests (Staging)
        run: |
          # Your smoke test commands
          npm run smoke-tests:staging

      # ===== VALIDATION (CRITICAL - BEFORE PRODUCTION) =====
      - name: Validate Configuration - No Placeholders
        run: |
          echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
          echo "🔍 Scanning for placeholder values..."
          echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

          PATTERNS="YOUR_|TODO|CHANGEME|PLACEHOLDER|REPLACE_ME|FIXME|XXX"

          CONFIG_FILES=$(find . -type f \( \
            -name "*.env*" \
            -o -name ".env" \
            -o -name "*.config.js" \
            -o -name "*.config.ts" \
            -o -name "*.config.json" \
            -o -name "config.json" \
            -o -name "config.yaml" \
            -o -name "config.yml" \
            -o -name "production.json" \
            -o -name "production.yaml" \
            -o -name "secrets.json" \
          \) \
          -not -path "*/node_modules/*" \
          -not -path "*/.git/*" \
          -not -path "*/dist/*" \
          -not -path "*/build/*" \
          -not -path "*/.next/*" \
          -not -path "*/coverage/*" \
          2>/dev/null)

          if [ -z "$CONFIG_FILES" ]; then
            echo "⚠️  Warning: No configuration files found"
            exit 0
          fi

          echo "📁 Configuration files to validate:"
          echo "$CONFIG_FILES" | sed 's/^/  /'
          echo ""

          VIOLATIONS=$(echo "$CONFIG_FILES" | xargs grep -E "$PATTERNS" 2>/dev/null || true)

          if [ -n "$VIOLATIONS" ]; then
            echo "❌ VALIDATION FAILED"
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            echo ""
            echo "Found placeholder values in configuration files:"
            echo ""
            echo "$VIOLATIONS" | while IFS=: read -r file line content; do
              echo "  📄 File: $file"
              echo "  📍 Line: $line"
              echo "  ⚠️  Content: $content"
              echo ""
            done
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            echo "🚫 DEPLOYMENT BLOCKED"
            echo ""
            echo "Patterns blocked: YOUR_, TODO, CHANGEME, PLACEHOLDER, REPLACE_ME, FIXME, XXX"
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            exit 1
          fi

          echo "✅ VALIDATION PASSED"
          echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
          echo "No placeholder values detected. Safe to deploy."
          echo ""

      # ===== PRODUCTION READINESS GATE =====
      - name: Production Readiness Gate
        run: |
          echo "All pre-production checks passed:"
          echo "  ✅ Tests passed"
          echo "  ✅ Build successful"
          echo "  ✅ Staging deployment verified"
          echo "  ✅ Configuration validated (no placeholders)"
          echo ""
          echo "Ready for production deployment..."

      # ===== PRODUCTION DEPLOYMENT =====
      - name: Deploy to Production
        env:
          SSH_PRIVATE_KEY: ${{ secrets.SSH_DEPLOY_KEY }}
          PRODUCTION_HOST: 164.90.215.90
          PRODUCTION_USER: deploy
        run: |
          # Setup SSH
          mkdir -p ~/.ssh
          echo "$SSH_PRIVATE_KEY" > ~/.ssh/deploy_key
          chmod 600 ~/.ssh/deploy_key
          ssh-keyscan -H $PRODUCTION_HOST >> ~/.ssh/known_hosts

          # Deploy to production server
          rsync -avz -e "ssh -i ~/.ssh/deploy_key" \
            --exclude node_modules \
            --exclude .git \
            ./ $PRODUCTION_USER@$PRODUCTION_HOST:/opt/fairdrop-mcp/

          # Restart service
          ssh -i ~/.ssh/deploy_key $PRODUCTION_USER@$PRODUCTION_HOST \
            "cd /opt/fairdrop-mcp && npm ci --production && pm2 restart fairdrop-mcp"

      # ===== POST-DEPLOYMENT VERIFICATION =====
      - name: Health Check (Production)
        run: |
          echo "Waiting for service to start..."
          sleep 10

          # Health check endpoint (adjust as needed)
          curl -f http://164.90.215.90:3000/health || exit 1

          echo "✅ Production deployment successful and verified"
```

---

## Standalone Validation Command

For **manual validation** before committing or in pre-commit hooks:

```bash
# Single-line command (copy-paste ready)
find . -type f \( -name "*.env*" -o -name ".env" -o -name "*.config.js" -o -name "*.config.ts" -o -name "*.config.json" -o -name "config.json" -o -name "config.yaml" -o -name "config.yml" -o -name "production.json" -o -name "production.yaml" -o -name "secrets.json" \) -not -path "*/node_modules/*" -not -path "*/.git/*" -not -path "*/dist/*" -not -path "*/build/*" -not -path "*/.next/*" -not -path "*/coverage/*" 2>/dev/null | xargs grep -E "YOUR_|TODO|CHANGEME|PLACEHOLDER|REPLACE_ME|FIXME|XXX" && echo "❌ Found placeholders - fix before deployment" || echo "✅ No placeholders detected"
```

### Pretty version with detailed output:

```bash
#!/bin/bash
# validate-config.sh - Run this before deploying

echo "🔍 Validating configuration files..."

PATTERNS="YOUR_|TODO|CHANGEME|PLACEHOLDER|REPLACE_ME|FIXME|XXX"

CONFIG_FILES=$(find . -type f \( \
  -name "*.env*" \
  -o -name ".env" \
  -o -name "*.config.js" \
  -o -name "*.config.ts" \
  -o -name "*.config.json" \
  -o -name "config.json" \
  -o -name "config.yaml" \
  -o -name "config.yml" \
  -o -name "production.json" \
  -o -name "production.yaml" \
  -o -name "secrets.json" \
\) \
-not -path "*/node_modules/*" \
-not -path "*/.git/*" \
-not -path "*/dist/*" \
-not -path "*/build/*" \
-not -path "*/.next/*" \
-not -path "*/coverage/*" \
2>/dev/null)

if [ -z "$CONFIG_FILES" ]; then
  echo "⚠️  No configuration files found"
  exit 0
fi

echo "Files to check:"
echo "$CONFIG_FILES" | sed 's/^/  /'
echo ""

VIOLATIONS=$(echo "$CONFIG_FILES" | xargs grep -Hn -E "$PATTERNS" 2>/dev/null || true)

if [ -n "$VIOLATIONS" ]; then
  echo "❌ VALIDATION FAILED - Found placeholders:"
  echo ""
  echo "$VIOLATIONS"
  echo ""
  echo "🚫 Fix these before deploying to production"
  exit 1
fi

echo "✅ All configuration files validated - no placeholders found"
exit 0
```

---

## Integration Checklist

### Step 1: Add the validation step
- [ ] Open `.github/workflows/deploy.yml` in your Fairdrop MCP repository
- [ ] Locate the "Deploy to Production" step
- [ ] Insert the "Validate Configuration - No Placeholders" step **immediately before** it
- [ ] Ensure the validation step is NOT inside a conditional block that could skip it

### Step 2: Customize file patterns (if needed)
The default validation checks these file types:
- `.env*` and `.env` files
- `*.config.{js,ts,json}` files
- `config.{json,yaml,yml}` files
- `production.{json,yaml}` files
- `secrets.json` files

**If your Fairdrop MCP server uses different config file names**, update this section:

```bash
CONFIG_FILES=$(find . -type f \( \
  -name "*.env*" \
  -o -name ".env" \
  -o -name "*.config.js" \
  # ADD YOUR CUSTOM PATTERNS HERE
  -o -name "your-custom-config.json" \
\) \
```

### Step 3: Test the validation
Create a test branch and add a placeholder to verify it works:

```bash
# In a test branch
echo "API_KEY=YOUR_API_KEY_HERE" >> .env.production
git add .env.production
git commit -m "test: verify placeholder detection"
git push origin test-placeholder-validation
```

The GitHub Actions workflow should **FAIL** with a clear error message showing the placeholder.

### Step 4: Production deployment
Once validated:
- [ ] Merge the workflow update to `main`
- [ ] Verify the validation step appears in the workflow runs
- [ ] Confirm production deployments are blocked if placeholders are detected

---

## What This Prevents

This validation step will catch and block deployments with:

| Pattern | Example | Failure Mode Prevented |
|---------|---------|------------------------|
| `YOUR_` | `API_KEY=YOUR_API_KEY` | 401 Unauthorized |
| `TODO` | `# TODO: Add real endpoint` | Connection refused |
| `CHANGEME` | `PASSWORD=CHANGEME` | Authentication failure |
| `PLACEHOLDER` | `URL=PLACEHOLDER_URL` | DNS resolution failure |
| `REPLACE_ME` | `TOKEN=REPLACE_ME` | 403 Forbidden |
| `FIXME` | `# FIXME: Update before prod` | Runtime errors |
| `XXX` | `SECRET=XXX` | Service unavailable |

---

## Grep Pattern Reference

The core grep command uses this pattern:

```bash
grep -E "YOUR_|TODO|CHANGEME|PLACEHOLDER|REPLACE_ME|FIXME|XXX"
```

**Pattern breakdown:**
- `-E` = Extended regex (allows `|` for OR logic)
- `YOUR_` = Matches any text containing "YOUR_"
- `TODO` = Matches the word "TODO"
- `CHANGEME` = Matches "CHANGEME"
- `PLACEHOLDER` = Matches "PLACEHOLDER"
- `REPLACE_ME` = Matches "REPLACE_ME"
- `FIXME` = Matches "FIXME"
- `XXX` = Matches "XXX"

**To add custom patterns:**

```bash
# Add to the PATTERNS variable
PATTERNS="YOUR_|TODO|CHANGEME|PLACEHOLDER|REPLACE_ME|FIXME|XXX|localhost|example\.com|192\.168\."
```

---

## Workflow Placement

```yaml
jobs:
  deploy:
    steps:
      # ... earlier steps (build, test, staging) ...

      # ⚠️ CRITICAL: Place validation HERE (before production deploy)
      - name: Validate Configuration - No Placeholders
        run: |
          # ... validation script ...

      # ✅ Production deployment (only runs if validation passes)
      - name: Deploy to Production
        run: |
          # ... deployment commands ...
```

**Why this placement matters:**
- Runs **after** staging deployment and smoke tests (code is verified)
- Runs **before** production deployment (catches config issues)
- Fails fast (no wasted time deploying bad configs)
- Clear separation between validation and deployment

---

## Expected Output

### Success (no placeholders):
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔍 Scanning for placeholder values...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📁 Configuration files to validate:
  ./.env.production
  ./src/config/production.json
  ./config.yaml

✅ VALIDATION PASSED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
No placeholder values detected in configuration files.
Safe to proceed with deployment.
```

### Failure (placeholders found):
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔍 Scanning for placeholder values...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📁 Configuration files to validate:
  ./.env.production

❌ VALIDATION FAILED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Found placeholder values in configuration files:

  📄 File: ./.env.production
  📍 Line: 12
  ⚠️  Content: API_KEY=YOUR_API_KEY_HERE

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚫 DEPLOYMENT BLOCKED

Action required:
  1. Replace all placeholder values with actual configuration
  2. Ensure secrets are stored in GitHub Secrets
  3. Update config files to use environment variables
  4. Re-run the deployment workflow

Patterns blocked: YOUR_, TODO, CHANGEME, PLACEHOLDER, REPLACE_ME, FIXME, XXX
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Error: Process completed with exit code 1.
```

---

## Quick Copy-Paste Checklist

For the Fairdrop repository owner:

1. **Copy the validation step** (from "Quick Integration" section above)
2. **Open** `.github/workflows/deploy.yml`
3. **Find** the "Deploy to Production" step
4. **Paste** the validation step immediately before it
5. **Commit** with message: `ci: add placeholder validation before production deploy`
6. **Test** on a feature branch with a placeholder in a `.env` file
7. **Verify** the workflow blocks deployment and shows clear error
8. **Merge** to main

---

## Files to Create in Fairdrop Repository (Optional)

### 1. Validation script: `scripts/validate-config.sh`

```bash
#!/bin/bash
# Standalone configuration validation script
# Usage: ./scripts/validate-config.sh

set -e

echo "🔍 Validating configuration files for placeholders..."

PATTERNS="YOUR_|TODO|CHANGEME|PLACEHOLDER|REPLACE_ME|FIXME|XXX"

CONFIG_FILES=$(find . -type f \( \
  -name "*.env*" \
  -o -name ".env" \
  -o -name "*.config.js" \
  -o -name "*.config.ts" \
  -o -name "*.config.json" \
  -o -name "config.json" \
  -o -name "config.yaml" \
  -o -name "config.yml" \
  -o -name "production.json" \
  -o -name "production.yaml" \
  -o -name "secrets.json" \
\) \
-not -path "*/node_modules/*" \
-not -path "*/.git/*" \
-not -path "*/dist/*" \
-not -path "*/build/*" \
-not -path "*/.next/*" \
-not -path "*/coverage/*" \
2>/dev/null)

if [ -z "$CONFIG_FILES" ]; then
  echo "⚠️  No configuration files found"
  exit 0
fi

echo "Files to check:"
echo "$CONFIG_FILES" | sed 's/^/  /'
echo ""

VIOLATIONS=$(echo "$CONFIG_FILES" | xargs grep -Hn -E "$PATTERNS" 2>/dev/null || true)

if [ -n "$VIOLATIONS" ]; then
  echo "❌ VALIDATION FAILED - Found placeholders:"
  echo ""
  echo "$VIOLATIONS"
  echo ""
  echo "🚫 Fix these before deploying to production"
  exit 1
fi

echo "✅ All configuration files validated - no placeholders found"
exit 0
```

Make it executable:
```bash
chmod +x scripts/validate-config.sh
```

### 2. Pre-commit hook: `.husky/pre-commit` (if using Husky)

```bash
#!/bin/sh
. "$(dirname "$0")/_/husky.sh"

# Run config validation before commit
./scripts/validate-config.sh
```

---

## Support & Troubleshooting

### Issue: Validation step not running
**Cause**: Step is inside a conditional block or after production deploy
**Fix**: Ensure the step is at the correct position (see "Workflow Placement" above)

### Issue: False positives (legitimate uses of patterns)
**Solution**: Exclude specific files or use more specific patterns

```bash
# Exclude specific files
CONFIG_FILES=$(find . -type f \( -name "*.env*" -o -name ".env" \) \
  -not -path "*/node_modules/*" \
  -not -path "*/docs/*" \
  -not -path "*/examples/*" \
  2>/dev/null)
```

### Issue: Custom config files not checked
**Solution**: Add your file patterns to the `find` command (see "Step 2: Customize file patterns")

---

**Document version**: 1.0
**Created**: 2026-03-26
**Target**: Fairdrop MCP Server GitHub Actions workflow
**Prevents**: Silent deployment failures from placeholder values (401, connection refused)
