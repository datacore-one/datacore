---
name: Dev for Datacore
description: "Development operations — CI/CD deployment and Chrome-based verification"
version: 1.0.0
author: datacore-one
license: MIT
tags: [dev, deploy, cicd, devops]
x-datacore:
  module: dev
  tools: 0
  skills: 1
  agents: 0
  commands: 1
  workflows: 0
  engram_count: 0
  injection_policy: on_match
  match_terms: [deploy, deployment, cicd, devops, staging, production]
---

# Dev for Datacore

Development operations — deploy to staging/production and verify
via Chrome-based testing.

## What This Module Provides

**Skills**: deploy-status

**Commands**: /deploy

## When to Use

Triggers: deploy, deployment, cicd, devops, staging, production.
