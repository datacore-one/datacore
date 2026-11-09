"""DAY-6: Anything fixed overnight is listed as repaired. A check that couldn't
run says "could not tell", never "all clear".

Kind: deterministic. The real morning_repair collectors and recheck (the
03:30 step that writes the repairs fragment the briefing reads), on tmp
state/fragment dirs; only the host commands are faked.

Seeded failure (AM-15/17): a unit that failed at 02:00 and passes at 03:30
(must be "repaired"); a v2 check that failed at 02:00 whose log cannot be read
at 03:30 (must NOT be "repaired": nobody could tell); systemctl that cannot
run on the host (must be a finding, not an empty list). Verified red on
today's code for the last two.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import morning_repair as M

QUIET = ("red_cadences", "mail_triage", "escalations", "undelivered")


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "STATE", tmp_path / "state")
    monkeypatch.setattr(M, "FRAGMENTS", tmp_path / "frag")
    monkeypatch.setattr(M, "V2_LOG", tmp_path / "v2.log")
    monkeypatch.setattr(M, "_alert_group", lambda text: None)
    for name in QUIET:
        monkeypatch.setattr(M, name, lambda *a, **k: [])
    (tmp_path / "state").mkdir()
    return tmp_path


def _swept(host: Path, findings: list[dict]) -> None:
    day = M.datetime.now(M.timezone.utc).date().isoformat()
    (host / "state" / f"{day}.json").write_text(json.dumps({"findings": findings}))


def _fragment(host: Path) -> dict:
    day = M.datetime.now(M.timezone.utc).date().isoformat()
    return json.loads((host / "frag" / day / "repairs.json").read_text())


def _systemctl(ok: bool):
    def run(cmd, timeout=900, shell=False):
        if isinstance(cmd, list) and cmd[0] == "systemctl":
            return (0, "") if ok else (127, "systemctl: command not found")
        return 0, ""
    return run


def test_what_was_fixed_overnight_is_listed_as_repaired(host, monkeypatch):
    monkeypatch.setattr(M, "_run", _systemctl(ok=True))
    (host / "v2.log").write_text("  ok  VERSION 2.0.0\n")
    _swept(host, [{"id": "unit-x-service", "kind": "unit", "unit": "x.service",
                   "title": "unit x.service failed", "tried": "re-ran x.service (rc 0)"}])
    M.recheck()
    frag = _fragment(host)
    assert [r["title"] for r in frag["repaired"]] == ["unit x.service failed"]
    assert frag["still_failing"] == []


def test_a_check_that_could_not_run_never_counts_as_repaired(host, monkeypatch):
    monkeypatch.setattr(M, "_run", _systemctl(ok=True))
    # no v2.log at 03:30: the check could not run
    _swept(host, [{"id": "v2-egress-declared", "kind": "v2", "title": "v2-verify: egress declared",
                   "item": "repair-v2-egress-declared"}])
    M.recheck()
    frag = _fragment(host)
    repaired = [r["title"] for r in frag["repaired"]]
    assert "v2-verify: egress declared" not in repaired, (
        "the 03:30 check could not read its log, and the 02:00 failure was reported as repaired")
    text = json.dumps(frag).lower()
    assert "could not tell" in text or "egress declared" in json.dumps(frag["still_failing"]).lower()


def test_a_host_check_that_cannot_run_is_a_finding_not_all_clear(host, monkeypatch):
    monkeypatch.setattr(M, "_run", _systemctl(ok=False))
    found = M.failed_units()
    assert found, "systemctl could not run and the unit check reported nothing (all clear)"
    assert any("could not" in (f.get("title", "") + f.get("evidence", "")).lower() for f in found)
