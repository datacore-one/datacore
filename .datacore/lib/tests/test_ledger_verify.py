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
"""Tamper-evidence: what the chain proves, and what it cannot.

Measured 2026-08-15 by direct probe. Editing a payload is caught even when the
event's own hash is recomputed, because each event commits to its predecessor.
But TRUNCATION removes hashes rather than altering them, leaving a shorter,
internally perfect chain that verified clean while the fold silently saw less
history. A torn write, a full disk or an interrupted sync all produce exactly
that shape.
"""
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from ledger.log import EventLog  # noqa: E402
from ledger.verify import check_not_rewound  # noqa: E402


def _space(tmp_path, n=5):
    (tmp_path / ".datacore" / "events").mkdir(parents=True)
    log = EventLog(tmp_path, "winston")
    for i in range(n):
        log.append("item.create", {"id": f"x{i}", "title": f"t{i}",
                                   "check": "true", "effects": []})
    return tmp_path / ".datacore" / "events" / "winston.jsonl"


def test_intact_log_is_not_flagged(tmp_path):
    assert check_not_rewound(_space(tmp_path)) == []


def test_truncated_tail_is_detected(tmp_path):
    """The case the hash chain structurally cannot catch."""
    f = _space(tmp_path)
    f.write_text("\n".join(f.read_text().strip().split("\n")[:-2]) + "\n")
    errors = check_not_rewound(f)
    assert errors, "truncation must be caught by the watermark witness"
    assert "TRUNCATED" in errors[0] and "seq 4" in errors[0]


def test_no_watermark_is_silent_not_an_error(tmp_path):
    """A log this machine never wrote has no local witness.

    Absence of evidence must not be reported as evidence of tampering, or every
    freshly cloned foreign actor log would read as compromised.
    """
    f = _space(tmp_path)
    (tmp_path / ".datacore" / "state" / "seq-hwm" / "winston.seq").unlink()
    assert check_not_rewound(f) == []


def test_verify_cli_exits_nonzero_on_truncation(tmp_path):
    f = _space(tmp_path)
    f.write_text("\n".join(f.read_text().strip().split("\n")[:-2]) + "\n")
    r = subprocess.run([sys.executable, str(LIB / "ledger_cli.py"), "verify",
                        "--space", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode != 0 and "TRUNCATED" in r.stderr


class TestClosureKindIsDeclared:
    """Emitters state why an item closed; readers stop guessing at prose.

    Probed 2026-08-15: the string-matching fallback classified "task withdrawn
    by requester", "rolled back after review" and "not doing this" all as done,
    and an empty reason too. Every miss fell toward `done` -- the direction that
    inflates the weekly report, which is the failure this classifier exists to
    prevent.
    """

    def _item(self, reason=None, kind=None):
        class I:
            status = "dismissed"
            closed_reason = reason
            closed_kind = kind
        return I()

    def test_declared_kind_beats_the_prose(self):
        from ledger.fold import closure_kind
        # Wording the fallback gets WRONG; the declared field must win.
        i = self._item("task withdrawn by requester", "dropped")
        assert closure_kind(i) == "dropped"

    def test_declared_housekeeping_is_excluded_from_done(self):
        from ledger.fold import closure_kind, was_finished
        i = self._item("tidied up", "housekeeping")
        assert closure_kind(i) == "housekeeping" and not was_finished(i)

    def test_fallback_still_reads_legacy_events(self):
        """Events written before `kind` existed must still classify."""
        from ledger.fold import closure_kind
        assert closure_kind(self._item("gave up after 3 failed attempts")) == "dropped"
        assert closure_kind(self._item("duplicate: tracked under another id")) == "housekeeping"
        assert closure_kind(self._item("closed as DONE in next_actions.org")) == "done"
