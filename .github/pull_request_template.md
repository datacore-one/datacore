## Summary

<!-- Brief description of changes -->

## Type

- [ ] Module contribution (new or updated module)
- [ ] DIP change (specification update)
- [ ] Agent/command contribution
- [ ] Bug fix
- [ ] Documentation

## Privacy Checklist (DIP-0002)

> All `.base.md` files are PUBLIC and will be validated by CI.

- [ ] No email addresses in `.base.md` files
- [ ] No phone numbers or personal identifiers
- [ ] No API keys, tokens, or secrets
- [ ] No absolute file paths (`/Users/`, `/home/`)
- [ ] No organization-specific names or URLs in public layers
- [ ] Private content is in `.local.md` (gitignored) or `.org.md` (fork-tracked)

## Structural Checklist

- [ ] `module.yaml` present and valid (if module contribution)
- [ ] Agent/command registered in `.datacore/registry/` (if applicable)
- [ ] Tags used are registered in `.datacore/tags.yaml`
- [ ] No hardcoded space names (use dynamic discovery pattern)

## Test Plan

<!-- How to verify these changes work correctly -->
