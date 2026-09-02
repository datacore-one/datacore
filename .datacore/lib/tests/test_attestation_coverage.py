"""The attestation report must measure honestly, in both directions.

It would be easy to write a checker that always says "not active" and looks
vigilant. These tests pin both readings, because a monitor that cannot report
success is as useless as one that cannot report failure — and the whole reason
this exists is that `verify_chain` could only report success.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "ledger" / "attestation_coverage.py"


def _load(root: pathlib.Path):
    import os
    os.environ["DATACORE_ROOT"] = str(root)
    spec = importlib.util.spec_from_file_location("attcov", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".datacore" / "events").mkdir(parents=True)
    (tmp_path / ".datacore" / "keys").mkdir(parents=True)
    return tmp_path


def _events(root, *sigs):
    p = root / ".datacore" / "events" / "actor.jsonl"
    p.write_text("\n".join(
        json.dumps({"seq": i, "actor": "a", "sig": s}) for i, s in enumerate(sigs)))


def test_absent_registry_and_unsigned_events_report_inactive(root):
    _events(root, "", "", "")
    m = _load(root).measure()
    assert m["registry_exists"] is False
    assert m["signed"] == 0 and m["unsigned"] == 3
    assert m["coverage_pct"] == 0.0
    assert m["attestation_active"] is False


def test_signed_events_with_a_populated_registry_report_active(root):
    (root / ".datacore" / "keys" / "registry.yaml").write_text(
        "actors:\n  mac:\n    pubkey: abc\n")
    _events(root, "sig1", "sig2")
    m = _load(root).measure()
    assert m["known_actors"] == 1
    assert m["signed"] == 2 and m["unsigned"] == 0
    assert m["coverage_pct"] == 100.0
    assert m["attestation_active"] is True


def test_partial_coverage_is_reported_as_a_percentage_not_a_boolean(root):
    """A half-signed ledger is a real state — a rollout in progress, or signing
    that stopped. Collapsing it to active/inactive would hide exactly the
    transition worth watching."""
    (root / ".datacore" / "keys" / "registry.yaml").write_text(
        "actors:\n  mac:\n    pubkey: abc\n")
    _events(root, "sig1", "", "sig2", "")
    m = _load(root).measure()
    assert m["coverage_pct"] == 50.0


def test_a_registry_with_no_actors_is_not_active(root):
    """An empty registry loads as {"actors": {}} and never raises — which is
    how an absent one becomes indistinguishable from a working one."""
    (root / ".datacore" / "keys" / "registry.yaml").write_text("actors: {}\n")
    _events(root, "", "")
    assert _load(root).measure()["attestation_active"] is False


def test_malformed_lines_are_counted_not_silently_dropped(root):
    p = root / ".datacore" / "events" / "actor.jsonl"
    p.write_text('{"seq":0,"sig":""}\nnot json at all\n{"seq":1,"sig":"x"}\n')
    m = _load(root).measure()
    assert m["malformed_lines"] == 1
    assert m["events"] == 2
