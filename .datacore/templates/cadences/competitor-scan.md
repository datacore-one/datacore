---
cadence: competitor-scan
role: operator
frequency: weekly
duration: 30min
tools: [Read, Write, WebFetch, WebSearch, plur_recall_hybrid, datacore.search]
---

## Objective

Weekly scan of named competitors to detect new products, pricing changes,
positioning shifts, and engagement patterns. Surface anything that should
trigger a competitive response or hypothesis update.

## Steps

1. **Load competitor list**: Read
   `[space]/3-knowledge/reference/competitors.yaml` (create if missing). Each
   competitor entry should have:
   - `name`
   - `url`
   - `category` (direct / adjacent / aspirational)
   - `track_signals`: list of what to check (e.g., new listings, pricing,
     blog posts, social cadence)
   - `last_scan`: ISO timestamp
   - `last_summary`: brief from prior scan

2. **Recall prior scans**: `plur_recall_hybrid` for each competitor name +
   "scan" — surface what changed in prior cycles to spot trends.

3. **Scan each competitor**: For each entry past `last_scan + 7d`:
   - Fetch the tracked surfaces (homepage, pricing page, listings index,
     blog, RSS) via WebFetch
   - Extract new entries since `last_scan`
   - Note pricing changes, new product launches, removed offerings
   - Note positioning shifts (tagline, hero copy, audience targeting)

4. **Diff against prior summary**: Compare current state to `last_summary`.
   Categorize changes:
   - **NEW**: products, features, blog topics added
   - **REMOVED**: products discontinued, features deprecated
   - **CHANGED**: prices, copy, positioning
   - **PATTERN**: cadence shifts (e.g., 3 new listings/week vs prior 1/week)

5. **Identify response triggers**:
   - Competitor launched a product in our roadmap → assess timing risk
   - Competitor dropped pricing → assess our price-sensitivity hypotheses
   - Competitor exited a category we're entering → why? signal or noise?
   - Competitor pivoted to our exact positioning → opportunity or threat?

6. **Update knowledge**: Write changes to
   `[space]/3-knowledge/reference/competitors.yaml` (`last_scan`,
   `last_summary`). For material changes, write a zettel to
   `3-knowledge/zettel/competitor-{name}-{date}.md`.

7. **Surface decisions**: For any change classified as a response trigger,
   capture a `:CEO:` or `:CMO:` task in `org/inbox.org` describing the
   competitor change and the question to answer.

8. **Write scan memo**: Compile via `datacore.capture` — list of competitors
   scanned, key changes, response triggers identified.

## Output

- Updated `competitors.yaml` with fresh `last_scan` + `last_summary`
- Zettels for material changes
- Follow-up tasks for response triggers
- Scan memo in venture journal

## Success Criteria

- Every competitor in the list past their 7-day window was scanned (or
  explicitly skipped with reason)
- Changes are categorized, not just listed
- Response triggers produce specific decision-needed tasks, not "watch this"
- Zero competitors silently dropped from tracking — explicit retire or
  continue
