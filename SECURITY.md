# Security Policy

## Reporting a Vulnerability

**Do not open a public issue for security vulnerabilities.**

Instead, please email security concerns to: security@datacore.one

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We will acknowledge receipt within 48 hours and provide a detailed response within 7 days.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.x     | Yes       |
| < 1.0   | No        |

## Security Model

Datacore uses a layered privacy architecture (DIP-0002):

- **PUBLIC** (`.base.md`): Safe to share, validated by CI
- **PRIVATE** (`.local.md`): Never tracked, never shared
- **Secrets** (`.datacore/env/`): Gitignored, OS-encrypted

### Automated Protections

- Pre-commit hooks scan `.base.md` files for PII (emails, API keys, phone numbers)
- CI/CD validates all PRs against privacy rules before merge
- Credential index tracks API keys with rotation schedules

See [DIP-0002](/.datacore/dips/DIP-0002-layered-context-pattern.md) for the full security architecture.
