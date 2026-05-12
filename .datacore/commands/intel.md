---
name: intel
description: Social media intelligence analysis — extract content from X posts or YouTube videos, analyze entities and insights, route to knowledge base and CRM.
user_invocable: true
triggers:
  - analyze this post
  - analyze this tweet
  - social intel
recall:
  # DIP-0029 default — engrams scoped to this command + tag-matched.
  scopes:
    - command:intel
  tags:
    - intel
---

# /intel Command

## Command Context

### Agents This Command Invokes

| Agent | Purpose |
|-------|---------|
| `knowledge-extractor` | Fetches URL and creates a literature note |
| `social-intel-analyzer` | Analyzes literature note, proposes routing plan, gets user approval |
| `social-intel-writer` | Creates outputs (spawned by analyzer after approval) |

### Integration Points

- **knowledge-extractor** — content acquisition layer
- **social-intel-analyzer** — entity extraction, insight routing
- **CRM / 3-knowledge/** — output destinations

---

Social media intelligence: extract content from X posts or YouTube videos, analyze entities and insights, route to knowledge base and CRM.

## Usage

```
/intel <url>
/intel <url> --deep
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `<url>` | X post URL (`x.com/*/status/*` or `twitter.com/*/status/*`) or YouTube URL (`youtube.com/watch?v=*` or `youtu.be/*`) |
| `--deep` | Enable deep analysis mode (multi-hop entity expansion) |

## Workflow

### Step 1: Parse Input

Extract the URL from `$ARGUMENTS`. Check for the `--deep` flag and set depth accordingly:
- `--deep` present → depth = `deep`
- No flag → depth = `1-hop`

### Step 2: Validate URL

Accept only:
- X / Twitter: `x.com/*/status/*` or `twitter.com/*/status/*`
- YouTube: `youtube.com/watch?v=*` or `youtu.be/*`

If the URL does not match either pattern, show this error and stop:

```
This command handles X posts and YouTube videos.
For other URLs, use the knowledge-extractor directly.
```

If no URL is present in the arguments, ask the user to provide one.

### Step 3: Check for Existing Literature Note

Call `datacore.search` with the URL as the query to check if content was already extracted.

- If a matching literature note is found: use it directly (skip Step 4).
- If not found: proceed to Step 4.

### Step 4: Acquire Content

Spawn the `knowledge-extractor` agent with the URL. Wait for it to create the literature note and return its path.

If the fetch fails (agent reports an error or returns no content), show this error and stop:

```
Could not fetch content. Try opening the URL in your browser first, then retry.
```

### Step 5: Analyze and Route

Spawn the `social-intel-analyzer` agent with:
- The literature note path
- The depth mode (`1-hop` or `deep`)
- The source URL

The analyzer will:
1. Extract entities, insights, and signals from the note
2. Present a routing plan to the user (what to create, where to route it)
3. Wait for user approval before proceeding
4. Spawn `social-intel-writer` to create approved outputs

### Step 6: Report

After the analyzer and writer finish, display a consolidated report:

```
Intel complete.

Content: [literature note path]
Depth: [1-hop | deep]

Created:
- [list of outputs created: zettels, CRM entries, reference notes, etc.]

[Any follow-up suggestions or open questions]
```

## Error Handling

| Condition | Response |
|-----------|----------|
| URL is not X or YouTube | "This command handles X posts and YouTube videos. For other URLs, use the knowledge-extractor directly." |
| Fetch failure | "Could not fetch content. Try opening the URL in your browser first, then retry." |
| No entities found | "No significant entities or insights found. Literature note created at [path]." |

## Examples

```
/intel https://x.com/naval/status/1234567890
/intel https://twitter.com/elonmusk/status/1234567890 --deep
/intel https://youtube.com/watch?v=dQw4w9WgXcQ
/intel https://youtu.be/dQw4w9WgXcQ --deep
```
