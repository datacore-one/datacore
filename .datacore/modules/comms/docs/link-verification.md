# Link Verification Gate

## Overview

All social posts (via Late API and X API) now include automated link verification before scheduling/posting. This prevents broken links, non-public URLs, and non-user-facing content from being shared.

**Purpose**: Protect brand credibility by ensuring all shared links are accessible and appropriate.

## Architecture

### Two Posting Paths

1. **Late API** (scheduled posts) → `LateAPIClient` → `LinkVerifier`
2. **X API** (immediate posts) → `XPoster` → `LinkVerifier`

Both paths use the same `LinkVerifier` library for consistent validation.

### Link Verification Rules

A URL passes verification if ALL of the following are true:

1. **HTTP 200 response** - Link is accessible
2. **User-facing content type** - One of:
   - `text/html` (web pages)
   - `application/pdf` (documents)
   - `image/*` (images)
   - `video/*` (videos)
3. **Not behind auth wall** - URL doesn't contain:
   - `/login`, `/signin`, `/auth`
   - `/admin`, `/dashboard`, `/console`
   - `accounts.google.com`, `login.microsoftonline.com`
4. **No redirect to auth** - Final URL after redirects doesn't match reject patterns

### Rejected Examples

- `https://example.com/404` → Non-200 status (404)
- `https://api.example.com/data.json` → Non-user-facing content type
- `https://example.com/admin/report` → Admin page pattern
- `https://secure.site/content` → Redirects to `/login`

## Usage

### Late API (Scheduled Posts)

```python
from late_api_wrapper import LateAPIClient

# Link verification enabled by default
client = LateAPIClient(api_key, verify_links=True)

# This will verify all URLs before scheduling
client.create_post(
    content="Check this out: https://example.com",
    platforms=['twitter'],
    scheduled_time="2026-03-27T09:00:00Z"
)

# Emergency bypass (use with caution)
client.create_post(
    content="...",
    skip_verification=True  # Only for trusted content
)
```

### X API (Immediate Posts)

```python
from x_poster import XPoster

# Link verification enabled by default
poster = XPoster(account='fds', verify_links=True)

# This will verify all URLs before posting
poster.post("Privacy matters: https://fairdatasociety.org")

# Reply with link verification
poster.reply("More info: https://docs.fairdatasociety.org", reply_to_id="123456")

# Emergency bypass (use with caution)
poster.post("...", skip_verification=True)
```

### Content Scheduler

The content scheduler uses Late API and automatically verifies links:

```bash
python3 content_scheduler.py week-march.md
# Automatically verifies all URLs in scheduled posts
```

### Autonomous Poster

Autonomous engagement replies use XPoster with link verification:

```python
from autonomous_poster import AutonomousPoster

# XPoster instance has verify_links=True by default
poster = XPoster(account='fds')
auto = AutonomousPoster(poster=poster, ...)

# Link verification runs before auto-posting
auto.process_draft(draft_text="...", target_tweet_id="...")
```

## Verification Process

### 1. Extract URLs

```python
verifier.extract_urls("Check https://example.com and https://test.org")
# → ['https://example.com', 'https://test.org']
```

### 2. Verify Each URL

For each URL:
1. Check against reject patterns (fast, no HTTP request)
2. Send HTTP HEAD request (faster than GET)
3. If HEAD not supported (405), fall back to GET
4. Validate status code (must be 200)
5. Validate content type (must be user-facing)
6. Check redirect destination (if redirected)

### 3. Reject or Pass

- **All URLs valid** → Post proceeds
- **Any URL fails** → `LinkVerificationError` raised with details
- **No URLs in text** → Skip verification (pass)

## Error Handling

### Verification Failures

When a link fails verification, the post is rejected with detailed error:

```
[LINK VERIFICATION FAILED - @fds]
Link verification failed:
  - https://example.com/404: Non-200 status code: 404
  - https://api.example.com/data: Non-user-facing content type: application/json

Post NOT sent. Fix links and retry.
```

### Retry Logic

1. **Late API**: Post not scheduled, error logged, manual intervention needed
2. **X API**: Post not sent, error to stderr, exception raised to caller
3. **Autonomous**: Escalated to human approval queue with failure reason

### Bypass Options

Use `skip_verification=True` only when:
- URL is known to be valid but fails verification (e.g., special headers required)
- Emergency posting situation
- Internal testing

**Never skip verification in production autonomous flows.**

## Testing

### Unit Tests

```bash
cd /home/gregor/Data/.datacore/modules/comms
python3 tests/run_link_verification_tests.py
```

Tests cover:
- Valid link verification
- Invalid link rejection (404, wrong content type)
- Posts without links (skipped)
- Skip verification flag
- Both post() and reply() methods

### Manual Testing

Test with real URLs:

```bash
# Test link verifier standalone
python3 /home/gregor/Data/.datacore/lib/link_verifier.py --url https://example.com

# Test with Late API
python3 -c "
from late_api_wrapper import LateAPIClient
import os
client = LateAPIClient(os.environ['LATE_API_KEY'])
client.create_post('Test: https://example.com/404', platforms=['twitter'])
"
```

### Integration Test

Create a test post with known good/bad URLs:

```python
from x_poster import XPoster
import os

poster = XPoster(account='fds', verify_links=True)

# Should PASS
poster.post("Privacy matters: https://fairdatasociety.org", skip_verification=False)

# Should FAIL (404)
try:
    poster.post("Broken: https://fairdatasociety.org/nonexistent-page-12345")
except Exception as e:
    print(f"Expected failure: {e}")
```

## Performance

- **HEAD request**: ~100-500ms per URL
- **GET fallback**: ~200-1000ms per URL
- **Rate limiting**: 0.5s delay between URLs (be nice to servers)
- **Timeout**: 10 seconds per URL

**For posts with multiple URLs**: Verification is sequential to avoid hammering servers.

**Impact on posting**: +1-2 seconds per post with 1-2 links. Acceptable trade-off for credibility protection.

## Monitoring

### Logs

Link verification failures are logged to:
1. **stderr** - Immediate visibility during posting
2. **Engagement state** - Autonomous poster escalations
3. **Late API response** - Scheduler errors

### Metrics to Track

- Link verification failure rate
- Top rejection reasons (404, auth wall, wrong content type)
- Bypass usage frequency (should be rare)
- False positives (valid URLs rejected)

Review monthly to tune reject patterns.

## Configuration

### Environment Variables

None required - link verification is built into both APIs.

### Customization

To adjust verification rules, edit `/home/gregor/Data/.datacore/lib/link_verifier.py`:

```python
# Add allowed content types
ALLOWED_CONTENT_TYPES = [
    'text/html',
    'application/pdf',
    'application/epub+zip',  # ebooks
    ...
]

# Add reject patterns
REJECT_PATTERNS = [
    r'/login',
    r'/private',  # custom pattern
    ...
]
```

## Troubleshooting

### False Positives

**Symptom**: Valid URL rejected by verifier

**Causes**:
1. Server requires specific User-Agent header
2. Server blocks bots
3. URL behind CDN with rate limiting
4. Unusual content type

**Solutions**:
1. Check URL in browser - does it load?
2. Test with link_verifier CLI: `python3 link_verifier.py --url <URL>`
3. If legitimate, add exception to verifier or use `skip_verification=True`

### Performance Issues

**Symptom**: Posting takes too long

**Causes**:
1. Slow server response
2. Multiple URLs in post
3. Timeouts

**Solutions**:
1. Increase timeout in `link_verifier.py` (default: 10s)
2. Use `skip_verification=True` for time-critical posts
3. Pre-verify URLs before adding to calendar

### Bypass Overuse

**Symptom**: Many posts using `skip_verification=True`

**Cause**: Legitimate URLs failing verification

**Solution**: Fix verifier patterns rather than bypassing. Review rejection logs to identify patterns.

## Future Enhancements

1. **URL cache** - Cache verification results for 24h to avoid re-checking
2. **Async verification** - Parallel URL checking for posts with multiple links
3. **Smart retry** - Auto-retry on transient errors (429, 503)
4. **Allowlist** - Trusted domains skip verification (e.g., fairdatasociety.org)
5. **Metrics dashboard** - Visual tracking of verification stats

## Related Files

- `/home/gregor/Data/.datacore/lib/link_verifier.py` - Core verification logic
- `/home/gregor/Data/.datacore/lib/late_api_wrapper.py` - Late API integration
- `/home/gregor/Data/.datacore/modules/comms/lib/x_poster.py` - X API integration
- `/home/gregor/Data/.datacore/modules/comms/lib/content_scheduler.py` - Scheduler integration
- `/home/gregor/Data/.datacore/modules/comms/tests/test_link_verification.py` - Tests

## Support

For issues or questions:
1. Check this documentation
2. Review test cases for examples
3. Test with CLI tool: `python3 link_verifier.py --url <URL>`
4. File issue in datacore repository

---

**Last Updated**: 2026-03-26
**Author**: AI Task Executor (nightshift-L3064)
