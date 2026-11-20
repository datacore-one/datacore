"""The simulated workstation signs with a key the sandbox registry knows.

Signing has been always on since 2026-10-31. The simulator gave every agent
machine a fresh key and put its public half in the sandbox copy of
principals.yaml, but skipped the workstation: its copy kept the REAL
workstation's public key, while the simulated one signed with a key it made
itself. Week run of 2026-11-20: all 280 signature failures were the
workstation's events, on every machine, every night, and nothing else's.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import fleet_week_sim as sim  # noqa: E402

REAL = "ab" * 32


def _fleet(tmp_path: Path):
    seed = tmp_path / "seed"
    (seed / "config").mkdir(parents=True)
    (seed / "config" / "principals.yaml").write_text(
        yaml.safe_dump({"verify_keys": {"mac": REAL, "winston": REAL, "someone-else": REAL}}))
    machines = {
        "mac": sim.Machine("mac", "workstation", None, "mac", "mac", tmp_path / "m" / "mac"),
        "box": sim.Machine("box", "server", "winston", "winston", "box", tmp_path / "m" / "box"),
    }
    return SimpleNamespace(o=SimpleNamespace(seed=seed), machines=machines, root=tmp_path / "root")


def test_every_simulated_machine_signs_with_a_key_the_sandbox_registry_holds(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    fleet = _fleet(tmp_path)
    sim.Fleet._signing_keys(fleet)
    registered = yaml.safe_load(fleet.principals_doc.read_text())["verify_keys"]
    for m in fleet.machines.values():
        key_file = m.home / ".datacore" / "keys" / f"{m.actor}.key"
        assert key_file.is_file(), f"{m.name} (a {m.kind}) has no signing key in the sandbox"
        public = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key_file.read_text())).public_key()
        assert registered[m.actor] == public.public_bytes_raw().hex(), (
            f"{m.name}'s events would fail every signature check: the sandbox registry holds another key")
        assert registered[m.actor] != REAL
    assert registered["someone-else"] == REAL      # writers the sandbox does not simulate are left alone
