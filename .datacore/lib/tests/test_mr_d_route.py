"""mr-d-route: owner routing for findings that need a person on another host.

Three behaviours this branch adds:
  1. A unit-failure finding is delegated to the owner, not the repairer.
  2. A finding whose evidence shows a refused login is delegated to the owner.
  3. ledger_claim refuses a dev-route item whose code_root is outside the space.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import morning_repair as M  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _setup_sweep(tmp_path, monkeypatch, finding: dict):
    """Run sweep() with a single finding and return (delegated_payload, state)."""
    monkeypatch.setattr(M, "STATE", tmp_path / "state")
    monkeypatch.setattr(M, "pull_latest", lambda: "fleet sync rc 0")
    monkeypatch.setattr(M, "findings", lambda run_v2=True: [dict(finding)])
    monkeypatch.setattr(M, "remediate", lambda f: "")

    captured: list[dict] = []

    def fake_delegate(f, day):
        captured.append(dict(f))
        return f"repair-{f['id']}"

    monkeypatch.setattr(M, "delegate", fake_delegate)
    assert M.sweep() == 0
    state = json.loads(next((tmp_path / "state").glob("*.json")).read_text())
    return captured, state


# ── test 1: unit finding routed to owner ─────────────────────────────────────

def test_unit_finding_is_delegated_to_owner_not_repairer(tmp_path, monkeypatch):
    """A failed service should go to the owner; Miles cannot run systemctl on another host."""
    monkeypatch.setattr(M, "_owner", lambda: "gregor")
    monkeypatch.setattr(M, "_repairer", lambda: "miles")

    finding = {
        "id": "unit-fleet-sync-service",
        "kind": "unit",
        "unit": "fleet-sync.service",
        "title": "unit fleet-sync.service failed",
        "evidence": "Active: failed",
    }
    captured, _ = _setup_sweep(tmp_path, monkeypatch, finding)

    assert captured, "the finding was not delegated at all"
    assert captured[0].get("owner") == "gregor", (
        f"unit finding went to {captured[0].get('owner')!r}, expected 'gregor'"
    )


# ── test 2: refused-login finding routed to owner ────────────────────────────

def test_refused_login_finding_is_delegated_to_owner(tmp_path, monkeypatch):
    """A finding whose evidence shows a refused login cannot be fixed by an agent."""
    monkeypatch.setattr(M, "_owner", lambda: "gregor")
    monkeypatch.setattr(M, "_repairer", lambda: "miles")

    finding = {
        "id": "v2-fleet-sync-login",
        "kind": "v2",
        "title": "v2-verify: fleet-sync git login",
        "evidence": "remote: Invalid username or token; push rejected",
    }
    captured, _ = _setup_sweep(tmp_path, monkeypatch, finding)

    assert captured, "the finding was not delegated at all"
    assert captured[0].get("owner") == "gregor", (
        f"refused-login finding went to {captured[0].get('owner')!r}, expected 'gregor'"
    )


# ── test 3: claim job refuses a dev item whose code_root is outside the space ─

def test_dev_item_with_code_outside_space_is_refused(tmp_path):
    """_code_outside_space returns a reason when code_root is not inside the space."""
    from ledger_claim import _code_outside_space

    space = tmp_path / "2-datacore"
    space.mkdir(parents=True)
    outside_root = tmp_path / "Data"
    outside_root.mkdir()

    # code_root outside the space → non-empty refusal reason
    reason = _code_outside_space({"code_root": str(outside_root)}, space)
    assert reason, "expected a refusal reason when code_root is outside the space"
    assert str(outside_root) in reason or "outside" in reason.lower(), reason

    # no code_root set → allowed (empty reason)
    assert _code_outside_space({}, space) == ""

    # code_root equal to the space → allowed
    assert _code_outside_space({"code_root": str(space)}, space) == ""

    # code_root inside the space → allowed
    inside = space / "subdir"
    inside.mkdir()
    assert _code_outside_space({"code_root": str(inside)}, space) == ""
