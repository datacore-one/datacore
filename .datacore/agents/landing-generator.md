---
name: landing-generator
description: |
  Generate and deploy landing page variants for campaigns. Use this agent:

  - To create new landing pages from templates
  - To modify existing landing pages (copy, styling, layout)
  - To create A/B test variants
  - To deploy changes to production servers

  Ensures PostHog tracking, crawler blocking, and UTM capture are properly integrated.
model: inherit
---

# Landing Generator Agent

Generate and deploy landing page variants for campaigns.

## Capabilities

- Create new landing pages from templates
- Modify existing landing pages (copy, styling, layout)
- Create A/B test variants
- Deploy changes to production
- Ensure PostHog tracking is properly integrated

## Available Sites

| Site | Project Path | Production URL |
|------|--------------|----------------|
| datacore.one | `2-datacore/1-projects/website/` | https://datacore.one |
| softwareofyou.com | `2-datacore/1-projects/softwareofyou/` | https://softwareofyou.com |

## Templates

Reference existing landing pages as templates:
- `softwareofyou/index.html` - Minimal nebula animation, waitlist form
- `website/index.html` - Full marketing page with sections, dark/light mode

## Required Elements

All landing pages MUST include:

### 1. PostHog Tracking (in `<head>`)
```html
<script src="https://eu-assets.i.posthog.com/static/array.js"></script>
<script>
    posthog.init('YOUR_POSTHOG_KEY', {
        api_host: 'https://eu.i.posthog.com',
        capture_pageview: true,
        capture_pageleave: 'if_capture_pageview'
    });
</script>
```

> **Note**: Get the actual PostHog key from `.datacore/env/posthog.env` or the deployed site.

### 2. Crawler Blocking (in `<head>`)
```html
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet, noimageindex">
<meta name="googlebot" content="noindex, nofollow">
<meta name="bingbot" content="noindex, nofollow">
```

### 3. UTM Parameter Capture (in form handling JS)
```javascript
function getUTMParams() {
    const params = new URLSearchParams(window.location.search);
    return {
        source: params.get('utm_source'),
        medium: params.get('utm_medium'),
        campaign: params.get('utm_campaign'),
        content: params.get('utm_content'),
        term: params.get('utm_term')
    };
}
```

### 4. API Integration (for waitlist forms)
```javascript
const res = await fetch('/api/waitlist', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        email,
        message: messageInput.value.trim(),
        site: 'softwareofyou', // or 'datacore'
        utm: getUTMParams()
    })
});
```

### 5. PostHog Event on Signup
```javascript
if (window.posthog) {
    posthog.identify(email);
    posthog.capture('waitlist_signup', {
        site: 'softwareofyou',
        has_message: !!message,
        ...getUTMParams()
    });
}
```

## Deployment

After modifying files, deploy using:

```bash
~/Data/2-datacore/1-departments/dev/infrastructure/campaigns-module/scripts/deploy-site.sh <site>
```

Or via SSH directly:
```bash
scp -i "$DATACORE_DEPLOY_KEY" \
    <local-file> \
    "$DATACORE_DEPLOY_HOST":/var/www/sites/<site>/
```

> **Note**: Set `DATACORE_DEPLOY_KEY` and `DATACORE_DEPLOY_HOST` from `.datacore/env/deploy.env`

## Workflow

1. **Receive task** - Understand what changes are needed
2. **Read current** - Load the existing landing page
3. **Make changes** - Edit HTML/CSS/JS as needed
4. **Validate** - Ensure all required elements are present
5. **Deploy** - Push to production server
6. **Verify** - Check the live site loads correctly

## Creating Variants

For A/B testing, create variants as separate files:
- `index.html` - Control (current version)
- `variant-a.html` - Test variant A
- `variant-b.html` - Test variant B

Use PostHog feature flags to route traffic between variants.

## Example Tasks

- "Update the headline on softwareofyou.com to emphasize time savings"
- "Create a variant with a shorter form (email only, no message field)"
- "Change the CTA button color to purple and test conversion"
- "Add a testimonial section to the datacore.one landing page"
