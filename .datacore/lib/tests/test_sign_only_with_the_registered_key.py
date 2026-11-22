"""A machine never starts signing with a key the fleet registry does not hold.

Found on the chief of staff's machine, 2026-11-01: a subprocess ran with an
empty scratch key folder, so the local registry said nothing about the writer,
a fresh key was generated, and its events were signed with a key no verifier
accepts. The local registry's silence is no licence: when the fleet registry
(principals.yaml) names a key for the writer, only that key may sign.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from ledger import keys  # noqa: E402


def _dirs(tmp_path):
    return tmp_path / "keys", tmp_path / "registry.json"


def test_a_fresh_key_is_refused_when_the_fleet_registry_names_another(tmp_path, monkeypatch):
    keys_dir, registry = _dirs(tmp_path)
    monkeypatch.setattr(keys, "principals_verify_key", lambda actor: "ab" * 32)
    monkeypatch.setattr(keys, "key_history", lambda actor: [])
    with pytest.raises(keys.KeyMismatch) as exc:
        keys.ensure_keypair("writer-x", keys_dir=keys_dir, registry_path=registry)
    assert "writer-x" in str(exc.value) and "principals.yaml" in str(exc.value)
    # The fresh key stays on disk, unregistered: the owner's rotation reads it
    # from there. It is refused again on every later attempt, never signed with.
    kept = (keys_dir / "writer-x.key").read_text()
    with pytest.raises(keys.KeyMismatch):
        keys.ensure_keypair("writer-x", keys_dir=keys_dir, registry_path=registry)
    assert (keys_dir / "writer-x.key").read_text() == kept, "a second attempt minted another key"
    assert not registry.exists() or "writer-x" not in registry.read_text(), "the refused key was registered"


def test_a_writer_the_fleet_registry_does_not_know_still_gets_a_key(tmp_path, monkeypatch):
    keys_dir, registry = _dirs(tmp_path)
    monkeypatch.setattr(keys, "principals_verify_key", lambda actor: None)
    monkeypatch.setattr(keys, "key_history", lambda actor: [])
    keys.ensure_keypair("writer-y", keys_dir=keys_dir, registry_path=registry)
    assert (keys_dir / "writer-y.key").exists()


def test_the_registered_key_itself_is_accepted(tmp_path, monkeypatch):
    keys_dir, registry = _dirs(tmp_path)
    monkeypatch.setattr(keys, "principals_verify_key", lambda actor: None)
    monkeypatch.setattr(keys, "key_history", lambda actor: [])
    keys.ensure_keypair("writer-z", keys_dir=keys_dir, registry_path=registry)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    pub = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex((keys_dir / "writer-z.key").read_text().strip())).public_key().public_bytes_raw().hex()
    registry.unlink()                       # the local registry goes silent, as in a scratch folder
    monkeypatch.setattr(keys, "principals_verify_key", lambda actor: pub)
    keys.ensure_keypair("writer-z", keys_dir=keys_dir, registry_path=registry)
