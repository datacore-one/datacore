#!/usr/bin/env python3
"""Winston's 6-hourly check: which agent has stopped checking in?

Why: on 2026-10-18 the 15-minute host probe (cos_fleet_probe.sh, TCP 22) said
Data's machine was up all morning while the Data agent itself was stuck. A host
answering is not an agent working. Each agent now appends a signed
`agent.checkin` through the hub (ledger.checkin); this reads who stopped.

    agent_presence_check.py check    --space ~/Data/<space> --agents miles,tris,data,winston \\
                                     [--hub-url URL --token-id ID]
    agent_presence_check.py briefing --space ... --agents ...      # one line per agent

`check` SENDS through this host's alert route. Use `--dry-run` to see what it
would send (the state file is left alone).

READ. The hub first (GET /ledger/hub/{space}/presence). When the hub cannot be
reached, the git copy of the space on this machine (`read_events`) -- the same
fold, so the same answer up to the last pull. The alert says which it read.

ALERT. Through this host's own alert route (job_verify._send_command: the
`command:` in ~/.datacore/alerts.yaml, which on Winston's host is agent_alert.py
with Winston's bot; the test suite points it at nothing). No new Telegram
client, no credential read here. One alert per silence: an agent named once
stays in the state file (~/.datacore/state/agent-presence-alerts.json) until it
checks in again, and then one clear message is sent. A message that could not
be sent is not recorded, so the next cycle tries again.

The schedule is a repo-held template only (agent-presence-check.timer beside
this file); nothing here installs it. Exit 0 always for `check` unless the
read itself failed (exit 1), so a cron mail is never the alert channel.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledger import hub as hubmod  # noqa: E402
from ledger import presence as presmod  # noqa: E402

DEFAULT_STATE = Path.home() / ".datacore" / "state" / "agent-presence-alerts.json"
CONSUMER = "agent-presence-check"


def read(space_dir, *, agents, transport=None, now_ms: int | None = None,
         registry_path=None) -> tuple[dict, str]:
    """(presence, source): the hub's answer, or the git copy's when it is unreachable."""
    if transport is not None:
        try:
            return transport.presence(tuple(agents))["agents"], "hub"
        except hubmod.HubUnavailable:
            pass
    from ledger.log import read_events
    now = int(time.time() * 1000) if now_ms is None else now_ms
    return presmod.presence(read_events(Path(space_dir)), now, agents=agents, registry_path=registry_path), "git"


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    from file_utils import atomic_write_text
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def _send_default(text: str) -> bool:
    import job_verify
    return job_verify._send_command(text)


def _where(source: str) -> str:
    return "" if source == "hub" else " (the hub was unreachable: read from this machine's git copy)"


def run(space_dir, *, agents, transport=None, state_path=DEFAULT_STATE,
        send: Callable[[str], bool] | None = None, now_ms: int | None = None, registry_path=None) -> dict:
    """One cycle. {source, alerted, cleared, unsent, presence}."""
    send = send or _send_default
    pres, source = read(space_dir, agents=agents, transport=transport, now_ms=now_ms, registry_path=registry_path)
    state_path = Path(state_path)
    alerted_before = _load(state_path)
    alerted, cleared, unsent = [], [], []
    for agent in sorted(pres):
        p = pres[agent]
        if p["state"] != "ok" and agent not in alerted_before:
            text = (f"⚠️ Agent not checking in: {presmod.line(agent, p)}. "
                    f"Its host may still answer; the agent itself has not said it is working{_where(source)}.")
            if send(text):
                alerted.append(agent)
                alerted_before[agent] = {"state": p["state"], "last_ts": p["last_ts"], "source": source}
            else:
                unsent.append(agent)
        elif p["state"] == "ok" and agent in alerted_before:
            if send(f"✅ Agent checking in again: {presmod.line(agent, p)}{_where(source)}."):
                cleared.append(agent)
                del alerted_before[agent]
            else:
                unsent.append(agent)
    _save(state_path, alerted_before)
    return {"source": source, "alerted": alerted, "cleared": cleared, "unsent": unsent, "presence": pres}


def briefing(space_dir, *, agents, transport=None, now_ms: int | None = None,
             registry_path=None) -> tuple[list[str], str]:
    """The /today block: (one line per agent, source)."""
    pres, source = read(space_dir, agents=agents, transport=transport, now_ms=now_ms, registry_path=registry_path)
    lines = presmod.briefing_lines(pres)
    if source != "hub":
        lines = [ln + " [git copy]" for ln in lines]
    return lines, source


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("check", "briefing"))
    ap.add_argument("--space", required=True)
    ap.add_argument("--agents", required=True, help="comma-separated: the agents expected to check in")
    ap.add_argument("--hub-url", default=os.environ.get("DATACORE_HUB_URL"))
    ap.add_argument("--token-id", default=os.environ.get("DATACORE_HUB_TOKEN_ID"))
    ap.add_argument("--state", default=str(DEFAULT_STATE))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the alerts instead of sending them; the state file is not touched")
    a = ap.parse_args(argv)
    from ledger.checkin import Offline, transport_for
    space = Path(a.space).expanduser()
    agents = tuple(x.strip() for x in a.agents.split(",") if x.strip())
    transport = transport_for(a.hub_url, a.token_id, space.name, CONSUMER)
    transport = None if isinstance(transport, Offline) else transport
    try:
        if a.cmd == "briefing":
            lines, source = briefing(space, agents=agents, transport=transport)
            print(json.dumps({"lines": lines, "source": source}) if a.json else "\n".join(lines))
            return 0
        if a.dry_run:
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp) / "state.json"
                if Path(a.state).exists():
                    state.write_text(Path(a.state).read_text())
                result = run(space, agents=agents, transport=transport, state_path=state,
                             send=lambda text: print("WOULD SEND:", text) or True)
        else:
            result = run(space, agents=agents, transport=transport, state_path=Path(a.state))
    except Exception as exc:  # noqa: BLE001 -- say what failed, in one line
        print(f"agent_presence_check: could not read presence ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 1
    print(json.dumps({k: v for k, v in result.items() if k != "presence" or a.json}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
