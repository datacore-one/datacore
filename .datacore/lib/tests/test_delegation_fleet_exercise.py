"""delegation_fleet_exercise: the ring is the install's own (INS-3)."""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import roster  # noqa: E402
import delegation_fleet_exercise as ex  # noqa: E402


def test_the_ring_and_forbidden_edge_come_from_the_registry(monkeypatch):
    monkeypatch.setattr(roster, "section", lambda key, path=None: {
        "ring": {"cos": ["ops"], "ops": ["cos"]}, "forbidden": ["ops", "boss"]}
        if key == "delegation_exercise" else {})
    assert ex._exercise() == ({"cos": ["ops"], "ops": ["cos"]}, ("ops", "boss"))


def test_a_fresh_install_seeds_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(roster, "section", lambda key, path=None: {})
    ring, edge = ex._exercise()
    assert ring == {} and edge is None
    monkeypatch.setattr(ex, "RING", ring)
    monkeypatch.setattr(ex, "FORBIDDEN", edge)
    assert ex.main(["seed", "--space", str(tmp_path), "--as", "anyone", "--day", "2026-09-27",
                    "--assert-refusals"]) == 0
    assert "delegates to nobody" in capsys.readouterr().out
