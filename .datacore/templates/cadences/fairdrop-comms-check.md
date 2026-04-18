---
cadence: fairdrop-comms-check
role: cmo
frequency: daily
duration: 10min
tools: [Read, Glob, plur_recall_hybrid, Bash]
---

## Objective

Quick daily health check on Fairdrop communications pipeline — catch failures early and surface any product updates worth amplifying.

## Steps

1. **Check campaign pipeline**: Use `Glob` to find recent campaign files in `1-tracks/comms/campaigns/` and `Read` to check their status. Look for:
   - Scheduled posts that failed to publish
   - Drafts past their target date
   - Campaigns with expired approval windows
2. **Check for token/credential issues**: Use `Read` on comms module config or credential status files. Flag any expired API tokens or authentication failures that would block publishing.
3. **Check GitHub releases**: Run `gh release list --repo fairDataSociety/Fairdrop --limit 5` via `Bash` to see if any new releases dropped in the past 24 hours. If yes, note the version and key changes for a potential announcement.
4. **Quick social pulse**: Use `plur_recall_hybrid` to check recent social mention history for @FairDataSociety. Note any trending conversations or urgent replies needed.
5. **Log status**: Call `datacore.capture` with a brief status entry:
   - Pipeline: OK / ISSUE (detail)
   - Releases: none / vX.Y.Z released
   - Social: quiet / active / needs-attention
   - Action items (if any)

## Output

- Brief journal entry with pipeline status
- Escalation tasks if any failures are found

## Success Criteria

- Failed or stuck campaign posts are caught within 24 hours
- New product releases are surfaced for comms amplification
- Status entry takes under 10 minutes to produce
- No silent failures go undetected for more than one daily cycle
