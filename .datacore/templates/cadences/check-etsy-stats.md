---
cadence: check-etsy-stats
role: operator
frequency: daily
duration: 10min
tools: [Read, Write, WebFetch, plur_recall_hybrid]
---

## Objective

Daily snapshot of Etsy listing performance — views, favorites, sales, conversion
— for every active Forge listing. Detect anomalies (pricing-test wins, sudden
view drops, ranking changes) early.

## Steps

> **API status**: Etsy API access was rejected for our account (per
> `4-forge/venture.yaml` thesis). Until reapplication or alternative, this
> cadence runs in **manual-pull mode**: the operator records stats from the
> Etsy seller dashboard, the agent processes them.

1. **Load active listings**: Read `4-forge/1-tracks/dashboard.md` for the
   current active SKU list. Each listing should have:
   - SKU id
   - listing URL
   - last-recorded stats (views/favs/sales)
   - last-recorded date

2. **Recall prior anomalies**: `plur_recall_hybrid` for "Etsy stats" +
   "anomaly" — surface any prior unresolved spikes/drops.

3. **Fetch today's snapshot** (manual-pull mode):
   - If `4-forge/.datacore/state/etsy-stats-input-YYYY-MM-DD.yaml` exists,
     use it (operator-recorded)
   - Else, capture a `:operator:` task in `org/inbox.org` requesting the
     stats input file, and exit gracefully

4. **Compute deltas vs prior snapshot**: For each SKU:
   - views Δ% (24h, 7d)
   - favorites Δ% (24h, 7d)
   - sales count (24h)
   - conversion = sales / views

5. **Flag anomalies**:
   - Views dropped >30% day-over-day → INVESTIGATE (search ranking? competition?)
   - Favorites jumped >50% with no sales → HIGH-INTENT ALERT
   - First sale on a listing → SHIP NOTIFICATION
   - Conversion >5% on a listing for 3 days running → SCALE CANDIDATE

6. **Update dashboard**: Write current stats + flags to
   `4-forge/1-tracks/dashboard.md`. Append timestamped row.

7. **Capture follow-ups**: For each INVESTIGATE / SCALE flag, create a
   `:operator:forge:` task with the specific question.

## Output

- Updated `4-forge/1-tracks/dashboard.md` with today's row
- Anomaly flags surfaced
- Follow-up tasks for investigations and scale candidates

## Success Criteria

- Every active SKU has a today entry (or is explicitly marked "no input")
- Anomalies are quantified vs threshold, not just narrated
- First-sale detection fires reliably (no missed celebrations)

## Future

Once Etsy API access is restored, replace manual-pull with API call. Schema
of the YAML input file is identical to API response shape so the swap is
mechanical.
