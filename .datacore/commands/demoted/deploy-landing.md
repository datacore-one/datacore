# Deploy Landing Page

## Command Context

### When to Reference Module Specification

**Always reference when:**
- Deploying to production servers
- Validating source directory
- Checking deployment credentials
- Verifying HTTP after deploy

**Key decisions this DIP informs:**
- Deployment script location
- Credential storage pattern

### Quick Reference

| Question | Answer |
|----------|--------|
| SSH key? | `.datacore/env/credentials/deploy_key` |
| Server IP? | `.datacore/env/.env` → `DO_DROPLET_IP` |
| Sites supported? | Configured in deploy script (e.g., `example-product.com`) |

### Agents This Command Invokes

| Agent | Purpose |
|-------|---------|
| `landing-generator` | Page generation |

### Integration Points

- **Deploy script** - Shell-based SCP deployment
- **Credentials** - Gitignored env files

---

Deploy a landing page to production.

## Usage

```
/deploy-landing <site>
```

Where `<site>` is one of your configured sites. For example:
- `example-product.com` - Deploys from `2-projectspace/1-projects/website/`
- `example-landing.com` - Deploys from `2-projectspace/1-projects/landing/`

## What This Does

1. Validates the source directory has an `index.html`
2. Copies all files to the production server via SCP
3. Verifies the deployment with an HTTP check

## Prerequisites

- SSH deploy key at `.datacore/env/credentials/deploy_key`
- Server IP configured in `.datacore/env/.env` as `DO_DROPLET_IP`

## Example

```
/deploy-landing example-product.com
```

## Implementation

Run the deploy script:

```bash
~/Data/2-projectspace/1-departments/dev/infrastructure/campaigns-module/scripts/deploy-site.sh "$SITE"
```

Replace `$SITE` with the argument provided.
