# GTD System (org-mode)

This folder contains the GTD (Getting Things Done) system using Emacs org-mode.

## Setup

Copy the example files to create your GTD system:

```bash
cp inbox.org.example inbox.org
cp next_actions.org.example next_actions.org
cp someday.org.example someday.org
cp habits.org.example habits.org
```

## Files

| File | Purpose |
|------|---------|
| `inbox.org` | Single capture point - everything goes here first |
| `next_actions.org` | Actionable tasks organized by context/project |
| `someday.org` | Someday/maybe items for future consideration |
| `habits.org` | Recurring habits with org-habit tracking |

## Workflow

1. **Capture**: Add everything to `inbox.org`
2. **Process**: `/gtd-daily-end` processes inbox, routes items
3. **Delegate**: Tasks tagged `:AI:` are executed by agents overnight
4. **Review**: `/gtd-daily-start` shows completed AI work
5. **Do**: Focus on what matters

## AI Task Tags

Add these tags to tasks in `next_actions.org` for AI delegation:

| Tag | Agent | Purpose |
|-----|-------|---------|
| `:AI:research:` | gtd-research-processor | URL analysis, literature notes |
| `:AI:content:` | gtd-content-writer | Blog posts, emails, docs |
| `:AI:data:` | gtd-data-analyzer | Reports, metrics, insights |
| `:AI:pm:` | gtd-project-manager | Project tracking, blockers |

## Privacy

All `.org` files are gitignored. Only this README and `.example` templates are tracked.
