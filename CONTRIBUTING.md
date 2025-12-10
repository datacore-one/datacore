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

See [DIP-0001](dips/DIP-0001-contribution-model.md) for the full contribution model.

### Significant Changes (DIP Process)

For major changes, submit a Datacore Improvement Proposal:
1. Copy `dips/DIP-0000-template.md`
2. Fill in all sections
3. Submit PR with status: Draft
4. Iterate based on feedback

See [dips/](dips/) for existing proposals.

## Privacy Policy

**Critical**: Never commit personal data to this repository.

Before submitting:
- [ ] No personal identifiers (names, emails, usernames)
- [ ] No task/project details
- [ ] No file paths with personal folders
- [ ] Templates use generic examples

See `.datacore/specs/privacy-policy.md` for full guidelines.

## Code Style

- Markdown files: Clear headings, consistent formatting
- Python: PEP 8 style
- Agent/command definitions: Follow existing patterns

## Questions?

Open a discussion or issue on GitHub.

---

*"Live long and prosper."*
