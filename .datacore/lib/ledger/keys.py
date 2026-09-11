"""Agent keys: Ed25519 keypairs, sign/verify, public registry.

Each actor (agent, host, human) gets an Ed25519 keypair. The private key is
stored raw (hex-encoded) in a per-actor file outside the repo (mode 0600).
The corresponding verify (public) key is upserted into a tracked YAML
registry, keyed by actor name, so any party can verify signatures without
holding secrets.

    keys_dir       -- private keys live here; default ~/.datacore/keys
                      (OUTSIDE the repo -- never commit private keys)
    registry_path  -- public registry YAML; default
                      <DATACORE_ROOT>/.datacore/keys/registry.yaml (tracked)
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from file_utils import atomic_write_text, atomic_write_yaml, file_lock
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))

DEFAULT_KEYS_DIR = Path.home() / ".datacore" / "keys"
DEFAULT_REGISTRY_PATH = DATACORE_ROOT / ".datacore" / "keys" / "registry.yaml"


def validate_actor_name(actor: str) -> None:
    """Key and log identifiers share one filename-safe namespace."""
    if not isinstance(actor, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", actor):
        raise ValueError(f"invalid actor name {actor!r}: expected lowercase letters, digits, '-' or '_'")


def _key_path(actor: str, keys_dir: Path | None) -> Path:
    validate_actor_name(actor)
    return (keys_dir or DEFAULT_KEYS_DIR) / f"{actor}.key"


def _lock_path(actor: str, keys_dir: Path) -> Path:
    validate_actor_name(actor)
    return keys_dir / f".{actor}.lock"


def _load_registry(registry_path: Path, *, strict: bool = False) -> dict:
    """Verification fails closed; mutation refuses to replace invalid data."""
    try:
        data = yaml.safe_load(registry_path.read_text())
    except FileNotFoundError:
        return {"actors": {}}
    except (OSError, yaml.YAMLError):
        if strict:
            raise
        return {"actors": {}}
    if not isinstance(data, dict) or not isinstance(data.get("actors"), dict):
        if strict:
            raise ValueError(f"invalid signing registry at {registry_path}; preserving existing data")
        return {"actors": {}}
    return data


def _save_registry(registry_path: Path, data: dict) -> None:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_yaml(registry_path, data)


def ensure_keypair(
    actor: str,
    keys_dir: Path | None = None,
    registry_path: Path | None = None,
) -> str:
    """Ensure `actor` has a private key + registry entry; return verify-key hex.

    Idempotent: if the private key already exists, it is reused (not
    regenerated) and the registry entry is upserted to match it.

    Concurrency-safe: two processes cold-starting the same brand-new actor
    at once could otherwise both see no key file, each generate a different
    keypair, and race the key file + registry -- leaving one of them signing
    with a private key that no longer matches what's registered. An
    exclusive `fcntl.flock` on a per-actor lock file (`<keys_dir>/.<actor>.lock`)
    serializes the whole check-generate-write-key-write-registry sequence,
    and the key-file existence check is re-done *after* acquiring the lock
    (double-checked) so the loser of the race loads the winner's key instead
    of overwriting it.
    """
    keys_dir = keys_dir or DEFAULT_KEYS_DIR
    registry_path = registry_path or DEFAULT_REGISTRY_PATH
    key_path = _key_path(actor, keys_dir)
    # Lock the key AND the shared registry, in a consistent order. Per-actor
    # locks alone lose entries when different actors start simultaneously.
    with file_lock(key_path), file_lock(registry_path):
        registry = _load_registry(registry_path, strict=True)
        exists = key_path.exists()
        if exists:
            private_key = Ed25519PrivateKey.from_private_bytes(
                bytes.fromhex(key_path.read_text().strip())
            )
        else:
            private_key = Ed25519PrivateKey.generate()
        verify_key_hex = private_key.public_key().public_bytes_raw().hex()
        registered = registry["actors"].get(actor)
        if registered is not None and registered != verify_key_hex:
            raise ValueError(f"signing key for {actor!r} differs from its registered identity; restore the key or rotate explicitly")
        if not exists:
            atomic_write_text(key_path, private_key.private_bytes_raw().hex())
        registry["actors"][actor] = verify_key_hex
        _save_registry(registry_path, registry)
        return verify_key_hex


def sign(actor: str, data: bytes, keys_dir: Path | None = None) -> str:
    """Sign `data` with `actor`'s private key; return signature hex.

    Raises FileNotFoundError if the actor has no private key yet
    (call ensure_keypair first).
    """
    key_path = _key_path(actor, keys_dir)
    if not key_path.exists():
        raise FileNotFoundError(
            f"no private key for actor {actor!r} at {key_path} "
            "(call ensure_keypair first)"
        )
    private_key = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(key_path.read_text().strip())
    )
    return private_key.sign(data).hex()


def known_verify_key(actor: str, registry_path: Path | None = None) -> bool:
    """Do we hold ANY verify key for this writer (local registry or principals.yaml)?"""
    registry = _load_registry(registry_path or DEFAULT_REGISTRY_PATH)
    return bool(registry["actors"].get(actor) or principals_verify_key(actor))


def principals_verify_key(actor: str) -> str | None:
    """The writer's public key as distributed in registry/principals.yaml
    (`verify_keys`), for hosts that hold no local registry entry for it."""
    p = DATACORE_ROOT / ".datacore" / "registry" / "principals.yaml"
    try:
        import yaml
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        v = (d.get("verify_keys") or {}).get(actor)
        return str(v) if v else None
    except Exception:  # noqa: BLE001
        return None


def verify(
    actor: str,
    data: bytes,
    sig_hex: str,
    registry_path: Path | None = None,
) -> bool:
    """Verify `sig_hex` over `data` against `actor`'s registered verify key.

    Returns False (never raises) for unknown actors, malformed hex, or a
    signature that doesn't match.
    """
    registry_path = registry_path or DEFAULT_REGISTRY_PATH
    registry = _load_registry(registry_path)
    # The local registry knows the writers that signed on THIS host; every
    # other writer's key is distributed through registry/principals.yaml.
    verify_key_hex = registry["actors"].get(actor) or principals_verify_key(actor)
    if not verify_key_hex:
        return False

    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(verify_key_hex))
        public_key.verify(bytes.fromhex(sig_hex), data)
        return True
    except (TypeError, ValueError, InvalidSignature):
        return False
