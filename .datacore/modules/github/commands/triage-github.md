# /triage-github

Scan GitHub repos for activity, generate a summary, and create actionable GTD tasks.

## Trigger

- Standalone: `/triage-github`
- Called by `/today` hook
- Can be triggered by nightshift as a pre-processing step

## Behavior

Execute these steps in order:

### Step 1: Discover Repos

Run repo discovery (uses cache if < 7 days old):

```bash
python3 .datacore/modules/github/lib/repo_discovery.py \
  --data-dir ~/Data
```

Parse the JSON output to get the list of orgs and the `org_to_spaces` mapping.

### Step 2: Scan GitHub

Run the full scan using discovered orgs:

```bash
python3 .datacore/modules/github/lib/github_scanner.py \
  --username plur9 \
  --orgs "<comma-separated orgs from step 1>" \
  --hours 24 \
  --cache .datacore/modules/github/data/scan_cache.json \
  --format json
```

### Step 3: Create Tasks

If `auto_task_create` is enabled (default: true), create tasks from actionable items:

```bash
python3 .datacore/modules/github/lib/task_creator.py \
  --scan-file .datacore/modules/github/data/scan_cache.json \
  --repos-file .datacore/modules/github/data/repos.json \
  --data-dir ~/Data
```

### Step 4: Generate Summary

Use the scan results to produce a markdown summary. Call `format_summary()` from
`github_scanner.py` or generate inline using this template:

**YOUR ITEMS section** (full structured detail for mentions + authored issues):

```markdown
### GitHub: Your Items

**@plur9 mentions:**
- owner/repo#N — "Issue title"
  State: open | [View](url)

**Your issues with new activity:**
- owner/repo#N — "Issue title"
  Latest by @commenter: comment snippet...
  [View](url)
```

**OTHER ACTIVITY section** (compact counts per org):

```markdown
### GitHub: Org Activity

| Org | New Issues | Closed | PRs Merged |
|-----|-----------|--------|------------|
| org-name (space) | N | N | N |
```

**TASKS CREATED section** (audit trail):

```markdown
### GitHub: Tasks Created

- N new tasks created in next_actions.org
- N items skipped (already tracked)
- Tasks tagged :AI:github: for nightshift processing
```

### Step 5: Display or Return

If called standalone: display the full summary to the user.
If called by /today hook: return the summary for insertion into the daily briefing.

## Settings

Read from `.datacore/modules/github/module.yaml`:

| Setting | Key | Default |
|---------|-----|---------|
| GitHub username | `username` | plur9 |
| Scan window | `scan_hours` | 24 |
| Auto-create tasks | `auto_task_create` | true |
| Excluded repos | `exclude_repos` | [] |
| Included repos | `include_repos` | [] |

## Error Handling

| Condition | Behavior |
|-----------|----------|
| `gh` CLI not authenticated | Show error: "Run `gh auth login` first" |
| Rate limited | Use cached data, show warning |
| No repos discovered | Show warning, suggest checking git remotes |
| Org file missing | Skip task creation for that space, log warning |
