"""Who is checking in: a fold over `agent.checkin` events.

Each agent's heartbeat timer appends a signed `agent.checkin`
{agent, host, interval_s, ts} through the hub (`python3 -m ledger.checkin`).
Presence is the latest check-in per agent that verifies, against the interval
that agent declared:

    ok      the last check-in is at most 1.25 intervals old (timer jitter)
    late    at most 2 intervals old
    silent  older than that, or no check-in at all from an expected agent

The same read serves the hub (`Hub.presence`, GET /ledger/hub/{space}/presence),
the git copy (`presence(read_events(space))` -- the fallback when the hub is
unreachable), Winston's 6-hourly check (agent_presence_check.py) and the
briefing (`briefing_lines`).

Only a check-in whose signature verifies for its writer counts: the hub
refuses unsigned ones, and a line that reached the git copy some other way --
unsigned, or edited after signing -- is skipped here. In a hub space the git
copy is read through the hub's own admission rule first (`admitted_events`,
ledger.hub_admission; audit findings 2 and 6): a check-in signed for another
space, hub or log, or one past a refused event of its log, is not one here. What it does NOT prove:
that a check-in is the agent's own process rather than another one using its
writer key. A check-in counts for an agent only when that agent is the
principal its writer writes for (or, with no principal, the writer itself),
and its time agrees with its envelope HLC (Codex re-audit, new defect 7).
"""
from __future__ import annotations

import re

from .events import CHECKIN_TYPE, canonical_bytes, event_body

OK_WITHIN = 1.25
LATE_WITHIN = 2.0
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")


def _valid(payload) -> bool:
    return (isinstance(payload, dict) and isinstance(payload.get("agent"), str)
            and bool(_NAME.fullmatch(payload["agent"]))
            and type(payload.get("interval_s")) is int and payload["interval_s"] > 0
            and type(payload.get("ts")) is int)


#: How far a check-in's own time may lie from its envelope HLC, and ahead of
#: the hub's clock (both stamped by the writer's one wall clock at append time).
CLOCK_BOUND_MS = 10 * 60 * 1000


def agent_of(actor: str) -> str:
    """The agent a writer may check in as: the principal it writes for in the
    registry, or -- for a writer that belongs to no principal -- its own base
    name (Codex re-audit, new defect 7: a check-in named any agent)."""
    from actor_identity import base_writer
    try:
        from actor_identity import principal_of
        name, _ = principal_of(actor)
    except (ImportError, ValueError):
        name = None
    return name or base_writer(actor)


def payload_problem(actor: str, payload, now_ms: int | None = None) -> str | None:
    """Why a check-in payload is not one `actor` may send now, or None."""
    if not _valid(payload):
        return "a check-in carries agent, interval_s (> 0) and ts"
    if payload["agent"] != agent_of(actor):
        return f"{actor!r} checks in as {agent_of(actor)!r}, not {payload['agent']!r}"
    if now_ms is not None and payload["ts"] > now_ms + CLOCK_BOUND_MS:
        return f"its time is {(payload['ts'] - now_ms) // 1000} s ahead of the clock"
    return None


def checkin_problem(e, now_ms: int | None = None) -> str | None:
    """Why `e` does not count as a check-in (None: it does). Its agent is its
    writer's, and its time agrees with its own envelope HLC and is not ahead of
    `now_ms` (the hub's clock at the front door)."""
    why = payload_problem(e.actor, e.payload, now_ms)
    if why:
        return why
    try:
        stamped = int(str(e.hlc).split(".", 1)[0])
    except ValueError:
        return "its hlc is unreadable"
    if abs(e.payload["ts"] - stamped) > CLOCK_BOUND_MS:
        return f"its time is {abs(e.payload['ts'] - stamped) // 1000} s from its own hlc"
    return None


def _signed(e, registry_path) -> bool:
    if not e.sig:
        return False
    from .keys import verify
    try:
        return bool(verify(e.actor, canonical_bytes(event_body(e)), e.sig, registry_path=registry_path))
    except Exception:  # noqa: BLE001 -- an unknown writer or a malformed sig is "not signed"
        return False


def admitted_events(space_dir, now_ms: int, *, registry_path=None) -> list:
    """The events of a space's git copy that the hub's admission rule admits.

    A space without a hub has no admission rule; its events are read as they
    are, and `presence` still checks each check-in's signature."""
    from .finality import effective_events
    from .hub import authoritative_events
    from .log import read_events
    events, kw = authoritative_events(space_dir, read_events(space_dir), registry_path=registry_path,
                                      now_ms=now_ms)
    if not kw:
        return events
    # A check-in the hub refused (a rejected proposal) or the owner reversed is
    # not one -- the one correction rule's answer (round 6, ledger.finality).
    return effective_events(events, kw)


def state_of(age_s: float, interval_s: int) -> str:
    if age_s <= interval_s * OK_WITHIN:
        return "ok"
    if age_s <= interval_s * LATE_WITHIN:
        return "late"
    return "silent"


def presence(events, now_ms: int, *, agents=(), registry_path=None) -> dict[str, dict]:
    """{agent: {state, last_ts, age_s, interval_s, host, actor}} for every agent
    that checked in or is expected (`agents`). Newest verified check-in wins."""
    # Only a check-in whose agent is its writer's and whose time agrees with
    # its own hlc counts (Codex re-audit, new defect 7).
    candidates = sorted((e for e in events if e.type == CHECKIN_TYPE and _valid(e.payload)
                         and checkin_problem(e) is None),
                        key=lambda e: (e.payload["ts"], e.hlc), reverse=True)
    latest: dict[str, object] = {}
    for e in candidates:
        agent = e.payload["agent"]
        if agent not in latest and _signed(e, registry_path):
            latest[agent] = e
    out: dict[str, dict] = {}
    for agent in sorted(set(agents) | set(latest)):
        e = latest.get(agent)
        if e is None:
            out[agent] = {"state": "silent", "last_ts": None, "age_s": None, "interval_s": None,
                          "host": None, "actor": None}
            continue
        p = e.payload
        if p["ts"] > now_ms:
            # NEGATIVE AGE IS AN ERROR, never clamped to "just now" (Codex
            # re-audit, new defect 7): this reader's clock and the writer's disagree.
            out[agent] = {"state": "error", "last_ts": p["ts"], "age_s": None, "interval_s": p["interval_s"],
                          "host": p.get("host"), "actor": e.actor,
                          "error": f"its last check-in is {(p['ts'] - now_ms) // 1000} s ahead of this "
                                   "reader's clock"}
            continue
        age_s = (now_ms - p["ts"]) // 1000
        out[agent] = {"state": state_of(age_s, p["interval_s"]), "last_ts": p["ts"], "age_s": age_s,
                      "interval_s": p["interval_s"], "host": p.get("host"), "actor": e.actor}
    return out


def _span(seconds) -> str:
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} min"
    if seconds < 48 * 3600:
        return f"{round(seconds / 3600)} h"
    return f"{round(seconds / 86400)} days"


def line(agent: str, p: dict) -> str:
    """One plain status line, starting with the agent's name."""
    name = agent.capitalize()
    if p.get("last_ts") is None:
        return f"{name}: silent, never checked in"
    if p["state"] == "error":
        return f"{name}: error, {p.get('error') or 'its check-in time cannot be read'}"
    every = _span(p["interval_s"])
    ago = _span(p["age_s"])
    where = f" from {p['host']}" if p.get("host") else ""
    if p["state"] == "ok":
        return f"{name}: ok, checked in {ago} ago{where} (every {every})"
    return f"{name}: {p['state']}, last check-in {ago} ago{where}, expected every {every}"


def briefing_lines(pres: dict[str, dict]) -> list[str]:
    """The briefing's agent block: one line per agent, sorted by name."""
    return [line(a, pres[a]) for a in sorted(pres)]
