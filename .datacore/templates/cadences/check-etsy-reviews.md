---
cadence: check-etsy-reviews
role: operator
frequency: daily
duration: 10min
tools: [Read, Write, WebFetch, plur_recall_hybrid]
---

## Objective

Daily check for new Etsy reviews on active Forge listings. Surface negative
reviews immediately for response, identify patterns across positive reviews to
inform listing copy.

## Steps

> **API status**: Etsy API rejected. Manual-pull mode same as `check-etsy-stats`.

1. **Load active listings**: Read `4-forge/1-tracks/dashboard.md` for SKU
   list with review-count and last-review-date per listing.

2. **Recall prior review patterns**: `plur_recall_hybrid` for SKU id +
   "review" — surface recurring complaints / praise themes.

3. **Pull today's reviews** (manual-pull mode):
   - If `4-forge/.datacore/state/etsy-reviews-input-YYYY-MM-DD.yaml` exists,
     use it
   - Else, capture a `:operator:` task and exit gracefully

4. **For each new review**:
   - Star rating (1-5)
   - Review text
   - Listing
   - Date

5. **Classify and act**:
   - **1-2 stars** → URGENT: capture a `:operator:forge:respond-to-review:`
     task with draft response (acknowledge + offer remedy)
   - **3 stars (mixed)** → flag for theme analysis
   - **4-5 stars** → extract praise themes for listing-copy reinforcement
   - **Pattern across reviews** (3+ mentions of same praise/complaint in 7d)
     → write a zettel to `3-knowledge/zettel/etsy-review-pattern-{SKU}.md`

6. **Update dashboard**: Append review-count delta + average rating to
   `4-forge/1-tracks/dashboard.md`.

## Output

- Urgent response drafts for negative reviews
- Pattern zettels for recurring themes
- Updated dashboard with review metrics

## Success Criteria

- Zero 1-2 star reviews go uncaptured for >24h
- Every new review is processed (no silent drops)
- Pattern detection fires only on n≥3, not single reviews
