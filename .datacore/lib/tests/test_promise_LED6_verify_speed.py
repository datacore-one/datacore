"""LED-6: Checking a space's full history finishes within ten seconds.

Seeded failure: `keys.verify` re-reads and YAML-parses principals.yaml (and the
local key registry) once per SIGNED event, so verify cost is (signed events) x
(YAML parse). Measured on 2-datacore: 54.6 s, against 3.1 s with the lookups
cached (audit A-core #1). A 300-event signed log therefore triggers ~600 YAML
parses today; the promise needs the key registries parsed at most once per
verify call.

The timing smoke over the real 2-datacore space is printed, not asserted: the
deterministic bound is the parse count.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import yaml

from ledger.events import Event, body_dict, canonical_bytes, compute_hash, to_line
from ledger.keys import ensure_keypair, sign
from ledger.verify import verify_chain

N_SIGNED = 300


def _signed_log(tmp_path: Path) -> tuple[Path, Path]:
    keys_dir = tmp_path / "keys"
    registry = tmp_path / "registry.yaml"
    actor = "evalspeedwriter"
    ensure_keypair(actor, keys_dir=keys_dir, registry_path=registry)
    path = tmp_path / "space" / ".datacore" / "events" / f"{actor}.jsonl"
    path.parent.mkdir(parents=True)
    prev, lines = "GENESIS", []
    for seq in range(N_SIGNED):
        body = body_dict(seq, f"{1700000000000 + seq}.0000.{actor}", actor, "metric.attest",
                         {"metric": "m", "value": seq}, prev)
        h = compute_hash(body)
        lines.append(to_line(Event(**body, hash=h,
                                   sig=sign(actor, canonical_bytes(body), keys_dir=keys_dir))))
        prev = h
    path.write_text("\n".join(lines) + "\n")
    return path, registry


def test_key_registries_are_parsed_at_most_once_per_verify_call(tmp_path, monkeypatch):
    path, registry = _signed_log(tmp_path)
    calls = {"n": 0}
    real_safe_load, real_load = yaml.safe_load, yaml.load

    def counting_safe_load(*a, **k):
        calls["n"] += 1
        return real_safe_load(*a, **k)

    def counting_load(*a, **k):
        calls["n"] += 1
        return real_load(*a, **k)

    monkeypatch.setattr(yaml, "safe_load", counting_safe_load)
    monkeypatch.setattr(yaml, "load", counting_load)
    errors = verify_chain(path, registry_path=registry)
    assert errors == []
    # principals.yaml + the local registry, each at most once -- never per event.
    assert calls["n"] <= 2, f"{calls['n']} YAML parses for {N_SIGNED} signed events"


def test_a_second_verify_does_not_reparse_unchanged_registries(tmp_path, monkeypatch):
    path, registry = _signed_log(tmp_path)
    assert verify_chain(path, registry_path=registry) == []
    calls = {"n": 0}
    real = yaml.safe_load

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(yaml, "safe_load", counting)
    assert verify_chain(path, registry_path=registry) == []
    assert calls["n"] == 0


def test_a_changed_registry_is_seen(tmp_path):
    """The cache must never outlive an edit: a replaced key stops verifying."""
    path, registry = _signed_log(tmp_path)
    assert verify_chain(path, registry_path=registry) == []
    data = yaml.safe_load(registry.read_text())
    data["actors"]["evalspeedwriter"] = "00" * 32
    time.sleep(0.01)
    registry.write_text(yaml.safe_dump(data) + "# edited\n")
    assert any("signature" in e for e in verify_chain(path, registry_path=registry))


REAL = Path(__file__).resolve().parents[3] / "2-datacore"


@pytest.mark.skipif(not (REAL / ".datacore" / "events").is_dir(), reason="no 2-datacore space here")
def test_timing_smoke_on_the_real_2_datacore_space():
    """Printed, not asserted (run with -s to see it)."""
    t0 = time.monotonic()
    files = sorted((REAL / ".datacore" / "events").glob("*.jsonl"))
    n_err = sum(len(verify_chain(p)) for p in files)
    print(f"\nLED-6 smoke: verify 2-datacore, {len(files)} logs, {n_err} error(s), "
          f"{time.monotonic() - t0:.1f} s")
