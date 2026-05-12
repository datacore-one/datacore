---
name: create-podcast
description: create-podcast command
recall:
  # DIP-0029 default — engrams scoped to this command + tag-matched.
  scopes:
    - command:create-podcast
  tags:
    - create-podcast
---

# Create Podcast Command

## Command Context

### When to Reference Research Module

**Always reference when:**
- User wants custom podcast on specific topic outside daily processing
- Need to compile research on focused subject (competitive analysis, market trends)
- Want to create learning material from curated sources
- Testing podcast generation before nightshift processing
- Need podcast from specific research_learning.org section

**Key decisions this command informs:**
- Whether to provide URLs directly or pull from research_learning.org
- Optimal source count for topic depth (5-10 recommended)
- Custom instructions for podcast style (competitive, educational, market research)
- When to create multiple focused podcasts vs one broad podcast

### Quick Reference

| Question | Answer |
|----------|--------|
| What inputs does it accept? | Direct URLs, research_learning.org section, or interactive prompts |
| What's the optimal source count? | 5-10 sources for depth (min 3, max 12) |
| How long does generation take? | 5-10 minutes typically, timeout at 30 minutes |
| Where are podcasts saved? | 0-personal/content/podcasts/ or team space |

### Agents This Command Invokes

| Agent | Purpose |
|-------|---------|
| podcast-creator | Manages full podcast lifecycle: notebook creation, source addition, audio generation, download |

### Integration Points

- **podcast-creator agent** - Invoked with user-provided sources and topic
- **research_learning.org** - Optional source for pulling URLs by section
- **nlm CLI** - External NotebookLM integration tool
- **Podcast output directory** - Files saved to designated location
- **User confirmation** - Interactive prompts for source selection and settings

---

Create an ad-hoc NotebookLM podcast from provided URLs or a topic.

## Usage

```
/create-podcast [options]
```

## Input Options

### Option 1: Direct URLs
User provides specific URLs:
```
/create-podcast
URLs:
- https://article1.com
- https://article2.com
- https://article3.com
Topic: "Market Analysis Q4"
```

### Option 2: From research_learning.org Section
User specifies a section to pull from:
```
/create-podcast
Section: Project Alpha
Topic: "Competitive Landscape"
Max sources: 8
```

### Option 3: Interactive
Prompt user for inputs:
1. Ask for topic/title
2. Ask for source method (URLs or section)
3. Collect sources
4. Confirm before generating

## Workflow

1. **Gather Sources**
   - Validate URLs are accessible
   - Check count (3-10 optimal)
   - Warn if too many/few

2. **Invoke podcast-creator**
   ```json
   {
     "title": "[User topic]",
     "sources": ["url1", "url2", ...],
     "instructions": "[Based on topic type]",
     "duration_target": "30min",
     "output_path": "~/Data/0-personal/content/podcasts/"
   }
   ```

3. **Monitor Progress**
   - Show status updates
   - Notify when complete

4. **Report Result**
   - Podcast file location
   - Duration
   - Notebook ID for reference

## Example Interaction

```
User: /create-podcast

Claude: I'll help you create a podcast. Let me gather some information:

**What's the topic/title for this podcast?**

User: Project Alpha competitive landscape analysis

Claude: **How would you like to provide sources?**
1. Paste URLs directly
2. Pull from research_learning.org section
3. Let me suggest sources based on topic

User: 2

Claude: I'll pull from the Project Alpha section. Found 12 pending items.

**Select sources (max 10 for depth):**
☑ Competitor X Pricing Model
☑ Healthcare Analytics Platform Comparison
☑ Market Size Analysis 2025
☐ [remaining items...]

Confirm: Create podcast with 6 sources?

User: yes

Claude: Creating podcast...
→ Notebook created: "Project Alpha Competitive Analysis 2025-12-18"
→ Adding 6 sources...
→ Generating audio (this takes 5-10 minutes)...
→ Status: processing...
→ Status: ready

Podcast created successfully!
File: ~/Data/0-personal/content/podcasts/alpha-competitive-2025-12-18.mp3
Duration: 26:42
Notebook ID: abc123-def456
```

## Instruction Templates

**Competitive Analysis:**
```
Create an in-depth competitive analysis podcast. For each competitor:
1. Summarize their offering and positioning
2. Identify strengths and weaknesses
3. Compare to our approach
Conclude with strategic recommendations.
```

**Market Research:**
```
Create a comprehensive market research podcast covering:
1. Key findings from each source
2. Market trends and patterns
3. Opportunities and threats
4. Actionable insights
```

**Learning/Educational:**
```
Create an educational podcast that explains concepts clearly:
1. Break down complex ideas
2. Use examples and analogies
3. Build understanding progressively
4. Summarize key takeaways
```

## Error Handling

### nlm CLI Not Found
```
nlm CLI not found or not configured.

To install:
  go install github.com/tmc/nlm@latest

To configure path in settings.local.yaml:
  research:
    nlm_path: "/path/to/nlm"
```

### Too Few Sources
```
Only 2 sources provided. Minimum recommended: 3

Podcasts with fewer sources tend to be shallow.

Options:
1. Add more sources
2. Proceed anyway (not recommended)
3. Cancel
```

### Too Many Sources
```
15 sources provided. Maximum recommended: 10

Too many sources reduces depth per topic.

Options:
1. Select top 10 most relevant
2. Split into multiple podcasts
3. Proceed anyway (coverage will be shallow)
```

### URL Fetch Failed
```
Failed to access 2 URLs:
- https://example.com/paywall (403 Forbidden)
- https://example.com/removed (404 Not Found)

Options:
1. Continue with 4 remaining sources
2. Replace failed URLs
3. Cancel
```

### Audio Generation Failed
```
Audio generation failed after 15 minutes.

Possible causes:
- NotebookLM service unavailable
- Sources too large or complex
- Temporary API error

Options:
1. Retry generation
2. Keep notebook (generate audio later manually)
3. Delete notebook and cancel
```

### Audio Generation Timeout
```
Generation taking longer than expected (>20 minutes).

The podcast is still being created by NotebookLM.

Options:
1. Continue waiting
2. Check status later (notebook ID: abc123)
3. Cancel monitoring (podcast may still complete)
```

## Settings Reference

Related settings in `~/.datacore/settings.local.yaml`:

```yaml
research:
  nlm_path: null  # Path to nlm binary (null = detect from PATH)

  podcast_output_dir: "0-personal/content/podcasts"

  podcast_defaults:
    duration_target: "30min"
    max_sources: 10
    min_sources: 3

  # Power user settings
  auto_skip_source_warnings: false  # Skip too few/many source warnings
```

## Your Boundaries

**YOU CAN:**
- Gather URLs from user or research_learning.org
- Validate source accessibility
- Invoke podcast-creator agent
- Monitor generation progress
- Report results and file locations

**YOU CANNOT:**
- Access paywalled or authenticated content
- Guarantee exact podcast duration
- Speed up NotebookLM processing
- Modify source content

**YOU MUST:**
- Validate source count (warn if <3 or >10)
- Confirm before starting generation
- Report all failures with options
- Provide notebook ID for manual recovery
