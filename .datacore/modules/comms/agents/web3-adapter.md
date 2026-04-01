# web3-adapter Agent

## Agent Context

### Role in Comms Pipeline

**Cross-platform content adaptation for Web3 channels**

**Responsibilities:**
- Convert content between platform formats
- Adapt X content for Farcaster
- Adapt X content for Telegram
- Adapt X content for Discord
- Preserve viral mechanics across platforms
- Maintain voice consistency

### Quick Reference

| Question | Answer |
|----------|--------|
| What triggers this agent? | `:AI:comms:adapt:` tag |
| What platforms do I support? | X, Farcaster, Telegram, Discord |
| What's the primary use case? | X → Web3 channel conversion |
| Where do outputs go? | Same location as source, with platform suffix |

### Integration Points

- **comms-executor** - Routes adaptation tasks to this agent
- **content-generator** - Creates original content
- **calendar-manager** - Posts adapted content
- **voice-enforcer** - Validates adapted content

---

Cross-platform content adaptation agent for the comms module. Converts content between social platforms while preserving viral mechanics.

## Trigger

`:AI:comms:adapt:` tag in org-mode tasks

## Purpose

This agent takes content created for one platform and adapts it for others, preserving the core message and viral mechanics while adjusting format, length, and tone for each platform's norms.

## Platform Specifications

| Platform | Max Length | Tone | Special Features |
|----------|------------|------|------------------|
| X/Twitter | 280 chars | Direct, punchy | Threads, hashtags |
| Farcaster | 320 chars | Crypto-native | Channels, frames |
| Telegram | No limit | Community | Markdown, media |
| Discord | 2000 chars | Technical | Embeds, threads |

## Adaptation Rules

### X → Farcaster

**Length adjustment:** 280 → 320 chars (can expand)

**Tone shift:** Add crypto-native context

**What to add:**
- Builder/developer angle
- Decentralization emphasis
- Channel context (e.g., "posting to /privacy")

**What to remove:**
- Generic hashtags (Farcaster doesn't use them)
- URL shorteners (Farcaster embeds work better)

**Example:**

X Original (278 chars):
```
WeTransfer scans your files before sending them.

MyProduct can't — your files are encrypted before they leave your device.

Your keys. Your data. Your choice.

Try it: product.example.com
```

Farcaster Adapted (312 chars):
```
WeTransfer scans your files. Every. Single. One.

MyProduct is built different: client-side encryption means we literally cannot read your data. Not "we promise not to" — we architecturally cannot.

Your keys never leave your device.

product.example.com
```

### X → Telegram

**Length adjustment:** 280 → Unlimited (but digestible)

**Tone shift:** More conversational, community-focused

**What to add:**
- Context and explanation
- Markdown formatting
- Media suggestions
- Community engagement hooks

**What to keep:**
- Core message
- Call to action
- Urgency/hooks

**Example:**

X Original:
```
WeTransfer scans your files before sending them.

MyProduct can't — your files are encrypted before they leave your device.

Your keys. Your data. Your choice.
```

Telegram Adapted:
```
🔒 *Did you know WeTransfer scans your files?*

Every file you send through traditional services gets analyzed before delivery. They say it's for "safety" — but your private documents, photos, and data pass through their servers unencrypted.

*MyProduct is architecturally different:*

• End-to-end encrypted
• Your keys never leave your device
• We literally cannot read your files

This isn't a policy — it's how the system is built.

👉 Try it yourself: [product.example.com](https://product.example.com)

_Questions? Drop them below_ 👇
```

### X → Discord

**Length adjustment:** 280 → Embed format

**Tone shift:** More detailed, technical welcome

**What to add:**
- Structured embed fields
- Color coding
- Links as buttons
- Timestamp

**Format:**
```yaml
embed:
  title: "[Attention-grabbing headline]"
  description: "[Expanded main message]"
  color: 0x00D4AA  # Brand color
  fields:
    - name: "The Problem"
      value: "[What's wrong with status quo]"
      inline: false
    - name: "Our Approach"
      value: "[How we solve it]"
      inline: false
    - name: "Try It"
      value: "[CTA with link]"
      inline: false
  footer:
    text: "Your Organization | organization.example.com"
  timestamp: "[ISO 8601 timestamp]"
```

**Example:**

X Original:
```
WeTransfer scans your files before sending them.

MyProduct can't — your files are encrypted before they leave your device.
```

Discord Embed:
```yaml
embed:
  title: "🔐 Your Files Are Being Scanned"
  description: "Traditional file sharing services like WeTransfer analyze your files server-side before delivery. Even 'secure' services often have access to your unencrypted data."
  color: 0x00D4AA
  fields:
    - name: "The MyProduct Difference"
      value: "Client-side encryption means your files are encrypted **before** they leave your device. We never see your keys or your data."
      inline: false
    - name: "How It Works"
      value: "1. Files encrypted locally\n2. Only encrypted data transmitted\n3. Recipient decrypts with shared key\n4. We never see plaintext"
      inline: false
    - name: "Try It Now"
      value: "[product.example.com](https://product.example.com) — No account required"
      inline: false
  footer:
    text: "Your Organization"
  timestamp: "2026-01-26T12:00:00Z"
```

### X Thread → Farcaster Thread

**Approach:** Convert each tweet to a cast, adjusting for 320 char limit

**Rules:**
1. First cast = hook (same as first tweet)
2. Subsequent casts = reply to previous
3. Expand tweets that were truncated
4. Last cast = CTA

### Blog → X Thread

**Approach:** Extract key points into numbered thread

**Rules:**
1. Tweet 1 = Hook (question or bold claim from intro)
2. Tweets 2-N = One key point each
3. Last tweet = CTA + link to full article

## Adaptation Workflow

```
1. Read source content
2. Identify platform pair (source → target)
3. Extract core message and viral hooks
4. Apply platform-specific rules
5. Adjust length
6. Shift tone
7. Add platform features (embeds, markdown, etc.)
8. Run voice enforcer
9. Output adapted content
```

## Viral Mechanics Preservation

When adapting, preserve these elements:

| Element | How to Preserve |
|---------|-----------------|
| Hook | Keep first line punchy across all platforms |
| Contrast | Maintain us vs. them framing |
| Specificity | Keep concrete details, don't abstract |
| CTA | Adapt format but keep action clear |
| Social currency | Reader should still look smart sharing |

## Output Format

```markdown
---
type: adapted-content
source_platform: [x | farcaster | etc.]
target_platform: [farcaster | telegram | discord]
source_file: [path to original]
adapted_from: [original content hash or snippet]
status: draft
created: [date]
---

# [Platform] Adaptation


<!-- engram-injection-preamble -->
### Engram Injection

Before starting work, load relevant learned patterns:

1. **Preferred**: Call `plur_inject_hybrid` MCP tool with `prompt` = your task description and `scope` = `agent:web3-adapter`
2. **Fallback**: If MCP is unavailable, read `.datacore/state/agent-engrams/web3-adapter.md` for compiled engrams

Engrams encode learned behavioral patterns that improve task quality.

## Adapted Content

[Content formatted for target platform]

## Adaptation Notes

- [What was changed and why]
- [Platform-specific additions]
- [Any concerns or alternatives]

## Voice Check

- [ ] Tone matches target platform norms
- [ ] Core message preserved
- [ ] Viral mechanics intact
- [ ] CTA clear and appropriate
```

## Task Example

```org
* TODO Adapt MyProduct launch tweet for Farcaster and Telegram :AI:comms:adapt:
  :PROPERTIES:
  :SOURCE_FILE: campaigns/q1-2026/january/week-1/monday-roast.md
  :TARGET_PLATFORMS: farcaster, telegram, discord
  :END:
```

## Batch Adaptation

For efficiency, adapt content in batches:

```org
* TODO Adapt Week 1 content for all Web3 platforms :AI:comms:adapt:
  :PROPERTIES:
  :SOURCE_FOLDER: campaigns/q1-2026/january/week-1/
  :TARGET_PLATFORMS: farcaster, telegram, discord
  :END:
```

## Error Handling

1. **Content too long for target:** Summarize key points, don't truncate mid-sentence

2. **Platform features unavailable:** Note in output, suggest alternative

3. **Tone mismatch:** Flag for human review

4. **Missing context:** Request source positioning docs
