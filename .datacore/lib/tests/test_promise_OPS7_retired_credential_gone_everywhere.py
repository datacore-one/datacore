"""OPS-7: "A retired credential is removed from every machine in one step, and no
running process still sees it afterwards."

Kind: deterministic. The one step that reaches every machine is
secrets/scripts/distribute.sh; it runs against a disposable two-host fleet
(fake ssh/scp, fixture values only -- see _creds_fleet_harness.py). The
credential is retired by removing it from the central store (OI-13:
ANTHROPIC_API_KEY was retired from global.env only; the hosts' local stores were
never proven clean).

After the one step:
  - no Datacore-owned store on any host still holds the retired variable
    (assembled .env AND the per-host stores: ~/.config/cos.env,
    ~/.datacore/datacore.env, env/local.env);
  - processes that read credentials from an env file at start (systemd
    EnvironmentFile, sourced crons) are told to reload -- observable here as a
    restart/reload issued to the host in the same run. Without it a daemon
    started before the retirement keeps the value in its environment.

Seeded failure: the retired key survives in a host's cos.env, or the step
delivers the new env without touching any running consumer.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _creds_fleet_harness import Fleet  # noqa: E402

RETIRED = "FIXTURE_RETIRED_API_KEY"


def _fleet(tmp_path, local_copies: bool) -> Fleet:
    f = Fleet(tmp_path)
    f.central({"FIXTURE_KEPT_API_KEY": "fixture-kept"})          # RETIRED no longer central
    for h in f.hosts:
        f.write_store(h, "Data/.datacore/env/.env",
                      {"FIXTURE_KEPT_API_KEY": "fixture-kept", RETIRED: "fixture-old"})
        if local_copies:
            f.write_store(h, ".config/cos.env", {RETIRED: "fixture-old"})
            f.write_store(h, ".datacore/datacore.env", {RETIRED: "fixture-old"})
    return f


def _holders(f: Fleet) -> list[str]:
    return [f"{h}:{rel}" for h in f.hosts for rel, vals in f.stores(h).items() if RETIRED in vals]


def test_retired_key_leaves_the_assembled_env(tmp_path):
    f = _fleet(tmp_path, local_copies=False)
    out = f.distribute()
    assert out.returncode == 0, out.stdout + out.stderr
    assert _holders(f) == [], f"still held after the one step: {_holders(f)}"


def test_retired_key_leaves_every_store_on_every_host(tmp_path):
    f = _fleet(tmp_path, local_copies=True)
    out = f.distribute()
    assert _holders(f) == [], (
        f"retired credential still on the machines after the one step: {_holders(f)}\n"
        + out.stdout[-800:])


def test_running_consumers_are_told_to_reload(tmp_path):
    f = _fleet(tmp_path, local_copies=False)
    f.distribute()
    log = f.ssh_log()
    assert re.search(r"systemctl\s+(?:--user\s+)?(?:restart|reload|try-restart|try-reload-or-restart)"
                     r"|kill\s+-(?:HUP|1)\b", log), (
        "the retirement reached the files but no running process was restarted or "
        "reloaded, so a daemon started earlier still holds the value.\nremote commands:\n"
        + log[-1200:])
