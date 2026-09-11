#!/usr/bin/env python3
"""Compatibility entry point for conservative, stage-aware conflict recovery.

The retired incident-specific reconciler selected a remote Org file and a fixed
list of local headings. That cannot preserve arbitrary later edits, and missing
Git evidence must never become empty replacement text. Pass explicit repository
paths; ambiguous Org/journal conflicts remain intact for manual reconciliation.
"""
from resolve_ledger_conflicts import main

if __name__ == '__main__':
    raise SystemExit(main())
