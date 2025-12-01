# Contributing to Datacore

Thank you for your interest in contributing to Datacore!

## Ways to Contribute

### Report Issues
- Bug reports
- Feature requests
- Documentation improvements

Open an issue at [github.com/datacore-one/datacore/issues](https://github.com/datacore-one/datacore/issues)

### Submit Pull Requests

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Ensure no personal data is included (see Privacy Policy below)
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

### Develop Modules

Create specialized modules for specific domains:

**Small improvements to existing modules:**
1. Fork the module repo
2. Make improvements
3. Submit PR

**Register a new module:**
1. Create module structure (see `.datacore/CATALOG.md`)
2. Use the `module-registrar` agent: `:AI:module:register:`
3. Agent creates DIP (for significant changes), repo, and PR

See [DIP-0001](.datacore/dips/DIP-0001-contribution-model.md) for the full contribution model.

### Significant Changes (DIP Process)

For major changes, submit a Datacore Improvement Proposal:
1. Copy `.datacore/dips/DIP-0000-template.md`
2. Fill in all sections
3. Submit PR with status: Draft
4. Iterate based on feedback

See [.datacore/dips/](.datacore/dips/) for existing proposals.

## Privacy Policy

**Critical**: Never commit personal data to this repository.

Before submitting:
- [ ] No personal identifiers (names, emails, usernames)
- [ ] No task/project details
- [ ] No file paths with personal folders
- [ ] Templates use generic examples

See `.datacore/specs/privacy-policy.md` for full guidelines.

## Git Hooks (DIP-0002)

Datacore ships git hooks that prevent private content from leaking into public `.base.md` layers. Install them after cloning:

```bash
# Symlink hooks from .datacore/hooks/ into .git/hooks/
ln -sf ../../.datacore/hooks/pre-commit .git/hooks/pre-commit
ln -sf ../../.datacore/hooks/pre-push .git/hooks/pre-push
```

**What they do:**
- **pre-commit**: Validates staged `.base.md` files for PII (emails, phone numbers, API keys, dollar amounts). Blocks commit if found.
- **pre-push**: Same validation on all `.base.md` files in the push range. Safety net for commits made with `--no-verify` or in other environments. Also runs `git-lfs pre-push`.

The hooks call `context_merge.py validate` under the hood. If you need to bypass validation temporarily (e.g., for a test), use `--no-verify` — but never push unvalidated `.base.md` files.

## Layered Context (DIP-0002)

Context files use four privacy layers:

| Layer | Suffix | Visibility |
|-------|--------|------------|
| PUBLIC | `.base.md` | Tracked, PRable upstream |
| ORG | `.org.md` | Tracked in fork |
| TEAM | `.team.md` | Optional |
| PRIVATE | `.local.md` | Gitignored |

**Contributions modify `.base.md` files only.** CI automatically validates these for PII via `context_merge.py validate`.

## Dynamic Space Discovery

Never hardcode space names like `0-personal` or `1-teamspace`. Use the filesystem discovery pattern:

```python
spaces = [p for p in data_dir.iterdir()
          if p.is_dir() and p.name[:1].isdigit()]
```

## Code Style

- Markdown files: Clear headings, consistent formatting
- Python: PEP 8 style (internal scripts are Python, not TypeScript)
- Agent/command definitions: Follow existing patterns
- Tags: Registered in `.datacore/tags.yaml` before use

## Contributor Recognition

We recognize contributions through a tiered badge system. Badges are awarded based on merged pull requests.

### Tiers

| Badge | Tier | Criteria |
|-------|------|----------|
| First PR | Newcomer | First merged pull request |
| Module Contributor | Builder | Contributed to or created a module |
| Core Contributor | Maintainer | 10+ merged PRs or significant system changes |

### Current Contributors

*Maintained manually. Open a PR to add yourself after your first merge.*

| Contributor | Tier | Notable Contributions |
|-------------|------|-----------------------|
| <!-- Add contributors here --> | | |

A full points-based reward system with leaderboards and bounties is planned. See the [contribution pipeline design](docs/plans/2026-03-03-contribution-pipeline-design.md) for details.

## Questions?

Open a discussion or issue on GitHub.

---

*"Live long and prosper."*
