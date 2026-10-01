#!/usr/bin/env python3
"""This installation's own people and agents, by what they ARE, not by name.

Shipped code must not name anyone of ours (INS-3): a stranger's install has
its own owner and its own agents. What code needs is "the owner", "the chief
of staff", "the agent that runs operations" -- so it asks for that here, and
the answer comes from the gitignored `.datacore/registry/principals.yaml`
(template: `principals.yaml.example`). No registry, no names: every lookup
returns None / [] / {} and the caller falls back to something neutral.

A top-level section of the same file (`section(key)`) holds the few settings
that are lists of this install's agents, such as the delegation exercise's
ring or the cross-model audit's roster.

Read-only, and never raises: a registry that `actor_identity` would refuse is
reported by the tools that enforce it (claim_gate, v2_verify), not here.
"""
from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


def _path(path: Path | None) -> Path:
    if path is not None:
        return Path(path)
    import actor_identity
    return actor_identity.PRINCIPALS  # resolved at call time: drills repoint it


def _principals(path: Path | None = None) -> dict:
    import actor_identity
    try:
        return actor_identity.principals(_path(path)) or {}
    except (ValueError, OSError):
        return {}


def entries(path: Path | None = None) -> dict:
    """{name: entry} for every principal; {} when there is no usable registry."""
    return dict(_principals(path))


def owner(path: Path | None = None) -> str | None:
    """The principal whose decision is final, or None."""
    for name, p in _principals(path).items():
        if str(p.get("decision") or "").strip().lower() == "final":
            return name
    return None


def by_role(role: str, path: Path | None = None) -> str | None:
    """The principal whose `role:` is `role` (case-insensitive), or None."""
    want = role.strip().lower()
    for name, p in _principals(path).items():
        if str(p.get("role") or "").strip().lower() == want:
            return name
    return None


def _hosts(entry: dict) -> list[str]:
    hosts = entry.get("hosts") or []
    return [str(h) for h in hosts] if isinstance(hosts, list) else []


def person_on(host: str, path: Path | None = None) -> str | None:
    """The person whose machine `host` is (a human principal listing it under
    `hosts`), the owner first; None when no person works there."""
    people = [(n, p) for n, p in _principals(path).items()
              if p.get("kind") == "human" and host in _hosts(p)]
    people.sort(key=lambda np: str(np[1].get("decision") or "").strip().lower() != "final")
    return people[0][0] if people else None


def resident_agent(host: str, path: Path | None = None) -> str | None:
    """The agent that answers for machine `host`: the first agent principal
    listing it under `hosts`. None on a person's machine -- an agent that also
    runs there does not make it the agent's -- and when no agent lives there."""
    if person_on(host, path):
        return None
    for name, p in _principals(path).items():
        if p.get("kind") == "agent" and host in _hosts(p):
            return name
    return None


def display(name: str, path: Path | None = None) -> str:
    """A principal's display name; the name itself when it has none."""
    return str((_principals(path).get(name) or {}).get("display") or name)


def agents(path: Path | None = None) -> list[str]:
    """Every agent principal, in registry order."""
    return [n for n, p in _principals(path).items() if p.get("kind") == "agent"]


def section(key: str, path: Path | None = None) -> dict:
    """A top-level mapping of the registry file other than `principals`, or {}."""
    try:
        import yaml
        data = yaml.safe_load(_path(path).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 -- absent or unreadable is "not configured"
        return {}
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


if __name__ == "__main__":
    print(f"owner: {owner()}")
    for a in agents():
        print(f"agent: {a} ({display(a)})")
