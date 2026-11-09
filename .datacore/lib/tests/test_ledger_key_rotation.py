"""Signing-key rotation: old and new key both valid (owner decision 2026-10-03).

Fleet sim 2026-10-03, break 12: a rebuilt host regenerates its ledger key. The
host then correctly refuses to write, and since 411aaab it says so with both
fingerprints. But there was no way back: the key-collection tool accepts a key
only if it verifies ALL of the writer's existing signatures, and a new key
verifies none of the old ones. So a rebuilt host could never be re-registered.

The owner's rule:
  * a writer may hold several registered public keys, each valid from a time;
  * a new key is added only when the owner says so explicitly (a flag the owner
    passes), never automatically;
  * the rotation is itself a ledger event (`key.rotate`);
  * events signed with the old key before the rotation stay verifiable;
  * events after the rotation verify with the new key;
  * a key that was never registered still fails, naming both fingerprints.

Temporary keys only. Nothing here reads or writes a real key file.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from ledger import keys  # noqa: E402
from ledger.events import EVENT_TYPES, Event, body_dict, canonical_bytes, compute_hash, to_line  # noqa: E402
from ledger.keys import KeyMismatch, ensure_keypair  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402
from ledger.verify import verify_chain  # noqa: E402

_spec = importlib.util.spec_from_file_location("kc_rot", LIB / "ledger_keys_collect.py")
kc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kc)


def _pub(priv: Ed25519PrivateKey) -> str:
    return priv.public_key().public_bytes_raw().hex()


class Fleet:
    """A temporary install: one rebuilt writer ('winston') and the owner's workstation ('mac')."""

    def __init__(self, root: Path, monkeypatch):
        self.root = root
        monkeypatch.setattr(keys, "DATACORE_ROOT", root)
        self.principals = root / ".datacore" / "registry" / "principals.yaml"
        self.principals.parent.mkdir(parents=True)
        self.space = root / "2-datacore"
        (self.space / ".datacore" / "events").mkdir(parents=True)
        self.keys = root / "hostkeys"            # winston's ~/.datacore/keys
        self.registry = root / "registry.yaml"   # winston's local registry
        self.old = ensure_keypair("winston", keys_dir=self.keys, registry_path=self.registry)
        self.principals.write_text(
            "version: 1\n# a comment the writer must keep\n"
            f"verify_keys:\n  winston: {self.old}\nother_section:\n  keep: me\n")

    def log(self) -> EventLog:
        return EventLog(self.space, "winston", keys_dir=self.keys, registry_path=self.registry, sign=True)

    def rebuild(self) -> str:
        """The host is rebuilt: its private key is gone and a new one exists."""
        new = Ed25519PrivateKey.generate()
        (self.keys / "winston.key").write_text(new.private_bytes_raw().hex())
        return _pub(new)

    def approve(self, new_key: str, **kw):
        kw.setdefault("owner_approves", True)
        kw.setdefault("approver", "mac")
        return keys.approve_rotation("winston", new_key, space_dir=self.space,
                                     keys_dir=self.root / "mackeys",
                                     registry_path=self.root / "mac-registry.yaml", **kw)

    def errors(self):
        return verify_chain(self.space / ".datacore" / "events" / "winston.jsonl", registry_path=self.registry)


def _forge_append(path: Path, priv: Ed25519PrivateKey, hlc: str, payload: dict) -> None:
    """Append one correctly chained event signed with `priv` (a key the writer no longer holds)."""
    last = json.loads(path.read_text().splitlines()[-1])
    seq, prev = last["seq"] + 1, last["hash"]
    body = body_dict(seq, hlc, "winston", "item.create", payload, prev)
    ev = Event(seq=seq, hlc=hlc, actor="winston", type="item.create", payload=payload, prev=prev,
               hash=compute_hash(body), sig=priv.sign(canonical_bytes(body)).hex())
    with path.open("a") as fh:
        fh.write(to_line(ev) + "\n")


def test_key_rotate_is_a_ledger_event_type():
    assert "key.rotate" in EVENT_TYPES


def test_without_the_owners_flag_nothing_is_registered_or_written(tmp_path, monkeypatch):
    f = Fleet(tmp_path, monkeypatch)
    f.log().append("item.create", {"id": "before"})
    new = f.rebuild()
    before = f.principals.read_text()
    with pytest.raises(keys.RotationRefused) as refused:
        f.approve(new, owner_approves=False)
    assert "owner" in str(refused.value).lower()
    assert f.principals.read_text() == before
    assert not (f.space / ".datacore" / "events" / "mac.jsonl").exists()
    # And never automatically: the rebuilt host still refuses to write.
    with pytest.raises(KeyMismatch):
        f.log().append("item.create", {"id": "after"})


def test_a_writer_cannot_approve_its_own_new_key(tmp_path, monkeypatch):
    f = Fleet(tmp_path, monkeypatch)
    new = f.rebuild()
    with pytest.raises(keys.RotationRefused):
        f.approve(new, approver="winston")


def test_rotation_records_a_ledger_event_and_both_keys_with_valid_from(tmp_path, monkeypatch):
    f = Fleet(tmp_path, monkeypatch)
    f.log().append("item.create", {"id": "before"})
    new = f.rebuild()
    out = f.approve(new, reason="host rebuilt")

    rot = [e for e in read_events(f.space) if e.type == "key.rotate"]
    assert len(rot) == 1 and rot[0].actor == "mac"
    p = rot[0].payload
    assert (p["actor"], p["old_key"], p["new_key"]) == ("winston", f.old, new)
    assert p["valid_from"] == out["valid_from"] and p["reason"] == "host rebuilt"

    doc = yaml.safe_load(f.principals.read_text())
    assert doc["verify_keys"]["winston"] == new, "the current key is the new one"
    hist = doc["verify_key_history"]["winston"]
    assert [h["key"] for h in hist] == [f.old, new]
    assert hist[0]["valid_from"] is None and hist[1]["valid_from"] == out["valid_from"]
    assert hist[1]["event"] == rot[0].hash
    assert doc["other_section"] == {"keep": "me"}, "other sections survive"
    assert "# a comment the writer must keep" in f.principals.read_text()
    assert [k for _, k in keys.key_history("winston")] == [f.old, new]


def test_old_events_verify_with_the_old_key_and_new_events_with_the_new(tmp_path, monkeypatch):
    f = Fleet(tmp_path, monkeypatch)
    f.log().append("item.create", {"id": "a"})
    f.log().append("item.create", {"id": "b"})
    new = f.rebuild()
    time.sleep(0.01)
    f.approve(new)
    time.sleep(0.01)
    # The rebuilt host is accepted now: the owner approved exactly the key it holds.
    f.log().append("item.create", {"id": "c"})
    f.log().append("item.create", {"id": "d"})
    assert f.errors() == []
    assert yaml.safe_load(f.registry.read_text())["actors"]["winston"] == new


def test_the_old_key_does_not_sign_after_the_rotation(tmp_path, monkeypatch):
    """Both keys are valid, each for its own period: a lost old key cannot write new history."""
    f = Fleet(tmp_path, monkeypatch)
    old_priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex((f.keys / "winston.key").read_text()))
    f.log().append("item.create", {"id": "a"})
    new = f.rebuild()
    out = f.approve(new)
    after = keys.parse_valid_from(out["valid_from"]) + 5000
    _forge_append(f.space / ".datacore" / "events" / "winston.jsonl", old_priv, f"{after}.0000",
                  {"id": "late"})
    errs = f.errors()
    assert len(errs) == 1 and "line 2" in errs[0], errs
    assert f.old[:12] in errs[0] and new[:12] in errs[0], "names the retired and the current key"


def test_a_key_that_was_never_registered_still_fails_naming_both_fingerprints(tmp_path, monkeypatch):
    f = Fleet(tmp_path, monkeypatch)
    f.log().append("item.create", {"id": "a"})
    new = f.rebuild()
    f.approve(new)
    stranger = f.rebuild()                    # rebuilt again, and this key was never approved
    with pytest.raises(KeyMismatch) as caught:
        f.log().append("item.create", {"id": "b"})
    said = str(caught.value)
    assert stranger[:12] in said and new[:12] in said, said
    assert "--rotate winston" in said and "--owner-approves" in said, "names the owner's command"
    # A reader of an event signed with that key: fails, and names the registered key.
    stranger_priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex((f.keys / "winston.key").read_text()))
    _forge_append(f.space / ".datacore" / "events" / "winston.jsonl", stranger_priv,
                  f"{int(time.time() * 1000)}.0000", {"id": "x"})
    errs = f.errors()
    assert len(errs) == 1 and "not the key registered for 'winston'" in errs[0], errs
    assert new[:12] in errs[0]


def test_a_retired_key_restored_from_backup_is_refused_at_write_time(tmp_path, monkeypatch):
    f = Fleet(tmp_path, monkeypatch)
    old_hex = (f.keys / "winston.key").read_text()
    new = f.rebuild()
    f.approve(new)
    f.log().append("item.create", {"id": "c"})
    (f.keys / "winston.key").write_text(old_hex)          # someone restores the old backup
    with pytest.raises(KeyMismatch) as caught:
        f.log().append("item.create", {"id": "d"})
    assert "retired" in str(caught.value)


def test_a_second_host_adopts_the_recorded_rotation_without_a_second_event(tmp_path, monkeypatch):
    """Every other host runs the same owner command; it reads the recorded event, it does not mint one."""
    f = Fleet(tmp_path, monkeypatch)
    new = f.rebuild()
    first = f.approve(new)
    f.principals.write_text(f"verify_keys:\n  winston: {f.old}\n")   # another host's principals
    again = f.approve(None, approver="nightshift")                   # key taken from the event
    assert again["valid_from"] == first["valid_from"] and again["adopted"] is True
    assert len([e for e in read_events(f.space) if e.type == "key.rotate"]) == 1
    assert [k for _, k in keys.key_history("winston")] == [f.old, new]


def test_collect_cli_requires_the_owner_flag(tmp_path, monkeypatch, capsys):
    f = Fleet(tmp_path, monkeypatch)
    new = f.rebuild()
    monkeypatch.setattr(sys, "argv", ["x", "--rotate", "winston", "--new-key", new,
                                      "--space", str(f.space), "--actor", "mac"])
    assert kc.main() == 2
    assert "--owner-approves" in capsys.readouterr().err
    assert keys.key_history("winston") == [(None, f.old)]
    monkeypatch.setattr(sys, "argv", ["x", "--rotate", "winston", "--new-key", new, "--owner-approves",
                                      "--space", str(f.space), "--actor", "mac"])
    monkeypatch.setattr(kc, "_rotation_keys", lambda: (tmp_path / "mackeys", tmp_path / "mac-registry.yaml"))
    assert kc.main() == 0
    out = capsys.readouterr().out
    assert f.old[:12] in out and new[:12] in out
    assert [k for _, k in keys.key_history("winston")] == [f.old, new]


def test_collect_scores_a_rotated_writer_by_period_not_by_one_key(tmp_path, monkeypatch):
    f = Fleet(tmp_path, monkeypatch)
    f.log().append("item.create", {"id": "a"})
    new = f.rebuild()
    time.sleep(0.01)
    f.approve(new)
    time.sleep(0.01)
    f.log().append("item.create", {"id": "b"})
    evidence = kc.signatures(tmp_path)["winston"]
    assert kc.proves(new, evidence) == (1, 1), "one key alone explains only half"
    assert kc.proves_history(keys.key_history("winston"), evidence) == (2, 0)
