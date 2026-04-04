# Knowledge Promoter

Scans journal entries for high-value content and promotes it to permanent
knowledge artifacts via knowledge-extractor.

## When to Use

- Weekly review (automated scan of past week's journals)
- Manual `/promote` command for on-demand promotion
- Nightshift weekly task (scheduled Sunday night)

## Inputs

- **Space**: Target space to scan (default: detect from cwd)
- **Period**: How far back to scan (default: 7 days)
- **Threshold**: Minimum promotion score (default: 0.4)

## Workflow

1. **Scan**: Run `journal_scanner.py` on journal entries within the period
2. **Present**: Show scored sections to user with promotion recommendations
3. **Confirm**: User approves/rejects each candidate (or auto-approve in nightshift mode)
4. **Extract**: For each approved section, spawn `knowledge-extractor` with:
   - Input: the journal section text
   - Source type: "journal entry"
   - Target space: same space as journal
   - Instruction: create zettels for reusable concepts, skip literature note
     (journal IS the source record)
5. **Link back**: Add `Promoted: [[Zettel Name]]` annotation to original journal section
6. **Log**: Append promotion record to journal for the day

## Heuristics (journal_scanner.py)

The scanner uses pattern matching, not LLM inference:
- **Positive**: root cause analysis, architecture decisions, research findings,
  wiki-links, code blocks, paper references, substantial bullet lists
- **Negative**: standup notes, WIP items, quick syncs
- **Length bonus**: longer sections score higher (capped)
- Threshold 0.4 = moderate confidence. Adjust per space.

## Modes

### Interactive (`/promote`)
- Shows each candidate with score and preview
- User confirms or skips each one
- Can override threshold: `/promote --threshold 0.6`
- Can target specific date: `/promote --date 2026-04-01`

### Nightshift (autonomous)
- Runs with threshold 0.6 (higher bar for unsupervised)
- Auto-approves all candidates above threshold
- Creates promotion summary in morning briefing
- Skips sections already annotated with `Promoted:`

## Integration Points

- **Weekly review**: `/promote --period 7d` as part of review checklist
- **Nightshift**: Scheduled Sunday night task in `next_actions.org`
- **Session end**: Suggest `/promote` if session had substantial journal entries

## Output

Per promoted section:
- 0-3 zettels in `[space]/3-knowledge/zettel/`
- `Promoted: [[Zettel Name]]` annotation in source journal
- Promotion log entry in today's journal
