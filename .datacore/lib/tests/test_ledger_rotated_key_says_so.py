"""A rotated signing key on one host says exactly that, and what to do (fleet sim 2026-10-03, break 12).

Fault F26 regenerated the always-on host's ledger key (a rebuilt host) without
re-registering it. The host correctly refused to write, but every ingest said only

    ValueError: signing key for 'winston' differs from its registered identity;
    restore the key or rotate explicitly

which names neither the host's key file, nor which key is registered where, nor that
nothing was written and other writers are unaffected, and "rotate explicitly" names
no command. And a reader verifying such an event said "unknown actor or invalid
signature", which cannot tell a forged event from a rebuilt host.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

from ledger.keys import KeyMismatch, ensure_keypair  # noqa: E402
from ledger.log import EventLog  # noqa: E402
from ledger.verify import verify_chain  # noqa: E402


def _rotate(tmp_path) -> tuple[Path, Path, str]:
    keys, reg = tmp_path / "keys", tmp_path / "registry.yaml"
    old = ensure_keypair("winston", keys_dir=keys, registry_path=reg)
    (keys / "winston.key").unlink()          # the host is rebuilt: a new key is generated
    return keys, reg, old


def test_the_host_is_told_its_key_is_not_the_registered_one_and_what_to_do(tmp_path):
    keys, reg, old = _rotate(tmp_path)
    with pytest.raises(KeyMismatch) as caught:
        ensure_keypair("winston", keys_dir=keys, registry_path=reg)
    said = str(caught.value)
    assert isinstance(caught.value, ValueError), "callers that catch ValueError keep working"
    assert "winston" in said
    assert str(keys / "winston.key") in said, "names this host's key file"
    assert str(reg) in said, "names where the registered key is"
    assert old[:12] in said, "names the registered key by fingerprint"
    assert "nothing was written" in said.lower()
    assert "other writers" in said.lower()
    assert "restore" in said.lower() and "register" in said.lower()


def test_the_new_key_file_is_not_left_behind_as_a_silent_identity(tmp_path):
    """The refusal must not leave a brand-new private key that the next start adopts."""
    keys, reg, old = _rotate(tmp_path)
    with pytest.raises(KeyMismatch):
        ensure_keypair("winston", keys_dir=keys, registry_path=reg)
    assert yaml.safe_load(reg.read_text())["actors"]["winston"] == old


def test_a_reader_says_the_event_is_signed_with_an_unregistered_key(tmp_path):
    log = EventLog(tmp_path / "space", "mac", keys_dir=tmp_path / "keys",
                   registry_path=tmp_path / "registry.yaml", sign=True)
    log.append("item.create", {"id": "t1"})
    other = tmp_path / "other.yaml"           # a registry holding a DIFFERENT key for mac
    ensure_keypair("mac", keys_dir=tmp_path / "otherkeys", registry_path=other)
    errors = verify_chain(log.path, registry_path=other)
    sig = [e for e in errors if "signature" in e]
    assert sig, errors
    assert "not the key registered for 'mac'" in sig[0], sig[0]
    assert "rotated or regenerated" in sig[0], sig[0]


def test_an_unknown_writer_is_still_called_unknown(tmp_path):
    log = EventLog(tmp_path / "space", "mac", keys_dir=tmp_path / "keys",
                   registry_path=tmp_path / "registry.yaml", sign=True)
    log.append("item.create", {"id": "t1"})
    empty = tmp_path / "empty.yaml"
    empty.write_text("actors: {}\n")
    errors = verify_chain(log.path, registry_path=empty)
    assert any("no registered key for 'mac'" in e for e in errors), errors
