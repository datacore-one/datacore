"""distribute.sh delivers into an agent pod's volume (2026-10-26).

Data and Winston moved into Kubernetes pods on the box on 2026-10-25. Their
home is a volume, not the box user's home, and the fleet README said it plainly:
"`creds sync --to-k8s` does not exist yet". So a rotated key reached no pod:
Data kept the Datafund X token it carried over, revoked when the owner
regenerated it with write permission.

A manifest names the host path of the agent's Data checkout inside its volume
(`pod_data:`). The assembled .env and the derived index are installed there,
mode 600, owned by `pod_uid` when given, through `sudo -n` when `pod_sudo: true`.
The box user's own ~/Data is not touched for that instance.
"""
from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _creds_fleet_harness import Fleet  # noqa: E402


def _pod_fleet(tmp_path):
    f = Fleet(tmp_path)
    pod_data = tmp_path / "volumes" / "pvc-data-home" / ".openclaw" / "workspace" / "Data"
    pod_data.mkdir(parents=True)
    (f.secrets / "instances" / "hostb.yaml").write_text(
        f"ssh_alias: hostb\npod_data: {pod_data}\nspaces: []\n")
    f.central({"FIXTURE_API_KEY": "fixture-v1"})
    (f.secrets / "credential-index.yaml").write_text(
        "credentials:\n- id: fixture-api-key\n  var_name: FIXTURE_API_KEY\n  scope: global\n")
    return f, pod_data


def test_env_and_index_land_in_the_pod_volume(tmp_path):
    f, pod_data = _pod_fleet(tmp_path)
    out = f.distribute()
    env = pod_data / ".datacore/env/.env"
    assert env.is_file(), out.stdout[-1500:] + out.stderr[-500:]
    assert "FIXTURE_API_KEY=fixture-v1" in env.read_text()
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert (pod_data / ".datacore/secrets/credential-index.yaml").is_file()
    assert "✓ hostb -> hostb (pod)" in out.stdout, out.stdout[-1500:]


def test_the_box_users_own_home_is_not_written_for_a_pod(tmp_path):
    f, _ = _pod_fleet(tmp_path)
    f.distribute()
    assert not f.host_file("hostb", "Data/.datacore/env/.env").exists()
    assert f.host_file("hosta", "Data/.datacore/env/.env").exists()


def test_a_rotated_value_reaches_the_pod(tmp_path):
    f, pod_data = _pod_fleet(tmp_path)
    f.distribute()
    f.central({"FIXTURE_API_KEY": "fixture-v2"})
    f.distribute()
    assert "FIXTURE_API_KEY=fixture-v2" in (pod_data / ".datacore/env/.env").read_text()


def test_a_pod_is_not_reported_as_unauditable(tmp_path):
    f, _ = _pod_fleet(tmp_path)
    out = f.distribute()
    assert "hostb: could not audit" not in out.stdout, out.stdout[-1500:]
