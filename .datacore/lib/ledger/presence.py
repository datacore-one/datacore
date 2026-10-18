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
unsigned, or edited after signing -- is skipped here. What it does NOT prove:
that the writer is the agent it names. Binding an agent name to one writer key
is the principals registry's job and is not done here.
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


def _signed(e, registry_path) -> bool:
    if not e.sig:
        return False
    from .keys import verify
    try:
        return bool(verify(e.actor, canonical_bytes(event_body(e)), e.sig, registry_path=registry_path))
    except Exception:  # noqa: BLE001 -- an unknown writer or a malformed sig is "not signed"
        return False


def state_of(age_s: float, interval_s: int) -> str:
    if age_s <= interval_s * OK_WITHIN:
        return "ok"
    if age_s <= interval_s * LATE_WITHIN:
        return "late"
    return "silent"


def presence(events, now_ms: int, *, agents=(), registry_path=None) -> dict[str, dict]:
    """{agent: {state, last_ts, age_s, interval_s, host, actor}} for every agent
    that checked in or is expected (`agents`). Newest verified check-in wins."""
    candidates = sorted((e for e in events if e.type == CHECKIN_TYPE and _valid(e.payload)),
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
        age_s = max(0, now_ms - p["ts"]) // 1000
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
    every = _span(p["interval_s"])
    ago = _span(p["age_s"])
    where = f" from {p['host']}" if p.get("host") else ""
    if p["state"] == "ok":
        return f"{name}: ok, checked in {ago} ago{where} (every {every})"
    return f"{name}: {p['state']}, last check-in {ago} ago{where}, expected every {every}"


def briefing_lines(pres: dict[str, dict]) -> list[str]:
    """The briefing's agent block: one line per agent, sorted by name."""
    return [line(a, pres[a]) for a in sorted(pres)]
