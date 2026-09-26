"""AGT-9: I can message Winston, Miles, Tris and Data one-to-one on Telegram,
and the agent I wrote to answers.

Kind: production contract (read-only ssh, timeouts; no message is sent). For
each agent, on its own host:
  * the process that answers its bot is running (hermes-gateway on the box for
    Winston, datacore-telegram / miles_bot.py on nightshift for Miles,
    hermes-gateway on hermes for Tris, openclaw-gateway on plur-claw for Data);
  * its bot token is a credential the broker knows and can prove alive on that
    host (`creds.py doctor --id <bot>` -> ok; n-a is never a pass). Without it
    nobody can tell whether the bot I write to is the one that answers.

Not covered (needs a live Telegram round trip, out of scope for an eval that
must not message anyone): that a message to bot X is answered by agent X and
not another poller of the same token.

Seeded failure: modules/telegram/tests is empty; bots per roster (@kton9_bot /
Winston, @datacore_1_bot / Miles, @TrisHermes_bot / Tris, @plurclaw_bot /
Data) were never checked together. Verified by pointing an agent at a unit
that does not exist.
"""
from __future__ import annotations

import subprocess

import pytest

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]

AGENTS = {
    # agent: (ssh host, unit that answers its bot, broker id of its bot token -- miles/tris ids are
    # the names their entries should get; neither bot token is in the index today)
    "winston": ("winston", "hermes-gateway", "winston-telegram-bot"),
    "miles": ("nightshift", "datacore-telegram", "miles-telegram-bot"),
    "tris": ("hermes", "hermes-gateway", "tris-telegram-bot"),
    "data": ("plur-claw", "openclaw-gateway", "mrdata-telegram-bot"),
}


def _ssh(host, cmd):
    return subprocess.run([*SSH, host, cmd], capture_output=True, text=True, timeout=50)


@pytest.mark.production
@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_the_process_that_answers_the_agents_bot_is_running(agent):
    host, unit, _ = AGENTS[agent]
    r = _ssh(host, f"systemctl is-active {unit} 2>/dev/null || systemctl --user is-active {unit} 2>/dev/null; true")
    assert r.returncode == 0, f"{host}: unreachable ({r.stderr.strip()[-160:]})"
    assert "active" in r.stdout.split(), f"{agent}: {unit} on {host} is {r.stdout.strip() or 'unknown'}"


@pytest.mark.production
@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_the_agents_bot_is_a_known_live_credential_on_its_host(agent):
    host, _, cred = AGENTS[agent]
    r = _ssh(host, f"cd ~/Data && timeout 40 python3 .datacore/lib/creds.py doctor --id {cred} 2>&1 | tail -4")
    assert r.returncode == 0, f"{host}: unreachable ({r.stderr.strip()[-160:]})"
    lines = [l.split() for l in r.stdout.splitlines() if cred in l]
    state = lines[0][0] if lines else "missing"
    assert state == "ok", f"{agent}'s bot credential {cred!r} on {host}: {state} -- {r.stdout.strip()[-200:]!r}"
