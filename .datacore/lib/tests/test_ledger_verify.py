"""Tests for ledger.verify - chain + signature verification.

`verify_chain` is a read-only diagnostic over a single writer's event-log
file: it never mutates the file and never raises on malformed data --
every problem becomes a string in the returned list, naming the 1-based
line number and the reason.
"""

import json

from ledger.log import EventLog
from ledger.verify import verify_chain


def _mk_log(tmp_path, actor, sign=True):
    return EventLog(
        tmp_path / "space",
        actor,
        keys_dir=tmp_path / "keys",
        registry_path=tmp_path / "registry.yaml",
        sign=sign,
    )


def _lines(path):
    return path.read_text().splitlines()


def _rewrite(path, lines):
    path.write_text("\n".join(lines) + "\n")


# --- valid chains -------------------------------------------------------


def test_valid_signed_chain_is_clean(tmp_path):
    log = _mk_log(tmp_path, "mac", sign=True)
    log.append("item.create", {"id": "t1"})
    log.append("item.claim", {"id": "t1"})
    log.append("item.complete", {"id": "t1"})

    errors = verify_chain(log.path, registry_path=tmp_path / "registry.yaml")
    assert errors == []


def test_valid_unsigned_chain_clean_nonstrict_but_strict_flags_every_line(tmp_path):
    log = _mk_log(tmp_path, "mac", sign=False)
    log.append("item.create", {"id": "t1"})
    log.append("item.claim", {"id": "t1"})
    log.append("item.complete", {"id": "t1"})

    assert verify_chain(log.path) == []

    strict_errors = verify_chain(log.path, strict=True)
    # every one of the 3 unsigned events must be flagged, not just the first
    assert len(strict_errors) == 3
    for i, err in enumerate(strict_errors, start=1):
        assert f"line {i}" in err
        assert "unsigned" in err


# --- tampering ------------------------------------------------------------


def test_tampered_payload_middle_line_hash_mismatch(tmp_path):
    log = _mk_log(tmp_path, "mac", sign=True)
    log.append("item.create", {"id": "t1"})
    log.append("item.claim", {"id": "t1"})
    log.append("item.complete", {"id": "t1"})

    lines = _lines(log.path)
    middle = json.loads(lines[1])
    middle["payload"] = {"id": "TAMPERED"}
    lines[1] = json.dumps(middle, sort_keys=True, separators=(",", ":"))
    _rewrite(log.path, lines)

    errors = verify_chain(log.path, registry_path=tmp_path / "registry.yaml")
    assert any("line 2" in e and "hash mismatch" in e for e in errors)


def test_swapped_lines_linkage_and_seq_errors(tmp_path):
    log = _mk_log(tmp_path, "mac", sign=True)
    log.append("item.create", {"id": "t1"})
    log.append("item.claim", {"id": "t1"})
    log.append("item.complete", {"id": "t1"})

    lines = _lines(log.path)
    lines[0], lines[1] = lines[1], lines[0]
    _rewrite(log.path, lines)

    errors = verify_chain(log.path, registry_path=tmp_path / "registry.yaml")
    assert any("line 1" in e and "linkage" in e for e in errors)
    assert any("line 1" in e and "seq" in e for e in errors)
    assert any("line 2" in e and "linkage" in e for e in errors)
    assert any("line 2" in e and "seq" in e for e in errors)


def test_corrupted_sig_hex_signature_error(tmp_path):
    log = _mk_log(tmp_path, "mac", sign=True)
    log.append("item.create", {"id": "t1"})

    lines = _lines(log.path)
    event = json.loads(lines[0])
    event["sig"] = "not-valid-hex-zzz"
    lines[0] = json.dumps(event, sort_keys=True, separators=(",", ":"))
    _rewrite(log.path, lines)

    errors = verify_chain(log.path, registry_path=tmp_path / "registry.yaml")
    assert any(
        "line 1" in e and "signature" in e and "unknown actor or invalid signature" in e
        for e in errors
    )


def test_wrong_but_well_formed_sig_hex_signature_error(tmp_path):
    log = _mk_log(tmp_path, "mac", sign=True)
    log.append("item.create", {"id": "t1"})

    lines = _lines(log.path)
    event = json.loads(lines[0])
    event["sig"] = "00" * 64  # syntactically valid hex, wrong signature bytes
    lines[0] = json.dumps(event, sort_keys=True, separators=(",", ":"))
    _rewrite(log.path, lines)

    errors = verify_chain(log.path, registry_path=tmp_path / "registry.yaml")
    assert any(
        "line 1" in e and "signature" in e and "unknown actor or invalid signature" in e
        for e in errors
    )


def test_unknown_actor_signature_error(tmp_path):
    log = _mk_log(tmp_path, "mac", sign=True)
    log.append("item.create", {"id": "t1"})

    # A registry that never registered "mac" -- actor is unknown to it.
    empty_registry = tmp_path / "empty_registry.yaml"
    empty_registry.write_text("actors: {}\n")

    errors = verify_chain(log.path, registry_path=empty_registry)
    assert any(
        "line 1" in e and "signature" in e and "unknown actor or invalid signature" in e
        for e in errors
    )


def test_torn_trailing_line_reported(tmp_path):
    log = _mk_log(tmp_path, "mac", sign=True)
    log.append("item.create", {"id": "t1"})

    with open(log.path, "ab") as f:
        f.write(b'{"seq": 99, "hlc": "garbage-torn-write-no-close')

    errors = verify_chain(log.path, registry_path=tmp_path / "registry.yaml")
    assert any("torn trailing line" in e for e in errors)
    assert any(e.startswith("line 2:") for e in errors)
    # the valid first event must still verify clean -- no spillover errors
    assert not any(e.startswith("line 1:") for e in errors)


def test_malformed_non_final_line_reported_not_raised(tmp_path):
    log = _mk_log(tmp_path, "mac", sign=True)
    log.append("item.create", {"id": "t1"})
    log.append("item.claim", {"id": "t1"})

    lines = _lines(log.path)
    corrupted = lines[0] + "\nnot valid json {{{\n" + lines[1] + "\n"
    log.path.write_text(corrupted)

    # must not raise
    errors = verify_chain(log.path, registry_path=tmp_path / "registry.yaml")
    assert any("line 2" in e and "malformed" in e for e in errors)


# --- unreadable path (never raise, report instead) ---------------------------


def test_nonexistent_path_reported_not_raised(tmp_path):
    missing = tmp_path / "nope" / "does-not-exist.jsonl"

    # must not raise FileNotFoundError
    errors = verify_chain(missing, registry_path=tmp_path / "registry.yaml")
    assert len(errors) == 1
    assert str(missing) in errors[0]


def test_directory_path_reported_not_raised(tmp_path):
    a_directory = tmp_path / "space"
    a_directory.mkdir()

    # must not raise IsADirectoryError
    errors = verify_chain(a_directory, registry_path=tmp_path / "registry.yaml")
    assert len(errors) == 1
    assert str(a_directory) in errors[0]
