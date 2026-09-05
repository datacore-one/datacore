#!/usr/bin/env python3
"""Who may claim what, and who may create what — decided before the append.

The ledger records facts; this decides which facts a writer is allowed to
add. Two gates (product description, stages 4 and 5):

  check_claim(actor, item)   a claim by an unregistered writer is refused; an
                             item whose effects a principal may never perform
                             is refused for that principal (no grant can allow
                             a never).
  check_create(actor, item)  a delegation chain deeper than max_hops is
                             refused (agent A creates for B creates for A —
                             the ledger would faithfully record the loop
                             forever); an item addressed to someone the
                             requester may not delegate to is refused; a
                             writer past its daily creation allowance is
                             refused.

Limits come from `approvals_policy.yaml` (`principals:`), with documented
defaults: agents get max_hops 3 and 50 creates a day; humans are unbounded.
A refusal is a one-line reason the caller prints; nothing is dropped
silently.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from actor_identity import principal_of, principals as _principals

DEFAULT_MAX_HOPS = 3
DEFAULT_MAX_CREATES_PER_DAY = 50


def _limits(actor: str, policy=None) -> tuple[str | None, dict, dict]:
    """(principal name, principal registry entry, policy limits) for a writer."""
    name, entry = principal_of(actor)
    lims = {}
    if policy is not None and getattr(policy, "principals", None):
        lims = dict(policy.principals.get(name or actor) or {})
    return name, entry, lims


def check_claim(actor: str, payload: dict | None, policy=None) -> tuple[bool, str]:
    name, entry, lims = _limits(actor, policy)
    if name is None:
        return False, f"unregistered writer {actor!r} — declare it in registry/principals.yaml"
    effects = set((payload or {}).get("effects") or [])
    never = set(lims.get("never_effects") or [])
    if effects & never:
        return False, f"{name} may never perform {', '.join(sorted(effects & never))}"
    return True, f"{name} may claim"


def creates_today(space_dir: Path | None, actor: str, today: dt.date | None = None) -> int:
    """How many item.create events this writer appended today, from its own log."""
    if not space_dir:
        return 0
    f = Path(space_dir) / ".datacore" / "events" / f"{actor}.jsonl"
    if not f.exists():
        return 0
    today = today or dt.datetime.now(dt.timezone.utc).date()
    n = 0
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        if '"item.create"' not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") != "item.create":
            continue
        ms = str(e.get("hlc", "")).split(".")[0]
        if ms.isdigit() and dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).date() == today:
            n += 1
    return n


def check_create(actor: str, payload: dict | None, policy=None, space_dir: Path | None = None,
                 today: dt.date | None = None) -> tuple[bool, str]:
    name, entry, lims = _limits(actor, policy)
    payload = payload or {}
    if name is None:
        return False, f"unregistered writer {actor!r} — declare it in registry/principals.yaml"
    human = str(entry.get("kind") or "") == "human"
    max_hops = lims.get("max_hops", DEFAULT_MAX_HOPS)
    hops = payload.get("hops", 0)
    try:
        hops = int(hops or 0)
    except (TypeError, ValueError):
        return False, f"hops must be an integer (got {hops!r})"
    if not human and hops > max_hops:
        return False, f"delegation chain is {hops} hops deep; {name} may go {max_hops}"
    assignee = payload.get("assignee")
    requester = payload.get("requested_by") or actor
    if assignee and assignee != requester:
        rname, _, rlims = _limits(str(requester), policy)
        allowed = rlims.get("may_delegate_to")
        if allowed is not None and assignee not in allowed:
            return False, f"{rname or requester} may not delegate to {assignee} (may_delegate_to: {', '.join(allowed) or 'nobody'})"
    if not human:
        cap = lims.get("max_creates_per_day", DEFAULT_MAX_CREATES_PER_DAY)
        n = creates_today(space_dir, actor, today)
        if n >= cap:
            return False, f"{name} has created {n} item(s) today; the allowance is {cap}"
    return True, f"{name} may create"
