# Datacore Installation Guide

This guide covers setting up new spaces and maintaining existing ones.

## Setting Up a New Space

Use the `create-space` agent to scaffold new team or personal spaces.

### Conversational Setup

Simply tell Claude you want to create a space:

```
"Create a new team space for Acme"
"Set up a new personal space"
"I need a new space called acme"
```

The agent will:
1. Ask for space name, type (team/personal), and description
2. Determine the next available space number
3. Clone the datacore-org template from GitHub (or use local templates as fallback)
4. Generate all required files with your provided info
5. Initialize git repository
6. Optionally create a GitHub repo if you provide an organization name
7. Generate the composed CLAUDE.md
8. Validate the structure

### What Gets Created

```
[N]-[name]/
├── .datacore/
│   ├── config.yaml          # Space configuration
│   ├── learning/            # AI learning files (gitignored)
│   │   ├── patterns.md
│   │   ├── corrections.md
│   │   └── preferences.md
│   ├── agents/              # Space-specific agents
│   └── commands/            # Space-specific commands
├── org/
│   ├── inbox.org            # GTD inbox
│   └── next_actions.org     # GTD next actions
├── 0-inbox/                 # Unprocessed content
├── 1-tracks/                # Work tracks
│   ├── ops/
│   ├── product/
│   ├── dev/
│   ├── research/
│   └── comms/
├── 2-projects/              # Code repos (gitignored)
├── 3-knowledge/             # Knowledge base
│   ├── pages/
│   ├── zettel/
│   ├── literature/
│   ├── reference/
│   └── insights.md
├── 4-archive/               # Historical content
├── journal/                 # Daily logs
├── CLAUDE.base.md           # PUBLIC context layer
├── CLAUDE.space.md          # SPACE context layer
├── CLAUDE.md                # Composed (gitignored)
├── _index.md                # Space navigation
└── .gitignore
```

## Auditing an Existing Space

To check if an existing space is properly configured:

```
"Audit the acme space"
"Check if 1-teamspace has correct structure"
```

The audit reports:
- Missing required folders
- Missing required files
- CLAUDE layer status
- Git initialization status

## Fixing a Broken Space

If audit finds issues:

```
"Fix the broken space at 3-partnerspace"
"Repair the acme space structure"
```

The agent will:
1. Show what's missing
2. Ask for confirmation before fixing
3. Create missing folders/files from templates
4. Regenerate composed CLAUDE.md
5. Report what was fixed

## Using org-mode Tags

You can also trigger space operations via org-mode tasks:

```org
* TODO Create new space for Acme Corp :AI:space:create:
* TODO Audit teamspace space structure :AI:space:audit:
```

## Manual Installation (Not Recommended)

If you need to set up a space manually without the agent, clone the template:

```bash
gh repo clone datacore-one/datacore-org ~/Data/[N]-[name]
```

Then customize CLAUDE.space.md with your organization info.

**Note**: Using the `create-space` agent is strongly recommended as it ensures all required files are properly configured and validated.

## Troubleshooting

### Journaling doesn't work

Run `audit` mode to check if org files exist:
```
"Audit this space"
```

Usually caused by missing `org/inbox.org` or `org/next_actions.org`.

### CLAUDE.md not found

The composed CLAUDE.md is generated from layer files. Regenerate it:

```bash
python .datacore/lib/context_merge.py rebuild --path [space]
```

### Space not recognized

Check that:
1. Directory follows `[N]-[name]/` naming pattern
2. `.datacore/config.yaml` exists
3. `CLAUDE.base.md` and `CLAUDE.space.md` exist
