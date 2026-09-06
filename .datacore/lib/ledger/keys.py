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

import fcntl
import os
from pathlib import Path

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))

DEFAULT_KEYS_DIR = Path.home() / ".datacore" / "keys"
DEFAULT_REGISTRY_PATH = DATACORE_ROOT / ".datacore" / "keys" / "registry.yaml"


def _key_path(actor: str, keys_dir: Path | None) -> Path:
    return (keys_dir or DEFAULT_KEYS_DIR) / f"{actor}.key"


def _lock_path(actor: str, keys_dir: Path) -> Path:
    return keys_dir / f".{actor}.lock"


def _load_registry(registry_path: Path) -> dict:
    """Load the registry YAML, tolerating structural corruption.

    Guarantees a return value of the shape {"actors": {...}} no matter what
    is on disk: missing file, unparseable YAML (partial write, merge-conflict
    markers), a non-mapping top-level document (e.g. a YAML list), or a
    present-but-null/non-mapping `actors` key all fall back to an empty
    actors mapping rather than raising. Callers (ensure_keypair, verify) rely
    on this to never propagate a malformed on-disk registry as an exception.
    """
    if not registry_path.exists():
        return {"actors": {}}
    try:
        data = yaml.safe_load(registry_path.read_text())
    except yaml.YAMLError:
        return {"actors": {}}
    if not isinstance(data, dict):
        return {"actors": {}}
    if not isinstance(data.get("actors"), dict):
        data["actors"] = {}
    return data


def _save_registry(registry_path: Path, data: dict) -> None:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(yaml.safe_dump(data, sort_keys=True))


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
    keys_dir.mkdir(parents=True, exist_ok=True)
    key_path = _key_path(actor, keys_dir)
    lock_path = _lock_path(actor, keys_dir)

    with open(lock_path, "a+b") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            # Re-check under the lock: another process may have just created
            # (or upserted) this actor's key while we were waiting for it.
            if key_path.exists():
                private_key = Ed25519PrivateKey.from_private_bytes(
                    bytes.fromhex(key_path.read_text().strip())
                )
            else:
                private_key = Ed25519PrivateKey.generate()
                key_path.write_text(private_key.private_bytes_raw().hex())
                os.chmod(key_path, 0o600)

            verify_key_hex = private_key.public_key().public_bytes_raw().hex()

            registry = _load_registry(registry_path)
            registry["actors"][actor] = verify_key_hex
            _save_registry(registry_path, registry)

            return verify_key_hex
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


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
    except (ValueError, InvalidSignature):
        return False
