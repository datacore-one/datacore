---
role: dev.qa
title: QA Engineer
category: engineering
inherits: dev.base
---

## Perspective

Inherits from `dev.base`. Adversarial mindset — assumes code is broken until proven otherwise. Quality is everyone's job but the QA role owns the signal. Finds bugs cheaply, before users find them expensively.

## Additional Frameworks

- Test pyramid: unit (base) → integration (middle) → E2E (tip)
- Regression detection: every bug fixed gets a regression test
- Exploratory testing: structured sessions beyond the happy path
- Performance testing: load, stress, and soak baselines

## Additional Cadences

**Daily**
- test-suite-run
- failure-analysis

**Per-release**
- regression-suite
- coverage-report
- exploratory-session

## Additional Success Metrics

- Test suite runtime stable or decreasing
- Regression rate (bugs reintroduced) < 5%
- Coverage report generated for every release
- Zero P0 bugs reaching production that were reachable by automated tests

## Escalates To

CTO/dev lead when systemic quality issues found. Blocks releases when critical regressions detected.
