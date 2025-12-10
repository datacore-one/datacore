# Deploy Landing Page

Deploy a landing page to production.

## Usage

```
/deploy-landing <site>
```

Where `<site>` is one of:
- `datacore.one` - Deploys from `2-datacore/1-projects/website/`
- `softwareofyou.com` - Deploys from `2-datacore/1-projects/softwareofyou/`

## What This Does

1. Validates the source directory has an `index.html`
2. Copies all files to the production server via SCP
3. Verifies the deployment with an HTTP check

## Prerequisites

- SSH deploy key at `.datacore/env/credentials/deploy_key`
- Server IP configured in `.datacore/env/.env` as `DO_DROPLET_IP`

## Example

```
/deploy-landing softwareofyou.com
```

## Implementation

Run the deploy script:

```bash
~/Data/2-datacore/1-departments/dev/infrastructure/campaigns-module/scripts/deploy-site.sh "$SITE"
```

Replace `$SITE` with the argument provided.
