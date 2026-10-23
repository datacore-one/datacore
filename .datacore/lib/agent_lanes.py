#!/usr/bin/env python3
"""Agent lanes: which work each agent may claim, and how much per period.

One file the owner edits, `.datacore/config/agent_lanes.yaml`, names each
agent's lane (roles, `:AI:<kind>:` tags, priority ids) and its budget (claims
per period). `check_lane(actor, item)` answers whether that agent may claim
that item now; claim_gate.check_claim applies it to every ledger claim, and
any other claimer (the nightshift org queue) can call it the same way.

The lane of an item is read from the item itself, most specific first:

  1. `lane: <agent>`           an explicit lane;
  2. `role: <role>`            the role it needs (venture roles, and the role
                               templates `.datacore/templates/roles/<role>.md`
                               that the role-pool work loads);
  3. `tags`                    `:AI:<kind>:` tags (`AI` itself is no lane);
  4. `priority: <id>`          a priority id the owner mapped to a lane.

Markers that point at two lanes make the item ambiguous: refused, never
guessed. An item with no marker is in no other agent's lane, so the lane
does not refuse it: who may take an unmarked item is the dispatcher's rule
(ledger_claim dispatches only items addressed to the claimer; the policy
refuses a claim on an item assigned to someone else).

The budget counts the principal's `item.claim` events in the current period
(UTC day, ISO week, or calendar month) across every space's ledger, by every
writer the principal owns (`writes_as`). A spent budget refuses the claim
with a one-line reason; nothing is dropped silently.

No lane file at all is an unconfigured installation: no lane gate. A lane file
that cannot be read refuses every claim by an agent, because could-not-tell
is not the same answer as yes. An agent the file does not name is not gated.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

LIB = Path(__file__).resolve().parent
DEFAULT_LANES_FILE = LIB.parent / "config" / "agent_lanes.yaml"
PERIODS = ("day", "week", "month")


def load_lanes(path: Path | None = None) -> dict | None:
    """The validated lane file, or None when there is none.

    Returns {"period", "lanes": {agent: {roles, tags, priorities, budget}},
    "role_owner", "tag_owner", "priority_owner"}. Raises ValueError for a
    file that exists but cannot be trusted."""
    import yaml
    p = Path(path or DEFAULT_LANES_FILE)
    if not p.exists():
        return None
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"lane file cannot be read ({type(exc).__name__})") from None
    if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("lanes"), dict):
        raise ValueError("lane file must have version: 1 and a lanes mapping")
    period = raw.get("period", "day")
    if period not in PERIODS:
        raise ValueError(f"period must be one of {', '.join(PERIODS)}")
    lanes: dict[str, dict] = {}
    owners = {"roles": {}, "tags": {}, "priorities": {}}
    for agent, spec in raw["lanes"].items():
        if not isinstance(spec, dict):
            raise ValueError(f"lane {agent!r} must be a mapping")
        entry = {}
        for key in ("roles", "tags", "priorities"):
            vals = spec.get(key) or []
            if not isinstance(vals, list) or any(not isinstance(v, str) or not v for v in vals):
                raise ValueError(f"lane {agent!r}: {key} must be a list of names")
            entry[key] = [v.lower() for v in vals]
            for v in entry[key]:
                if v in owners[key] and owners[key][v] != agent:
                    raise ValueError(f"{key[:-1]} {v!r} is in two lanes ({owners[key][v]}, {agent})")
                owners[key][v] = str(agent)
        budget = spec.get("budget")
        tasks = budget.get("tasks") if isinstance(budget, dict) else None
        if type(tasks) is not int or tasks < 0:
            raise ValueError(f"lane {agent!r}: budget.tasks must be a whole number of claims, 0 or more")
        entry["budget"] = {"tasks": tasks}
        lanes[str(agent)] = entry
    return {"period": period, "lanes": lanes, "role_owner": owners["roles"],
            "tag_owner": owners["tags"], "priority_owner": owners["priorities"]}


def _tags(raw) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return []
    out = []
    for t in raw:
        for part in str(t).split(":"):
            part = part.strip().lower()
            if part and part != "ai":
                out.append(part)
    return out


def lane_of(item: dict, cfg: dict) -> tuple[str | None, str]:
    """(agent whose lane the item is in, how that was read). None with the
    reason when the item points at no lane or at more than one."""
    item = item or {}
    found: dict[str, str] = {}
    lane = item.get("lane")
    if isinstance(lane, str) and lane:
        found[f"lane {lane}"] = lane.lower()
    role = item.get("role")
    if isinstance(role, str) and role.lower() in cfg["role_owner"]:
        found[f"role {role}"] = cfg["role_owner"][role.lower()]
    for tag in _tags(item.get("tags")):
        if tag in cfg["tag_owner"]:
            found[f"tag {tag}"] = cfg["tag_owner"][tag]
    prio = item.get("priority")
    if isinstance(prio, str) and prio.lower() in cfg["priority_owner"]:
        found[f"priority {prio}"] = cfg["priority_owner"][prio.lower()]
    agents = set(found.values())
    if len(agents) > 1:
        return None, "ambiguous: " + ", ".join(f"{k} -> {v}" for k, v in sorted(found.items()))
    if agents:
        return agents.pop(), ", ".join(sorted(found))
    return None, "no lane marker"


def period_start(period: str, now: dt.datetime) -> dt.datetime:
    day = now.astimezone(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        return day - dt.timedelta(days=day.weekday())
    if period == "month":
        return day.replace(day=1)
    return day


def _root() -> Path:
    return Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))


def _writer_events(space: Path, writers: set[str]):
    """The task-log events of these writers in one space: their own files only
    (`<writer>.jsonl` and branch-scoped `<writer>-run-<date>.jsonl`), parsed as
    read_events parses them. Reading every writer's log of a large space takes
    seconds; a claim should not."""
    from actor_identity import base_writer
    from ledger import log as ledger_log
    events_dir = Path(space) / ".datacore" / "events"
    if not events_dir.is_dir():
        return []
    out = []
    for path in sorted(events_dir.glob("*.jsonl")):
        if base_writer(path.stem) not in writers:
            continue
        raw = path.read_bytes()
        try:
            file_events, _ = ledger_log._parse_log_bytes(raw, path)
        except ledger_log.CorruptLogError:
            file_events = ledger_log._parse_prefix(raw)   # same rule as read_events
        out.extend(file_events)
    return out


def claims_in_period(principal: str, root: Path | None = None, now: dt.datetime | None = None,
                     period: str = "day") -> int:
    """The principal's `item.claim` events since the period began, in every
    space under `root`, by any writer it owns."""
    from actor_identity import _writer_names, base_writer, principals
    entry = principals().get(principal) or {}
    writers = _writer_names(entry, principal) | {base_writer(principal)}
    now = now or dt.datetime.now(dt.timezone.utc)
    since_ms = int(period_start(period, now).timestamp() * 1000)
    until_ms = int(now.timestamp() * 1000)
    root = Path(root) if root else _root()
    n = 0
    for space in sorted(root.glob("[0-9]*-*")):
        if not space.is_dir():
            continue
        for event in _writer_events(space, writers):
            if event.type != "item.claim" or base_writer(str(event.actor)) not in writers:
                continue
            try:
                ms = int(str(event.hlc).split(".", 1)[0])
            except ValueError:
                continue
            if since_ms <= ms <= until_ms:
                n += 1
    return n


def check_lane(actor: str, item: dict | None, *, root: Path | None = None, now: dt.datetime | None = None,
               usage=None, path: Path | None = None) -> tuple[bool, str]:
    """May `actor` claim `item` now? (allowed, one-line reason)."""
    from actor_identity import principal_of
    try:
        cfg = load_lanes(path)
    except ValueError as exc:
        return False, f"agent lanes are unreadable ({exc}); fix .datacore/config/agent_lanes.yaml"
    if cfg is None:
        return True, "no lane file; lane gate inactive on this installation"
    try:
        name = principal_of(actor)[0] or actor
    except ValueError as exc:
        return False, f"principal registry is unreadable ({exc})"
    mine = cfg["lanes"].get(name)
    if mine is None:
        return True, f"{name}: no lane declared"
    item = item or {}
    owner, how = lane_of(item, cfg)
    if owner is None and how.startswith("ambiguous"):
        return False, f"{name} may not claim it: the item is {how}; give it one lane"
    if owner is None:
        owner, how = name, "no lane marker on the item"
    if owner != name:
        return False, f"out of lane: the item is in {owner}'s lane ({how}); hand it to {owner}"
    budget = mine["budget"]["tasks"]
    now = now or dt.datetime.now(dt.timezone.utc)
    count = (usage or claims_in_period)(name, root=root, now=now, period=cfg["period"])
    if count >= budget:
        return False, (f"{name} has used {count} of {budget} claims this {cfg['period']}: "
                       f"the budget is spent (agent_lanes.yaml)")
    return True, f"{name}: in lane ({how}); {count} of {budget} claims this {cfg['period']}"


if __name__ == "__main__":
    import json
    import sys
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    cfg = load_lanes()
    if cfg is None:
        print("no lane file")
        raise SystemExit(0)
    now = dt.datetime.now(dt.timezone.utc)
    print(json.dumps({a: {"budget": s["budget"]["tasks"], "used": claims_in_period(a, now=now, period=cfg["period"])}
                      for a, s in cfg["lanes"].items()}, indent=2))
