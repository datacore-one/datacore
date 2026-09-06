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
ABSENT_AFTER_HOURS = 26  # a principal whose contracts have not been verified for this long is absent


def _limits(actor: str, policy=None) -> tuple[str | None, dict, dict]:
    """(principal name, principal registry entry, policy limits) for a writer."""
    name, entry = principal_of(actor)
    lims = {}
    if policy is not None and getattr(policy, "principals", None):
        lims = dict(policy.principals.get(name or actor) or {})
    return name, entry, lims


def check_claim(actor: str, payload: dict | None, policy=None, space_dir: Path | None = None) -> tuple[bool, str]:
    name, entry, lims = _limits(actor, policy)
    if name is None:
        return False, f"unregistered writer {actor!r} — declare it in registry/principals.yaml"
    effects = set((payload or {}).get("effects") or [])
    never = set(lims.get("never_effects") or [])
    if effects & never:
        return False, f"{name} may never perform {', '.join(sorted(effects & never))}"
    ok, why = check_budget(actor, space_dir)
    if not ok:
        return False, why
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
    if assignee and assignee != actor:
        is_absent, note = absent(str(assignee), root=(Path(space_dir).parent if space_dir else None))
        if is_absent:
            payload["assignee_absent"] = note  # recorded, never worked around
    if not human:
        cap = lims.get("max_creates_per_day", DEFAULT_MAX_CREATES_PER_DAY)
        n = creates_today(space_dir, actor, today)
        if n >= cap:
            return False, f"{name} has created {n} item(s) today; the allowance is {cap}"
    return True, f"{name} may create"


def month_to_date_cents(space_dir: Path | None, writers: list[str], today: dt.date | None = None) -> int:
    """Spend recorded this calendar month by any of a principal's writer logs."""
    if not space_dir:
        return 0
    today = today or dt.datetime.now(dt.timezone.utc).date()
    total = 0
    for w in writers:
        f = Path(space_dir) / ".datacore" / "events" / f"{w}.jsonl"
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"spend.record"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") != "spend.record":
                continue
            ms = str(e.get("hlc", "")).split(".")[0]
            if not ms.isdigit():
                continue
            d = dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).date()
            if (d.year, d.month) == (today.year, today.month):
                try:
                    total += int((e.get("payload") or {}).get("cents") or 0)
                except (TypeError, ValueError):
                    pass
    return total


def check_budget(actor: str, space_dir: Path | None = None, today: dt.date | None = None) -> tuple[bool, str]:
    """Stage 4: a declared monthly budget binds at claim time. Undeclared means no ceiling — and principals_check.py says so."""
    name, entry, _ = _limits(actor)
    if name is None:
        return False, f"unregistered writer {actor!r}"
    budget = entry.get("budget_monthly_usd")
    if budget is None:
        return True, f"{name}: no budget declared"
    writers = [str(w) for w in (entry.get("writes_as") or [])] or [name]
    spent = month_to_date_cents(space_dir, writers, today) / 100
    if spent >= float(budget):
        return False, f"{name} has spent {spent:.2f} of a {budget} USD monthly budget"
    return True, f"{name}: {spent:.2f} of {budget} USD this month"


def absent(principal: str, root: Path | None = None, now: float | None = None) -> tuple[bool, str]:
    """Stage 5: is a principal absent? Its contracts are verified on its own
    machine and attested to the ledger (job_verify -> metric.attest
    job.verify). No passing attestation from any of its writers within
    ABSENT_AFTER_HOURS means nobody has heard from it; an item addressed to it
    waits and says why, and is never quietly reassigned."""
    import glob, time
    root = Path(root) if root else Path(__import__("os").environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
    now = now or time.time()
    name, entry, _ = _limits(principal)
    if name is None:
        return True, f"unregistered principal {principal!r}"
    writers = {str(w) for w in (entry.get("writes_as") or [])} | {name}
    latest_ok, latest_any = 0.0, 0.0
    for f in glob.glob(str(root / "[0-9]-*" / ".datacore" / "events" / "*.jsonl")) + glob.glob(str(root / ".datacore" / "events" / "*.jsonl")):
        if Path(f).stem not in writers:
            continue
        for line in Path(f).read_text(encoding="utf-8", errors="replace").splitlines():
            if '"job.verify"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            p = e.get("payload") or {}
            if p.get("metric") != "job.verify":
                continue
            ms = str(e.get("hlc", "")).split(".")[0]
            if not ms.isdigit():
                continue
            t = int(ms) / 1000
            latest_any = max(latest_any, t)
            if p.get("ok"):
                latest_ok = max(latest_ok, t)
    if not latest_any:
        return True, f"{name}: never heard from (no job.verify attestation on the record)"
    age_h = (now - latest_ok) / 3600 if latest_ok else float("inf")
    if age_h > ABSENT_AFTER_HOURS:
        return True, f"{name}: last passing verification {age_h:.0f}h ago" if latest_ok else f"{name}: verifications on the record, none passing"
    return False, f"{name}: verified {age_h:.0f}h ago"


def check_override(actor: str, item_id: str | None, space_dir: Path, policy=None) -> tuple[bool, str]:
    """Stage 5 arbitration: closing, releasing or reassigning another
    principal's item is allowed only for a principal that precedes the owner
    in the policy's arbitration order. The owner itself is always allowed;
    an unowned item is anyone's to close."""
    order = list(getattr(policy, "arbitration", None) or [])
    if not item_id:
        return True, "no item"
    try:
        from ledger.fold import fold
        from ledger.log import read_events
        state = fold(read_events(Path(space_dir)))
    except Exception as exc:  # noqa: BLE001
        return True, f"could not fold {space_dir}: {exc}"
    item = state.items.get(item_id)
    if item is None or not getattr(item, "owner", None):
        return True, "unowned"
    owner_p = principal_of(str(item.owner))[0] or str(item.owner)
    actor_p = principal_of(actor)[0] or actor
    if owner_p == actor_p:
        return True, "own item"
    if actor_p in order and (owner_p not in order or order.index(actor_p) < order.index(owner_p)):
        return True, f"{actor_p} arbitrates over {owner_p}"
    return False, f"{actor_p} may not override {owner_p}'s item (arbitration: {' > '.join(order) or 'none declared'})"
