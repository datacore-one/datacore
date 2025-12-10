# Sync

Unified sync command for repositories and external task systems.

## Usage

```
/sync           # Full sync: repos + external tasks
/sync pull      # Pull only (repos + external tasks)
/sync push      # Push only (repos + external tasks)
/sync status    # Show sync status
/sync tasks     # Sync tasks only (skip repo sync)
```

**Replaces:** `./sync` shell script (DIP-0010)

## Behavior

### Full Sync (`/sync`)

```
SYNC
====

1. Repository Sync
------------------
[Run git pull for all configured repos]

datacore (root).......... [PULLED/UP-TO-DATE/CONFLICT]
datafund-space........... [PULLED/UP-TO-DATE/CONFLICT]
datacore-space........... [PULLED/UP-TO-DATE/CONFLICT]

2. External Task Sync (DIP-0010)
--------------------------------
[If sync.tasks.enabled in settings.yaml]

Pulling external changes...
  GitHub:
    - datacore-one/datacore: [N] issues updated
    - datacore-one/datafund-space: [N] issues updated

Routing to org-mode...
  - [N] tasks to inbox.org
  - [N] tasks to next_actions.org (with :AI: tags)

Pushing org changes...
  - [N] state changes synced
  - [N] new tasks created in GitHub

3. Summary
----------
Repos: [X] synced, [Y] conflicts
Tasks: [X] pulled, [Y] pushed
Last sync: [timestamp]

SYNC COMPLETE
```

### Pull Only (`/sync pull`)

```
SYNC PULL
=========

1. Repository Pull
------------------
[Pull all repos]

2. External Task Pull (DIP-0010)
--------------------------------
[Pull from GitHub only, route to org]

Changes:
  - [N] new issues
  - [N] updated issues
  - [N] closed issues

Routed:
  - inbox.org: [N] new entries
  - next_actions.org: [N] entries (AI tasks)
```

### Push Only (`/sync push`)

```
SYNC PUSH
=========

1. Repository Push
------------------
[Commit and push all repos with changes]

datacore (root).......... [3 files] → Pushed
datafund-space........... [No changes]
datacore-space........... [1 file] → Pushed

2. External Task Push (DIP-0010)
--------------------------------
[Push org-mode changes to GitHub]

State changes:
  - github:datacore-one/datacore#42 → CLOSED
  - github:datacore-one/datacore#43 → REOPENED

New issues created:
  - "Implement feature X" → github:datacore-one/datacore#44

Pushed: [N] changes
```

### Status (`/sync status`)

```
SYNC STATUS
===========

Repositories:
  datacore (root).......... [CLEAN/DIRTY] [UP-TO-DATE/AHEAD/BEHIND]
  datafund-space........... [CLEAN/DIRTY] [UP-TO-DATE/AHEAD/BEHIND]
  datacore-space........... [CLEAN/DIRTY] [UP-TO-DATE/AHEAD/BEHIND]

External Sync (DIP-0010):
  Enabled.................. [YES/NO]
  Last sync................ [timestamp] ([N] hours ago)

  Adapters:
    GitHub................. [CONNECTED/DISCONNECTED]
      - Configured repos: [N]
      - Last pull: [timestamp]
      - Pending push: [N] changes

  Pending Changes:
    Org → External: [N] tasks with unsynced changes
    External → Org: [N] issues need routing
```

### Tasks Only (`/sync tasks`)

```
SYNC TASKS
==========

[Skip repo sync, only sync external tasks]

1. Pull External Changes
------------------------
GitHub:
  - datacore-one/datacore: [N] changes
  - datacore-one/datafund-space: [N] changes

2. Route to Org
---------------
  - inbox.org: [N] new
  - next_actions.org: [N] AI tasks

3. Push Org Changes
-------------------
  - [N] state changes
  - [N] new issues

TASKS SYNCED
```

## Implementation

### Repository Sync

Uses existing `./sync` logic:

```bash
# Pull
git -C ~/Data pull --rebase
git -C ~/Data/1-datafund pull --rebase
git -C ~/Data/2-datacore pull --rebase

# Push
git -C ~/Data add . && git -C ~/Data commit -m "Sync" && git -C ~/Data push
# Repeat for spaces
```

### External Task Sync

Uses sync engine from `.datacore/lib/sync/`:

```python
from sync.engine import SyncEngine

engine = SyncEngine()
engine.load_config()

# Pull and route
changes = engine.pull_all()
# Route changes to org files via router

# Push org changes
result = engine.push_all(org_changes)
```

## Configuration

In `.datacore/settings.yaml`:

```yaml
sync:
  pull_on_today: true
  push_on_wrap_up: true

  # External task sync (DIP-0010)
  tasks:
    enabled: true
    poll_interval: 10m

  adapters:
    github:
      enabled: true
      repos:
        - owner: datacore-one
          repo: datacore
        - owner: datacore-one
          repo: datafund-space
      label_mapping:
        ":AI:": "ai-task"
        "[#A]": "priority-high"

  routing:
    - source: github
      condition: "labels contains 'ai-task'"
      destination: next_actions.org
      tags: [":AI:"]

    - source: github
      condition: "assignee is null"
      destination: inbox.org
```

## Duplicate Detection

When routing external tasks, the sync engine:

1. Checks for existing EXTERNAL_ID match in org files
2. If no match, searches by title similarity
3. If match found, links instead of creating duplicate
4. Updates `:EXTERNAL_ID:` and `:EXTERNAL_URL:` properties

## Error Handling

```
SYNC ERROR
==========

Repository errors:
  - datafund-space: Merge conflict in org/next_actions.org
    Resolution: Manual merge required

External sync errors:
  - GitHub API: Rate limit exceeded (retry in 15 min)
  - Auth: GitHub token expired

Some operations failed. See above for details.
```

## Integration Points

### /today
- Runs `/sync pull` automatically (if `sync.pull_on_today: true`)
- Includes external task summary in briefing

### /wrap-up
- Runs `/sync push` automatically (if `sync.push_on_wrap_up: true`)
- Reports pushed changes

### /diagnostic
- Section 12 shows external sync health
- Tests adapter connectivity
- Reports sync history statistics

## Files

**Read:**
- `.datacore/settings.yaml` - Configuration
- `org/inbox.org` - For routing
- `org/next_actions.org` - For AI task routing and change detection

**Update:**
- `org/inbox.org` - Add routed external tasks
- `org/next_actions.org` - Add AI tasks, sync state changes

**Database:**
- `.datacore/state/sync_history.db` - Sync operation history

## Related

- DIP-0010: Task Sync Architecture
- `/diagnostic` - Section 12: External Sync Health
- `/today` - Morning briefing with sync
- `/wrap-up` - Session close with push
