---
cadence: product-portfolio-assessment
role: operator
frequency: monthly
duration: 1h
tools: [Read, Write, plur_recall_hybrid, datacore.search]
---

## Objective

Monthly assessment of the Forge product portfolio — what's earning, what's
not, what should be promoted, what should be retired. Drives the
keep/improve/kill decision for every active SKU.

## Steps

1. **Load portfolio**: Read `4-forge/1-tracks/dashboard.md` for all LIVE
   products with month-to-date metrics:
   - revenue
   - units sold
   - views
   - favorites
   - conversion rate
   - average review rating + review count
   - days live

2. **Recall portfolio strategy**: Read `4-forge/1-tracks/strategy.yaml` and
   `plur_recall_hybrid` for "Forge portfolio" — note current strategy (value
   play, fewer products, higher differentiation per CLAUDE.space.md).

3. **Score each LIVE product**:
   - **revenue_score**: $/month rank within portfolio
   - **conversion_score**: conversion rate vs portfolio median
   - **review_score**: rating × log(review count + 1)
   - **days_live**: tenure (younger products get grace period)

4. **Classify each product**:
   - **PROMOTE** — top-quartile revenue + ≥4★ rating → invest in variants,
     bundle, ads
   - **MAINTAIN** — middle quartiles, healthy metrics → keep, no extra investment
   - **IMPROVE** — bottom quartile but <60d live, OR mixed reviews → iterate
     listing copy / mockups / pricing
   - **RETIRE** — bottom quartile, >90d live, no improvement after 1 iteration
     → pull from sale

5. **For each PROMOTE candidate**: Capture `:operator:forge:` task to design
   the next variant or bundle.

6. **For each IMPROVE candidate**: Capture task with specific iteration
   hypothesis (e.g., "test pricing $9.99 → $7.99 for 30d").

7. **For each RETIRE candidate**: Capture `:operator:forge:` task to draft
   retirement (write learning zettel, archive assets, remove from dashboard).

8. **Check portfolio shape vs strategy**: Per CLAUDE.space.md current
   strategy is "value play, fewer products, higher differentiation, target
   30-50 listings, $5K-15K/mo within 12 months." Are we on track?
   - Listing count vs target trajectory
   - Revenue vs target trajectory
   - Differentiation signals (review themes, price points)

9. **Write portfolio memo**: Via `datacore.capture` — score table, classify
   actions, strategy alignment assessment, next month's portfolio shape goal.

## Output

- Portfolio memo in 4-forge journal
- Action tasks per PROMOTE / IMPROVE / RETIRE classification
- Updated dashboard with classification column

## Success Criteria

- Every LIVE product has a classification (no "tbd")
- RETIRE decisions produce a learning zettel (don't waste the failure)
- PROMOTE decisions get specific next-variant tasks (not "consider scaling")
- Strategy alignment is quantified vs target trajectory, not "feels on track"
