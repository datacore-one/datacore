# dev module

Development workflows for Datacore — deployment, CI/CD monitoring, production verification.

## Commands

- `/deploy [project]` — Full deploy workflow: push, monitor CI, verify in production
- `/deploy-status [project]` — Quick CI/CD status check

## Setup

Each project needs a `deploy.yaml` in its root:

```yaml
project: my-project
gh_repo: org/repo

git:
  remote: origin
  branch: main

pipeline:
  workflow: "Deploy"
  critical_jobs:
    - "Build"
    - "Deploy"

verify:
  - type: curl
    name: "Health check"
    url: https://my-app.com/health
    expect: 200
```

See `CLAUDE.base.md` for full schema documentation.

## Philosophy

Deploy is not done until verified in production. Tests are necessary but not sufficient.
