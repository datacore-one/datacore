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
import time
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


#: (kind, path) -> ((mtime_ns, size, ino), value). Verification reads the same
#: two small YAML files for every SIGNED event, and parsing them dominated
#: verify: 705 parses, 15 of 15.7 s on 6-meridian's miles.jsonl, 54.6 s for
#: 2-datacore against 3.1 s cached (ledger audit A-core #1, promise LED-6).
#: Keyed on the file's stat signature, never held forever: an operator edits
#: principals.yaml while a long job runs, and a rotated key must be seen.
_FILE_CACHE: dict = {}
#: verify-key hex -> Ed25519PublicKey (or None when the hex is not a key).
_PUBKEYS: dict = {}


def _stat_key(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _cached(kind: str, path: Path, load):
    sig = _stat_key(path)
    hit = _FILE_CACHE.get((kind, str(path)))
    if sig is not None and hit is not None and hit[0] == sig:
        return hit[1]
    value = load()
    if sig is not None:
        _FILE_CACHE[(kind, str(path))] = (sig, value)
    return value


def _public_key(verify_key_hex: str):
    if verify_key_hex not in _PUBKEYS:
        try:
            _PUBKEYS[verify_key_hex] = Ed25519PublicKey.from_public_bytes(bytes.fromhex(verify_key_hex))
        except (TypeError, ValueError):
            _PUBKEYS[verify_key_hex] = None
    return _PUBKEYS[verify_key_hex]


def _registry_actors(registry_path: Path) -> dict:
    """The local registry's actor -> key map, for VERIFICATION only (cached)."""
    return _cached("registry", registry_path, lambda: dict(_load_registry(registry_path)["actors"]))


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


class KeyMismatch(ValueError):
    """This host's private key is not the key registered for its writer."""


def _mismatch(actor: str, key_path: Path, have: str, registry_path: Path, registered: str) -> KeyMismatch:
    """Say exactly what happened and what to do (fleet sim 2026-10-03, break 12).

    It used to say "differs from its registered identity; restore the key or
    rotate explicitly": no key file, no registry, no fingerprints, and no
    command behind "rotate explicitly" (none exists).
    """
    return KeyMismatch(
        f"ledger signing key for {actor!r} on this host ({key_path}, public key {have[:12]}...) is not "
        f"the key registered for {actor!r} (its registered identity is {registered[:12]}... in "
        f"{registry_path}). A regenerated key "
        f"or a rebuilt host. Nothing was written to the ledger from this host; other writers are not "
        f"affected. To fix: restore the original private key to {key_path} from this host's backup. "
        f"If it is lost, the key must be rotated, which is the owner's call and never automatic: on the "
        f"owner's workstation run `python3 .datacore/lib/ledger_keys_collect.py --rotate {actor} "
        f"--hosts <this host's ssh alias> --owner-approves` (it records a key.rotate event and registers "
        f"the new public key {have[:12]}...), publish that space's ledger, then run "
        f"`python3 .datacore/lib/ledger_keys_collect.py --rotate {actor} --owner-approves` on this host "
        f"and on every host that verifies its events. Events signed before the rotation keep verifying "
        f"with the old key.")


def _retired(actor: str, key_path: Path, have: str, current: str, since: str | None) -> KeyMismatch:
    return KeyMismatch(
        f"ledger signing key for {actor!r} on this host ({key_path}, public key {have[:12]}...) was "
        f"retired when the owner approved {current[:12]}... (valid from {since}). An old key restored "
        f"from a backup. Nothing was written to the ledger from this host; other writers are not "
        f"affected. Put the current key back at {key_path}, or have the owner approve a new one with "
        f"`python3 .datacore/lib/ledger_keys_collect.py --rotate {actor} --owner-approves`.")


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
        # An owner-approved rotation (principals.yaml verify_key_history) is the
        # only thing that can make a different key acceptable here; a key that
        # rotation retired is refused even if the local registry still holds it.
        history = key_history(actor)
        current = history[-1][1] if len(history) > 1 else None
        if current and verify_key_hex != current and verify_key_hex in {k for _, k in history}:
            raise _retired(actor, key_path, verify_key_hex, current, _iso(history[-1][0]))
        if registered is not None and registered != verify_key_hex and verify_key_hex != current:
            raise _mismatch(actor, key_path, verify_key_hex, registry_path, str(current or registered))
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
    return bool(_registry_actors(registry_path or DEFAULT_REGISTRY_PATH).get(actor)
                or principals_verify_key(actor) or key_history(actor))


def principals_path() -> Path:
    return DATACORE_ROOT / ".datacore" / "registry" / "principals.yaml"


def _principals() -> dict:
    """principals.yaml's key sections: {"verify_keys": {...}, "verify_key_history": {...}} (cached)."""
    p = principals_path()

    def load() -> dict:
        try:
            d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 -- unreadable means no proven keys
            return {"verify_keys": {}, "verify_key_history": {}}
        vk = d.get("verify_keys") if isinstance(d, dict) else None
        vh = d.get("verify_key_history") if isinstance(d, dict) else None
        return {"verify_keys": {str(k): str(v) for k, v in vk.items() if v} if isinstance(vk, dict) else {},
                "verify_key_history": vh if isinstance(vh, dict) else {}}

    return _cached("principals", p, load)


def principals_verify_key(actor: str) -> str | None:
    """The writer's CURRENT proven public key from registry/principals.yaml (`verify_keys`,
    written by ledger_keys_collect only for keys that verify that writer's real
    signed events, or that the owner approved in a rotation)."""
    return _principals()["verify_keys"].get(actor)


def parse_valid_from(value) -> int | None:
    """A history entry's valid_from as epoch milliseconds; None means "from the start"."""
    if value in (None, "", "null"):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


def key_history(actor: str) -> list[tuple[int | None, str]]:
    """Every key the writer has held, oldest first: [(valid_from_ms | None, verify_key_hex)].

    From principals.yaml `verify_key_history` (owner-approved rotations). A
    writer with no rotation has one entry, its `verify_keys` key, valid from
    the start. A writer principals.yaml does not know has none.
    """
    raw = _principals()["verify_key_history"].get(actor)
    out: list[tuple[int | None, str]] = []
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict) and entry.get("key"):
                try:
                    out.append((parse_valid_from(entry.get("valid_from")), str(entry["key"])))
                except (TypeError, ValueError):
                    continue          # an unreadable entry is no key at all
        out.sort(key=lambda e: -1 if e[0] is None else e[0])
    if not out:
        current = principals_verify_key(actor)
        return [(None, current)] if current else []
    return out


def key_at(history: list[tuple[int | None, str]], at_ms: int | None) -> str | None:
    """The key valid at `at_ms`: the latest entry whose valid_from is at or before it."""
    chosen = None
    for since, key in history:
        if since is None or (at_ms is not None and since <= at_ms):
            chosen = key
    return chosen


def verify(
    actor: str,
    data: bytes,
    sig_hex: str,
    registry_path: Path | None = None,
    at_ms: int | None = None,
) -> bool:
    """Verify `sig_hex` over `data` against `actor`'s registered verify key.

    `at_ms` is when the event says it was written (its hlc). With a rotation
    on record, each key is valid for its own period: the old key until the
    owner-approved new key's valid_from, the new key from then on. Without
    `at_ms` (a caller that has no event time) any key the writer was ever
    approved to hold is accepted.

    Returns False (never raises) for unknown actors, malformed hex, or a
    signature that doesn't match.
    """
    registry_path = registry_path or DEFAULT_REGISTRY_PATH
    # A PROVEN key wins, and is the only key for its actor. principals.yaml
    # `verify_keys` holds keys ledger_keys_collect accepted only because they
    # verify that writer's real signed events. The local registry is not
    # evidence of anything: `ensure_keypair` GENERATES a key for every actor a
    # host ever opened an EventLog for, so nightshift holds its own "winston",
    # "data" and "mac" keys that no such writer signs with.
    #
    # The local entry used to win. On 2026-09-17 that made nightshift report
    # genuine winston and data events as failed signatures, and -- the other
    # half of the same inversion -- it would have ACCEPTED an event signed with
    # nightshift's locally generated "winston" private key, which sits on its
    # disk. The local registry is now a fallback, for actors not yet collected.
    history = key_history(actor)
    if history:
        candidates = [key_at(history, at_ms)] if at_ms is not None else [k for _, k in history]
    else:
        candidates = [_registry_actors(registry_path).get(actor)]
    for verify_key_hex in candidates:
        if not verify_key_hex:
            continue
        public_key = _public_key(str(verify_key_hex))
        if public_key is None:
            continue
        try:
            public_key.verify(bytes.fromhex(sig_hex), data)
            return True
        except (TypeError, ValueError, InvalidSignature):
            continue
    return False


class RotationRefused(ValueError):
    """A key rotation that was not (or could not be) approved; nothing was written."""


def recorded_rotations(space_dir: Path, actor: str) -> list:
    """The `key.rotate` events for `actor` in a space's ledger, oldest first."""
    from .log import read_events
    return [e for e in read_events(Path(space_dir))
            if e.type == "key.rotate" and isinstance(e.payload, dict) and e.payload.get("actor") == actor]


def approve_rotation(
    actor: str,
    new_key: str | None,
    *,
    owner_approves: bool,
    space_dir: Path,
    approver: str,
    keys_dir: Path | None = None,
    registry_path: Path | None = None,
    now_ms: int | None = None,
    reason: str = "",
    sign: bool | None = None,
) -> dict:
    """Register `new_key` as `actor`'s signing key from now on; the old key stays valid for the past.

    Only with the owner's explicit approval (`owner_approves`, the
    `--owner-approves` flag). The approval is a `key.rotate` event in
    `space_dir`, written by `approver`, who must not be the writer being
    rotated. If that space already records a rotation of `actor` to this key
    (the owner approved it on another host), it is adopted as recorded and no
    second event is written: every host then holds the same valid_from.

    Writes principals.yaml: `verify_key_history` gains the new key with its
    valid_from (and the old key, valid from the start, when this is the first
    rotation), and `verify_keys` becomes the new key. Never reads, writes or
    moves a private key.
    """
    if owner_approves is not True:
        raise RotationRefused(
            f"a new signing key for {actor!r} is registered only with the owner's explicit approval "
            f"(--owner-approves); nothing was written")
    validate_actor_name(actor)
    if approver == actor:
        raise RotationRefused(
            f"{actor!r} cannot approve its own new key; the owner approves it from another machine. "
            f"Nothing was written")
    recorded = recorded_rotations(space_dir, actor)
    if new_key is None:
        if not recorded:
            raise RotationRefused(
                f"no key.rotate for {actor!r} is recorded in {space_dir}; pass --new-key or --hosts on the "
                f"owner's workstation first. Nothing was written")
        new_key = str(recorded[-1].payload.get("new_key"))
    new_key = str(new_key).strip().lower()
    if _public_key(new_key) is None:
        raise RotationRefused(f"{new_key[:16]!r}... is not an Ed25519 public key; nothing was written")

    history = key_history(actor)
    existing = next((e for e in reversed(recorded) if e.payload.get("new_key") == new_key), None)
    if existing is not None:
        old_key = str(existing.payload.get("old_key") or "")
        valid_from = str(existing.payload.get("valid_from"))
        event_hash, adopted = existing.hash, True
    else:
        if not history:
            raise RotationRefused(
                f"principals.yaml holds no key for {actor!r}, so there is nothing to rotate from; collect "
                f"its key first (ledger_keys_collect.py --apply). Nothing was written")
        if new_key in {k for _, k in history}:
            raise RotationRefused(f"{new_key[:12]}... is already a registered key for {actor!r}; nothing was written")
        old_key = history[-1][1]
        valid_from = _iso(now_ms if now_ms is not None else int(time.time() * 1000))
        from .log import EventLog
        event = EventLog(Path(space_dir), approver, keys_dir=keys_dir, registry_path=registry_path,
                         sign=sign).append("key.rotate", {
                             "actor": actor, "old_key": old_key, "new_key": new_key,
                             "valid_from": valid_from, "reason": reason})
        event_hash, adopted = event.hash, False

    entries = [{"key": k, "valid_from": _iso(t)} for t, k in history]
    if old_key and old_key not in {e["key"] for e in entries}:
        entries.insert(0, {"key": old_key, "valid_from": None})
    if new_key not in {e["key"] for e in entries}:
        entries.append({"key": new_key, "valid_from": valid_from, "event": event_hash})
    _write_principals_keys(actor, entries, new_key)
    return {"event": event_hash, "old_key": old_key, "new_key": new_key,
            "valid_from": valid_from, "adopted": adopted}


def replace_yaml_block(text: str, name: str, block: list[str]) -> str:
    """Replace the top-level `name:` block (its indented lines) in `text`, or append it.

    Everything else -- comments, other sections, their order -- is kept.
    """
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    start = next((i for i, l in enumerate(lines) if l.startswith(f"{name}:")), None)
    if start is None:
        return "".join(lines + block)
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end].startswith((" ", "\t"))):
        end += 1
    return "".join(lines[:start] + block + lines[end:])


def _write_principals_keys(actor: str, entries: list[dict], current: str) -> None:
    p = principals_path()
    with file_lock(p):
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        doc = yaml.safe_load(text) or {}
        vk = {str(k): str(v) for k, v in (doc.get("verify_keys") or {}).items()}
        vk[actor] = current
        vh = dict(doc.get("verify_key_history") or {})
        vh[actor] = entries
        text = replace_yaml_block(text, "verify_keys", ["verify_keys:\n"] + [f"  {a}: {vk[a]}\n" for a in sorted(vk)])
        block = ["verify_key_history:\n"]
        for a in sorted(vh):
            block.append(f"  {a}:\n")
            for e in vh[a]:
                block.append(f"    - key: {e['key']}\n")
                vf = e.get("valid_from")
                block.append(f"      valid_from: {'null' if vf is None else repr(str(vf))}\n")
                if e.get("event"):
                    block.append(f"      event: {e['event']}\n")
        text = replace_yaml_block(text, "verify_key_history", block)
        atomic_write_text(p, text)
