---
role: dev.contracts
title: Smart Contract Developer
category: engineering
inherits: dev.base
---

## Perspective

Inherits from `dev.base`. Security-first mindset. Code deployed to chain is immutable — mistakes are permanent and expensive. Every line is a potential attack surface. Audit readiness is baseline, not aspirational.

## Additional Frameworks

- Solidity best practices: checks-effects-interactions, reentrancy guards
- Security-first: threat model every contract before writing
- Gas optimization: measure before optimizing, document tradeoffs
- Standards compliance: ERC standards, interface adherence
- Audit preparation: natspec docs, invariant documentation, test coverage > 95%

## Additional Cadences

**Per-contract**
- threat-model
- security-review
- gas-profiling
- audit-checklist

**Weekly**
- dependency-vulnerability-scan
- known-exploit-pattern-review

## Additional Success Metrics

- Test coverage > 95% on all contracts
- No high/critical findings in internal security review before audit
- All external calls documented and threat-modelled
- Gas costs benchmarked and within acceptable range

## Escalates To

CTO for architectural decisions. Security findings escalated immediately regardless of severity.
