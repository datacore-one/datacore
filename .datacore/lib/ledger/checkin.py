"""An agent says it is working: one signed `agent.checkin` through the hub.

Called by each agent's EXISTING heartbeat timer, one line added to it:

    cd ~/Data/.datacore/lib && python3 -m ledger.checkin --space ~/Data/<space> \\
        --agent data --interval 1800 --hub-url http://<hub>:<port> --token-id <broker id>

It is an ordinary HubWriter append: the event lands in this writer's own log
first (always), then goes to the hub. When the hub is unreachable -- or no
`--hub-url` is given -- it stays buffered in the local log and is replayed on
the next check-in that reaches the hub; git publication of the space carries it
either way, which is what the check reads when the hub is down.

The hub's bearer token is asked of the credentials broker at send time
(`creds get <token-id> --consumer ledger-checkin`) and never printed. A broker
that serves nothing is treated as an unreachable hub: buffer, do not fail the
heartbeat.

Exit 0 when the check-in was accepted or buffered, 2 when the space does not
use a hub or the hub refused it (the reason on stderr).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import hub as hubmod
from .events import CHECKIN_TYPE

CONSUMER = "ledger-checkin"
_LIB = Path(__file__).resolve().parents[1]


class Offline:
    """The transport when no hub is named: every call is 'unreachable'."""

    def _down(self, *_a, **_k):
        raise hubmod.HubUnavailable("no hub named for this writer")

    append = request_lease = renew_lease = status = presence = seal_request = reconcile = _down


def broker_token(cred_id: str, consumer: str = CONSUMER) -> str:
    """The broker's value for `cred_id` (stdout of `creds get`); HubUnavailable when none."""
    exe = shutil.which("creds") or str(_LIB.parent / "bin" / "creds")
    try:
        r = subprocess.run([exe, "get", cred_id, "--consumer", consumer],
                           capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError) as e:
        raise hubmod.HubUnavailable(f"the broker could not be asked for {cred_id} ({type(e).__name__})") from None
    if r.returncode != 0 or not r.stdout.strip():
        raise hubmod.HubUnavailable(f"the broker served no {cred_id}")
    return r.stdout.strip()


def transport_for(hub_url: str | None, token_id: str | None, space_name: str, consumer: str = CONSUMER):
    """HttpTransport to the hub, or Offline when none is named or no token is served."""
    if not hub_url or not token_id:
        return Offline()
    try:
        token = broker_token(token_id, consumer)
    except hubmod.HubUnavailable:
        return Offline()
    return hubmod.HttpTransport(hub_url, token, space_name)


def send(space_dir, *, agent: str, interval_s: int, transport, actor: str | None = None,
         host: str | None = None, keys_dir=None, registry_path=None, state_dir=None, clock=None) -> dict:
    """Append one check-in; {status: accepted|duplicate|buffered, seq, hash}."""
    if type(interval_s) is not int or interval_s <= 0:
        raise ValueError("interval_s must be a positive number of seconds")
    import actor_identity
    actor = actor or actor_identity.this_actor()
    host = host or actor_identity.short_hostname()
    writer = hubmod.HubWriter(space_dir, actor, transport, keys_dir=keys_dir, registry_path=registry_path,
                              state_dir=state_dir, clock=clock)
    payload = {"agent": agent, "host": host, "interval_s": interval_s, "ts": int(writer.clock())}
    event, receipt = writer.append(CHECKIN_TYPE, payload)
    if receipt is None:
        return {"status": "buffered", "seq": event.seq, "hash": event.hash}
    return {"status": receipt.get("status", "accepted"), "seq": event.seq, "hash": event.hash}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m ledger.checkin", description=__doc__.split("\n")[0])
    ap.add_argument("--space", required=True, help="the space directory whose hub carries check-ins")
    ap.add_argument("--agent", required=True, help="the agent's name, as the roster and principals registry name it")
    ap.add_argument("--interval", type=int, required=True, help="seconds between this agent's check-ins")
    ap.add_argument("--actor", default=None, help="the ledger writer (default: this host's declared actor)")
    ap.add_argument("--hub-url", default=os.environ.get("DATACORE_HUB_URL"))
    ap.add_argument("--token-id", default=os.environ.get("DATACORE_HUB_TOKEN_ID"),
                    help="the broker id of the hub's bearer token")
    a = ap.parse_args(argv)
    space = Path(a.space).expanduser()
    try:
        result = send(space, agent=a.agent, interval_s=a.interval, actor=a.actor,
                      transport=transport_for(a.hub_url, a.token_id, space.name))
    except hubmod.HubRefused as exc:
        print(f"ledger.checkin: refused ({exc.code}): {exc.message}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
